// DuskHostViewController.m — boots the SDL3/Dusklight engine under the SwiftUI
// app entry (visionOS) and owns the 2D<->3D transition sequencing.
//
// Phase-2 port of Shipwright-ios's SohHostViewController (STEREO-3D-GUIDE §5 +
// §1.4). Dusklight differs from SoH in the window plumbing: aurora's own
// per-frame scene glue (overlay 0026) keeps SDL's UIWindow matched to the scene
// bounds, so park/restore is done purely by requesting scene geometry and letting
// the glue follow -- no separate restore-tick controller (SoH's SohIosShell job).

#import "DuskHostViewController.h"
#import "DuskImmersive.h"

extern void SDL_SetMainReady(void);
// Engine entry exposed by overlay patch 0030 (aurora/lib/main.cpp) on visionOS.
extern int dusk_engine_main(int argc, char** argv);
// Defined in DuskVisionApp.swift (@_cdecl) -- flips the SwiftUI immersive state.
extern void Dusk_SetImmersiveMode(bool on);

// Master 3D flag. Engine-visible; aurora's 3D gates (patch 0032) read it to go
// offscreen while the immersive space owns rendering.
volatile int gDusk3DMode = 0;
volatile int gDuskAudioAnchorStatus = 0;

// Engine -> compositor eye bridge (M3). aurora renders each eye into an
// IOSurface-backed texture and publishes the IOSurfaceRef here + bumps the frame
// counter; the immersive loop wraps the IOSurface as an MTLTexture and samples it.
// NULL/0 until the first eye frame, so the loop shows its test pattern until then.
void* volatile gDusk3DEyeTexture[2] = { NULL, NULL };   // (unused in M3; kept for API)
volatile int gDusk3DEyeFrames[2] = { 0, 0 };
void* volatile gDusk3DEyeSurfaces[2] = { NULL, NULL };  // IOSurfaceRef, written by aurora
volatile int gDusk3DEyeW = 0;
volatile int gDusk3DEyeH = 0;

void* Dusk3D_GetEyeIOSurface(int eye) {
    if (eye < 1 || eye > 2) {
        return NULL;
    }
    return gDusk3DEyeFrames[eye - 1] > 0 ? gDusk3DEyeSurfaces[eye - 1] : NULL;
}

void* Dusk3D_GetEyeMTLTexture(int eye) {
    if (eye < 1 || eye > 2)
        return NULL;
    // Gate on the per-eye rendered flag: the texture is UNDEFINED until first
    // drawn (guide §9.2 #20).
    return gDusk3DEyeFrames[eye - 1] > 0 ? gDusk3DEyeTexture[eye - 1] : NULL;
}
int Dusk3D_GetEyeFrames(int eye) {
    return (eye >= 1 && eye <= 2) ? gDusk3DEyeFrames[eye - 1] : 0;
}

int Dusk_Get3DMode(void) { return gDusk3DMode; }

static BOOL sDuskHostBooted = NO;

// Defined below; forward-declared so viewDidAppear can widen the launch window.
static void Dusk_RequestWindowSize(CGSize size);
static UIWindow* Dusk_GameWindow(void);

@implementation DuskHostViewController

- (void)viewDidLoad {
    [super viewDidLoad];
    self.view.backgroundColor = UIColor.blackColor;
}

