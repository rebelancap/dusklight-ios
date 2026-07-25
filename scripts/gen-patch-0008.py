#!/usr/bin/env python3
"""Overlay patch 0008: let the bundle identifier be overridden for signing.

CMakeLists.txt hardcodes:

    set(DUSK_BUNDLE_IDENTIFIER dev.twilitrealm.dusk)

A plain set() creates a *normal* variable that shadows any -D cache value, so the
identifier cannot be overridden from the command line at all.

That matters for signing. `dev.twilitrealm.dusk` is upstream's namespace, and
Apple App IDs are globally unique, so a downstream signing identity generally
cannot register or sign it. This makes the identifier a build-time override
(`-DDUSK_BUNDLE_IDENTIFIER=...`) so a device build can sign under its own team
and id without touching the vendor tree.

Guarded so it is completely inert upstream: without an override the identifier is
byte-for-byte what it was. Same pattern as DUSK_VISIONOS_RESOURCE_DIR in patch
0001 -- keep the port's local facts out of the vendor tree.
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
REL = "CMakeLists.txt"
SRC = ROOT / "vendor/dusklight" / REL

orig = SRC.read_text()

old = ('set(DUSK_BUNDLE_NAME Dusklight)\n'
       'set(DUSK_BUNDLE_IDENTIFIER dev.twilitrealm.dusk)\n')
new = ('set(DUSK_BUNDLE_NAME Dusklight)\n'
       '# Overridable: Apple App IDs are globally unique, so a downstream signing\n'
       '# identity cannot use upstream\'s namespace. Inert unless -D is passed.\n'
       'if (NOT DUSK_BUNDLE_IDENTIFIER)\n'
       '    set(DUSK_BUNDLE_IDENTIFIER dev.twilitrealm.dusk)\n'
       'endif ()\n')

n = orig.count(old)
assert n == 1, f"expected 1 match, got {n}"
text = orig.replace(old, new)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(text); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1

out = ROOT / "overlay/patches/0008-dusk-bundle-id-overridable.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
