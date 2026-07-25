#!/usr/bin/env python3
"""Overlay patch 0029: bound the static GX texture cache (fixes the play-session
memory leak that jetsam-kills the app).

MEASURED (perf-soak log, patch 0027): over a ~15 min play session confined to the
beginning levels, phys_footprint climbed monotonically 832 -> 4900 MiB and
available-before-jetsam fell to ~216 MiB, at which point the OS killed the app
(no crash backtrace -- the file log just ends -- the signature of a jetsam kill).
fps held a flat 120 and thermal only reached "fair", so this is purely a memory
leak, not a perf/thermal problem.

ROOT CAUSE: `s_textureObjectCaches` (this file) is an unbounded map keyed by
`texObjId`, and `GXInitTexObj`/`GXInitTexObjCI` mint a *fresh monotonic id on
every call* (dolphin/gx/GXTexture.cpp `next_tex_obj_id()`). On real GC/Wii
hardware `GXInitTexObj` is a free POD initializer the game calls every frame for
stack-local and reused texture objects (fonts in JUTResFont, particles/rain in
d_kankyo_rain, UI, effects); aurora turned it into a cache-key allocator. So each
frame the *same logical texture* gets a *new id*, misses the cache, allocates a
brand-new GPU texture via `new_static_texture_2d`, and stores it forever -- the
only removal path is an explicit `GXDestroyTexObj`, which the game issues for
essentially none of them (222 GXInitTexObj call sites vs 6 GXDestroyTexObj). The
replacement cache stayed flat at 511 MiB precisely because *replaced* textures
return a shared handle out of that bounded cache, so the churn only copies a
shared_ptr; the ~4400 MiB of growth is the *non-replaced* textures each getting a
fresh GPU allocation per fresh id.

FIX (lowest-risk, mirrors the proven `expire_cached_bind_groups` in
gfx/common.cpp): age-out entries in `s_textureObjectCaches` that have not been
resolved for `StaticTextureCacheRetainFrames`, swept every
`StaticTextureCacheSweepPeriod` frames off `gfx::current_frame()`. Correctness is
preserved because:
  * Each cache entry still holds the *exact* texture uploaded for its id -- the
    sweep never rewrites data, so it can never serve a stale texture.
  * `lastUsedFrame` is stamped on every hit and on store, so a *stable* texture
    (re-resolved every frame) never ages out -- only one-shot churn ids do.
  * The retain window (90 frames = 0.75 s @120 / 1.5 s @60) is far above Dawn's
    frames-in-flight, and a texture still bound in `g_gxState.textures[]` or held
    by a cached bind-group view is kept alive by its own shared_ptr / Dawn's
    internal refcount -- so eviction frees GPU memory only once nothing uses it.
  * Evicting a static entry runs `clear_texture_dependency` exactly like
    `evict_texture_object`, keeping the TLUT-user bookkeeping consistent.

This converts unbounded per-frame growth into a bounded working-set window,
which stops the jetsam. (Eliminating the re-upload *waste* entirely -- content-
addressing the cache key, or deriving texObjId from content -- is a larger,
riskier change deferred behind a measurement; fps is already fine, so bounding
memory is the win that matters now.) The dynamic-palette conv textures under
`s_tlutObjectCaches` are a separate, far smaller contributor (cleared on
resize/shutdown) and are left alone pending their own measurement.

Not platform-gated: the leak and the fix are host-agnostic; visionOS just hits
the jetsam ceiling first. Confined to gx.cpp; no other patch touches it.
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
REL = "extern/aurora/lib/gx/gx.cpp"
SRC = ROOT / "vendor/dusklight" / REL

orig = SRC.read_text()
text = orig

# --- hunk 1: lastUsedFrame field on CachedTextureEntry -----------------------
entry_old = ('struct CachedTextureEntry {\n'
             '  gfx::TextureHandle handle;\n'
             '  u32 texDataVersion = 0;\n'
             '  u32 tlutObjId = 0;\n'
             '  u32 tlutDataVersion = 0;\n'
             '};\n')
entry_new = ('struct CachedTextureEntry {\n'
             '  gfx::TextureHandle handle;\n'
             '  u32 texDataVersion = 0;\n'
             '  u32 tlutObjId = 0;\n'
             '  u32 tlutDataVersion = 0;\n'
             '  u32 lastUsedFrame = 0;\n'
             '};\n')
assert text.count(entry_old) == 1, f"entry anchor: {text.count(entry_old)}"
text = text.replace(entry_old, entry_new)

# --- hunk 2: stamp lastUsedFrame on store ------------------------------------
store_old = ('  entry.handle = std::move(handle);\n'
             '  entry.texDataVersion = obj.texDataVersion;\n'
             '  entry.tlutObjId = tlutObjId;\n'
             '  entry.tlutDataVersion = tlutDataVersion;\n')
store_new = ('  entry.handle = std::move(handle);\n'
             '  entry.texDataVersion = obj.texDataVersion;\n'
             '  entry.tlutObjId = tlutObjId;\n'
             '  entry.tlutDataVersion = tlutDataVersion;\n'
             '  entry.lastUsedFrame = gfx::current_frame();\n')
assert text.count(store_old) == 1, f"store anchor: {text.count(store_old)}"
text = text.replace(store_old, store_new)

# --- hunk 3: sweep fn + constants, and resolve_static_texture (call + stamp) --
rst_old = (
    'gfx::TextureHandle resolve_static_texture(const GXTexObj_& obj) {\n'
    '  ZoneScoped;\n'
    '  if (s_staticTextureCacheClearPending.exchange(false, std::memory_order_acq_rel)) {\n'
    '    do_clear_static_texture_cache();\n'
    '  }\n'
    '\n'
    '  if (obj.texObjId != 0) {\n'
    '    if (const auto it = s_textureObjectCaches.find(obj.texObjId); it != s_textureObjectCaches.end()) {\n'
    '      const auto& entry = it->second;\n'
    '      if (entry.handle && entry.texDataVersion == obj.texDataVersion && entry.tlutObjId == 0) {\n'
    '        return entry.handle;\n'
    '      }\n'
    '    }\n'
    '  }\n')
rst_new = (
    '// The game re-inits texture objects every frame and GXInitTexObj mints a fresh\n'
    '// texObjId each time (dolphin/gx/GXTexture.cpp), so s_textureObjectCaches -- keyed\n'
    '// by that id and only ever pruned on an explicit GXDestroyTexObj the game almost\n'
    '// never issues -- grows without bound (a fresh GPU texture per fresh id) until the\n'
    '// OS jetsam-kills the app. Age out entries not resolved for a while, mirroring the\n'
    '// bind-group cache (gfx/common.cpp expire_cached_bind_groups): stable textures are\n'
    '// re-resolved every frame so their lastUsedFrame stays current and they survive;\n'
    '// one-shot churn ids age out and their sole-owned GPU memory frees. The sweep never\n'
    '// touches an entry\'s data, so it can never serve a stale texture. Retain is well\n'
    '// above Dawn frames-in-flight, and anything still bound / referenced by a cached\n'
    '// bind-group view stays alive by its own refcount until nothing uses it.\n'
    'constexpr u32 StaticTextureCacheRetainFrames = 90;\n'
    'constexpr u32 StaticTextureCacheSweepPeriod = 16;\n'
    '\n'
    'void expire_static_texture_cache() {\n'
    '  const u32 frame = gfx::current_frame();\n'
    '  if (s_textureObjectCaches.empty() || frame == UINT32_MAX ||\n'
    '      frame % StaticTextureCacheSweepPeriod != 0) {\n'
    '    return;\n'
    '  }\n'
    '  // resolve_static_* run many times per frame; sweep at most once per frame.\n'
    '  static u32 s_lastSweepFrame = UINT32_MAX;\n'
    '  if (frame == s_lastSweepFrame) {\n'
    '    return;\n'
    '  }\n'
    '  s_lastSweepFrame = frame;\n'
    '  for (auto it = s_textureObjectCaches.begin(); it != s_textureObjectCaches.end();) {\n'
    '    if (frame - it->second.lastUsedFrame > StaticTextureCacheRetainFrames) {\n'
    '      // absl flat_hash_map::erase returns void and invalidates only the erased\n'
    '      // iterator; advance past it first (as expire_cached_bind_groups does).\n'
    '      clear_texture_dependency(it->first, it->second.tlutObjId);\n'
    '      s_textureObjectCaches.erase(it++);\n'
    '    } else {\n'
    '      ++it;\n'
    '    }\n'
    '  }\n'
    '}\n'
    '\n'
    'gfx::TextureHandle resolve_static_texture(const GXTexObj_& obj) {\n'
    '  ZoneScoped;\n'
    '  if (s_staticTextureCacheClearPending.exchange(false, std::memory_order_acq_rel)) {\n'
    '    do_clear_static_texture_cache();\n'
    '  }\n'
    '  expire_static_texture_cache();\n'
    '\n'
    '  if (obj.texObjId != 0) {\n'
    '    if (const auto it = s_textureObjectCaches.find(obj.texObjId); it != s_textureObjectCaches.end()) {\n'
    '      auto& entry = it->second;\n'
    '      if (entry.handle && entry.texDataVersion == obj.texDataVersion && entry.tlutObjId == 0) {\n'
    '        entry.lastUsedFrame = gfx::current_frame();\n'
    '        return entry.handle;\n'
    '      }\n'
    '    }\n'
    '  }\n')
assert text.count(rst_old) == 1, f"rst anchor: {text.count(rst_old)}"
text = text.replace(rst_old, rst_new)

# --- hunk 4: resolve_static_palette_texture (call + stamp) --------------------
rsp_old = (
    'gfx::TextureHandle resolve_static_palette_texture(const GXTexObj_& obj, const GXTlutObj_& tlut) {\n'
    '  ZoneScoped;\n'
    '  if (s_staticTextureCacheClearPending.exchange(false, std::memory_order_acq_rel)) {\n'
    '    do_clear_static_texture_cache();\n'
    '  }\n'
    '\n'
    '  if (obj.texObjId != 0) {\n'
    '    if (const auto it = s_textureObjectCaches.find(obj.texObjId); it != s_textureObjectCaches.end()) {\n'
    '      const auto& entry = it->second;\n'
    '      if (entry.handle && entry.texDataVersion == obj.texDataVersion && entry.tlutObjId == tlut.tlutObjId &&\n'
    '          entry.tlutDataVersion == tlut.tlutDataVersion) {\n'
    '        return entry.handle;\n'
    '      }\n'
    '    }\n'
    '  }\n')
rsp_new = (
    'gfx::TextureHandle resolve_static_palette_texture(const GXTexObj_& obj, const GXTlutObj_& tlut) {\n'
    '  ZoneScoped;\n'
    '  if (s_staticTextureCacheClearPending.exchange(false, std::memory_order_acq_rel)) {\n'
    '    do_clear_static_texture_cache();\n'
    '  }\n'
    '  expire_static_texture_cache();\n'
    '\n'
    '  if (obj.texObjId != 0) {\n'
    '    if (const auto it = s_textureObjectCaches.find(obj.texObjId); it != s_textureObjectCaches.end()) {\n'
    '      auto& entry = it->second;\n'
    '      if (entry.handle && entry.texDataVersion == obj.texDataVersion && entry.tlutObjId == tlut.tlutObjId &&\n'
    '          entry.tlutDataVersion == tlut.tlutDataVersion) {\n'
    '        entry.lastUsedFrame = gfx::current_frame();\n'
    '        return entry.handle;\n'
    '      }\n'
    '    }\n'
    '  }\n')
assert text.count(rsp_old) == 1, f"rsp anchor: {text.count(rsp_old)}"
text = text.replace(rsp_old, rsp_new)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(text); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1

out = ROOT / "overlay/patches/0029-aurora-bound-static-texture-cache.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