- (void)viewDidAppear:(BOOL)animated {
    [super viewDidAppear:animated];
    if (sDuskHostBooted)
        return;
    sDuskHostBooted = YES;
    NSLog(@"[DuskHost] window scene live — booting engine (dusk_engine_main)");

    // The 2D window launches ~4:3 (SDL default 1280x960) and OTA reinstalls reset it,
    // so it keeps opening "too square". SwiftUI .defaultSize can't win against the scene-geometry
    // requests we drive, so once the engine's window is live (a few seconds out), widen a
    // square-ish window to 16:9 — the game re-renders at the new window aspect. Guarded: run once,
    // only pre-3D, and only if it isn't already wide (so a persisted wide size is respected).
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(3.0 * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{
                       if (gDusk3DMode)
                           return;
                       UIWindowScene* scene = Dusk_GameWindow().windowScene;
                       CGSize s = scene ? scene.coordinateSpace.bounds.size : CGSizeZero;
                       if (s.width > 200 && s.width < s.height * 1.6)
                           Dusk_RequestWindowSize(CGSizeMake(1280, 720));
                   });
    // Boot from a RUNLOOP TIMER, never dispatch_async: the engine main never
    // returns (the game loop runs on this thread), and a never-ending GCD block
    // would occupy the serial main queue forever, starving all of SwiftUI/
    // MainActor. A timer callout leaves the queue free (guide §5.3 law (a)).
    [NSTimer scheduledTimerWithTimeInterval:0
                                    repeats:NO
                                      block:^(NSTimer* t) {
                                          SDL_SetMainReady();
                                          static char arg0[] = "dusklight";
                                          static char* argv[] = { arg0, NULL };
                                          const int rc = dusk_engine_main(1, argv);
                                          NSLog(@"[DuskHost] dusk_engine_main returned %d (engine quit)", rc);
                                      }];

    // Headless test harness (guide §8 M2/M4): DUSK_VP3D_AUTOENTER=<sec> auto-enters
    // 3D so the immersive lifecycle is verifiable in the sim without a gaze-pinch;
    // it then auto-exits after DUSK_VP3D_AUTOEXIT sec (default 12) to exercise the
    // exit/restore path too. No effect unless the env var is set.
    const char* autoEnter = getenv("DUSK_VP3D_AUTOENTER");
    if (autoEnter != NULL && autoEnter[0] != '\0') {
        double enterAt = atof(autoEnter);
        if (enterAt < 1.0)
            enterAt = 6.0;
        double exitAfter = 12.0;
        const char* ax = getenv("DUSK_VP3D_AUTOEXIT");
        if (ax != NULL && ax[0] != '\0')
            exitAfter = atof(ax);
        NSLog(@"[DuskHost] AUTOENTER harness: enter 3D at +%.0fs, exit at +%.0fs", enterAt, enterAt + exitAfter);
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(enterAt * NSEC_PER_SEC)),
                       dispatch_get_main_queue(), ^{ Dusk_Enter3D(true); });
        if (exitAfter > 0.0)
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)((enterAt + exitAfter) * NSEC_PER_SEC)),
                           dispatch_get_main_queue(), ^{ Dusk_Enter3D(false); });
    }
}

@end

// --- parked 2D window + curtain ("Playing in 3D") --------------------------
static CGSize dusk_pre3dSize = { 0, 0 }; // captured at entry START, guarded
static UIView* dusk_curtain = nil;

static BOOL Dusk_ViewTreeHasMetalLayer(UIView* v, int depth) {
    if (depth > 4)
        return NO;
    if ([v.layer isKindOfClass:NSClassFromString(@"CAMetalLayer")])
        return YES;
    for (UIView* s in v.subviews)
        if (Dusk_ViewTreeHasMetalLayer(s, depth + 1))
            return YES;
    return NO;
}

// The GAME window: SDL's UIWindow (hosts the CAMetalLayer view). The key window
// at ornament-tap time is the SwiftUI HOSTING window -- curtains/geometry aimed
// at "key" hit the wrong window (guide §9.3 #28).
static UIWindow* Dusk_GameWindow(void) {
    for (UIWindow* w in UIApplication.sharedApplication.windows)
        if (Dusk_ViewTreeHasMetalLayer(w, 0))
            return w;
    for (UIWindow* w in UIApplication.sharedApplication.windows)
        if (w.isKeyWindow)
            return w;
    return UIApplication.sharedApplication.windows.firstObject;
}

