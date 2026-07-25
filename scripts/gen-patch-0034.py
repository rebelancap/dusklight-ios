#!/usr/bin/env python3
"""Overlay patch 0034 (Phase 2 / M3+M4): enable Dawn's shared-texture-memory features.

STEREO-3D-GUIDE §3.4, Q-004 interop. The eye-render (patch 0032, aurora.cpp) imports
the composited game frame into an IOSurface-backed wgpu::Texture via
device.ImportSharedTextureMemory. Dawn requires the SharedTextureMemoryIOSurface
feature (+ SharedFenceMTLSharedEvent for fenced hand-off) to be enabled at device
creation or the import aborts. Enable them where the adapter advertises them.

Kept separate from 0032 because it touches a different file (webgpu/gpu.cpp) with no
adjacency to 0032's aurora.cpp hunks.
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent

REL_GPU = "extern/aurora/lib/webgpu/gpu.cpp"
gpu = (ROOT / "vendor/dusklight" / REL_GPU).read_text()
gpu_old = ('        requiredFeatures.push_back(feature);\n'
           '      }\n'
           '#ifdef TRACY_ENABLE\n'
           '      if (feature == wgpu::FeatureName::TimestampQuery) {\n'
           '        requiredFeatures.push_back(feature);\n'
           '      }\n'
           '#endif\n')
gpu_new = ('        requiredFeatures.push_back(feature);\n'
           '      }\n'
           '      // Phase 2 (visionOS 3D): the eye-render imports the game frame via an\n'
           '      // IOSurface-backed shared texture (+ MTLSharedEvent fences). Enable the\n'
           '      // features where the adapter has them so ImportSharedTextureMemory is granted.\n'
           '      if (feature == wgpu::FeatureName::SharedTextureMemoryIOSurface ||\n'
           '          feature == wgpu::FeatureName::SharedFenceMTLSharedEvent) {\n'
           '        requiredFeatures.push_back(feature);\n'
           '      }\n'
           '#ifdef TRACY_ENABLE\n'
           '      if (feature == wgpu::FeatureName::TimestampQuery) {\n'
           '        requiredFeatures.push_back(feature);\n'
           '      }\n'
           '#endif\n')
assert gpu.count(gpu_old) == 1, f"gpu anchor: {gpu.count(gpu_old)}"
gpu_after = gpu.replace(gpu_old, gpu_new)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(gpu); fb.write(gpu_after); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL_GPU}", "--label", f"b/{REL_GPU}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1, "no diff"

out = ROOT / "overlay/patches/0034-aurora-visionos-eye-render.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
