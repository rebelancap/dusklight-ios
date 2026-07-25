#!/usr/bin/env python3
"""Overlay patch 0016: clamp UNCOMPRESSED visionOS replacement textures to 2048.

Safety net for the one texture-pack hazard left after FABLE's analysis
(4K-TEXTURES-ANALYSIS-FABLE.md): **uncompressed 32-bpp replacements**. The M5
Vision Pro enables both TextureCompressionBC and TextureCompressionASTC, so the
compressed packs (Henriko 4.0d Mobile = ASTC 4x4, Henriko 4.0d 4K = BC7, and the
BC7 majority of TPDE+) upload compressed at ~8 bpp and are fine untouched. The
danger is uncompressed BGRA8/RGBA8 textures: TPDE+ 1.2 ships **394 BGRA8 files**
up to 8192x4096 = ~128 MB *decoded each*, and the opening cutscene creates enough
at once to exhaust the heap:

    aurora_end_frame -> resolve_sampled_textures -> find_replacement_for_key_locked
      -> dawn APICreateTexture -> metal MakeDebugName -> std::bad_alloc -> abort

Confirmed on device: TPDE+ (5456 registrations) aborts the same way the old
uncompressed Mobile 3.0b (PNG -> RGBA8) pack did. Capping the LRU cache (patch
0013) does NOT help -- the *simultaneous working set*, not cache growth, is the
problem, and a single frame can't evict the textures it is drawing.

Fix: box-filter downscale any replacement in an uncompressed 32-bpp format
(RGBA8/BGRA8 + sRGB variants) whose long edge exceeds kVisionMaxReplacementDim
(2048), in load_encoded_replacement. Per-texture memory drops sharply (a 8192x4096
BGRA8 monster: 128 MB -> ~8 MB at 2048x1024) and the pack loads. Compressed
formats (BC7/ASTC, blockSize != 4) pass through UNTOUCHED -- gate is format_info,
not a single enum. This is a stopgap: the proper fix for TPDE+ is converting its
394 BGRA8 files to ASTC 4x4 offline (astcenc), preserving full resolution; see
FABLE §4.4. 2048 keeps them usable meanwhile (FABLE's working-set math clears it).

Applied before mip handling, so embedded/sidecar mips are covered (mips dropped on
clamped textures only; the compressed packs' full mip chains are never touched --
that matters, it's the SoH "mipmap engine" win, FABLE §5). Cache accounting is
automatically correct (bytes from the now-smaller handle).

The box filter is byte-for-byte aurora's own gfx::downscale and was re-verified
standalone under ASan/UBSan (4096->2048, odd dims, averaging, no-op, no OOB).

visionOS-gated; every other platform untouched. Compressed + non-32-bpp formats
fall through unchanged.
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
REL = "extern/aurora/lib/gfx/texture_replacement.cpp"
SRC = ROOT / "vendor/dusklight" / REL

orig = SRC.read_text()
text = orig

# --- hunk 1: the downscale helper, before load_encoded_replacement -----------
helper_anchor = ('template <typename Source>\n'
                 'std::optional<aurora::gfx::ConvertedTexture> load_encoded_replacement(Source&& src) noexcept {\n')
helper = (
    '#if defined(__APPLE__) && TARGET_OS_VISION\n'
    '// A 4K RGBA8 replacement is ~64MB (+mips); a pack full of them exhausts the\n'
    '// app heap on visionOS during a texture-heavy moment (the opening cutscene) ->\n'
    '// bad_alloc in Metal texture creation -> abort. 4K is also invisible overkill in\n'
    '// a ~1216pt floating window. Box-filter downscale RGBA8 replacements so the long\n'
    '// edge is <= kVisionMaxReplacementDim, bounding per-texture memory ~4x. The\n'
    '// filter matches gfx::downscale and was verified standalone (ASan/UBSan).\n'
    'constexpr uint32_t kVisionMaxReplacementDim = 2048;\n'
    'void clamp_replacement_to_cap(aurora::gfx::ConvertedTexture& tex, uint32_t cap) noexcept {\n'
    '  if (tex.width <= cap && tex.height <= cap) {\n'
    '    return; // already within budget\n'
    '  }\n'
    '  uint32_t w = tex.width;\n'
    '  uint32_t h = tex.height;\n'
    '  // Box-filter downscale works on any UNCOMPRESSED 32-bpp layout -- RGBA8 and\n'
    '  // BGRA8 and their sRGB variants all average per-byte identically. Gate on\n'
    '  // format_info, not a single enum: the Henriko pack decodes DDS to BGRA8Unorm,\n'
    '  // which the old RGBA8Unorm-only gate skipped -> textures stayed 4K -> still OOM.\n'
    '  const auto info = aurora::gfx::format_info(tex.format);\n'
    '  if (info.compressed || info.blockSize != 4) {\n'
    '    static bool loggedSkip = false;\n'
    '    if (!loggedSkip) {\n'
    '      loggedSkip = true;\n'
    '      Log.warn("texture_replacement: visionOS cannot downscale oversized {}x{} replacement "\n'
    '               "(format {}, compressed {}, blockSize {}); memory risk remains",\n'
    '               w, h, static_cast<uint32_t>(tex.format), info.compressed, info.blockSize);\n'
    '    }\n'
    '    return;\n'
    '  }\n'
    '  if (w == 0 || h == 0 || tex.data.size() < static_cast<size_t>(w) * h * 4) {\n'
    '    return; // malformed; do not touch\n'
    '  }\n'
    '  const uint32_t origW = w;\n'
    '  const uint32_t origH = h;\n'
    '  // Halve with a 2x2 box filter until both dimensions fit. Reading only mip 0\n'
    '  // (the first w*h*4 bytes) drops any mip chain; we set mips=1 below.\n'
    '  while (w > cap || h > cap) {\n'
    '    const uint32_t nw = std::max(1u, w / 2);\n'
    '    const uint32_t nh = std::max(1u, h / 2);\n'
    '    aurora::ByteBuffer dst{static_cast<size_t>(nw) * nh * 4};\n'
    '    const uint8_t* srcPixels = tex.data.data();\n'
    '    uint8_t* dstPixels = dst.data();\n'
    '    for (uint32_t y = 0; y < nh; ++y) {\n'
    '      const uint32_t y0 = std::min(y * 2, h - 1);\n'
    '      const uint32_t y1 = std::min(y * 2 + 1, h - 1);\n'
    '      for (uint32_t x = 0; x < nw; ++x) {\n'
    '        const uint32_t x0 = std::min(x * 2, w - 1);\n'
    '        const uint32_t x1 = std::min(x * 2 + 1, w - 1);\n'
    '        const uint8_t* p00 = srcPixels + (static_cast<size_t>(y0) * w + x0) * 4;\n'
    '        const uint8_t* p01 = srcPixels + (static_cast<size_t>(y0) * w + x1) * 4;\n'
    '        const uint8_t* p10 = srcPixels + (static_cast<size_t>(y1) * w + x0) * 4;\n'
    '        const uint8_t* p11 = srcPixels + (static_cast<size_t>(y1) * w + x1) * 4;\n'
    '        uint8_t* o = dstPixels + (static_cast<size_t>(y) * nw + x) * 4;\n'
    '        for (int c = 0; c < 4; ++c) {\n'
    '          o[c] = static_cast<uint8_t>(\n'
    '              (static_cast<uint32_t>(p00[c]) + p01[c] + p10[c] + p11[c] + 2) / 4);\n'
    '        }\n'
    '      }\n'
    '    }\n'
    '    tex.data = std::move(dst);\n'
    '    w = nw;\n'
    '    h = nh;\n'
    '  }\n'
    '  tex.width = w;\n'
    '  tex.height = h;\n'
    '  tex.mips = 1;\n'
    '  static bool loggedOnce = false;\n'
    '  if (!loggedOnce) {\n'
    '    loggedOnce = true;\n'
    '    Log.info("texture_replacement: visionOS downscaling oversized replacements "\n'
    '             "(e.g. format {} {}x{} -> {}x{}) to bound memory",\n'
    '             static_cast<uint32_t>(tex.format), origW, origH, w, h);\n'
    '  }\n'
    '}\n'
    '#endif\n'
    '\n')
assert text.count(helper_anchor) == 1, f"helper anchor: {text.count(helper_anchor)}"
text = text.replace(helper_anchor, helper + helper_anchor)

# --- hunk 2: the call site, after size validation ----------------------------
call_anchor = ('  if (!validate_texture_size(base->format, base->width, base->height, src.name())) {\n'
               '    return std::nullopt;\n'
               '  }\n'
               '\n'
               '  if (base->mips > 1) {\n')
call_new = ('  if (!validate_texture_size(base->format, base->width, base->height, src.name())) {\n'
            '    return std::nullopt;\n'
            '  }\n'
            '\n'
            '#if defined(__APPLE__) && TARGET_OS_VISION\n'
            '  clamp_replacement_to_cap(*base, kVisionMaxReplacementDim);\n'
            '#endif\n'
            '\n'
            '  if (base->mips > 1) {\n')
assert text.count(call_anchor) == 1, f"call anchor: {text.count(call_anchor)}"
text = text.replace(call_anchor, call_new)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(text); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1

out = ROOT / "overlay/patches/0016-aurora-visionos-cap-replacement-size.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