static void Dusk_RequestWindowSize(CGSize size) {
    UIWindow* w = Dusk_GameWindow();
    UIWindowScene* scene = w.windowScene;
    if (scene == nil) {
        NSLog(@"[DuskHost] geometry request skipped: no scene");
        return;
    }
    @try {
        UIWindowSceneGeometryPreferencesVision* prefs =
            [[UIWindowSceneGeometryPreferencesVision alloc] initWithSize:size];
        [scene requestGeometryUpdateWithPreferences:prefs
                                       errorHandler:^(NSError* e) {
                                           NSLog(@"[DuskHost] geometry update failed: %@", e);
                                       }];
        NSLog(@"[DuskHost] geometry request %.0fx%.0f", size.width, size.height);
    } @catch (NSException* ex) {
        NSLog(@"[DuskHost] geometry request THREW: %@", ex);
    }
}

static void Dusk_SetCurtain(bool show) {
    UIWindow* w = Dusk_GameWindow();
    if (show) {
        if (dusk_curtain != nil || w == nil)
            return;
        UIView* v = [[UIView alloc] initWithFrame:w.bounds];
        v.backgroundColor = UIColor.blackColor;
        v.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
        UILabel* l = [[UILabel alloc] initWithFrame:v.bounds];
        l.text = @"Playing in 3D";
        l.textColor = [UIColor colorWithWhite:0.85 alpha:1.0];
        l.font = [UIFont systemFontOfSize:28 weight:UIFontWeightSemibold];
        l.textAlignment = NSTextAlignmentCenter;
        l.autoresizingMask = v.autoresizingMask;
        [v addSubview:l];
        [w addSubview:v];
        dusk_curtain = v;
    } else if (dusk_curtain != nil) {
        [dusk_curtain removeFromSuperview];
        dusk_curtain = nil;
    }
}

void Dusk_Enter3D(bool on) {
    // UIKit work below -- marshal any off-main caller (e.g. a console bridge).
    if (!NSThread.isMainThread) {
        dispatch_async(dispatch_get_main_queue(), ^{ Dusk_Enter3D(on); });
        return;
    }
    if (on) {
        if (gDusk3DMode)
            return;
        NSLog(@"[DuskHost] entering 3D: engine offscreen, opening space");
        // Capture the pre-3D size at entry START, before anything moves, and
        // never re-capture an already-parked size (the "window stays tiny" trap,
        // guide §9.3 #26 -- reject <=600pt like sm64).
        if (dusk_pre3dSize.width < 1) {
            UIWindowScene* scene = Dusk_GameWindow().windowScene;
            CGSize s = scene ? scene.coordinateSpace.bounds.size : CGSizeZero;
            if (s.width > 600) {
                dusk_pre3dSize = s;
                NSLog(@"[DuskHost] captured pre-3D size %.0fx%.0f", s.width, s.height);
            }
        }
        gDusk3DMode = 1; // BEFORE the space opens (drawable-acquire stall, §9.1 #5)
        Dusk_SetImmersiveMode(true);
        Dusk_SetCurtain(true); // immediately -- the frozen last frame confuses
        // Park AFTER entry settles: a resize animation racing the space-open
        // transition wedges (guide §9.3 #27). Only if still in 3D.
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(1.5 * NSEC_PER_SEC)),
                       dispatch_get_main_queue(), ^{
                           if (gDusk3DMode)
                               Dusk_RequestWindowSize(CGSizeMake(480, 320));
                       });
    } else {
        if (!gDusk3DMode)
            return;
        NSLog(@"[DuskHost] exiting 3D: stopping render thread first");
        gDusk3DStop = 1;
        dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INTERACTIVE, 0), ^{
            // Wait for the loop to leave the layerRenderer BEFORE dismissing
            // (it must never touch a layerRenderer SwiftUI is tearing down --
            // guide §9.1 #6). 2 s timeout; it paces at the compositor cadence.
            for (int i = 0; i < 200 && gDusk3DRunning; i++)
                usleep(10 * 1000);
            if (gDusk3DRunning)
                NSLog(@"[DuskHost] WARNING: render thread still running at dismiss");
            dispatch_async(dispatch_get_main_queue(), ^{
                Dusk_SetImmersiveMode(false); // Swift dismisses, then finalizes
            });
        });
    }
}

