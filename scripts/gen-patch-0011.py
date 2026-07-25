#!/usr/bin/env python3
"""Overlay patch 0011: visionOS high-refresh defaults (120Hz on M5).

The target Vision Pro is an M5 (120Hz panel; M2-gen was 90). The frame path already
supports it -- vsync is on (Fifo present), and aurora does nothing to cap the
present rate, so a 2D window is composited at up to the panel rate. What was
missing is the *game* producing distinct frames at that rate:

  - enableFrameInterpolation defaults to Off, so the game renders at TP's ~60Hz
    simulation rate. The display shows 60 distinct frames even at 120Hz.
  - In Unlimited mode the sim stays 60Hz but a fresh interpolated frame is
    rendered every present. With vsync that is present-locked to the compositor
    = 120 on M5, 90 on M2-gen. This is the clean way to hit the panel rate
    (no busy-sleep Limiter, unlike Capped).

So on visionOS ONLY, default enableFrameInterpolation to Unlimited.

Also default enableFpsOverlay to true on visionOS. This is a deliberate choice
for the port's current phase: 120Hz sustained at the 3840 render size (patch
0006) is GPU-bound, and the only way to know the device actually holds 120 --
and whether it thermal-throttles -- is to read the counter on the headset. It is
one toggle to turn off in Settings once verified. (Revisit: default it off once
the true-gpu_ms instrumentation the charter wants exists.)

Both are plain config defaults the user can change in Settings; nothing here
forces behavior. visionOS-gated via TARGET_OS_VISION, so every other platform's
defaults are byte-for-byte unchanged.
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
REL = "src/dusk/settings.cpp"
SRC = ROOT / "vendor/dusklight" / REL

orig = SRC.read_text()
text = orig

EDITS = [
    # Add the TargetConditionals include (guarded) after the existing includes.
    (
        '#include "dusk/settings.h"\n'
        '#include "dusk/config.hpp"\n'
        '#include <aurora/aurora.h>\n',

        '#include "dusk/settings.h"\n'
        '#include "dusk/config.hpp"\n'
        '#include <aurora/aurora.h>\n'
        '\n'
        '#ifdef __APPLE__\n'
        '#include <TargetConditionals.h>\n'
        '#endif\n'
        '\n'
        '// visionOS (M5, 120Hz panel) high-refresh defaults. See overlay 0011.\n'
        '#if defined(__APPLE__) && TARGET_OS_VISION\n'
        '#define DUSK_DEFAULT_FRAME_INTERP FrameInterpMode::Unlimited\n'
        '#define DUSK_DEFAULT_FPS_OVERLAY true\n'
        '#else\n'
        '#define DUSK_DEFAULT_FRAME_INTERP FrameInterpMode::Off\n'
        '#define DUSK_DEFAULT_FPS_OVERLAY false\n'
        '#endif\n',
        1,
    ),
    (
        '        .enableFpsOverlay {"game.enableFpsOverlay", false},\n',
        '        .enableFpsOverlay {"game.enableFpsOverlay", DUSK_DEFAULT_FPS_OVERLAY},\n',
        1,
    ),
    (
        '        .enableFrameInterpolation {"game.enableFrameInterpolation", FrameInterpMode::Off},\n',
        '        .enableFrameInterpolation {"game.enableFrameInterpolation", DUSK_DEFAULT_FRAME_INTERP},\n',
        1,
    ),
]

for old, new, want in EDITS:
    n = text.count(old)
    assert n == want, f"expected {want} match(es), got {n} for:\n{old[:120]!r}"
    text = text.replace(old, new)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(text); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1

out = ROOT / "overlay/patches/0011-dusk-visionos-highrefresh-defaults.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
