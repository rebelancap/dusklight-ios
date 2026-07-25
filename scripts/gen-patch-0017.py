#!/usr/bin/env python3
"""Overlay patch 0017: log the app's memory footprint under texture pressure.

Charter rule 2/5: measure, and keep the device diagnosable. The 4K-pack crash is
memory exhaustion, but no log line shows the app's actual footprint or the app's
memory *limit* -- so tuning the cache budget / downscale cap has been guesswork.

Add a phys_footprint readout (mach task_vm_info) and log it, rate-limited, from
find_replacement_for_key_locked whenever a new replacement texture is created
(the exact path that leads to the OOM). The next device log then shows the memory
trajectory during the opening cutscene and the ceiling it hits -- turning the
next tuning decision (is 2048/512MB right, or do we need 1024/256MB?) into a
measurement instead of a guess.

visionOS-gated, diagnostic-only. Every other platform is untouched.
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
REL = "extern/aurora/lib/gfx/texture_replacement.cpp"
SRC = ROOT / "vendor/dusklight" / REL

orig = SRC.read_text()
text = orig

# --- hunk 1: mach header ------------------------------------------------------
# Anchor on <vector> (well clear of patch 0013's TargetConditionals block, so the
# hunks never overlap). Re-include TargetConditionals here -- header-guarded, so
# the duplicate is harmless -- because TARGET_OS_VISION must be defined before it
# is tested, and 0013's include sits further down the file.
inc_old = '#include <vector>\n'
inc_new = ('#include <vector>\n'
           '\n'
           '#if defined(__APPLE__)\n'
           '#include <TargetConditionals.h>\n'
           '#if TARGET_OS_VISION\n'
           '#include <mach/mach.h>\n'
           '#include <os/proc.h>\n'
           '#endif\n'
           '#endif\n')
assert text.count(inc_old) == 1, f"include anchor: {text.count(inc_old)}"
text = text.replace(inc_old, inc_new)

# --- hunk 2: footprint helper, before find_replacement_for_key_locked --------
helper_anchor = ('std::optional<aurora::gfx::TextureHandle>\n'
                 'find_replacement_for_key_locked(const aurora::texture::ReplacementKey& key) noexcept {\n')
helper = (
    '#if defined(__APPLE__) && TARGET_OS_VISION\n'
    'uint64_t current_phys_footprint_mib() noexcept {\n'
    '  task_vm_info_data_t info{};\n'
    '  mach_msg_type_number_t count = TASK_VM_INFO_COUNT;\n'
    '  if (task_info(mach_task_self(), TASK_VM_INFO, reinterpret_cast<task_info_t>(&info), &count) == KERN_SUCCESS) {\n'
    '    return static_cast<uint64_t>(info.phys_footprint) / (1024ull * 1024ull);\n'
    '  }\n'
    '  return 0;\n'
    '}\n'
    '// Bytes the process may still allocate before jetsam -- the actual headroom,\n'
    '// and the number that decides whether a pack fits (FABLE 4.3).\n'
    'uint64_t available_memory_mib() noexcept {\n'
    '  return static_cast<uint64_t>(os_proc_available_memory()) / (1024ull * 1024ull);\n'
    '}\n'
    '#endif\n'
    '\n')
assert text.count(helper_anchor) == 1, f"helper anchor: {text.count(helper_anchor)}"
text = text.replace(helper_anchor, helper + helper_anchor)

# --- hunk 2b: pre-create first-miss log (fires BEFORE the first CreateTexture, so
#             it survives a crash on the very first creation) ------------------
pre_anchor = '  auto handle = load_entry_handle(key, *entry);\n'
pre_new = ('#if defined(__APPLE__) && TARGET_OS_VISION\n'
           '  static bool s_loggedFirstCreate = false;\n'
           '  if (!s_loggedFirstCreate) {\n'
           '    s_loggedFirstCreate = true;\n'
           '    Log.info("texture_replacement: first replacement creation starting; available {} MiB, footprint {} MiB",\n'
           '             available_memory_mib(), current_phys_footprint_mib());\n'
           '  }\n'
           '#endif\n'
           '  auto handle = load_entry_handle(key, *entry);\n')
assert text.count(pre_anchor) == 1, f"pre-create anchor: {text.count(pre_anchor)}"
text = text.replace(pre_anchor, pre_new)

# --- hunk 3: the log call, after the new texture is cached + evicted ----------
log_anchor = ('  s_replacementCacheBytes += replacementBytes;\n'
              '  evict_replacement_cache_if_needed();\n'
              '  return handle;\n')
log_new = ('  s_replacementCacheBytes += replacementBytes;\n'
           '  evict_replacement_cache_if_needed();\n'
           '#if defined(__APPLE__) && TARGET_OS_VISION\n'
           '  static uint32_t s_createCount = 0;\n'
           '  const uint32_t c = ++s_createCount;\n'
           '  if (c <= 8 || c == 16 || c == 32 || (c & 0x3F) == 0) { // every creation for the first 8, then sparse\n'
           '    Log.info("texture_replacement: {} created, cache {} MiB, footprint {} MiB, available {} MiB", c,\n'
           '             s_replacementCacheBytes / (1024ull * 1024ull), current_phys_footprint_mib(),\n'
           '             available_memory_mib());\n'
           '  }\n'
           '#endif\n'
           '  return handle;\n')
assert text.count(log_anchor) == 1, f"log anchor: {text.count(log_anchor)}"
text = text.replace(log_anchor, log_new)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(text); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1

out = ROOT / "overlay/patches/0017-aurora-visionos-mem-footprint-log.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
