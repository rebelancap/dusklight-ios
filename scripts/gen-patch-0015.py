#!/usr/bin/env python3
"""Overlay patch 0015: put the file log in Documents on visionOS (Files-visible).

Charter rule 5 makes file logs the remote-diagnosis floor, and the last device
crash (the 4K bad_alloc) was only diagnosable because a log sat somewhere
grabbable. Right now it does NOT: `InitializeFileLogging` is called with
`dusk::CachePath` (the pref path = Library/Application Support), which is **not
reachable from the Files app** on visionOS. So when the 4K crash hits
there is "no new log" to pull -- exactly what happened this round.

Fix: on visionOS, log into `dusk::ConfigPath` (the data path = Documents, made
Files-visible by patch 0004) instead of CachePath. The `logs/` folder then shows
up under Files -> On My Apple Vision Pro -> Dusklight and the user can retrieve
it. Every other platform keeps logging to CachePath (the log has no business in a
desktop user's Documents).

TargetConditionals.h is already included in m_Do_main.cpp (line ~100); no new
include needed. One-line, visionOS-gated.
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
REL = "src/m_Do/m_Do_main.cpp"
SRC = ROOT / "vendor/dusklight" / REL

orig = SRC.read_text()

old = ('    dusk::CachePath = dataPaths.cachePath;\n'
       '    dusk::InitializeFileLogging(dusk::CachePath, startupLogLevel);\n')
new = ('    dusk::CachePath = dataPaths.cachePath;\n'
       '#if defined(__APPLE__) && TARGET_OS_VISION\n'
       '    // Library/Application Support is not reachable from the Files app on\n'
       '    // visionOS, so a device crash would leave no grabbable log. Log into the\n'
       '    // data path (Documents, Files-visible via patch 0004) so it can be pulled\n'
       '    // for remote diagnosis (charter rule 5).\n'
       '    dusk::InitializeFileLogging(dusk::ConfigPath, startupLogLevel);\n'
       '#else\n'
       '    dusk::InitializeFileLogging(dusk::CachePath, startupLogLevel);\n'
       '#endif\n')

n = orig.count(old)
assert n == 1, f"expected 1 match, got {n}"
text = orig.replace(old, new)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(text); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1

out = ROOT / "overlay/patches/0015-dusk-visionos-logs-to-documents.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
