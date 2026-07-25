#!/usr/bin/env python3
"""Overlay patch 0013: cap the texture-replacement cache at 512 MB on visionOS.

The decoded-texture LRU cache budget is hardcoded 4 GB:

    constexpr uint64_t kReplacementCacheBudgetBytes = 4294967296; // 4GB

Diagnosed from a device crash log (patch 0012 put it in Files; build-ID
matched our binary, so it symbolicated exactly):

    Reason: SIGABRT (abort)   <- NOT jetsam (that would be SIGKILL)
    #03 ... MakeDebugName (small std::string alloc)   <- bad_alloc, heap exhausted
    #04 dawn ... Texture::CreateMetalTextureDescriptor
    ...
    #10 aurora ... find_replacement_for_key_locked (texture_replacement.cpp:916)
    #11 aurora ... gx::resolve_sampled_textures (gx.cpp:472, render time)

So: during gameplay the game lazily creates a Metal texture for a 4K replacement,
and a small allocation inside Dawn's texture creation throws std::bad_alloc (the
heap is exhausted by the 4K textures) -> uncaught -> std::terminate -> abort. A
4K RGBA texture with mips is ~85 MB; the 4 GB LRU budget let far too many pile up
before eviction, past the app's real memory ceiling.

Cap the cache at 512 MB on visionOS so the LRU evicts aggressively and leaves
headroom for a new 4K texture alloc plus the game's own footprint (assets, the
3840 framebuffers from patch 0006, Dawn/Metal). The 4 GB budget on other
platforms is untouched.

This pairs with the increased-memory-limit + extended-virtual-addressing
entitlements added in publish-vision-ota.sh (raise the ceiling). Belt and
suspenders: the entitlements give room, the 512 MB cap keeps the working set
bounded even if the OS ignores them. Tradeoff at 512 MB is more reload churn with
a huge pack -- strictly better than a crash, and tunable up if the entitlements
prove to take effect on device.

visionOS-gated; every other platform keeps the 4 GB budget.
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
REL = "extern/aurora/lib/gfx/texture_replacement.cpp"
SRC = ROOT / "vendor/dusklight" / REL

orig = SRC.read_text()

old = 'constexpr uint64_t kReplacementCacheBudgetBytes = 4294967296; // 4GB\n'
new = ('#if defined(__APPLE__)\n'
       '#include <TargetConditionals.h>\n'
       '#endif\n'
       '#if defined(__APPLE__) && TARGET_OS_VISION\n'
       '// Vision Pro jetsam-kills the app long before 4GB; a large (4K) texture\n'
       '// pack fills the LRU past the OS limit before it evicts. Cap at 512MB so the\n'
       '// cache stays under the limit (more reload churn, but no crash). See 0013.\n'
       'constexpr uint64_t kReplacementCacheBudgetBytes = 536870912; // 512MB (visionOS)\n'
       '#else\n'
       'constexpr uint64_t kReplacementCacheBudgetBytes = 4294967296; // 4GB\n'
       '#endif\n')

n = orig.count(old)
assert n == 1, f"expected 1 match, got {n}"
text = orig.replace(old, new)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(text); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1

out = ROOT / "overlay/patches/0013-aurora-visionos-texcache-budget.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
