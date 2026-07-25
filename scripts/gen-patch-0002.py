#!/usr/bin/env python3
"""Overlay patch 0002: build aurora's device_ios.mm on visionOS.

aurora_core.cmake compiles lib/device_ios.mm (the CoreHaptics rumble backend)
only under `if (IOS)`. ios.toolchain.cmake sets IOS=OFF for visionOS, so it is
skipped -- but the *C++* side still expects it: device.cpp only compiles its
no-op rumble stub under

    #elif !defined(SDL_PLATFORM_IOS) || defined(SDL_PLATFORM_TVOS)

and SDL3 defines SDL_PLATFORM_IOS on visionOS as well (it keys off
TARGET_OS_IPHONE, which is 1 on xrOS; SDL's own doc comment reads "defined if
compiling for iOS or visionOS"). So on visionOS *neither* implementation is
compiled and the link fails with an undefined aurora::device::rumble.

Fixing it CMake-side rather than widening the C++ gate keeps the change off the
shared device.cpp path that iOS depends on, and is consistent with treating
visionOS as UIKit-family (patch 0001). CoreHaptics is present in the xrOS SDK,
so this links; Vision Pro has no haptics hardware, and device_ios.mm already
gates every entry point on a runtime capability check
(CHHapticEngine.capabilitiesForHardware.supportsHaptics), so rumble_available()
simply reports false there. No behavior change for iOS.
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
REL = "extern/aurora/cmake/aurora_core.cmake"
SRC = ROOT / "vendor/dusklight" / REL

orig = SRC.read_text()

old = ('if (IOS)\n'
       '    find_library(COREHAPTICS_FRAMEWORK CoreHaptics REQUIRED)\n')
new = ('if (IOS OR VISIONOS)\n'
       '    find_library(COREHAPTICS_FRAMEWORK CoreHaptics REQUIRED)\n')

n = orig.count(old)
assert n == 1, f"expected 1 match, got {n}"
text = orig.replace(old, new)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(text); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1

out = ROOT / "overlay/patches/0002-aurora-device-ios-on-visionos.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
