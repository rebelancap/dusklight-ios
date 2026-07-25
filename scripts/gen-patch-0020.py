#!/usr/bin/env python3
"""Overlay patch 0020: keep the prelaunch background scene visible once a disc is ready.

The prelaunch menu is an asymmetric layout by design: the menu (logo + buttons)
is left-aligned (`menu { left: 96dp; max-width: 50vw }`) and the decorative
`prelaunch-bg.png` scene fills the rest of the window behind it. That reads fine
while the scene is there.

But `prelaunch.rcss` has `body.disc-ready .background { opacity: 0 }` -- it fades
the scene OUT the moment a disc loads. On a wide Vision Pro window that
leaves the whole right ~60% as empty black (screenshot IMG_0158), which is the
"menu not fitted / doesn't fill the window" complaint in the disc-ready state. The
pre-disc state (IMG_0157) keeps the scene and already fills.

Fix: keep the background at full opacity in the disc-ready state too, so the scene
fills the window in BOTH states, matching the pre-disc look. One value change
(0 -> 1) in the res stylesheet; no layout math touched.
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
REL = "res/rml/prelaunch.rcss"
SRC = ROOT / "vendor/dusklight" / REL

orig = SRC.read_text()

old = ('body.disc-ready .background {\n'
       '    opacity: 0;\n'
       '}\n')
new = ('body.disc-ready .background {\n'
       '    /* Keep the scene visible when a disc is ready: on a wide visionOS window\n'
       '       fading it out leaves the right side empty black (menu is left-aligned). */\n'
       '    opacity: 1;\n'
       '}\n')

n = orig.count(old)
assert n == 1, f"expected 1 match, got {n}"
text = orig.replace(old, new)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(text); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1

out = ROOT / "overlay/patches/0020-dusk-prelaunch-keep-background.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
