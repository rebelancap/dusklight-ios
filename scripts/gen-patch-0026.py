#!/usr/bin/env python3
"""Overlay patch 0026: glue the visionOS UIWindow/root view to the scene's real
geometry (fixes the off-center top+right clip; enables resize-to-fill).

ROOT CAUSE (WINDOW-BRIEF / WINDOW-ANSWER-FABLE.md): on visionOS the *scene*
owns the panel geometry (user-resizable, size restored by the system), but
SDL3's UIKit backend models a virtual 1280x720 display and never reconciles
with the scene:

- `UIKit_ComputeViewFrame` (visionOS) returns CGRectMake(window->x, window->y,
  w, h) -- SDL's window position on the FAKE display leaks in as a frame
  origin. A centered 1216x896 request on the virtual 1280x720 display yields
  origin (32, -88): gap left, clip top; a user panel narrower/taller than that
  rect adds the right clip and bottom gap. Exactly the observed asymmetry.
- Nothing observes scene-geometry changes, so `SDL_GetWindowSizeInPixels`
  (root view bounds x hardcoded 2.0) never changes on a panel resize -- which
  is why patch 0022's per-frame resize_swapchain() poll saw nothing.

SoH hit the identical bug ("the content renders cropped/offset") and fixed it
in its SDL2 compat patch by setting `self.frame = scene.coordinateSpace.bounds`
in the UIWindow's layoutSubviews (Shipwright-ios overlay patch 0021, SDL_uikitwindow.m
hunk). SDL3 exposes the UIWindow via SDL_PROP_WINDOW_UIKIT_WINDOW_POINTER, so we
apply the same glue from aurora each frame instead of patching SDL:

  scene.coordinateSpace.bounds -> uiwindow.frame -> root view frame ->
  SDL metal-view layoutSubviews -> drawableSize = bounds x 2 ->
  patch 0022's resize_swapchain() poll picks up the new pixel size ->
  Dawn reconfigures -> present viewport fills the panel.

Both frame fixes are idempotent per-frame compares (no-ops when already in
sync, e.g. in the simulator where the scene matches the request). The window
is also late-adopted onto the scene if it was created before the scene
connected (SDL's fallback 1280x720 window path). Every change -- and the first
call -- logs a geometry snapshot to the app file log, so the next device log
is the artifact that settles the model.
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
REL_MB = "extern/aurora/lib/dawn/MetalBinding.mm"
REL_WIN = "extern/aurora/lib/window.cpp"
SRC_MB = ROOT / "vendor/dusklight" / REL_MB
SRC_WIN = ROOT / "vendor/dusklight" / REL_WIN

# ---------------- MetalBinding.mm ----------------
mb_orig = SRC_MB.read_text()
mb = mb_orig

# NOTE: the SDL3_properties/SDL3_video/cstdio includes live in the tail block
# below (inside the TARGET_OS_VISION guard, next to the function that uses them)
# rather than in the top import block -- patch 0007 also edits that import block,
# and two patches editing the same hunk breaks apply-overlay's reverse-verify.
# Keeping 0026's MetalBinding.mm edit to a single, well-separated tail hunk keeps
# both patches independently verifiable.

tail_old = '} // namespace aurora::webgpu::utils\n'
tail_new = ('} // namespace aurora::webgpu::utils\n'
            '\n'
            '#if TARGET_OS_VISION\n'
            '#include <SDL3/SDL_properties.h>\n'
            '#include <SDL3/SDL_video.h>\n'
            '#include <cstdio>\n'
            '\n'
            'namespace aurora::window {\n'
            '// visionOS: the scene owns the panel geometry (user-resizable), but SDL3 builds\n'
            '// the window/view frames from its virtual 1280x720 display coordinates and never\n'
            '// reconciles them with the scene -- the render shows cropped/offset, and panel\n'
            '// resizes change nothing (SoH hit the identical bug; its SDL2 compat patch glues\n'
            '// the UIWindow to scene.coordinateSpace.bounds in layoutSubviews). SDL3 hands us\n'
            '// the UIWindow via properties, so the glue lives here instead of an SDL patch.\n'
            '// Returns true when `desc` holds a line worth logging (first call, scene\n'
            '// adoption, or an actual frame fix); idempotent no-op otherwise.\n'
            'bool sync_visionos_window_geometry(SDL_Window* sdlWindow, char* desc, size_t descSize) {\n'
            '  UIWindow* uiwindow = (__bridge UIWindow*)SDL_GetPointerProperty(\n'
            '      SDL_GetWindowProperties(sdlWindow), SDL_PROP_WINDOW_UIKIT_WINDOW_POINTER, nullptr);\n'
            '  if (uiwindow == nil) {\n'
            '    return false;\n'
            '  }\n'
            '  UIWindowScene* scene = uiwindow.windowScene;\n'
            '  bool adopted = false;\n'
            '  if (scene == nil) {\n'
            '    // The SDL window can be created before the scene connects (SDL then falls\n'
            '    // back to a bare 1280x720 UIWindow); adopt the scene once it exists.\n'
            '    for (UIScene* s in UIApplication.sharedApplication.connectedScenes) {\n'
            '      if ([s isKindOfClass:[UIWindowScene class]]) {\n'
            '        scene = (UIWindowScene*)s;\n'
            '        uiwindow.windowScene = scene;\n'
            '        adopted = true;\n'
            '        break;\n'
            '      }\n'
            '    }\n'
            '  }\n'
            '  if (scene == nil) {\n'
            '    return false;\n'
            '  }\n'
            '  const CGRect sceneBounds = scene.coordinateSpace.bounds;\n'
            '  if (sceneBounds.size.width <= 0 || sceneBounds.size.height <= 0) {\n'
            '    return false;\n'
            '  }\n'
            '  bool changed = adopted;\n'
            '  if (!CGRectEqualToRect(uiwindow.frame, sceneBounds)) {\n'
            '    uiwindow.frame = sceneBounds;\n'
            '    changed = true;\n'
            '  }\n'
            '  // Either frame can be the stale carrier: the window (created against the\n'
            '  // virtual display) or the root view (framed from SDL window x/y). Fix both;\n'
            '  // the SDL metal view is the root view, so its layoutSubviews re-derives the\n'
            '  // drawable from the corrected bounds.\n'
            '  UIView* root = uiwindow.rootViewController.view;\n'
            '  if (root != nil && !CGRectEqualToRect(root.frame, uiwindow.bounds)) {\n'
            '    root.frame = uiwindow.bounds;\n'
            '    changed = true;\n'
            '  }\n'
            '  // Rounded corners: without this the metal view draws SQUARE corners over the\n'
            '  // system glass (VISION-PRO-LUS-PLAYBOOK.md #1.4, verified on device). Match the\n'
            '  // system window look; masksToBounds clips the drawable to the radius. Idempotent\n'
            '  // -- only set when it has drifted, so the per-frame glue stays a no-op.\n'
            '  if (root != nil && root.layer.cornerRadius != 46.0) {\n'
            '    root.layer.cornerRadius = 46.0;\n'
            '    root.layer.cornerCurve = kCACornerCurveContinuous;\n'
            '    root.layer.masksToBounds = YES;\n'
            '  }\n'
            '  static bool s_logged_initial = false;\n'
            '  if (!changed && s_logged_initial) {\n'
            '    return false;\n'
            '  }\n'
            '  s_logged_initial = true;\n'
            '  const CGRect wf = uiwindow.frame;\n'
            '  const CGRect vf = root != nil ? root.frame : CGRectZero;\n'
            '  std::snprintf(desc, descSize,\n'
            '                "scene %.0fx%.0f | window (%.0f,%.0f %.0fx%.0f) | view (%.0f,%.0f %.0fx%.0f)%s%s",\n'
            '                (double)sceneBounds.size.width, (double)sceneBounds.size.height,\n'
            '                (double)wf.origin.x, (double)wf.origin.y, (double)wf.size.width,\n'
            '                (double)wf.size.height, (double)vf.origin.x, (double)vf.origin.y,\n'
            '                (double)vf.size.width, (double)vf.size.height,\n'
            '                adopted ? " [adopted scene]" : "", changed ? " [glued]" : "");\n'
            '  return true;\n'
            '}\n'
            '} // namespace aurora::window\n'
            '#endif\n')
assert mb.count(tail_old) == 1, f"MB tail anchor: {mb.count(tail_old)}"
mb = mb.replace(tail_old, tail_new)

# ---------------- window.cpp ----------------
# This patch OWNS the whole window.cpp window-fix (TargetConditionals include +
# the per-frame glue+poll block). It subsumes the former standalone poll patch
# 0022 (now in overlay/patches/disabled/): 0022 added the poll block and this
# patch extended it, and two patches editing the same hunk breaks
# apply-overlay's reverse-verify. One owner, one hunk.
win_orig = SRC_WIN.read_text()
win = win_orig

win_inc_old = ('#include "window.hpp"\n'
               '\n'
               '#ifdef AURORA_ENABLE_GX\n')
win_inc_new = ('#include "window.hpp"\n'
               '\n'
               '#ifdef __APPLE__\n'
               '#include <TargetConditionals.h>\n'
               '#endif\n'
               '\n'
               '#ifdef AURORA_ENABLE_GX\n')
assert win.count(win_inc_old) == 1, f"win include anchor: {win.count(win_inc_old)}"
win = win.replace(win_inc_old, win_inc_new)

poll_old = ('const AuroraEvent* poll_events() {\n'
            '  ZoneScoped;\n'
            '  g_events.clear();\n')
poll_new = ('const AuroraEvent* poll_events() {\n'
            '  ZoneScoped;\n'
            '  g_events.clear();\n'
            '\n'
            '#if defined(__APPLE__) && TARGET_OS_VISION\n'
            '  // visionOS: SDL3 never reconciles the UIWindow/root-view frames with the\n'
            '  // scene\'s real (user-resizable) geometry -- the render shows cropped/offset\n'
            '  // and never re-fits. Glue the frames to the scene each frame (idempotent;\n'
            '  // defined in dawn/MetalBinding.mm), then poll the swapchain into sync:\n'
            '  // SDL_EVENT_WINDOW_PIXEL_SIZE_CHANGED is not delivered on panel resize, and\n'
            '  // resize_swapchain() early-outs when the size is unchanged, so this is cheap.\n'
            '  // When the glue (or a resize) changes the metal view, the framebuffers\n'
            '  // re-create and the game refits (fill + resize-redraw).\n'
            '  bool sync_visionos_window_geometry(SDL_Window* sdlWindow, char* desc, size_t descSize);\n'
            '  static char s_visionGeometryDesc[224];\n'
            '  static char s_lastVisionGeometryLog[224];\n'
            '  if (sync_visionos_window_geometry(g_window, s_visionGeometryDesc, sizeof(s_visionGeometryDesc))) {\n'
            '    // SDL\'s updateKeyboard re-plants the stale frame on keyboard events; if that\n'
            '    // ever loops against the glue, identical lines would flood the file log --\n'
            '    // only log when the geometry description actually changes.\n'
            '    if (SDL_strcmp(s_visionGeometryDesc, s_lastVisionGeometryLog) != 0) {\n'
            '      Log.info("window: visionOS geometry {}", s_visionGeometryDesc);\n'
            '      SDL_strlcpy(s_lastVisionGeometryLog, s_visionGeometryDesc, sizeof(s_lastVisionGeometryLog));\n'
            '    }\n'
            '  }\n'
            '  resize_swapchain();\n'
            '#endif\n')
assert win.count(poll_old) == 1, f"poll anchor: {win.count(poll_old)}"
win = win.replace(poll_old, poll_new)

# ---------------- emit combined patch ----------------
chunks = []
for rel, orig, new in ((REL_MB, mb_orig, mb), (REL_WIN, win_orig, win)):
    with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
         tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
        fa.write(orig); fb.write(new); fa.flush(); fb.flush()
        r = subprocess.run(["diff", "-u", "--label", f"a/{rel}", "--label", f"b/{rel}",
                            fa.name, fb.name], capture_output=True)
    assert r.returncode == 1
    chunks.append(r.stdout.decode())

out = ROOT / "overlay/patches/0026-aurora-visionos-scene-geometry-glue.patch"
out.write_text(__doc__ + "\n" + "".join(chunks))
print(f"wrote {out}")
