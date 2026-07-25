// DuskHostViewController.h — boots the SDL3/Dusklight engine under the SwiftUI
// app entry (visionOS only) and owns the 2D<->3D transition sequencing.
//
// Phase-2 port of Shipwright-ios's SohHostViewController (STEREO-3D-GUIDE §5).
// visionOS requires a SwiftUI App to declare an ImmersiveSpace, so SDL's
// UIApplicationMain is NOT the process entry here (overlay patch 0030 drops
// SDL_main.h on visionOS and exposes `dusk_engine_main`); this VC calls that
// engine entry once the SwiftUI window scene is live. aurora's own per-frame
// scene glue (overlay 0026) then adopts SDL's UIWindow onto the active scene,
// so Dusklight needs no separate window-graft shell (SoH's SohIosShell job).
#pragma once

#import <UIKit/UIKit.h>

@interface DuskHostViewController : UIViewController
@end

#ifdef __cplusplus
extern "C" {
#endif

// Enter/leave stereoscopic 3D. Owns engine-side sequencing; flips the SwiftUI
// state (Dusk_SetImmersiveMode) that opens/dismisses the ImmersiveSpace.
// M1: stubbed (logs); the real sequencing lands in M2.
void Dusk_Enter3D(bool on);

// Called from SwiftUI after dismissImmersiveSpace completes — the authoritative
// back-to-2D trigger (the 2D window never deactivates under mixed immersion).
void Dusk_Exit3DFinalize(void);

// True while 3D mode is active (engine offscreen, space open or opening).
int Dusk_Get3DMode(void);

#ifdef __cplusplus
}
#endif
