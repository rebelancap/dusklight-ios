#!/usr/bin/env python3
"""Overlay patch 0019: correct replacement-texture label length + crash diagnostic.

Pairs with 0018. Two things:

1. ROOT FIX for the MakeDebugName bad_alloc (D-017): aurora sets the replacement
   texture + view labels with `.label = X.c_str()`. The WebGPU C++
   `StringView(const char*)` ctor stores `length = WGPU_STRLEN` (SIZE_MAX); Dawn's
   Metal MakeDebugName can then StrFormat a SIZE_MAX-length view -> bad_alloc.
   Pass `std::string_view(X)` instead, which uses the `StringView(std::string_view)`
   ctor and records the REAL length. Correct even if the label toggle is on, so
   this fixes the crash at the source (0018 removes the site; this fixes the cause).

2. DIAGNOSTIC: log the first replacement texture's actual size / mips / format /
   label+length right before CreateTexture, so if it somehow still aborts the next
   device log shows exactly what is being created (bogus dimensions? bogus label?).
   Fires once. visionOS-gated.

<string_view> and TARGET_OS_VISION are already included in this file.
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
REL = "extern/aurora/lib/gfx/texture_replacement.cpp"
SRC = ROOT / "vendor/dusklight" / REL

orig = SRC.read_text()
text = orig

# --- hunk 1: texture label -> string_view + diagnostic before CreateTexture ---
tex_old = ('  const wgpu::TextureDescriptor textureDescriptor{\n'
           '      .label = label.c_str(),\n')
tex_new = ('  const wgpu::TextureDescriptor textureDescriptor{\n'
           '      .label = std::string_view(label),\n')
assert text.count(tex_old) == 1, f"tex label anchor: {text.count(tex_old)}"
text = text.replace(tex_old, tex_new)

create_old = '  auto texture = g_device.CreateTexture(&textureDescriptor);\n'
create_new = ('#if defined(__APPLE__) && TARGET_OS_VISION\n'
              '  static bool s_loggedFirstConverted = false;\n'
              '  if (!s_loggedFirstConverted) {\n'
              '    s_loggedFirstConverted = true;\n'
              '    Log.info("texture_replacement: creating replacement {}x{} mips {} format {} label \'{}\' (len {})",\n'
              '             replacement.width, replacement.height, replacement.mips,\n'
              '             static_cast<uint32_t>(replacement.format), label, label.size());\n'
              '  }\n'
              '#endif\n'
              '  auto texture = g_device.CreateTexture(&textureDescriptor);\n')
assert text.count(create_old) == 1, f"create anchor: {text.count(create_old)}"
text = text.replace(create_old, create_new)

# --- hunk 2: view label -> string_view ---------------------------------------
view_old = ('  const wgpu::TextureViewDescriptor textureViewDescriptor{\n'
            '      .label = viewLabel.c_str(),\n')
view_new = ('  const wgpu::TextureViewDescriptor textureViewDescriptor{\n'
            '      .label = std::string_view(viewLabel),\n')
assert text.count(view_old) == 1, f"view label anchor: {text.count(view_old)}"
text = text.replace(view_old, view_new)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(text); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1

out = ROOT / "overlay/patches/0019-aurora-visionos-label-length-and-diag.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
