#!/usr/bin/env python3
"""Dawn patch 0001: compile the BC -> MTLPixelFormat mappings on iOS-family
targets (visionOS included), not just macOS.

ROOT FIX for the BC7 texture-pack abort (CRASH-2-ANSWER-FABLE.md). Dawn's Metal
backend has two guards that drifted apart upstream:

- PhysicalDeviceMTL.mm:611 ADVERTISES TextureCompressionBC on
  `DAWN_PLATFORM_IS(MACOS) || (defined(__IPHONE_16_4) &&
  __IPHONE_OS_VERSION_MIN_REQUIRED >= __IPHONE_16_4)` when the GPU reports
  supportsBCTextureCompression (YES on the M5 Vision Pro).
- UtilsMetal.mm:407 compiles the wgpu BC -> MTLPixelFormat cases ONLY under
  `#if DAWN_PLATFORM_IS(MACOS)`; every BC format on visionOS falls into a
  DAWN_UNREACHABLE() -> hard abort ("hard-abort in both debug and release",
  src/utils/assert.h:124).

So the first BC7 replacement texture created on device aborted the app, with the
noreturn call misattributed to MakeDebugName+0x0 in the backtrace. This patch
makes the mapping guard IDENTICAL to the advertisement guard. The xros 26.5 SDK
declares the BC constants (API_AVAILABLE ios(16.4), inherited by visionOS) and
runtime support is guaranteed because the feature is only advertised after
supportsBCTextureCompression returns YES.

Target: the pinned encounter/dawn checkout (AURORA_DAWN_REF 266c1cf8), which is
NOT vendored in-repo — it is fetched by AuroraDawnProvider.cmake. This patch is
applied (a) automatically on fresh fetches via the PATCH_COMMAND added by overlay
patch 0021, and (b) manually (patch -p1) to any pre-existing _deps/dawn-src.

Generator reads the dawn-src checkout given as argv[1] (default: the device
spike's _deps) and asserts the pristine anchor.
"""
import subprocess, pathlib, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
DAWN = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else \
    ROOT / "spikes/vision-device-build/_deps/dawn-src"
REL = "src/dawn/native/metal/UtilsMetal.mm"
SRC = DAWN / REL

orig = SRC.read_text()
text = orig

old = ('#if DAWN_PLATFORM_IS(MACOS)\n'
       '        case wgpu::TextureFormat::BC1RGBAUnorm:\n')
new = ('#if DAWN_PLATFORM_IS(MACOS) || \\\n'
       '    (defined(__IPHONE_16_4) && __IPHONE_OS_VERSION_MIN_REQUIRED >= __IPHONE_16_4)\n'
       '        case wgpu::TextureFormat::BC1RGBAUnorm:\n')
assert text.count(old) == 1, f"BC guard anchor: {text.count(old)} (dawn bumped or already patched?)"
assert text.count('#if DAWN_PLATFORM_IS(MACOS)') == 1, "multiple MACOS guards in UtilsMetal.mm"
text = text.replace(old, new)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(text); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1

out = ROOT / "overlay/dawn/0001-metal-bc-pixel-formats-ios16.patch"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
