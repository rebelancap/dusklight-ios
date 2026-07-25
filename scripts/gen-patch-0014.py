#!/usr/bin/env python3
"""Overlay patch 0014: pin the visionOS UI density ratio so menus don't overflow.

THE menu-cropping bug, correctly diagnosed at last. Device screenshots
(tmp/IMG_0138/0139) show the prelaunch menu rendered far too big: the logo is
huge and only the top two rows (SELECT DISC IMAGE/PLAY + SETTINGS) fit -- MODS,
QUIT, the disc status and the version line are all pushed off the bottom. Both
menu states (disc loaded and not) are affected. The window aspect is fine (~4:3,
same as the sim); this is purely SCALE.

Root cause is the RmlUi density ratio. The prelaunch/settings UI is laid out in
`dp` (density-independent points) against a reference window of ~1216x896 dp --
see res/rml/prelaunch.rcss: fixed `428dp` buttons, `48dp`/`12dp` gaps, and the
`@media (min-width: 1216dp)` breakpoint that switches the eyebrow to vw sizing.
`sync_context_metrics` sets the ratio from `window::get_window_size().scale`.

On the SIMULATOR the window happens to be exactly 1216 dp wide (native_fb 3840,
scale 3.158), so the menu composes perfectly -- which is why every sim screenshot
looked right and the bug never reproduced there. On the DEVICE the app runs in a
free-floating panel that is much smaller in *points*; SDL's display scale then
drives the density ratio up (dp-width ~600-700 from the screenshots), so the
dp-fixed elements (which do not shrink below their dp size) consume most of the
window and overflow.

Fix (visionOS only): stop deriving the ratio from the fickle window point-size.
Pin it to the reference dp footprint the design targets, computed from the actual
render dimensions:

    ratio = min(longEdge / 1216, shortEdge / 896)

min() of the two axes guarantees the 1216x896 dp footprint fits BOTH width and
height, for any panel size or aspect. This makes the menu composition
panel-size-invariant -- always the layout the simulator shows -- instead of
blowing up on a small panel. `s_uiScale` (an explicit user override) still wins
when set.

Verification note: on the sim this is a *no-op* (native_fb 3840x2829 ->
min(3840/1216, 2829/896) = 3.157 ~= the current 3.158), so the sim confirms
non-regression but cannot exercise the device path (it is already at the
reference). The logic is small and self-adjusting; a device log line
(`visionOS UI density ratio ...`) is emitted so the numbers can be confirmed on
hardware.

Isolated to the RmlUi density ratio; gameplay (GX/imgui) is untouched. Two hunks:
the TargetConditionals include and the ratio computation.
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
REL = "extern/aurora/lib/rmlui.cpp"
SRC = ROOT / "vendor/dusklight" / REL

orig = SRC.read_text()
text = orig

# --- hunk 1: TargetConditionals include (after <thread>) ---------------------
inc_old = ('#include <algorithm>\n'
           '#include <thread>\n')
inc_new = ('#include <algorithm>\n'
           '#include <thread>\n'
           '\n'
           '#ifdef __APPLE__\n'
           '#include <TargetConditionals.h>\n'
           '#endif\n')
assert text.count(inc_old) == 1, f"include anchor: {text.count(inc_old)}"
text = text.replace(inc_old, inc_new)

# --- hunk 2: the density ratio computation -----------------------------------
ratio_old = ('  const float ratio = s_uiScale > 0.0f ? s_uiScale : window::get_window_size().scale;\n'
             '  if (g_context->GetDensityIndependentPixelRatio() != ratio) {\n'
             '    g_context->SetDensityIndependentPixelRatio(ratio);\n'
             '  }\n')
ratio_new = (
    '#if defined(__APPLE__) && TARGET_OS_VISION\n'
    '  // The prelaunch/settings UI is laid out in `dp` against a reference window of\n'
    '  // ~1216x896 dp (res/rml/prelaunch.rcss: fixed 428dp buttons, the\n'
    '  // `@media (min-width: 1216dp)` breakpoint). On visionOS the window is a\n'
    '  // free-floating panel whose size in *points* varies, so deriving the density\n'
    '  // ratio from window scale makes the dp-fixed menu overflow on a panel that is\n'
    '  // small in points (logo huge, MODS/QUIT/version clipped). Pin the ratio to the\n'
    '  // reference footprint instead, from the actual render dimensions; min() of the\n'
    '  // two axes fits the footprint in both width and height, any aspect.\n'
    '  constexpr float kUiRefLongEdgeDp = 1216.0f;\n'
    '  constexpr float kUiRefShortEdgeDp = 896.0f;\n'
    '  const float longEdge = static_cast<float>(std::max(dimensions.x, dimensions.y));\n'
    '  const float shortEdge = static_cast<float>(std::min(dimensions.x, dimensions.y));\n'
    '  const float ratio = s_uiScale > 0.0f\n'
    '                          ? s_uiScale\n'
    '                          : std::max(0.25f, std::min(longEdge / kUiRefLongEdgeDp,\n'
    '                                                     shortEdge / kUiRefShortEdgeDp));\n'
    '  static bool loggedRatio = false;\n'
    '  if (!loggedRatio) {\n'
    '    loggedRatio = true;\n'
    '    Log.info("visionOS UI density ratio {} for render {}x{} -> {}x{} dp", ratio, dimensions.x,\n'
    '             dimensions.y, static_cast<int>(std::lround(dimensions.x / ratio)),\n'
    '             static_cast<int>(std::lround(dimensions.y / ratio)));\n'
    '  }\n'
    '#else\n'
    '  const float ratio = s_uiScale > 0.0f ? s_uiScale : window::get_window_size().scale;\n'
    '#endif\n'
    '  if (g_context->GetDensityIndependentPixelRatio() != ratio) {\n'
    '    g_context->SetDensityIndependentPixelRatio(ratio);\n'
    '  }\n')
assert text.count(ratio_old) == 1, f"ratio anchor: {text.count(ratio_old)}"
text = text.replace(ratio_old, ratio_new)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(text); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1

out = ROOT / "overlay/patches/0014-aurora-visionos-ui-density-ratio.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
