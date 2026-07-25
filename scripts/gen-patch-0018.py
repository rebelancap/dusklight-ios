#!/usr/bin/env python3
"""Overlay patch 0018: force-disable Dawn's backend-label toggle on visionOS.

THE real texture-pack crash -- and it was never a memory problem. The device
instrumentation (patch 0017) proved it: at the first replacement-texture creation
there are ~4100 MiB available and footprint is <1 GB, yet it aborts with
std::bad_alloc. bad_alloc with 4 GB free is not exhaustion -- it is one absurd
allocation. Backtrace:

    ...APICreateTexture -> metal::Texture::CreateMetalTextureDescriptor
      -> metal::MakeDebugName -> absl::StrFormat("%s_%s", prefix, label)
      -> std::bad_alloc -> abort (SIGABRT), on the FIRST replacement texture

`MakeDebugName` (dawn UtilsMetal.mm) formats the texture's user label into an
NSString ONLY when the `use_user_defined_labels_in_backend` toggle is enabled.
aurora sets replacement labels via `.label = label.c_str()`; the WebGPU C++
`StringView(const char*)` ctor stores `length = WGPU_STRLEN` (SIZE_MAX). On device
that reaches StrFormat as a SIZE_MAX-length view -> ~16-exabyte string -> bad_alloc.
The game's own textures never trip it in practice, but every pack's first
replacement texture does (any format), which is why the game runs fine WITHOUT a
pack and dies the instant it creates its first replacement.

The FIRST attempt only removed the toggle from aurora's *enable* list -- a no-op:
Dawn's default for it is "false unless backend validation is enabled, in which
case true" (Toggles.cpp), and it stayed on, so the crash persisted (build d3de…).
This version **explicitly adds it to the *disable* list** on visionOS, forcing it
off regardless of the default. `MakeDebugName` then returns just the prefix and
never formats the label. Cosmetic Metal-frame-capture aid; every other platform
keeps it.

Sim confirms non-regression (textures create, clamp fires, footprint flat, 0
crashes); the crash is device-only so the sim cannot prove the fix, only that
nothing broke. Ships with a diagnostic (patch 0019) that logs the crashing
texture's size/format/label, so if it somehow still aborts we see exactly what.

Three hunks: TargetConditionals include, enable-list gate (kept), disable-list add.
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
REL = "extern/aurora/lib/webgpu/gpu.cpp"
SRC = ROOT / "vendor/dusklight" / REL

orig = SRC.read_text()
text = orig

# --- hunk 1: TargetConditionals include --------------------------------------
inc_old = ('#include <vector>\n'
           '\n'
           '#include <aurora/aurora.h>\n')
inc_new = ('#include <vector>\n'
           '\n'
           '#ifdef __APPLE__\n'
           '#include <TargetConditionals.h>\n'
           '#endif\n'
           '\n'
           '#include <aurora/aurora.h>\n')
assert text.count(inc_old) == 1, f"include anchor: {text.count(inc_old)}"
text = text.replace(inc_old, inc_new)

# --- hunk 2: don't put it in the enable list on visionOS (belt) --------------
tog_old = ('#ifndef ANDROID\n'
           '        "use_user_defined_labels_in_backend",\n'
           '#endif\n')
tog_new = ('#if !defined(ANDROID) && !(defined(__APPLE__) && TARGET_OS_VISION)\n'
           '        "use_user_defined_labels_in_backend",\n'
           '#endif\n')
assert text.count(tog_old) == 1, f"enable anchor: {text.count(tog_old)}"
text = text.replace(tog_old, tog_new)

# --- hunk 3: FORCE it off via the disable list on visionOS (the real fix) -----
dis_old = ('    constexpr std::array disableToggles{\n'
           '        "timestamp_quantization",\n'
           '    };\n')
dis_new = ('    constexpr std::array disableToggles{\n'
           '        "timestamp_quantization",\n'
           '#if defined(__APPLE__) && TARGET_OS_VISION\n'
           '        // Force OFF: MakeDebugName formats the user label, aurora sets replacement\n'
           '        // labels via `.label = c_str()` -> StringView length = WGPU_STRLEN (SIZE_MAX)\n'
           '        // -> StrFormat bad_alloc on device. Default is on here; disable explicitly.\n'
           '        "use_user_defined_labels_in_backend",\n'
           '#endif\n'
           '    };\n')
assert text.count(dis_old) == 1, f"disable anchor: {text.count(dis_old)}"
text = text.replace(dis_old, dis_new)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(text); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1

out = ROOT / "overlay/patches/0018-aurora-visionos-disable-backend-labels.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
