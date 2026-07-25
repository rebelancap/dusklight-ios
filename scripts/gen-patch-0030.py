#!/usr/bin/env python3
"""Overlay patch 0030 (Phase 2 / M1): let SwiftUI own main() on visionOS.

STEREO-3D-GUIDE §5.2 + §5.5. Today the process entry on Apple platforms is
aurora/lib/main.cpp:

    #include <aurora/main.h>   // declares aurora_main + `#define main aurora_main`
    #undef main
    #include <SDL3/SDL_main.h> // renames THIS main -> SDL_main; SDL provides the
                               // real iOS/xrOS main() = UIApplicationMain bootstrap
    int main(int argc, char** argv) { return aurora_main(argc, argv); }

So on visionOS the game boots under SDL's UIApplicationMain / SDLUIKitSceneDelegate
(the `postFinishLaunch` frame in our backtraces), which then calls aurora_main ->
dusk's main (dusk/main.cpp:223, itself `aurora_main` via the header's
`#define main aurora_main`) -> game_main (m_Do_main.cpp:483).

Phase 2 needs an `ImmersiveSpace`, and that can ONLY be declared from a SwiftUI
`App` that owns the process entry point. Two `main()`s cannot coexist, and if
SDL's UIApplicationMain wins, the game boots under SDL's scene delegate with no
SwiftUI App for `openImmersiveSpace` to attach to. So on visionOS we do NOT pull
in SDL_main.h here; instead we expose a plain C entry `dusk_engine_main` that the
Swift host controller (app/visionos/DuskHostViewController) calls -- after
`SDL_SetMainReady()` -- once the SwiftUI scene is live (guide §5.2, the two GCD
laws in §5.3 govern HOW it is called: a runloop-timer, never dispatch_async).

Gate is **structural** (`TARGET_OS_VISION`), NOT an optional `-D` flag: the guide
(§10) warns the 3D shell must never hinge on a define someone can forget and
silently ship a main-less / SDL-boot binary. Every visionOS build takes this
path; iOS/tvOS/macOS/Windows/Linux/Android are untouched (still SDL_main). The
DUSK_SWIFT_MAIN define is still set by CMake (patch 0031) for the Swift/ObjC shell
TUs that want to know they own main, but the engine-entry switch here keys off the
platform so it cannot regress.

Match-asserted; touches only aurora/lib/main.cpp. Paired with the CMake + shell
integration in the M1 patch series (0031: CMake Swift target + SDL_main drop;
new shell files under app/visionos/, no patch needed -- they are our tree).
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
REL = "extern/aurora/lib/main.cpp"
SRC = ROOT / "vendor/dusklight" / REL

orig = SRC.read_text()

old = ('#include <aurora/main.h>\n'
       '#undef main\n'
       '\n'
       '#include <SDL3/SDL_main.h>\n'
       '\n'
       'int main(int argc, char** argv) { return aurora_main(argc, argv); }\n')

new = ('#include <aurora/main.h>\n'
       '#undef main\n'
       '\n'
       '#if defined(__APPLE__)\n'
       '#include <TargetConditionals.h>\n'
       '#endif\n'
       '\n'
       '#if defined(__APPLE__) && TARGET_OS_VISION\n'
       '// visionOS: SwiftUI\'s @main App owns the process entry point (required to\n'
       '// declare an ImmersiveSpace for Phase 2). Do NOT include SDL_main.h here -- on\n'
       '// iOS/xrOS it would install SDL\'s UIApplicationMain bootstrap as the real main(),\n'
       '// which cannot coexist with the SwiftUI @main. Instead expose a plain C entry the\n'
       '// Swift host controller calls (after SDL_SetMainReady()) once the scene is live.\n'
       'extern "C" int dusk_engine_main(int argc, char** argv) { return aurora_main(argc, argv); }\n'
       '#else\n'
       '#include <SDL3/SDL_main.h>\n'
       '\n'
       'int main(int argc, char** argv) { return aurora_main(argc, argv); }\n'
       '#endif\n')

assert orig.count(old) == 1, f"anchor: {orig.count(old)}"
text = orig.replace(old, new)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(text); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1

out = ROOT / "overlay/patches/0030-aurora-visionos-swiftui-main-entry.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
