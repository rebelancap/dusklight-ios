#!/usr/bin/env python3
"""Overlay patch 0032 (Phase 2 / M2->M3): engine goes offscreen + phase-locks in 3D.

STEREO-3D-GUIDE §3.5 (offscreen) + §3.7 (pacing) + §9.1 #5. While the immersive
space is open, app/visionos/DuskImmersive.m owns drawing to the CompositorServices
drawable. aurora's own frame loop must, when gDusk3DMode != 0:

  1. NOT acquire the parked 2D window's swapchain texture -- on device that
     drawable acquire can stall forever ("a hang, not a glitch"). Skip the
     acquire; the game still renders + submits its frame (so M3 can redirect that
     render into the eye textures), it is simply not blitted/presented.
  2. NOT run the surface-error handling on the (intentionally) empty acquire --
     that path would drop/reconfigure the surface every frame.
  3. NOT spam "Skipping present" every frame.
  4. Phase-lock to the compositor instead of free-running: with no present to
     pace it the loop hit ~650 fps in the sim (heat for nothing). The immersive
     loop signals once per compositor frame (dusk3d_pace_signal, right after its
     cp_time_wait_until); aurora blocks on dusk3d_wait_for_compositor_frame in the
     render-worker end-frame callback, BEFORE the frame slot releases, so the
     whole pipeline (and thus the main game loop, which paces on slot
     availability) runs at the compositor cadence. 50 ms timeout inside the wait
     so a dead/paused compositor never hangs the game.

gDusk3DMode + dusk3d_wait_for_compositor_frame are declared at FILE scope (a
block-scope `extern` mangles under C++ -- guide §9.4 #36); the flag is defined in
DuskHostViewController.m and the pace fns in DuskImmersive.m. visionOS-gated;
every other platform keeps the exact present path. This is the surface half of
the 3D engine plumbing; M3 adds the eye-render-target half in the same region.
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
REL = "extern/aurora/lib/aurora.cpp"
SRC = ROOT / "vendor/dusklight" / REL

orig = SRC.read_text()
text = orig

# The per-eye render definition (edit 8). Renders the composited game frame into an
# IOSurface-backed shared texture the immersive loop (DuskImmersive.m) wraps as an
# MTLTexture and samples on the panel. Per-eye (0=LEFT, 1=RIGHT); the double-drain
# above sets gDusk3DEye each pass. Publish is a simple post-submit increment;
# fence-completion publish is device-hardening (guide §9.7 #13).
FUNC_DEF = r'''}

#if defined(__APPLE__) && TARGET_OS_VISION
#include <IOSurface/IOSurfaceRef.h>
extern "C" void* volatile gDusk3DEyeSurfaces[2];
extern "C" volatile int gDusk3DEyeFrames[2];
extern "C" volatile int gDusk3DEyeW;
extern "C" volatile int gDusk3DEyeH;
extern "C" volatile int gDusk3DEye = 0; // 0=OFF, 1=LEFT, 2=RIGHT (read by shader_info.cpp)

wgpu::SharedTextureMemory s_dusk3dEyeStm[2];
wgpu::Texture s_dusk3dEyeTex[2];
uint32_t s_dusk3dEyeTexW[2] = {0, 0};
uint32_t s_dusk3dEyeTexH[2] = {0, 0};
bool s_dusk3dEyeInitialized[2] = {false, false};

void dusk3d_eye_render(const wgpu::CommandEncoder& encoder, int eye) {
  if (eye < 0 || eye > 1) {
    return;
  }
  // M5 crispness: size the eye to the GAME-RENDER resolution (present_source / fb),
  // NOT the tiny parked 2D window (native_fb / surfaceConfiguration). With the 3D
  // render scale cranked the game renders high-res, so the eye is a 1:1 crisp copy.
  const auto srcSz = webgpu::present_source().size;
  const uint32_t w = srcSz.width;
  const uint32_t h = srcSz.height;
  if (w == 0 || h == 0) {
    return;
  }
  if (!s_dusk3dEyeTex[eye] || s_dusk3dEyeTexW[eye] != w || s_dusk3dEyeTexH[eye] != h) {
    const size_t bpr = IOSurfaceAlignProperty(kIOSurfaceBytesPerRow, static_cast<size_t>(w) * 4u);
    const int32_t v_w = static_cast<int32_t>(w);
    const int32_t v_h = static_cast<int32_t>(h);
    const int32_t v_bpe = 4;
    const int32_t v_bpr = static_cast<int32_t>(bpr);
    const int32_t v_pf = 0x42475241; // 'BGRA'
    const void* keys[] = {kIOSurfaceWidth, kIOSurfaceHeight, kIOSurfaceBytesPerElement,
                          kIOSurfaceBytesPerRow, kIOSurfacePixelFormat};
    CFNumberRef nums[] = {CFNumberCreate(nullptr, kCFNumberSInt32Type, &v_w),
                          CFNumberCreate(nullptr, kCFNumberSInt32Type, &v_h),
                          CFNumberCreate(nullptr, kCFNumberSInt32Type, &v_bpe),
                          CFNumberCreate(nullptr, kCFNumberSInt32Type, &v_bpr),
                          CFNumberCreate(nullptr, kCFNumberSInt32Type, &v_pf)};
    CFDictionaryRef props =
        CFDictionaryCreate(nullptr, keys, reinterpret_cast<const void**>(nums), 5,
                           &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    IOSurfaceRef ios = IOSurfaceCreate(props);
    CFRelease(props);
    for (CFNumberRef n : nums) {
      CFRelease(n);
    }
    if (ios == nullptr) {
      Log.warn("dusk3d: IOSurfaceCreate failed {}x{}", w, h);
      return;
    }
    wgpu::SharedTextureMemoryIOSurfaceDescriptor ioDesc{};
    ioDesc.ioSurface = ios;
    wgpu::SharedTextureMemoryDescriptor stmDesc{};
    stmDesc.nextInChain = &ioDesc;
    s_dusk3dEyeStm[eye] = g_device.ImportSharedTextureMemory(&stmDesc);
    wgpu::TextureDescriptor td{};
    td.size = {w, h, 1};
    td.format = wgpu::TextureFormat::BGRA8Unorm;
    td.usage = wgpu::TextureUsage::RenderAttachment | wgpu::TextureUsage::TextureBinding;
    s_dusk3dEyeTex[eye] = s_dusk3dEyeStm[eye].CreateTexture(&td);
    s_dusk3dEyeTexW[eye] = w;
    s_dusk3dEyeTexH[eye] = h;
    s_dusk3dEyeInitialized[eye] = false;
    gDusk3DEyeSurfaces[eye] = ios; // owned by the STM (+ the loop's MTLTexture once wrapped)
    gDusk3DEyeW = static_cast<int>(w);
    gDusk3DEyeH = static_cast<int>(h);
    // Drop the create-ref: the SharedTextureMemory holds its own ref, and once the
    // immersive loop wraps this surface its MTLTexture holds another, so the surface
    // outlives any in-flight use and is freed only when BOTH release it. Without this
    // a resize (e.g. dragging Sharpness) leaks the old IOSurface every recreation.
    CFRelease(ios);
    Log.info("dusk3d: eye {} IOSurface {}x{} imported", eye, w, h);
  }
  if (!s_dusk3dEyeTex[eye]) {
    return;
  }
  wgpu::SharedTextureMemoryBeginAccessDescriptor beginDesc{};
  beginDesc.initialized = s_dusk3dEyeInitialized[eye];
  beginDesc.concurrentRead = false;
  if (!s_dusk3dEyeStm[eye].BeginAccess(s_dusk3dEyeTex[eye], &beginDesc)) {
    Log.warn("dusk3d: eye {} BeginAccess failed", eye);
    return;
  }
  const auto& src = webgpu::present_source();
  const auto eyeViewport = webgpu::calculate_present_viewport(w, h, src.size.width, src.size.height);
  const auto& resampled = webgpu::resample_present_source(encoder, eyeViewport);
  const auto bindGroup = webgpu::create_copy_bind_group(resampled);
  const auto eyeView = s_dusk3dEyeTex[eye].CreateView();
  const std::array attachments{wgpu::RenderPassColorAttachment{
      .view = eyeView,
      .loadOp = wgpu::LoadOp::Clear,
      .storeOp = wgpu::StoreOp::Store,
      .clearValue = wgpu::Color{0.0, 0.0, 0.0, 1.0},
  }};
  const wgpu::RenderPassDescriptor rpd{
      .label = "Dusk3D eye render",
      .colorAttachmentCount = attachments.size(),
      .colorAttachments = attachments.data(),
  };
  const auto pass = encoder.BeginRenderPass(&rpd);
  pass.SetPipeline(webgpu::g_CopyPipeline);
  pass.SetBindGroup(0, bindGroup, 0, nullptr);
  set_present_viewport(pass, eyeViewport, w, h);
  pass.Draw(3);
  pass.End();
  wgpu::SharedTextureMemoryEndAccessState endState{};
  s_dusk3dEyeStm[eye].EndAccess(s_dusk3dEyeTex[eye], &endState);
  s_dusk3dEyeInitialized[eye] = true;
  gDusk3DEyeFrames[eye] = gDusk3DEyeFrames[eye] + 1; // publish (simple; race device-only §9.7)
}

// M5: render-scale bridge for the shell's Sharpness slider. Cranks the game's
// internal render resolution (aurora's frame-buffer scale, a multiplier on the
// GC render-mode size) so the eye copy is crisp on the room-scale panel; the tiny
// parked 2D window is unaffected. 0 = native (restored on 3D exit for cheap 2D).
extern "C" void Dusk3D_SetRenderScale(float scale) {
  window::set_frame_buffer_scale(scale);
}

// Force the render aspect to the panel's so the game renders AT the panel shape
// (undistorted at any width/height, like 2D) instead of being stretched onto the
// quad. 0 = native window aspect (restored on 3D exit).
extern "C" void Dusk3D_SetPanelAspect(float aspect) {
  window::set_frame_buffer_aspect_override(aspect);
}
#endif

} // namespace
} // namespace aurora
'''

edits = [
    # 1. file-scope externs for the master 3D flag + the pace wait
    ('bool begin_frame() noexcept {\n',
     '#if defined(__APPLE__) && TARGET_OS_VISION\n'
     '// In 3D the immersive loop owns rendering to the compositor; the game must not\n'
     '// acquire the parked 2D window\'s drawable (it can stall -- guide 9.1 #5) and\n'
     '// must pace to the compositor instead of free-running (guide 3.7). gDusk3DMode\n'
     '// is defined in DuskHostViewController.m; the pace wait in DuskImmersive.m.\n'
     'extern "C" volatile int gDusk3DMode;\n'
     'extern "C" volatile int gDusk3DEye;\n'
     'extern "C" void dusk3d_wait_for_compositor_frame(void);\n'
     '// M3/M4: render the composited game frame into an eye IOSurface texture the\n'
     '// immersive loop samples on the panel (defined in this file, patch 0034).\n'
     'void dusk3d_eye_render(const wgpu::CommandEncoder& encoder, int eye);\n'
     '#endif\n'
     '\n'
     'bool begin_frame() noexcept {\n'),

    # 2. skip the surface acquire while in 3D
    ('    {\n'
     '      window::SurfaceLock surfaceLock;\n'
     '      if (window::is_presentable() && g_surface) {\n',
     '    bool present3DSuppressed = false;\n'
     '#if defined(__APPLE__) && TARGET_OS_VISION\n'
     '    // 3D mode: the immersive loop owns rendering; do not touch the parked 2D\n'
     '    // window\'s drawable (guide 9.1 #5). The game still renders + submits below.\n'
     '    present3DSuppressed = (gDusk3DMode != 0);\n'
     '#endif\n'
     '    {\n'
     '      window::SurfaceLock surfaceLock;\n'
     '      if (window::is_presentable() && g_surface && !present3DSuppressed) {\n'),

    # 3. in 3D, render the game into the eye IOSurface instead of the window
    #    (M3, patch 0034); otherwise present as normal (and no spam on 3D skip)
    ('    } else {\n'
     '      Log.info("Skipping present; window not presentable");\n'
     '    }\n',
     '    } else if (present3DSuppressed) {\n'
     '#if defined(__APPLE__) && TARGET_OS_VISION\n'
     '      dusk3d_eye_render(encoder, 0); // LEFT eye (this pass)\n'
     '#endif\n'
     '    } else {\n'
     '      Log.info("Skipping present; window not presentable");\n'
     '    }\n'),

    # 4. do not run surface-error handling on the intentionally-empty 3D acquire
    ('    } else if (g_surface) {\n'
     '      switch (surfaceStatus) {\n',
     '    } else if (g_surface && !present3DSuppressed) {\n'
     '      switch (surfaceStatus) {\n'),

    # 5. phase-lock: block on the compositor beat before the frame slot releases
    ('    TracyPlot("aurora: lastTextureUploadSize", static_cast<int64_t>(gfx::g_stats.lastTextureUploadSize));\n'
     '  });\n',
     '    TracyPlot("aurora: lastTextureUploadSize", static_cast<int64_t>(gfx::g_stats.lastTextureUploadSize));\n'
     '#if defined(__APPLE__) && TARGET_OS_VISION\n'
     '    if (present3DSuppressed) {\n'
     '      // Phase-lock to the compositor (guide 3.7): with no present to pace it the\n'
     '      // loop free-runs (~650 fps -> heat). The immersive loop signals once per\n'
     '      // compositor frame; block here, before the frame slot releases, so the game\n'
     '      // loop (which paces on slot availability) runs at the compositor rate.\n'
     '      dusk3d_wait_for_compositor_frame();\n'
     '      // Record a frame tick so the FPS overlay works in 3D -- calculate_fps() reads\n'
     '      // present timestamps, and present is suppressed here, so it would read 0.\n'
     '      gfx::after_present();\n'
     '    }\n'
     '#endif\n'
     '  });\n'),

    # 6. the per-eye render definition (after end_frame, before the namespace close).
    #    Renders the composited game frame into eye `eye`'s IOSurface-backed shared
    #    texture the immersive loop samples on the panel. Called once per eye by the
    #    stereo re-execution (patch 0035); mono (2D fallback) calls it with eye 0.
    ('}\n'
     '} // namespace\n'
     '} // namespace aurora\n',
     FUNC_DEF),

    # 7. STEREO (D-035): externs for the convergence inputs -- TP's camera distance
    #    (dusk, patch 0036) and the Depth-slider fraction (shell).
    ('void dusk3d_eye_render(const wgpu::CommandEncoder& encoder, int eye);\n'
     '#endif\n',
     'void dusk3d_eye_render(const wgpu::CommandEncoder& encoder, int eye);\n'
     '// Stereo params: gDusk3DCamDist is TP\'s eye->look-at distance (dusk, frame_interp);\n'
     '// gDusk3DSep is the Depth-slider fraction (shell, DuskHostViewController.m).\n'
     'extern "C" volatile float gDusk3DCamDist;\n'
     'extern "C" volatile float gDusk3DCamDistLink; // eye->getPlayer(0)->current.pos (readout candidate P0cur)\n'
     '// NEAR-DOUBLING readout probe (\xa7A): 3 more eye->Link candidates -- pick the ONE that\'s smooth,\n'
     '// 200-600, below camDistLA. P0=getPlayer(0), PL=getLinkPlayer; cur=current.pos, eye=eyePos.\n'
     'extern "C" volatile float gDusk3DLinkP0Eye;\n'
     'extern "C" volatile float gDusk3DLinkPLCur;\n'
     'extern "C" volatile float gDusk3DLinkPLEye;\n'
     'extern "C" volatile float gDusk3DSep;\n'
     'extern "C" volatile float gDusk3DFarDepth; // far-depth knee kFar (shell slider, 0.25-1.0)\n'
     '#endif\n'),

    # 8. STEREO: enable the two-eye packet re-execution + feed the adaptive
    #    convergence (C = smoothed camera distance; e = depthFrac * C).
    ('  gx::fifo::drain();\n'
     '  gfx::finish();\n'
     '  auto imguiDrawData = imgui::freeze();\n',
     '  gx::fifo::drain();\n'
     '  gfx::finish();\n'
     '#if defined(__APPLE__) && TARGET_OS_VISION\n'
     '  // visionOS 3D: render the frame for both eyes (packet re-execution in gfx::end_frame).\n'
     '  gfx::stereo_set_enabled(gDusk3DMode != 0);\n'
     '  gfx::stereo_set_debug(gDusk3DStereoDebug != 0); // outside the mode gate: probe reads in 2D too\n'
     '  gfx::stereo_set_texamt(gDusk3DTexAmt);\n'
     '  if (gDusk3DMode != 0) {\n'
     '    // Convergence C = smoothed camera->subject distance (STEREO-3D-RECOVERY \xa72.2):\n'
     '    // separation shrinks with C, so interiors (small C, near walls) stay comfortable\n'
     '    // and zero-parallax tracks the subject. Fallback + clamp until camdist telemetry\n'
     '    // (below) calibrates the range for TP\'s world scale.\n'
     '    // Subject-anchored convergence (STEP-AND-SHADOWS-ANSWER \xa7A/\xa7B): the subject is Link\'s HEAD\n'
     '    // (eyePos) -- the readout candidate the device log showed smooth + at/below the look-at\n'
     '    // (getPlayer(0) == getLinkPlayer here; feet/current.pos run larger). Cap at the look-at so\n'
     '    // C never sits FARTHER than the aim point (guards rare outlier frames). Puts Link on the\n'
     '    // panel and gives the shader\'s softplus dz-floor the near-field it needs to kill the slope\n'
     '    // step. (The earlier eye->Link backfire was reading current.pos, which came back garbage.)\n'
     '    const float linkHead = gDusk3DLinkP0Eye;   // eye -> Link.eyePos\n'
     '    const float camDistLA = gDusk3DCamDist;\n'
     '    float camDist = (linkHead > 1.0f) ? std::min(linkHead, camDistLA) : camDistLA;\n'
     '    if (!(camDist > 1.0f)) {\n'
     '      camDist = 800.0f;\n'
     '    }\n'
     '    camDist = std::clamp(camDist, 80.0f, 4000.0f);\n'
     '    static float s_convSmooth = 0.0f;\n'
     '    s_convSmooth = s_convSmooth <= 0.0f ? camDist : s_convSmooth + (camDist - s_convSmooth) * 0.08f;\n'
     '    const float C = s_convSmooth;\n'
     '    const float depthFrac = gDusk3DSep;    // Depth slider (0 at OFF)\n'
     '    float kFar = gDusk3DFarDepth;          // Far Depth slider (knee)\n'
     '    if (!(kFar > 0.0f)) {\n'
     '      kFar = 0.5f;\n'
     '    }\n'
     '    float kNear = gDusk3DNearDepth;        // Near Depth slider (near clamp; V-doubling)\n'
     '    if (!(kNear > 0.0f)) {\n'
     '      kNear = 1.0f;\n'
     '    }\n'
     '    // Softplus dz-floor (STEP-AND-SHADOWS-ANSWER \xa7C): floors the effective near depth at\n'
     '    // ~kGrad*C*ln2 so the near-ground disparity gradient can\'t get steep enough to tear into a\n'
     '    // step on a slope -- C-inf smooth, no shelf edge. Pass s = kGrad*C; shader does the softplus.\n'
     '    float kGrad = gDusk3DKGrad;            // Slope smoothing slider (0 = off)\n'
     '    if (!(kGrad >= 0.0f)) {\n'
     '      kGrad = 0.0f;\n'
     '    }\n'
     '    gfx::stereo_set_params(depthFrac * C, 1.0f / C, kFar, kNear, kGrad * C);\n'
     '    static int s_camLog = 0;\n'
     '    if ((s_camLog++ % 120) == 0) {\n'
     '      Log.info("dusk3d: camDistLA {} P0cur {} P0eye {} PLcur {} PLeye {} C {} kNear {}", camDistLA,\n'
     '               gDusk3DCamDistLink, gDusk3DLinkP0Eye, gDusk3DLinkPLCur, gDusk3DLinkPLEye, C, kNear);\n'
     '    }\n'
     '  }\n'
     '#endif\n'
     '  auto imguiDrawData = imgui::freeze();\n'),

    # 9. STEREO: the end-frame callback takes (eye, isLast) instead of (encoder).
    ('                  imguiDrawData = std::move(imguiDrawData)](wgpu::CommandEncoder& encoder) {\n',
     '                  imguiDrawData = std::move(imguiDrawData)](wgpu::CommandEncoder& encoder, int eye,\n'
     '                                                            bool isLast) {\n'),

    # 10. STEREO: render the selected eye, and finalize (submit/present/pace) only on
    #     the last eye so the single command encoder is finished exactly once.
    ('    } else if (present3DSuppressed) {\n'
     '#if defined(__APPLE__) && TARGET_OS_VISION\n'
     '      dusk3d_eye_render(encoder, 0); // LEFT eye (this pass)\n'
     '#endif\n'
     '    } else {\n'
     '      Log.info("Skipping present; window not presentable");\n'
     '    }\n'
     '    webgpu::gpu_prof::frame_end(encoder);\n',
     '    } else if (present3DSuppressed) {\n'
     '#if defined(__APPLE__) && TARGET_OS_VISION\n'
     '      dusk3d_eye_render(encoder, eye); // eye 0 (LEFT) or 1 (RIGHT, stereo re-execution)\n'
     '#endif\n'
     '    } else {\n'
     '      Log.info("Skipping present; window not presentable");\n'
     '    }\n'
     '    // Stereo executes the frame twice; finalize (submit/present/pace) only on the\n'
     '    // last eye so the single command encoder is finished exactly once.\n'
     '    if (!isLast) {\n'
     '      return;\n'
     '    }\n'
     '    webgpu::gpu_prof::frame_end(encoder);\n'),

    # 11. EXPERIENCE (device feedback): note why MSAA stays 1 (aborts under TP).
    ('  if (g_config.msaa == 0) {\n'
     '    g_config.msaa = 1;\n'
     '  }\n',
     '  if (g_config.msaa == 0) {\n'
     '    // NB: MSAA > 1 aborts under TP -- aurora does not support depth-tex copies from\n'
     '    // multisampled EFB targets, which TP issues. Crisp edges come from the render-scale\n'
     '    // (Sharpness) supersample instead, which also antialiases. See D-036.\n'
     '    g_config.msaa = 1;\n'
     '  }\n'),

    # 12. EXPERIENCE: stash the frame's RmlUi layer (FPS counter, toasts) so the eye
    #     render can composite it -- the 2D present blit that draws it is suppressed in 3D.
    ('extern "C" volatile float gDusk3DFarDepth; // far-depth knee kFar (shell slider, 0.25-1.0)\n'
     '#endif\n',
     'extern "C" volatile float gDusk3DFarDepth; // far-depth knee kFar (shell slider, 0.25-1.0)\n'
     'extern "C" volatile float gDusk3DNearDepth; // near-depth knee kNear (shell slider; V-doubling clamp)\n'
     'extern "C" volatile float gDusk3DKGrad;     // softplus dz-floor factor (shell slider; slope-step smoothing)\n'
     'extern "C" volatile int gDusk3DStereoDebug; // WATER-ANSWER \xa7D false-color probe (shell toggle)\n'
     'extern "C" volatile float gDusk3DTexAmt;    // WATER-ANSWER projected-texcoord shift strength (shell slider; 0=off)\n'
     '// FPS/RmlUi overlay for the 3D panel: the end-frame callback stashes the frame\'s rml\n'
     '// layer here so the eye render can composite it -- the 2D present blit that normally\n'
     '// draws it (FPS counter, toasts) is suppressed in 3D. Worker-thread only.\n'
     'wgpu::BindGroup s_dusk3dRmlBindGroup;\n'
     'bool s_dusk3dRmlOverlay = false;\n'
     '#endif\n'),

    # 13. EXPERIENCE: stash the rml layer before the eye render.
    ('    } else if (present3DSuppressed) {\n'
     '#if defined(__APPLE__) && TARGET_OS_VISION\n'
     '      dusk3d_eye_render(encoder, eye); // eye 0 (LEFT) or 1 (RIGHT, stereo re-execution)\n'
     '#endif\n',
     '    } else if (present3DSuppressed) {\n'
     '#if defined(__APPLE__) && TARGET_OS_VISION\n'
     '      s_dusk3dRmlBindGroup = rmlBindGroup; // FPS/toast overlay -> composited onto the eye\n'
     '      s_dusk3dRmlOverlay = rmlOverlay;\n'
     '      dusk3d_eye_render(encoder, eye); // eye 0 (LEFT) or 1 (RIGHT, stereo re-execution)\n'
     '#endif\n'),

    # 14. EXPERIENCE: composite the rml overlay onto the eye texture (shows FPS on panel).
    ('  set_present_viewport(pass, eyeViewport, w, h);\n'
     '  pass.Draw(3);\n'
     '  pass.End();\n',
     '  set_present_viewport(pass, eyeViewport, w, h);\n'
     '  pass.Draw(3);\n'
     '  // Composite the RmlUi overlay (FPS counter, toasts) so it shows on the 3D panel.\n'
     '  if (s_dusk3dRmlBindGroup && s_dusk3dRmlOverlay) {\n'
     '    pass.SetPipeline(webgpu::g_CopyPremultipliedAlphaPipeline);\n'
     '    pass.SetBindGroup(0, s_dusk3dRmlBindGroup, 0, nullptr);\n'
     '    pass.Draw(3);\n'
     '  }\n'
     '  pass.End();\n'),
]

for old, new in edits:
    assert text.count(old) == 1, f"anchor {old[:48]!r}: {text.count(old)}"
    text = text.replace(old, new)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(text); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1

out = ROOT / "overlay/patches/0032-aurora-visionos-3d-offscreen.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
