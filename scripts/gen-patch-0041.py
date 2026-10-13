#!/usr/bin/env python3
"""Overlay patch 0041: stop the mod catalog/loader from treating visionOS as macOS.

New with the 2026-10 upstream sync, which added an online mod catalog
(src/dusk/mods/catalog.cpp) and native-code mod loading (mods/loader/natives.cpp)
that are compiled in even with DUSK_ENABLE_CODE_MODS=OFF. Both pick the platform
with TARGET_OS_IOS, which is 0 on visionOS (D-009), so xrOS fell through to the
`__APPLE__ && !TARGET_OS_TV && __aarch64__` branch and called itself "macos-arm64":

  * catalog_platform() would ask the catalog for macOS packages, and
  * supports_native_installs() said TRUE -- offering to download a macOS dylib that
    a sandboxed visionOS app can never load (it is unsigned and the wrong platform);
  * natives.cpp would look for "native/macos-arm64/" libraries inside mods.

Fix, visionOS-only:
  1. catalog.cpp: visionOS reports the iOS package platform ("ios-arm64") -- the
     same UIKit-family answer iOS gives -- and, like iOS/tvOS, does NOT support
     native installs ("Native libraries must be bundled and signed with the app").
     Asset-only mods (texture/audio packs) still browse and install.
  2. natives.cpp: visionOS has NO native mod platform (empty string), so the loader
     never tries to dlopen anything; a mod that needs natives reports "Mod not
     supported on this platform", which is the truth on this port.

Conservative by construction: it only removes a capability that could not work.
Every other platform evaluates exactly as before.
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor/dusklight"

OUT = "0041-dusk-visionos-mods-not-macos.patch"

EDITS = [
    ("src/dusk/mods/catalog.cpp", [
        ('#elif defined(__APPLE__) && TARGET_OS_IOS\n'
         '    return "ios-arm64";\n',
         '#elif defined(__APPLE__) && (TARGET_OS_IOS || TARGET_OS_VISION)\n'
         '    return "ios-arm64";\n'),
        ('#if defined(__APPLE__) && (TARGET_OS_IOS || TARGET_OS_TV)\n'
         '    // Native libraries must be bundled and signed with the app.\n',
         '#if defined(__APPLE__) && (TARGET_OS_IOS || TARGET_OS_TV || TARGET_OS_VISION)\n'
         '    // Native libraries must be bundled and signed with the app.\n'),
    ]),
    ("src/dusk/mods/loader/natives.cpp", [
        ('#if TARGET_OS_IOS\n'
         'static constexpr std::string_view k_nativePlatform = "ios-arm64"sv;\n',
         '#if TARGET_OS_VISION\n'
         '// visionOS: no loadable native mod platform (sandboxed, unsigned dylibs never load).\n'
         'static constexpr std::string_view k_nativePlatform = ""sv;\n'
         '#elif TARGET_OS_IOS\n'
         'static constexpr std::string_view k_nativePlatform = "ios-arm64"sv;\n'),
    ]),
]

chunks = []
for rel, edits in EDITS:
    src = VENDOR / rel
    orig = src.read_text()
    text = orig
    for e in edits:
        old, new = e[0], e[1]
        want = e[2] if len(e) > 2 else 1
        n = text.count(old)
        assert n == want, f"{rel}: expected {want} match(es), got {n} for:\n{old[:160]!r}"
        text = text.replace(old, new)
    with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
         tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
        fa.write(orig); fb.write(text); fa.flush(); fb.flush()
        r = subprocess.run(["diff", "-u", "--label", f"a/{rel}", "--label", f"b/{rel}",
                            fa.name, fb.name], capture_output=True)
    assert r.returncode == 1, f"{rel}: diff produced no change"
    chunks.append(r.stdout.decode())

out = ROOT / "overlay/patches/" / OUT
out.write_text(__doc__ + "\n" + "".join(chunks))
print(f"wrote {out} ({len(chunks)} file(s))")
