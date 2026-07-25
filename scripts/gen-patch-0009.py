#!/usr/bin/env python3
"""Overlay patch 0009: make the tree configurable with the Xcode generator.

Needed for OTA. `xcodebuild archive` + `-exportArchive` is how the build gets
signed, and that requires a real .xcodeproj -- but configuring with -GXcode dies
at generate time:

    CMake Error in CMakeLists.txt:
      Xcode does not support per-config per-source COMPILE_DEFINITIONS:
        $<$<CONFIG:Debug>:DEBUG=1>
      specified for source:
        .../src/dusk/imgui/ImGuiAudio.cpp

The Xcode generator is multi-config and must emit every configuration, so it
cannot express a *per-source* define that varies *by config*. Restricting
CMAKE_CONFIGURATION_TYPES to a single type does NOT help -- tested; CMake rejects
the construct outright, not the variation.

Only this one site is affected. The sibling constructs are fine:
  - CMakeLists.txt:433 sets per-source defines that are NOT per-config.
  - CMakeLists.txt:437 uses the same $<CONFIG:Debug> genex but per-*target*,
    which Xcode supports.

Fix: under the Xcode generator only, resolve the genex up front against the
configured type(s). Ninja/Makefiles keep the genex verbatim, so every existing
build -- including our own Ninja device build that all the measurements came
from -- is byte-for-byte unchanged.

The resolved branch is deliberately conservative: DEBUG=1 is applied only if
Debug is genuinely the configured type. scripts/build-visionos-xcode.sh
configures single-config RelWithDebInfo (it exists purely to archive and sign),
so in practice it resolves to empty -- which is exactly what the genex would have
produced for that config anyway.
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
REL = "CMakeLists.txt"
SRC = ROOT / "vendor/dusklight" / REL

orig = SRC.read_text()

old = ('set_source_files_properties(\n'
       '        ${GAME_DEBUG_FILES}\n'
       '        PROPERTIES\n'
       '        COMPILE_DEFINITIONS "$<$<CONFIG:Debug>:DEBUG=1>"\n'
       ')\n')

new = ('if (CMAKE_GENERATOR STREQUAL "Xcode")\n'
       '    # The Xcode generator cannot express per-config per-source\n'
       '    # COMPILE_DEFINITIONS, so resolve the genex against the configured\n'
       '    # type(s) instead. Generator-guarded: Ninja/Makefiles keep the genex.\n'
       '    set(_dusk_game_debug_defs "")\n'
       '    if (CMAKE_BUILD_TYPE STREQUAL "Debug" OR CMAKE_CONFIGURATION_TYPES STREQUAL "Debug")\n'
       '        set(_dusk_game_debug_defs "DEBUG=1")\n'
       '    endif ()\n'
       '    set_source_files_properties(\n'
       '            ${GAME_DEBUG_FILES}\n'
       '            PROPERTIES\n'
       '            COMPILE_DEFINITIONS "${_dusk_game_debug_defs}"\n'
       '    )\n'
       'else ()\n'
       '    set_source_files_properties(\n'
       '            ${GAME_DEBUG_FILES}\n'
       '            PROPERTIES\n'
       '            COMPILE_DEFINITIONS "$<$<CONFIG:Debug>:DEBUG=1>"\n'
       '    )\n'
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

out = ROOT / "overlay/patches/0009-dusk-xcode-generator-compat.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
