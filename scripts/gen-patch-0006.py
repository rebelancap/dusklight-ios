#!/usr/bin/env python3
"""Overlay patch 0006: push the visionOS drawable toward a 3840 long edge.

VISION-PRO-GUIDE 1.2 -- "the single biggest 2D fidelity lever" on visionOS is
rendering into a high-resolution drawable and letting the compositor supersample
it down. A window rendered at ~1400px and warped up looks soft; one rendered at
3840 and downfiltered looks sharp.

aurora already does the *other* half correctly: it creates its window with
SDL_WINDOW_HIGH_PIXEL_DENSITY (window.cpp:260), so the guide's "SDL renders at
1/3 res" trap does not apply here. What is left is the drawable size itself.
Measured on the Vision Pro 4K simulator:

    [INFO | aurora] Using framebuffer size 2432x1792 scale 2

2432 is the scene's backing size, well under the guide's 3840 target.

HOW THIS PLUMBS THROUGH (see MEASUREMENTS.md frame map):
    get_window_size() -> resize_swapchain(fb_w, fb_h, native_fb_w, native_fb_h)
      native_fb -> surfaceConfiguration.width/height -> Dawn -> CAMetalLayer.drawableSize
      fb        -> the game's own render target (g_frameBuffer)
So inflating native_fb raises BOTH the drawable and the game's render resolution,
which is precisely what the guide asks for ("then tell the renderer to render at
that size"). fb_w/fb_h are seeded from native_fb before g_frameBufferScale is
applied, so the existing Render-Scale knob still works relative to the new size.

WHY `scale` IS MULTIPLIED TOO (this is the subtle part):
rmlui -- which draws the prelaunch UI and settings -- maps *input* through the
derived ratio native_fb/width (rmlui.cpp:155,161), so that stays self-consistent
under any push. But it sizes the UI itself with `size.scale`
(rmlui.cpp:67: `s_uiScale > 0 ? s_uiScale : window::get_window_size().scale`),
which is SDL's display scale and does NOT follow native_fb. Push native_fb
without scale and the UI keeps rendering at 2x inside a 3.16x buffer -- i.e. the
menus visibly shrink. Multiplying scale by the same factor keeps the UI's
physical size constant. (Shipwright needed an equivalent imgui-framebuffer-scale
fix; same class of coupling.)

SAFE ON THIS PATH: g_renderer is only created when the backend is
wgpu::BackendType::Null (aurora.cpp:158), so on our Metal build it is nullptr and
resize_swapchain's SDL_SetRenderLogicalPresentation/SDL_SetRenderScale branch --
the other consumer of native_fb -- never runs.

visionOS-only: guarded on TARGET_OS_VISION, so iOS/tvOS/macOS/Windows/Linux/
Android are byte-for-byte unaffected. TargetConditionals.h arrives via SDL3's
headers on Apple; the #if is inside an `#ifdef __APPLE__` for safety, and an
undefined identifier evaluates to 0 in #if anyway.
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
REL = "extern/aurora/lib/window.cpp"
SRC = ROOT / "vendor/dusklight" / REL

orig = SRC.read_text()
text = orig

EDITS = [
    # 1. Include TargetConditionals explicitly rather than relying on transitive SDL.
    (
        '#include "window.hpp"\n',
        '#include "window.hpp"\n'
        '\n'
        '#ifdef __APPLE__\n'
        '#include <TargetConditionals.h>\n'
        '#endif\n',
        1,
    ),
    # 2. The push itself, right after SDL reports the backing size and before
    #    fb_w/fb_h are seeded from it.
    (
        '  ASSERT(SDL_GetWindowSizeInPixels(g_window, &native_fb_w, &native_fb_h), "Failed to get window size in pixels: {}",\n'
        '         SDL_GetError());\n'
        '\n'
        '  int fb_w = native_fb_w;\n'
        '  int fb_h = native_fb_h;\n',

        '  ASSERT(SDL_GetWindowSizeInPixels(g_window, &native_fb_w, &native_fb_h), "Failed to get window size in pixels: {}",\n'
        '         SDL_GetError());\n'
        '\n'
        '  float visionDrawablePush = 1.f;\n'
        '#if defined(__APPLE__) && TARGET_OS_VISION\n'
        '  // visionOS composites and supersamples this window, so drawable resolution\n'
        '  // is the main 2D fidelity lever: SDL reports the scene backing size (~2432\n'
        '  // long edge), which reads soft. Push the long edge up to kVisionLongEdge and\n'
        '  // let the compositor downfilter. Raises the drawable AND the game render\n'
        '  // target, since fb_w/fb_h are seeded from native_fb just below.\n'
        '  {\n'
        '    constexpr int kVisionLongEdge = 3840;\n'
        '    const int longEdge = native_fb_w > native_fb_h ? native_fb_w : native_fb_h;\n'
        '    if (longEdge > 0 && longEdge < kVisionLongEdge) {\n'
        '      visionDrawablePush = static_cast<float>(kVisionLongEdge) / static_cast<float>(longEdge);\n'
        '      native_fb_w = static_cast<int>(static_cast<float>(native_fb_w) * visionDrawablePush + 0.5f);\n'
        '      native_fb_h = static_cast<int>(static_cast<float>(native_fb_h) * visionDrawablePush + 0.5f);\n'
        '    }\n'
        '  }\n'
        '#endif\n'
        '\n'
        '  int fb_w = native_fb_w;\n'
        '  int fb_h = native_fb_h;\n',
        1,
    ),
    # 3. Keep the UI physically the same size: rmlui sizes itself with `scale`,
    #    which would otherwise stay at SDL's 2x inside the enlarged buffer.
    (
        '  const float scale = SDL_GetWindowDisplayScale(g_window);\n',
        '  // rmlui sizes the UI with `scale` but maps input through the derived\n'
        '  // native_fb/width ratio; carry the drawable push into scale so the menus\n'
        '  // keep their physical size instead of shrinking inside the bigger buffer.\n'
        '  const float scale = SDL_GetWindowDisplayScale(g_window) * visionDrawablePush;\n',
        1,
    ),
]

for old, new, want in EDITS:
    n = text.count(old)
    assert n == want, f"expected {want} match(es), got {n} for:\n{old[:160]!r}"
    text = text.replace(old, new)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(text); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1

out = ROOT / "overlay/patches/0006-aurora-visionos-drawable-3840.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
