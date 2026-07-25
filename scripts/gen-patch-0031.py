#!/usr/bin/env python3
"""Overlay patch 0031: keep C/ObjC-only compile flags off the Swift compiler.

Once the visionOS build gained a Swift target (the SwiftUI shell, patch 0001
edit 5b), two UNGUARDED `add_compile_options` calls started leaking C clang flags
into the swiftc invocation, and swiftc hard-errors on them:

    error: unknown argument: '-Wno-declaration-after-statement'

  * CMakeLists.txt:203 (the `elseif (APPLE)` arm): `-Wno-declaration-after-statement`
    and `-Wno-non-pod-varargs`.
  * CMakeLists.txt:311 (the ARM `-fsigned-char` arm).

Under Ninja these directory-level options apply to EVERY target in the directory,
including the Swift static lib — a separate target does not escape them (that only
sidesteps per-target genex flags). The file already uses the correct idiom right
next door (the MSVC and ASan blocks wrap their flags in
`$<$<COMPILE_LANGUAGE:C,CXX,...>:...>`); these two arms simply predate any Swift
target and never needed the guard. Add it.

Behavior-preserving for every existing platform: C/C++/ObjC/ObjC++ sources still
receive the exact same flags (those are all the languages non-visionOS builds
have); only Swift — which exists solely on visionOS — is excluded. Two isolated
one-line edits, clear of every other patch's hunks.
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
REL = "CMakeLists.txt"
SRC = ROOT / "vendor/dusklight" / REL

orig = SRC.read_text()
text = orig

edits = [
    (
        '    add_compile_options(-Wno-declaration-after-statement -Wno-non-pod-varargs)\n',
        '    add_compile_options(\n'
        '        $<$<COMPILE_LANGUAGE:C,CXX,OBJC,OBJCXX>:-Wno-declaration-after-statement>\n'
        '        $<$<COMPILE_LANGUAGE:C,CXX,OBJC,OBJCXX>:-Wno-non-pod-varargs>)\n',
    ),
    (
        '    add_compile_options(-fsigned-char)\n',
        '    add_compile_options($<$<COMPILE_LANGUAGE:C,CXX,OBJC,OBJCXX>:-fsigned-char>)\n',
    ),
]

for old, new in edits:
    assert text.count(old) == 1, f"anchor {old[:50]!r}: {text.count(old)}"
    text = text.replace(old, new)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(text); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1

out = ROOT / "overlay/patches/0031-dusk-cmake-swift-flag-guard.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
