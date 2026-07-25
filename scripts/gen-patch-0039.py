#!/usr/bin/env python3
"""Overlay patch 0039 (visionOS): open the 2D window 16:9, not the 4:3 GameCube ratio.

The floating window "launched square then snapped wide after 3-4s": aurora creates a
RESIZABLE SDL window (visionOS is NOT the TARGET_OS_IOS fullscreen branch) at
config.windowWidth/Height = defaultWindow{Width,Height}*2 = 1216x896 (~4:3), which the
shell's scene-geometry request only widened seconds later -> a visible square flash.

Fix at the source: on visionOS seed the DEFAULT window (the else branch, used on fresh
installs and after every OTA reinstall) at 1280x720 (16:9), so the very first frame is
widescreen and there is nothing to snap. TARGET_OS_VISION is already used in this file
(m_Do_main.cpp:571). The remember-window-size path is untouched, so a user's dragged
size still persists within an install. The shell's launch-widen stays as a belt-and-
suspenders fallback but no longer fires (its guard only triggers on a square-ish window).
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor/dusklight"

EDITS = {
    "src/m_Do/m_Do_main.cpp": [
        ('        } else {\n'
         '            config.windowWidth = defaultWindowWidth * 2;\n'
         '            config.windowHeight = defaultWindowHeight * 2;\n'
         '        }\n',
         '        } else {\n'
         '#if defined(__APPLE__) && TARGET_OS_VISION\n'
         '            // visionOS: open the floating window 16:9 widescreen, not the 4:3 GameCube\n'
         '            // ratio (reads as "too square" and flashed before the shell resized it).\n'
         '            config.windowWidth = 1280;\n'
         '            config.windowHeight = 720;\n'
         '#else\n'
         '            config.windowWidth = defaultWindowWidth * 2;\n'
         '            config.windowHeight = defaultWindowHeight * 2;\n'
         '#endif\n'
         '        }\n'),
    ],
}

chunks = []
for rel, edits in EDITS.items():
    orig = (VENDOR / rel).read_text()
    text = orig
    for old, new in edits:
        assert text.count(old) == 1, f"{rel}: anchor {old[:56]!r} count {text.count(old)}"
        text = text.replace(old, new)
    with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
         tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
        fa.write(orig); fb.write(text); fa.flush(); fb.flush()
        r = subprocess.run(["diff", "-u", "--label", f"a/{rel}",
                            "--label", f"b/{rel}", fa.name, fb.name], capture_output=True)
    assert r.returncode == 1, f"{rel}: no diff"
    chunks.append(r.stdout.decode())

out = ROOT / "overlay/patches/0039-dusk-visionos-window-16x9.patch"
out.write_text(__doc__ + "\n" + "".join(chunks))
print(f"wrote {out}")
