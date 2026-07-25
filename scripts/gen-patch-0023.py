#!/usr/bin/env python3
"""Overlay patch 0023: boot-time compressed-texture-format self-test (visionOS).

FABLE's recommendation after the BC7 crash (D-017): the BC7 abort was invisible
until a cutscene created the first BC7 texture. A create-and-destroy of a 4x4
texture of each *advertised* compressed format at gfx init turns any future
format-mapping regression into a single boot-log line (or, if a mapping is broken,
a boot-time abort with a clear preceding log line naming the format) instead of a
mid-game crash hunt.

Placed just before `g_initialized = true` in webgpu init, where `g_device` exists
and `g_bcTexturesSupported`/`g_astcTexturesSupported` are set. Logs one line per
supported class. visionOS-gated (this is where the guards bite; keeps the diff
scoped). No label on the descriptors -- nothing to trip.
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
REL = "extern/aurora/lib/webgpu/gpu.cpp"
SRC = ROOT / "vendor/dusklight" / REL

orig = SRC.read_text()

old = ('  gpu_prof::initialize();\n'
       '  resize_swapchain(size.fb_width, size.fb_height, size.native_fb_width, size.native_fb_height, true);\n'
       '  g_initialized = true;\n')
new = ('  gpu_prof::initialize();\n'
       '  resize_swapchain(size.fb_width, size.fb_height, size.native_fb_width, size.native_fb_height, true);\n'
       '#if defined(__APPLE__) && TARGET_OS_VISION\n'
       '  // Boot-time canary: create+destroy a 4x4 of each advertised compressed format.\n'
       '  // A broken format->MTLPixelFormat mapping (see the BC7 crash, patch 0021) then\n'
       '  // aborts HERE with the format named just above, not mid-cutscene.\n'
       '  {\n'
       '    struct FmtProbe { const char* name; wgpu::TextureFormat format; bool supported; };\n'
       '    const FmtProbe probes[] = {\n'
       '        {"BC1", wgpu::TextureFormat::BC1RGBAUnorm, g_bcTexturesSupported},\n'
       '        {"BC3", wgpu::TextureFormat::BC3RGBAUnorm, g_bcTexturesSupported},\n'
       '        {"BC7", wgpu::TextureFormat::BC7RGBAUnorm, g_bcTexturesSupported},\n'
       '        {"ASTC4x4", wgpu::TextureFormat::ASTC4x4Unorm, g_astcTexturesSupported},\n'
       '    };\n'
       '    for (const auto& p : probes) {\n'
       '      if (!p.supported) {\n'
       '        continue;\n'
       '      }\n'
       '      Log.info("format self-test: probing 4x4 {} ...", p.name);\n'
       '      const wgpu::TextureDescriptor desc{\n'
       '          .usage = wgpu::TextureUsage::TextureBinding | wgpu::TextureUsage::CopyDst,\n'
       '          .dimension = wgpu::TextureDimension::e2D,\n'
       '          .size = {4, 4, 1},\n'
       '          .format = p.format,\n'
       '          .mipLevelCount = 1,\n'
       '          .sampleCount = 1,\n'
       '      };\n'
       '      auto probe = g_device.CreateTexture(&desc);\n'
       '      if (probe) {\n'
       '        probe.Destroy();\n'
       '        Log.info("format self-test: {} OK", p.name);\n'
       '      } else {\n'
       '        Log.error("format self-test: {} FAILED to create", p.name);\n'
       '      }\n'
       '    }\n'
       '  }\n'
       '#endif\n'
       '  g_initialized = true;\n')

n = orig.count(old)
assert n == 1, f"expected 1 match, got {n}"
text = orig.replace(old, new)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(text); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1

out = ROOT / "overlay/patches/0023-aurora-visionos-format-self-test.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
