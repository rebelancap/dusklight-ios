#!/usr/bin/env python3
"""Overlay patch 0027: periodic perf/thermal log for the soak test (visionOS).

The overlay (patch 0025) *displays* fps/ms/thermal live on the headset, but writes
nothing to the file log -- so a 10-15 min soak leaves no trace to analyze. This
logs the full picture every 5 s from the frame loop: fps, frame time, OS thermal
state (patch 0024), app phys_footprint, and available-before-jetsam. One run then
yields the whole trajectory -- thermal creep, fps stability, memory drift -- which
is what actually answers "is there optimization headroom / is it stable."

(gpu_ms -- true GPU time per frame -- is the follow-on: aurora's timestamp path is
gated behind TRACY_ENABLE, so surfacing it needs the query path enabled
independently; wall frame time + thermal already separate throttling from pacing.)

visionOS-gated; in aurora::update() (runs every frame). SDL_GetTicks for a
wall-clock 5 s cadence independent of fps.
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
REL = "extern/aurora/lib/aurora.cpp"
SRC = ROOT / "vendor/dusklight" / REL

orig = SRC.read_text()
text = orig

# --- hunk 1: includes + extern decls -----------------------------------------
inc_old = '#include "system_info.hpp"\n'
inc_new = ('#include "system_info.hpp"\n'
           '\n'
           '#ifdef __APPLE__\n'
           '#include <TargetConditionals.h>\n'
           '#if TARGET_OS_VISION\n'
           '#include <SDL3/SDL_timer.h>\n'
           '#include <mach/mach.h>\n'
           '#include <os/proc.h>\n'
           'extern "C" float aurora_get_fps();\n'
           'extern "C" const char* aurora_get_thermal_state();\n'
           '#endif\n'
           '#endif\n')
assert text.count(inc_old) == 1, f"include anchor: {text.count(inc_old)}"
text = text.replace(inc_old, inc_new)

# --- hunk 2: the periodic log in update() ------------------------------------
upd_old = ('const AuroraEvent* update() noexcept {\n'
           '  ZoneScoped;\n'
           '  if (g_initialFrame) {\n'
           '    g_initialFrame = false;\n'
           '    input::initialize();\n'
           '  }\n'
           '  return window::poll_events();\n'
           '}\n')
upd_new = ('const AuroraEvent* update() noexcept {\n'
           '  ZoneScoped;\n'
           '  if (g_initialFrame) {\n'
           '    g_initialFrame = false;\n'
           '    input::initialize();\n'
           '  }\n'
           '#if defined(__APPLE__) && TARGET_OS_VISION\n'
           '  {\n'
           '    // Soak trace: log the perf/thermal/memory picture every 5 s so a play\n'
           '    // session leaves an analyzable trajectory in the file log.\n'
           '    static uint64_t s_lastPerfLogMs = 0;\n'
           '    const uint64_t nowMs = SDL_GetTicks();\n'
           '    if (nowMs - s_lastPerfLogMs >= 5000) {\n'
           '      s_lastPerfLogMs = nowMs;\n'
           '      const float fps = aurora_get_fps();\n'
           '      const float frameMs = fps > 0.f ? 1000.f / fps : 0.f;\n'
           '      uint64_t footprintMiB = 0;\n'
           '      task_vm_info_data_t vmInfo{};\n'
           '      mach_msg_type_number_t count = TASK_VM_INFO_COUNT;\n'
           '      if (task_info(mach_task_self(), TASK_VM_INFO, reinterpret_cast<task_info_t>(&vmInfo), &count) ==\n'
           '          KERN_SUCCESS) {\n'
           '        footprintMiB = static_cast<uint64_t>(vmInfo.phys_footprint) / (1024ull * 1024ull);\n'
           '      }\n'
           '      const uint64_t availMiB = static_cast<uint64_t>(os_proc_available_memory()) / (1024ull * 1024ull);\n'
           '      Log.info("perf soak: {:.0f} fps  {:.1f} ms  thermal {}  footprint {} MiB  available {} MiB", fps,\n'
           '               frameMs, aurora_get_thermal_state(), footprintMiB, availMiB);\n'
           '    }\n'
           '  }\n'
           '#endif\n'
           '  return window::poll_events();\n'
           '}\n')
assert text.count(upd_old) == 1, f"update anchor: {text.count(upd_old)}"
text = text.replace(upd_old, upd_new)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(text); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1

out = ROOT / "overlay/patches/0027-aurora-visionos-perf-soak-log.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
