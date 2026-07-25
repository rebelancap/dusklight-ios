// Dusk-Bridging-Header.h — the ObjC/C surface exposed to DuskVisionApp.swift.
// Wired under Ninja via `-import-objc-header` (CMake patch 0031), not the
// XCODE_ATTRIBUTE_SWIFT_OBJC_BRIDGING_HEADER form (a no-op under Ninja).
#pragma once

#import "DuskImmersive.h"
#import "DuskHostViewController.h"

#ifdef __cplusplus
extern "C" {
#endif

// Live stereo tuning from the settings sheet (host VC pushes into engine state).
void Dusk3D_SetStereoParams(float depthFrac, float convBias, float farDepth, float nearDepth);

// Slope-smoothing (softplus dz-floor) strength; kills the step behind Link on slopes.
void Dusk3D_SetKGrad(float kGrad);

// WATER-ANSWER §D false-color probe toggle (diagnostic: tints draws by shift category).
void Dusk3D_SetStereoDebug(int on);

// WATER-ANSWER fix strength: shift projected texcoords so the water shine tracks its surface.
void Dusk3D_SetTexAmt(float amt);
void Dusk3D_SetTxsAll(int all);

// NEAR-DOUBLING fix: which transparency class to pin to the panel (0=off..4=alpha-tested).
void Dusk3D_SetFlatMode(int mode);
// Size gate: only pin draws with <= this many verts (small sprites, not large decals). 0=no limit.
void Dusk3D_SetFlatMaxVtx(int n);

// In 3D, toggle the on-panel menu surface; in 2D, a no-op for now (M1).
void DuskIos_ToggleMenuKey(void);

// Diagnostic: audio front-stage anchor status (0 unset / 1 ok / 2 threw).
void DuskIos_SetAudioAnchorStatus(int s);

#ifdef __cplusplus
}
#endif