// Live stereo tuning from the settings sheet (the Depth slider). aurora's stereo
// projection transform (shader_info.cpp) reads these every draw in the eye passes.
volatile float gDusk3DSep = 0.00975f;  // eye-offset fraction (60% on the rescaled slider = old 30%)
volatile float gDusk3DConv = 1.0f;    // convergence bias (pinned; auto-adapts later)
volatile float gDusk3DFarDepth = 0.5f; // far-depth knee kFar (Far Depth slider, 0.25-1.0)
volatile float gDusk3DNearDepth = 1.0f; // near-depth knee kNear (Near Depth slider; V-doubling clamp)
volatile float gDusk3DKGrad = 0.35f;    // softplus dz-floor factor (Slope smoothing slider; kills the slope step)
void Dusk3D_SetStereoParams(float depthFrac, float convBias, float farDepth, float nearDepth) {
    gDusk3DSep = depthFrac;
    gDusk3DConv = convBias;
    gDusk3DFarDepth = farDepth;
    gDusk3DNearDepth = nearDepth;
    NSLog(@"[DuskHost] SetStereoParams depth=%.4f conv=%.2f far=%.2f near=%.2f", depthFrac, convBias, farDepth, nearDepth);
}

// Slope-smoothing softplus dz-floor factor (kGrad). Separate setter (its own slider).
void Dusk3D_SetKGrad(float kGrad) {
    gDusk3DKGrad = kGrad;
    NSLog(@"[DuskHost] slope smoothing kGrad %.3f", gDusk3DKGrad);
}

// WATER-ANSWER §D false-color probe. When on, the fragment shader tints draws by category
// (red=ortho/HUD, green=perspective mesh, blue=position/projective texgen = the suspected
// water layer). Works in 2D too (e=0 keeps the geometry unshifted), so it is sim-testable.
volatile int gDusk3DStereoDebug = 0;
void Dusk3D_SetStereoDebug(int on) {
    gDusk3DStereoDebug = on ? 1 : 0;
    NSLog(@"[DuskHost] stereo debug probe %d", gDusk3DStereoDebug);
}

// WATER-ANSWER fix: shift the projected texture-coordinate chains (sky/reflection shine)
// by the mesh's per-eye parallax, scaled by this strength (the slider; 0 = off). The
// 1.0 "theoretical" amount over-shifts (reads as blur), so it's tunable by eye.
volatile float gDusk3DTexAmt = 0.f;
void Dusk3D_SetTexAmt(float amt) {
    gDusk3DTexAmt = amt;
    NSLog(@"[DuskHost] texcoord-shift strength %.3f", gDusk3DTexAmt);
}

// TEXGEN-SPLIT-ANSWER §1: which chains the texcoord shift applies to. 0 (default) = only
// ViewProjmap-tagged chains (the water shine) — world-projected overlays (terrain detail,
// cloud + drop shadows) stay glued to the ground. 1 = legacy shift-everything (A/B + recovery
// if the J3D mode split ever mis-fires). Read by aurora at the shader-config snapshot.
volatile int gDusk3DTxsAll = 0;
void Dusk3D_SetTxsAll(int all) {
    gDusk3DTxsAll = all;
    NSLog(@"[DuskHost] texcoord-shift scope %s", all ? "ALL layers (legacy)" : "water only");
}

// NEAR-DOUBLING §5: which transparency class to pin to the panel (so the doubling sprites
// render single). 0=off 1=glows(additive+noZ) 2=no-Z-write 3=transparent(blend/alpha-test)
// 4=alpha-tested. Cycling this finds the butterfly/flower draw class. Default 1 (safe).
volatile int gDusk3DFlatMode = 1;
void Dusk3D_SetFlatMode(int mode) {
    gDusk3DFlatMode = mode;
    NSLog(@"[DuskHost] flatten sprite mode %d", gDusk3DFlatMode);
}

