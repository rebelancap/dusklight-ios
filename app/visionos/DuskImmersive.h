// DuskImmersive.h — visionOS stereoscopic "3D screen" mode (CompositorServices).
//
// Phase-2 port of Shipwright-ios's SohImmersive.h (STEREO-3D-GUIDE §6), itself
// descended from vkQuake-ios. M2 ships the REAL render loop (ARKit anchor,
// pacing, per-eye passes, panel + dim quad) showing a built-in test pattern; the
// engine eye-texture bridge (Dusk3D_GetEye*) stays NULL/0 until M3 wires aurora
// to render into IOSurface-backed eye targets.
#pragma once

#import <CompositorServices/CompositorServices.h>

#ifdef __cplusplus
extern "C" {
#endif

// The frame loop. Runs on a DEDICATED thread (main would block the engine's
// frame pump). Returns when stopped or the layer invalidates.
void Dusk3D_Immersive_Run(cp_layer_renderer_t layer_renderer);

// Stop/running handshake: set stop, then wait for running==0 BEFORE dismissing
// the immersive space (the loop must never touch a layerRenderer SwiftUI is
// tearing down).
extern volatile int gDusk3DStop;
extern volatile int gDusk3DRunning;

// Panel placement + tuning (live; called from the settings sheet + enter).
void Dusk3D_SetPanel(float dist, float halfW, float halfH);
void Dusk3D_SetHeight(float h);
void Dusk3D_SetDim(float dim);   // 0..1 UI scale; perceptual curve applied inside
void Dusk3D_Recenter(void);      // re-capture the head anchor next tracked frame

// Engine -> compositor eye-texture bridge (Fast-path equivalent; NULL/0 until the
// engine's eye framebuffers exist in M3 -- the loop shows a test pattern until
// then, so the compositor path is verifiable in the sim before any aurora work).
void* Dusk3D_GetEyeMTLTexture(int eye);  // 1=left, 2=right; NULL = not ready (legacy path)
void* Dusk3D_GetEyeIOSurface(int eye);   // IOSurfaceRef the loop wraps as an MTLTexture; NULL = not ready
int Dusk3D_GetEyeFrames(int eye);        // completed renders per eye (liveness)
extern volatile int gDusk3DEyeW;         // eye texture size (aurora publishes it with the surface)
extern volatile int gDusk3DEyeH;

// Phase-lock (guide §3.7): the immersive loop signals once per compositor frame
// (right after its cp_time_wait_until pace point); aurora's game loop, while in
// 3D, blocks on the wait so it runs at the compositor cadence instead of
// free-running (measured ~650 fps -> heat for nothing). The pending flag is
// capped at 1 (never a backlog); the wait has a 50 ms timeout so a dead/paused
// compositor never hangs the game.
void dusk3d_pace_signal(void);
void dusk3d_wait_for_compositor_frame(void);

// Shell reconcile when the system (Crown) dismisses the space out from under us.
void Dusk3D_Immersive_Ended(void);

// M5 fidelity: crank the game's internal render resolution while in 3D so the eye
// copy is crisp on the room-scale panel (the tiny parked 2D window is unaffected).
// Dusk3D_SetRenderScale is aurora's frame-buffer-scale lever (0 = native); it is
// applied through the game engine and reset to 0 on 3D exit for cheap 2D.
// Dusk_SetSharpness is the shell setter the Sharpness slider calls -- it stores the
// value and applies it ONLY while in 3D (so the 2D window never renders heavy).
void Dusk3D_SetRenderScale(float scale);
void Dusk_SetSharpness(float sharp);
// Force the render aspect to the panel's (game renders at the panel shape, undistorted
// at any width/height -- like 2D). 0 = native window aspect. Gated to 3D by the shell.
void Dusk3D_SetPanelAspect(float aspect);
void Dusk_SetPanelAspect(float aspect); // shell setter: applies only while in 3D

#ifdef __cplusplus
}
#endif
