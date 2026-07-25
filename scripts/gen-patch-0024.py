#!/usr/bin/env python3
"""Overlay patch 0024: expose the OS thermal state (visionOS) for the live overlay.

A live perf/thermal readout on the headset to judge whether there's
optimization headroom. NSProcessInfo.thermalState is the OS signal (nominal ->
fair -> serious -> critical). device_ios.mm already imports Foundation and is the
aurora Obj-C TU compiled for visionOS (device + sim), so add a small C accessor
here; the overlay (patch 0025, dusk) declares and calls it.

Returns a static string; safe to call every frame.
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
REL = "extern/aurora/lib/device_ios.mm"
SRC = ROOT / "vendor/dusklight" / REL

orig = SRC.read_text()

old = ('#include <algorithm>\n'
       '#include <atomic>\n'
       '\n'
       '@interface AuroraDeviceHaptics : NSObject\n')
new = ('#include <algorithm>\n'
       '#include <atomic>\n'
       '\n'
       '// Live thermal readout for the perf overlay (patch 0024). NSProcessInfo is the\n'
       '// OS signal for throttling; the overlay shows it so you can see headroom.\n'
       'extern "C" const char* aurora_get_thermal_state() {\n'
       '  switch ([[NSProcessInfo processInfo] thermalState]) {\n'
       '  case NSProcessInfoThermalStateNominal:\n'
       '    return "nominal";\n'
       '  case NSProcessInfoThermalStateFair:\n'
       '    return "fair";\n'
       '  case NSProcessInfoThermalStateSerious:\n'
       '    return "serious";\n'
       '  case NSProcessInfoThermalStateCritical:\n'
       '    return "critical";\n'
       '  default:\n'
       '    return "unknown";\n'
       '  }\n'
       '}\n'
       '\n'
       '@interface AuroraDeviceHaptics : NSObject\n')

n = orig.count(old)
assert n == 1, f"expected 1 match, got {n}"
text = orig.replace(old, new)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(text); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1

out = ROOT / "overlay/patches/0024-aurora-visionos-thermal-state.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