// Size gate: only flatten draws with <= this many vertices, so tiny sprites (butterflies,
// flowers) get pinned but large same-class draws (ground decals / walking paths) keep depth.
// 0 = no limit. The "Sprite size" slider.
volatile int gDusk3DFlatMaxVtx = 48;
void Dusk3D_SetFlatMaxVtx(int n) {
    gDusk3DFlatMaxVtx = n;
    NSLog(@"[DuskHost] flatten sprite max verts %d", gDusk3DFlatMaxVtx);
}

// M5: the Sharpness slider setter. Stores the render scale and applies it (aurora's
// frame-buffer scale) ONLY while in 3D -- in 2D it would make the visible window
// render heavy for no benefit. On 3D entry the Swift applyAll() re-pushes the stored
// value (gDusk3DMode is already 1 by then); on exit Dusk_Exit3DFinalize resets to 0.
static float gDusk3DSharp = 4.0f; // default 3D render supersample (crisp)
void Dusk_SetSharpness(float sharp) {
    gDusk3DSharp = sharp;
    if (gDusk3DMode)
        Dusk3D_SetRenderScale(sharp);
    NSLog(@"[DuskHost] sharpness %.2f (mode=%d)", sharp, gDusk3DMode);
}

// The game renders AT the panel aspect (undistorted at any width/height, like 2D).
// Applied only in 3D; reset to native (0) on 3D exit so the 2D window renders normally.
static float gDusk3DPanelAspect = 0.0f;
void Dusk_SetPanelAspect(float aspect) {
    gDusk3DPanelAspect = aspect;
    if (gDusk3DMode)
        Dusk3D_SetPanelAspect(aspect);
}

void Dusk_Exit3DFinalize(void) {
    NSLog(@"[DuskHost] 3D exit finalized — engine back onscreen");
    gDusk3DMode = 0;
    Dusk3D_SetRenderScale(0.0f); // M5: back to native render res for cheap 2D
    Dusk3D_SetPanelAspect(0.0f); // back to native window aspect for 2D
    // Restore the pre-3D size FIRST (curtain still up), then reveal after the
    // scene settles. The 0026 glue follows the scene each frame, so requesting
    // the geometry is enough; re-request once mid-way in case the first request
    // lands during the dismiss transition (guide §5.8: a request is not a result).
    if (dusk_pre3dSize.width >= 1) {
        Dusk_RequestWindowSize(dusk_pre3dSize);
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.25 * NSEC_PER_SEC)),
                       dispatch_get_main_queue(), ^{
                           if (!gDusk3DMode && dusk_pre3dSize.width >= 1)
                               Dusk_RequestWindowSize(dusk_pre3dSize);
                       });
    }
    dusk_pre3dSize = CGSizeMake(0, 0); // allow a fresh capture next entry
    // Drop the curtain after the restore settles (hides the small frame
    // expanding mid-restore, guide §1.4).
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.6 * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{
                       if (!gDusk3DMode)
                           Dusk_SetCurtain(false);
                   });
}

// Crown/system dismissal (loop saw the layer invalidated): reconcile the shell +
// SwiftUI state so the ornament button and engine mode match reality.
void Dusk3D_Immersive_Ended(void) {
    dispatch_async(dispatch_get_main_queue(), ^{
        if (gDusk3DMode) {
            NSLog(@"[DuskHost] immersive ended by system — reconciling to 2D");
            Dusk_SetImmersiveMode(false);
        }
    });
}

void DuskIos_ToggleMenuKey(void) { NSLog(@"[DuskHost] ToggleMenuKey — no-op (v1 omits the Menu button)"); }
void DuskIos_SetAudioAnchorStatus(int s) { gDuskAudioAnchorStatus = s; }
