#!/usr/bin/env python3
"""Overlay patch 0040: build borealis for visionOS as the UIKit-family platform it is.

New with the 2026-10 upstream sync. Upstream moved the app-platform layer (data
folders, file IO, file-select dialogs, Discord, HTTP, logging, crash handler) out of
dusklight into the extern/borealis submodule, and borealis dispatches on iOS with
both idioms this port keeps tripping over (DECISIONS D-009):

  CMake  `IOS OR CMAKE_SYSTEM_NAME STREQUAL "iOS"` -- ios.toolchain.cmake sets IOS=OFF
         and CMAKE_SYSTEM_NAME=visionOS for PLATFORM=(SIMULATOR_)VISIONOS, so
         visionOS compiled NEITHER the Apple IO backend (io_apple.mm: security-scoped
         bookmarks + the Documents/imported store) NOR the UIKit document picker
         (file_select/ios.mm + ios_import.mm).
  C++    `TARGET_OS_IOS` -- 0 on visionOS, so io.cpp compiled the desktop stubs
         ("Bookmarks are not supported on this platform") and the no-op
         release_access().

Together that would leave visionOS with no file picker and no way to reopen a
picked disc: upstream's own fix for the Inbox-purge re-prompt (it now keeps a
security-scoped bookmark, or imports into Documents/imported) is exactly the
io_apple.mm path. It is what retired patch 0028.

Fix, visionOS-only (every other platform evaluates identically):
  1. CMakeLists.txt: compile io_apple.mm and the UIKit picker sources (and link
     Foundation / UIKit / UniformTypeIdentifiers) for CMAKE_SYSTEM_NAME visionOS too.
  2. CMakeLists.txt: Discord defaults OFF on visionOS and is rejected there, as on
     iOS/tvOS (the SDK ships no xros slice -- same reasoning as patch 0001 had for
     dusk's old DUSK_ENABLE_DISCORD).
  3. src/io.cpp: the seven `TARGET_OS_IOS` bookmark gates (and the inverse gate on
     the release_access stub) also accept TARGET_OS_VISION.

The picker's #define gate (file_select.cpp) is patch 0003 and the data-folder
gates (data.cpp) are patches 0004/0005 -- same bug class, kept in their original
slots. TARGET_OS_VISION needs no guard: TargetConditionals.h is included under
__APPLE__ in io.cpp and an undefined identifier is 0 inside #if.
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor/dusklight"

OUT = "0040-borealis-visionos-uikit-family.patch"

EDITS = [
    ("extern/borealis/CMakeLists.txt", [
        # 2. Discord: default OFF + rejected on visionOS, like iOS/tvOS (both gates).
        ('CMAKE_SYSTEM_NAME MATCHES "^(iOS|tvOS)$")\n',
         'CMAKE_SYSTEM_NAME MATCHES "^(iOS|tvOS|visionOS)$")\n',
         2),
        # 1a. Apple IO backend (bookmarks, Documents/imported).
        ('    if (IOS OR CMAKE_SYSTEM_NAME STREQUAL "iOS")\n'
         '        find_library(BOREALIS_FOUNDATION_FRAMEWORK Foundation REQUIRED)\n',
         '    if (IOS OR CMAKE_SYSTEM_NAME STREQUAL "iOS" OR CMAKE_SYSTEM_NAME STREQUAL "visionOS")\n'
         '        find_library(BOREALIS_FOUNDATION_FRAMEWORK Foundation REQUIRED)\n',
         1),
        # 1b. UIKit document picker.
        ('    elseif (IOS OR CMAKE_SYSTEM_NAME STREQUAL "iOS")\n'
         '        find_library(BOREALIS_UIKIT_FRAMEWORK UIKit REQUIRED)\n',
         '    elseif (IOS OR CMAKE_SYSTEM_NAME STREQUAL "iOS" OR CMAKE_SYSTEM_NAME STREQUAL "visionOS")\n'
         '        find_library(BOREALIS_UIKIT_FRAMEWORK UIKit REQUIRED)\n',
         1),
    ]),
    ("extern/borealis/src/io.cpp", [
        # 3a. the bookmark paths
        ('#if defined(__APPLE__) && TARGET_OS_IOS && !TARGET_OS_TV && !TARGET_OS_MACCATALYST\n',
         '#if defined(__APPLE__) && (TARGET_OS_IOS || TARGET_OS_VISION) && !TARGET_OS_TV && !TARGET_OS_MACCATALYST\n',
         7),
        # 3b. the no-op release_access stub is for platforms WITHOUT io_apple.mm
        ('#if !defined(__APPLE__) || !TARGET_OS_IOS || TARGET_OS_TV || TARGET_OS_MACCATALYST\n',
         '#if !defined(__APPLE__) || !(TARGET_OS_IOS || TARGET_OS_VISION) || TARGET_OS_TV || TARGET_OS_MACCATALYST\n',
         1),
    ]),
]

chunks = []
for rel, edits in EDITS:
    src = VENDOR / rel
    orig = src.read_text()
    text = orig
    for e in edits:
        old, new = e[0], e[1]
        want = e[2] if len(e) > 2 else 1
        n = text.count(old)
        assert n == want, f"{rel}: expected {want} match(es), got {n} for:\n{old[:160]!r}"
        text = text.replace(old, new)
    with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
         tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
        fa.write(orig); fb.write(text); fa.flush(); fb.flush()
        r = subprocess.run(["diff", "-u", "--label", f"a/{rel}", "--label", f"b/{rel}",
                            fa.name, fb.name], capture_output=True)
    assert r.returncode == 1, f"{rel}: diff produced no change"
    chunks.append(r.stdout.decode())

out = ROOT / "overlay/patches/" / OUT
out.write_text(__doc__ + "\n" + "".join(chunks))
print(f"wrote {out} ({len(chunks)} file(s))")
