#!/usr/bin/env python3
"""Overlay patch 0022: poll the window size each frame on visionOS (fill + resize).

In-game screenshots show the game not filling the rounded visionOS panel
(a gap along the bottom), in menu and gameplay alike, and it does not re-fit when
the panel is resized. Root cause: aurora only resizes the swapchain in response to
`SDL_EVENT_WINDOW_PIXEL_SIZE_CHANGED` (window.cpp `process_event`), and visionOS
does NOT reliably deliver that event when the user drags the floating window. So
the framebuffer stays at its startup size while the panel changes -> the presented
image no longer matches the window -> gap / letterbox, and no redraw on resize.

Fix: on visionOS, call `resize_swapchain()` once at the top of every `poll_events()`
(which the frame loop already calls each frame via `aurora::update()`). It is
idempotent -- `window::resize_swapchain()` early-outs when `get_window_size() ==
g_windowSize`, and `webgpu::resize_swapchain_internal` early-outs when neither the
surface nor the framebuffer size changed -- so the steady-state cost is one
`SDL_GetWindowSizeInPixels` call. When the panel actually changes, the poll catches
it (the direct size query is up to date even though the event never fired), the
swapchain + framebuffers re-create at the new size, and the game's own
`updateRenderSize()` re-derives its aspect and fills the new window. That is the
"redraw itself when you go tall/wide" behavior (matches Ship of Harkinian).

visionOS-gated; other platforms keep the event-driven path (their resize events
are reliable). Two hunks: the TargetConditionals include and the poll.
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
REL = "extern/aurora/lib/window.cpp"
SRC = ROOT / "vendor/dusklight" / REL

orig = SRC.read_text()
text = orig

# --- hunk 1: TargetConditionals include --------------------------------------
inc_old = ('#include "window.hpp"\n'
           '\n'
           '#ifdef AURORA_ENABLE_GX\n')
inc_new = ('#include "window.hpp"\n'
           '\n'
           '#ifdef __APPLE__\n'
           '#include <TargetConditionals.h>\n'
           '#endif\n'
           '\n'
           '#ifdef AURORA_ENABLE_GX\n')
assert text.count(inc_old) == 1, f"include anchor: {text.count(inc_old)}"
text = text.replace(inc_old, inc_new)

# --- hunk 2: poll resize at the top of poll_events ---------------------------
poll_old = ('const AuroraEvent* poll_events() {\n'
            '  ZoneScoped;\n'
            '  g_events.clear();\n')
poll_new = ('const AuroraEvent* poll_events() {\n'
            '  ZoneScoped;\n'
            '  g_events.clear();\n'
            '\n'
            '#if defined(__APPLE__) && TARGET_OS_VISION\n'
            '  // visionOS does not reliably deliver SDL_EVENT_WINDOW_PIXEL_SIZE_CHANGED on a\n'
            '  // panel resize, so poll the swapchain into sync every frame. resize_swapchain()\n'
            '  // early-outs when the size is unchanged, so this is cheap; when the window does\n'
            '  // change, the framebuffers re-create and the game refits (fill + resize-redraw).\n'
            '  resize_swapchain();\n'
            '#endif\n')
assert text.count(poll_old) == 1, f"poll anchor: {text.count(poll_old)}"
text = text.replace(poll_old, poll_new)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(text); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1

out = ROOT / "overlay/patches/0022-aurora-visionos-poll-window-resize.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
