#!/usr/bin/env python3
"""Overlay patch 0025: live perf/thermal in the on-screen overlay.

Extends the existing FPS overlay (dusk `Overlay::update`) into the live readout
requested -- viewable on the headset while playing. Adds frame time (ms,
cross-platform) and, on visionOS, the OS thermal state from patch 0024
(`aurora_get_thermal_state`). gpu_ms (true GPU time via Dawn timestamp queries) is
the follow-on; wall frame time + thermal already show pacing vs throttling.

The overlay is toggled by `game.enableFpsOverlay` (defaulted ON for visionOS by
patch 0011) and positioned by `game.fpsOverlayCorner`, both already wired.
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
REL = "src/dusk/ui/overlay.cpp"
SRC = ROOT / "vendor/dusklight" / REL

orig = SRC.read_text()
text = orig

# --- hunk 1: TargetConditionals + thermal decl -------------------------------
inc_old = ('#include "overlay.hpp"\n'
           '\n'
           '#include "aurora/lib/logging.hpp"\n')
inc_new = ('#include "overlay.hpp"\n'
           '\n'
           '#ifdef __APPLE__\n'
           '#include <TargetConditionals.h>\n'
           '#endif\n'
           '#if defined(__APPLE__) && TARGET_OS_VISION\n'
           'extern "C" const char* aurora_get_thermal_state();\n'
           '#endif\n'
           '\n'
           '#include "aurora/lib/logging.hpp"\n')
assert text.count(inc_old) == 1, f"include anchor: {text.count(inc_old)}"
text = text.replace(inc_old, inc_new)

# --- hunk 2: the fps text line -> fps + frame time (+ thermal on visionOS) ----
txt_old = ('            if (refreshLabel) {\n'
           '                mFpsLastUpdate = now;\n'
           '                mFpsCounter->SetInnerRML(escape(fmt::format("{:.0f} FPS", fps)));\n'
           '            }\n')
txt_new = ('            if (refreshLabel) {\n'
           '                mFpsLastUpdate = now;\n'
           '                const float frameMs = fps > 0.f ? 1000.f / fps : 0.f;\n'
           '#if defined(__APPLE__) && TARGET_OS_VISION\n'
           '                mFpsCounter->SetInnerRML(escape(fmt::format(\n'
           '                    "{:.0f} FPS  {:.1f} ms  {}", fps, frameMs, aurora_get_thermal_state())));\n'
           '#else\n'
           '                mFpsCounter->SetInnerRML(escape(fmt::format("{:.0f} FPS  {:.1f} ms", fps, frameMs)));\n'
           '#endif\n'
           '            }\n')
assert text.count(txt_old) == 1, f"text anchor: {text.count(txt_old)}"
text = text.replace(txt_old, txt_new)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(text); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1

out = ROOT / "overlay/patches/0025-dusk-visionos-perf-thermal-overlay.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
