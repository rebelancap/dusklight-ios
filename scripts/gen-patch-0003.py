#!/usr/bin/env python3
"""Overlay patch 0003: route visionOS to the UIKit file-select, not the macOS one.

file_select.cpp picks its backend with TARGET_OS_IOS:

    #if defined(__APPLE__) && !TARGET_OS_IOS && !TARGET_OS_TV && !TARGET_OS_MACCATALYST
    #define USE_MACOS_FOLDER_DIALOG 1
    #if defined(__APPLE__) && TARGET_OS_IOS && !TARGET_OS_MACCATALYST
    #define USE_IOS_DIALOG 1

On visionOS TARGET_OS_IOS is 0 (only TARGET_OS_IPHONE and TARGET_OS_VISION are
1), so xrOS selects USE_MACOS_FOLDER_DIALOG and the link fails on
dusk::ShowMacOSFolderSelect -- whose implementation (file_select_macos.mm) is
correctly NOT compiled there, because it needs AppKit, which does not exist on
visionOS (patch 0001).

Worth noting the tree uses two different idioms for "is this iOS": aurora's
device.cpp keys off SDL_PLATFORM_IOS (TARGET_OS_IPHONE-based, so it IS set on
visionOS) while this file keys off TARGET_OS_IOS (not set on visionOS). They
disagree about what visionOS is, which is why the two link failures had
opposite causes.

visionOS is UIKit-family and UIDocumentPickerViewController works there, so
select the iOS dialog. Consistent with patch 0001 compiling
src/dusk/ios/FileSelectDialog.m under `if (IOS OR VISIONOS)`.

TARGET_OS_VISION needs no #ifndef guard: TargetConditionals.h is already
included above, and an undefined identifier evaluates to 0 inside #if, so older
SDKs keep their current behavior.
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
REL = "src/dusk/file_select.cpp"
SRC = ROOT / "vendor/dusklight" / REL

orig = SRC.read_text()
text = orig

EDITS = [
    (
        '#if defined(__APPLE__) && !TARGET_OS_IOS && !TARGET_OS_TV && !TARGET_OS_MACCATALYST\n'
        '#define USE_MACOS_FOLDER_DIALOG 1\n',

        '#if defined(__APPLE__) && !TARGET_OS_IOS && !TARGET_OS_TV && !TARGET_OS_MACCATALYST && \\\n'
        '    !TARGET_OS_VISION\n'
        '#define USE_MACOS_FOLDER_DIALOG 1\n',
        1,
    ),
    (
        '#if defined(__APPLE__) && TARGET_OS_IOS && !TARGET_OS_MACCATALYST\n'
        '#define USE_IOS_DIALOG 1\n',

        '#if defined(__APPLE__) && (TARGET_OS_IOS || TARGET_OS_VISION) && !TARGET_OS_MACCATALYST\n'
        '#define USE_IOS_DIALOG 1\n',
        1,
    ),
]

for old, new, want in EDITS:
    n = text.count(old)
    assert n == want, f"expected {want} match(es), got {n} for:\n{old[:120]!r}"
    text = text.replace(old, new)

with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
     tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
    fa.write(orig); fb.write(text); fa.flush(); fb.flush()
    r = subprocess.run(["diff", "-u", "--label", f"a/{REL}", "--label", f"b/{REL}",
                        fa.name, fb.name], capture_output=True)
assert r.returncode == 1

out = ROOT / "overlay/patches/0003-dusk-file-select-visionos-uikit.patch"
out.write_text(__doc__ + "\n" + r.stdout.decode())
print(f"wrote {out}")
