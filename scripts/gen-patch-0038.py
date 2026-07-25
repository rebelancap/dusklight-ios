#!/usr/bin/env python3
"""Overlay patch 0038 (Phase 2 / experience): framebuffer aspect override for the panel.

Device feedback: the 3D panel stretched/squeezed the picture at non-default
width/height (unlike 2D, which renders undistorted at any window shape). Root cause:
the game renders into a fixed-aspect framebuffer that is then mapped onto the panel
quad -- any quad aspect != render aspect => stretch.

Fix: let the shell force the framebuffer (render) aspect to the panel's aspect, so
the game renders AT the panel shape (undistorted at any width/height, exactly like
2D). aurora.cpp exposes Dusk3D_SetPanelAspect -> window::set_frame_buffer_aspect_override
(patch 0032); the shell drives it from the Width/Height sliders and clears it (0) on
3D exit. All edits are in pristine upstream regions of window.cpp/.hpp (no existing
patch touches the frame-buffer-scale code).
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor/dusklight/extern/aurora"

EDITS = {
    "lib/window.cpp": [
        ('float g_frameBufferScale = 0.f;\n'
         'bool g_frameBufferAspectFit = false;\n',
         'float g_frameBufferScale = 0.f;\n'
         'bool g_frameBufferAspectFit = false;\n'
         '// visionOS 3D: force the framebuffer (render) aspect to the panel\'s, so the game\n'
         '// renders AT the panel shape (undistorted at any aspect, like 2D) instead of being\n'
         '// stretched onto the quad. 0 = use the window/drawable aspect. Cleared on 3D exit.\n'
         'float g_frameBufferAspectOverride = 0.f;\n'),
        ('  if (g_frameBufferScale > 0.f) {\n'
         '    const auto [baseW, baseH] = vi::configured_fb_size();\n'
         '    const auto [scaledW, scaledH] =\n'
         '        scale_frame_buffer_to_aspect(static_cast<int>(baseW), static_cast<int>(baseH), g_frameBufferScale,\n'
         '                                     static_cast<float>(fb_w) / static_cast<float>(fb_h));\n',
         '  if (g_frameBufferScale > 0.f) {\n'
         '    const auto [baseW, baseH] = vi::configured_fb_size();\n'
         '    const float fbAspect = g_frameBufferAspectOverride > 0.f\n'
         '                               ? g_frameBufferAspectOverride\n'
         '                               : static_cast<float>(fb_w) / static_cast<float>(fb_h);\n'
         '    const auto [scaledW, scaledH] =\n'
         '        scale_frame_buffer_to_aspect(static_cast<int>(baseW), static_cast<int>(baseH), g_frameBufferScale, fbAspect);\n'),
        ('void set_frame_buffer_aspect_fit(bool fit) {\n'
         '  if (g_frameBufferAspectFit == fit) {\n'
         '    return;\n'
         '  }\n'
         '\n'
         '  g_frameBufferAspectFit = fit;\n'
         '  request_frame_buffer_resize();\n'
         '}\n',
         'void set_frame_buffer_aspect_fit(bool fit) {\n'
         '  if (g_frameBufferAspectFit == fit) {\n'
         '    return;\n'
         '  }\n'
         '\n'
         '  g_frameBufferAspectFit = fit;\n'
         '  request_frame_buffer_resize();\n'
         '}\n'
         '\n'
         'void set_frame_buffer_aspect_override(float aspect) {\n'
         '  if (aspect < 0.f) {\n'
         '    aspect = 0.f;\n'
         '  }\n'
         '  if (g_frameBufferAspectOverride == aspect) {\n'
         '    return;\n'
         '  }\n'
         '  g_frameBufferAspectOverride = aspect;\n'
         '  request_frame_buffer_resize();\n'
         '}\n'),
    ],
    "lib/window.hpp": [
        ('void set_frame_buffer_scale(float scale);\n'
         'void set_frame_buffer_aspect_fit(bool fit);\n',
         'void set_frame_buffer_scale(float scale);\n'
         'void set_frame_buffer_aspect_fit(bool fit);\n'
         'void set_frame_buffer_aspect_override(float aspect);\n'),
    ],
}

chunks = []
for rel, edits in EDITS.items():
    orig = (VENDOR / rel).read_text()
    text = orig
    for old, new in edits:
        assert text.count(old) == 1, f"{rel}: anchor {old[:52]!r} count {text.count(old)}"
        text = text.replace(old, new)
    with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
         tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
        fa.write(orig); fb.write(text); fa.flush(); fb.flush()
        r = subprocess.run(["diff", "-u", "--label", f"a/extern/aurora/{rel}",
                            "--label", f"b/extern/aurora/{rel}", fa.name, fb.name], capture_output=True)
    assert r.returncode == 1, f"{rel}: no diff"
    chunks.append(r.stdout.decode())

out = ROOT / "overlay/patches/0038-aurora-visionos-fb-aspect.patch"
out.write_text(__doc__ + "\n" + "".join(chunks))
print(f"wrote {out}")
