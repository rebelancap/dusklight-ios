#!/usr/bin/env python3
"""Overlay patch 0021: apply the Dawn BC-mapping fix at FetchContent time.

Pairs with overlay/dawn/0001-metal-bc-pixel-formats-ios16.patch (see
gen-dawn-patch-0001.py for the root-cause writeup: Dawn advertises
TextureCompressionBC on visionOS but compiles the BC -> MTLPixelFormat cases
macOS-only, so the first BC7 texture create hits DAWN_UNREACHABLE -> abort).

Dawn is not vendored: AuroraDawnProvider.cmake FetchContent-fetches the pinned
encounter/dawn tarball into each build dir's _deps. This patch adds a
PATCH_COMMAND so the populate step applies the fix. The command is IDEMPOTENT,
mirroring apply-overlay.sh's probe logic: try --forward; if that fails, accept
only if a reverse dry-run proves the patch is already applied; otherwise fail
the configure loudly. Guarded by if(EXISTS) so the aurora tree still configures
standalone (outside this repo).

WHY idempotent (learned the hard way, 2026-07-18): the patch step re-runs on
EXISTING _deps source dirs whenever the declare args change, not just on fresh
fetches. A plain `patch -p1` run on an already-patched tree is detected by
non-interactive patch(1) as "previously applied" and is silently REVERSE-
APPLIED with exit 0 -- the first build after this patch landed reverted the
Dawn fix while the build stayed green (caught by the disassembly check: the
rebuilt MetalPixelFormat still had no BC constants).
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
REL = "extern/aurora/cmake/AuroraDawnProvider.cmake"
SRC = ROOT / "vendor/dusklight" / REL

orig = SRC.read_text()
text = orig

old = ('    include(FetchContent)\n'
       '    FetchContent_Declare(dawn\n'
       '      URL "https://github.com/encounter/dawn/archive/${AURORA_DAWN_REF}.tar.gz"\n'
       '      DOWNLOAD_EXTRACT_TIMESTAMP FALSE\n'
       '      EXCLUDE_FROM_ALL\n'
       '    )\n')
new = ('    include(FetchContent)\n'
       '    # Dusklight: Dawn advertises TextureCompressionBC on visionOS but its\n'
       '    # Metal format map compiles BC cases macOS-only -> DAWN_UNREACHABLE abort\n'
       '    # on the first BC7 texture. Apply the guard fix at populate time.\n'
       '    # Idempotent (the step re-runs on existing source dirs when declare args\n'
       '    # change): forward-apply, else verify already-applied via reverse dry-run,\n'
       '    # else fail the configure loudly (e.g. context mismatch after a dawn bump).\n'
       '    set(_dusk_dawn_bc_patch\n'
       '      "${CMAKE_CURRENT_LIST_DIR}/../../../../../overlay/dawn/0001-metal-bc-pixel-formats-ios16.patch")\n'
       '    set(_dusk_dawn_patch_cmd "")\n'
       '    if (EXISTS "${_dusk_dawn_bc_patch}")\n'
       '      set(_dusk_dawn_patch_cmd PATCH_COMMAND sh -c\n'
       '        "patch -p1 --forward --force --fuzz=0 -i \'${_dusk_dawn_bc_patch}\' || patch -p1 -R --force --fuzz=0 --dry-run -i \'${_dusk_dawn_bc_patch}\' > /dev/null")\n'
       '    endif ()\n'
       '    FetchContent_Declare(dawn\n'
       '      URL "https://github.com/encounter/dawn/archive/${AURORA_DAWN_REF}.tar.gz"\n'
       '      DOWNLOAD_EXTRACT_TIMESTAMP FALSE\n'
       '      ${_dusk_dawn_patch_cmd}\n'
       '      EXCLUDE_FROM_ALL\n'
       '    )\n')
assert text.count(old) == 1, f"FetchContent anchor: {text.count(old)}"
text = text.replace(old, new)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(text); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1

out = ROOT / "overlay/patches/0021-aurora-dawn-bc-mapping-fetchpatch.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
