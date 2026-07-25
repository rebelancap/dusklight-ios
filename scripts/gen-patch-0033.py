#!/usr/bin/env python3
"""Overlay patch 0033: auto-discover a disc image in the data dir on boot.

Reported: "I continue to get asked to select a disc image on every install." The
disc-persistence fix (0028) makes a selected disc survive RELAUNCHES and crashes,
but a fresh install / full delete wipes the app container entirely (iOS
sandboxing -- nothing an app can do), so the stored path AND the disc file are
gone and the picker returns.

This makes the Files drop-off pipeline the real path (charter: "Files-app
drop-off is the entire asset story"): on boot, if no valid disc is configured,
scan the data dir (Documents, per patch 0004) for a valid disc image and use it.
So a disc that survived an app UPDATE is picked up even if the stored path was
lost, and after a delete the user just drops the disc back into the Dusklight
folder via Files -- no picker, no in-app verification navigation. Only a metadata
`iso::inspect` is used (fast, header-only); full hash verification stays with the
prelaunch UI. Non-recursive top-level scan (the disc lives in the data dir root,
next to config.json; texture_replacements/ etc. are skipped).

Placed right after the existing bad-path invalidation (m_Do_main.cpp ~725),
before the empty path forces the prelaunch UI. Clear of every other patch's
hunks (0015 touches this file only near line 568).
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
REL = "src/m_Do/m_Do_main.cpp"
SRC = ROOT / "vendor/dusklight" / REL

orig = SRC.read_text()

old = ('        forcePreLaunchUI = true;\n'
       '        saveConfigBeforePrelaunch = true;\n'
       '    }\n'
       '\n'
       '    std::string dvd_path = dusk::getSettings().backend.isoPath;\n')

new = ('        forcePreLaunchUI = true;\n'
       '        saveConfigBeforePrelaunch = true;\n'
       '    }\n'
       '\n'
       '    // Auto-discover a disc in the data dir when none is configured (fresh install,\n'
       '    // cleared bad path, or a disc just dropped in via Files). Makes the Files\n'
       '    // drop-off pipeline work without the picker, and recovers a disc that survived\n'
       '    // an app update even if the stored path was lost. A full delete still wipes the\n'
       '    // container (iOS sandboxing), but dropping the disc back into the Dusklight\n'
       '    // folder is then enough -- no picker. Metadata-only inspect (header-read, fast);\n'
       '    // full hash verification stays with the prelaunch UI.\n'
       '    if (dusk::getSettings().backend.isoPath.getValue().empty()) {\n'
       '        std::error_code scanEc;\n'
       '        const auto dataDir = dusk::data::configured_data_path();\n'
       '        std::filesystem::directory_iterator scanIt(dataDir, scanEc);\n'
       '        const std::filesystem::directory_iterator scanEnd;\n'
       '        for (; !scanEc && scanIt != scanEnd; scanIt.increment(scanEc)) {\n'
       '            if (!scanIt->is_regular_file(scanEc)) {\n'
       '                continue;\n'
       '            }\n'
       '            dusk::iso::DiscInfo scanInfo{};\n'
       '            if (dusk::iso::inspect(scanIt->path().string().c_str(), scanInfo) ==\n'
       '                dusk::iso::ValidationError::Success) {\n'
       '                const std::string found = dusk::io::fs_path_to_string(scanIt->path());\n'
       '                DuskLog.info("Auto-discovered a valid disc image in the data dir: {}", found);\n'
       '                dusk::getSettings().backend.isoPath.setValue(found);\n'
       '                dusk::getSettings().backend.isoVerification.setValue(\n'
       '                    dusk::DiscVerificationState::Unknown);\n'
       '                discInfo = scanInfo;\n'
       '                forcePreLaunchUI = false;\n'
       '                saveConfigBeforePrelaunch = true;\n'
       '                break;\n'
       '            }\n'
       '        }\n'
       '    }\n'
       '\n'
       '    std::string dvd_path = dusk::getSettings().backend.isoPath;\n')

assert orig.count(old) == 1, f"anchor: {orig.count(old)}"
text = orig.replace(old, new)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(text); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1

out = ROOT / "overlay/patches/0033-dusk-visionos-auto-discover-disc.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
