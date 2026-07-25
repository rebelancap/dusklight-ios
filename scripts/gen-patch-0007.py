#!/usr/bin/env python3
"""Overlay patch 0007: claim the gamepad on visionOS (GCEventInteraction).

VISION-PRO-GUIDE 1.3 calls this "THE critical input fix" and "the #1 thing that
will look broken first": on visionOS the system converts game-controller input
into gaze-and-pinch UI events and **withholds it from GCController**, so the game
sees a dead pad while the pad drives the system pointer. Every sibling port hit
this. It is REQUIRED for a 2D window / shared space.

Apple's own GCEventInteraction.h says the same thing, and says exactly where the
interaction goes:

    "By default, the system converts game controller actions into pinch events
     and sends them to the view the user is gazing at [...] If you use the Game
     Controller framework to handle game controller events for part of your user
     interface, add an instance of GCEventInteraction to the root of that part of
     your app's view hierarchy. For example, if you are writing a game using
     Metal, add this interaction to the view that hosts your game's
     CAMetalLayer."

MetalBinding.mm is literally that view: it calls SDL_Metal_CreateView(window) and
hands SDL_Metal_GetLayer(view) to Dawn as the wgpu surface. So the interaction is
attached there, on the view that owns the CAMetalLayer -- not guessed, prescribed.

Details that matter:
  - handledEventTypes = GCUIEventTypeGamepad claims pad events only; gaze/pinch
    for the rest of the UI is untouched.
  - `receivesEventsInView` is left at its default NO, which per the header means
    pad events are delivered **exclusively** through the GameController
    framework. That is what we want: SDL3's Apple controller backend reads
    GCController, so once claimed, SDL sees the pad and dusklight's existing
    bind system works unchanged.
  - API_AVAILABLE(visionos(2.0)) matches our deployment target exactly, and the
    @available guard keeps it honest anyway.
  - The view retains interactions added via addInteraction:, so the ARC local is
    fine (MetalBinding.mm is compiled -fobjc-arc).
  - visionOS-only: TARGET_OS_VISION. iOS/tvOS/macOS are untouched -- iOS does not
    need this (no gaze layer to steal presses) and GCEventInteraction is
    API_UNAVAILABLE on macOS/tvOS.

**NOT VERIFIABLE IN THE SIMULATOR.** The sim does not reproduce visionOS routing
pad input to the gaze layer; it happily reports a virtual pad either way (the sim
logs `Added controller 'Gamepad' vid 05ac` with or without this patch). This
lands so the first device OTA can verify it -- see MEASUREMENTS.
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
REL = "extern/aurora/lib/dawn/MetalBinding.mm"
SRC = ROOT / "vendor/dusklight" / REL

orig = SRC.read_text()
text = orig

EDITS = [
    (
        '#include "BackendBinding.hpp"\n'
        '\n'
        '#import <Foundation/Foundation.h>\n'
        '#include <SDL3/SDL_metal.h>\n',

        '#include "BackendBinding.hpp"\n'
        '\n'
        '#import <Foundation/Foundation.h>\n'
        '#include <SDL3/SDL_metal.h>\n'
        '\n'
        '#include <TargetConditionals.h>\n'
        '#if TARGET_OS_VISION\n'
        '#import <GameController/GameController.h>\n'
        '#import <UIKit/UIKit.h>\n'
        '#endif\n',
        1,
    ),
    (
        '  SDL_MetalView view = SDL_Metal_CreateView(window);\n'
        '  std::shared_ptr<wgpu::SurfaceSourceMetalLayer> desc = std::make_shared<wgpu::SurfaceSourceMetalLayer>();\n',

        '  SDL_MetalView view = SDL_Metal_CreateView(window);\n'
        '#if TARGET_OS_VISION\n'
        '  // visionOS converts game-controller input into gaze-and-pinch UI events and\n'
        '  // withholds it from GCController, so without this the pad drives the system\n'
        '  // pointer and the game sees a dead controller. Apple\'s GCEventInteraction\n'
        '  // docs say to attach the interaction to the view hosting the CAMetalLayer,\n'
        '  // which is exactly this view. receivesEventsInView stays at its default NO,\n'
        '  // so pad events arrive *exclusively* via GameController -- which is what\n'
        '  // SDL3\'s Apple controller backend reads.\n'
        '  if (@available(visionOS 2.0, *)) {\n'
        '    UIView* metalView = (__bridge UIView*)view;\n'
        '    if (metalView != nil) {\n'
        '      GCEventInteraction* padClaim = [[GCEventInteraction alloc] init];\n'
        '      padClaim.handledEventTypes = GCUIEventTypeGamepad;\n'
        '      [metalView addInteraction:padClaim];\n'
        '    }\n'
        '  }\n'
        '#endif\n'
        '  std::shared_ptr<wgpu::SurfaceSourceMetalLayer> desc = std::make_shared<wgpu::SurfaceSourceMetalLayer>();\n',
        1,
    ),
]

for old, new, want in EDITS:
    n = text.count(old)
    assert n == want, f"expected {want} match(es), got {n} for:\n{old[:140]!r}"
    text = text.replace(old, new)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(text); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1

out = ROOT / "overlay/patches/0007-aurora-visionos-gamepad-claim.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
