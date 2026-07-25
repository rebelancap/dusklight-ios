#!/usr/bin/env python3
"""Overlay patch 0001: teach dusklight's CMake about visionOS (xrOS).

ios.toolchain.cmake already supports PLATFORM=VISIONOS/SIMULATOR_VISIONOS and
sets VISIONOS=ON, IOS=OFF, CMAKE_SYSTEM_NAME=visionOS. But the tree dispatches
on IOS/TVOS only, so visionOS falls into the *desktop macOS* branches: it looks
for AppKit (macOS-only -> configure failure), builds the macOS file-select, and
skips the UIKit paths it should take. visionOS is a UIKit-family platform, so
it wants the iOS-side code, not the macOS side.

Mirrors the existing tvOS normalization hack at the top of CMakeLists.txt and
follows the tree's own convention of a distinct platform variable per platform
(rather than forcing IOS=ON, which would leak into SDL3/Dawn subprojects that do
their own platform detection and have real visionOS support of their own).

The visionOS resource dir is indirected through DUSK_VISIONOS_RESOURCE_DIR so
the port's Info.plist/icons can live outside the vendor tree (upstream stays
pristine); it defaults to platforms/visionos.

Discord is gated OUT on visionOS: the SDK ships no xros slice.

The Apple exports/stub step is skipped on visionOS entirely. symgen (a pinned
prebuilt third-party binary, encounter/symgen v1.2.3) cannot read xrOS Mach-O
objects -- it scans them, reports "0 exports from 2053 objects", and then hard
-fails with "No symbols in dusklight_exports.exp". It also rejects
`--platform visionos` outright ("expected macos, ios, or tvos"), so there is no
flag that makes it work. That step exists only to let code mods link against the
game (DUSK_ENABLE_CODE_MODS, default OFF); the game links fine without it. The
patch fails loudly if code mods are requested on visionOS rather than silently
producing an empty exports list. Revisit via SYMGEN_PATH.
"""
import subprocess, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor/dusklight"

EDITS = [
    # ---- CMakeLists.txt -----------------------------------------------------
    ("CMakeLists.txt", [
        # 1. Normalize VISIONOS the same way tvOS is normalized. CMAKE_SYSTEM_NAME
        #    is re-derived at every sub-project() call, so this must be re-flippable.
        (
            'if (APPLE AND NOT TVOS AND CMAKE_SYSTEM_NAME STREQUAL tvOS)\n'
            '    # ios.toolchain.cmake hack for SDL\n'
            '    set(TVOS ON)\n'
            '    set(IOS OFF)\n'
            'endif ()\n',

            'if (APPLE AND NOT TVOS AND CMAKE_SYSTEM_NAME STREQUAL tvOS)\n'
            '    # ios.toolchain.cmake hack for SDL\n'
            '    set(TVOS ON)\n'
            '    set(IOS OFF)\n'
            'endif ()\n'
            'if (APPLE AND NOT VISIONOS AND CMAKE_SYSTEM_NAME STREQUAL visionOS)\n'
            '    # Same hack for visionOS: the toolchain sets VISIONOS, but\n'
            '    # CMAKE_SYSTEM_NAME is re-derived per sub-project().\n'
            '    set(VISIONOS ON)\n'
            '    set(IOS OFF)\n'
            'endif ()\n',
            1,
        ),
        # 2. Discord has no xros slice.
        (
            'if (DUSK_ENABLE_DISCORD AND NOT ANDROID AND NOT IOS AND NOT TVOS)\n',
            'if (DUSK_ENABLE_DISCORD AND NOT ANDROID AND NOT IOS AND NOT TVOS AND NOT VISIONOS)\n',
            1,
        ),
        # 3. Resource dir: visionOS gets its own, indirectable out of vendor.
        (
            '    if (IOS)\n'
            '        set(DUSK_RESOURCE_DIR ${CMAKE_CURRENT_SOURCE_DIR}/platforms/ios)\n'
            '    elseif (TVOS)\n'
            '        set(DUSK_RESOURCE_DIR ${CMAKE_CURRENT_SOURCE_DIR}/platforms/tvos)\n',

            '    if (IOS)\n'
            '        set(DUSK_RESOURCE_DIR ${CMAKE_CURRENT_SOURCE_DIR}/platforms/ios)\n'
            '    elseif (VISIONOS)\n'
            '        if (NOT DUSK_VISIONOS_RESOURCE_DIR)\n'
            '            set(DUSK_VISIONOS_RESOURCE_DIR ${CMAKE_CURRENT_SOURCE_DIR}/platforms/visionos)\n'
            '        endif ()\n'
            '        set(DUSK_RESOURCE_DIR ${DUSK_VISIONOS_RESOURCE_DIR})\n'
            '    elseif (TVOS)\n'
            '        set(DUSK_RESOURCE_DIR ${CMAKE_CURRENT_SOURCE_DIR}/platforms/tvos)\n',
            1,
        ),
        # 4. Hardened runtime is a macOS-only Xcode attribute.
        (
            '    if (NOT IOS AND NOT TVOS)\n'
            '        list(APPEND _apple_bundle_properties\n'
            '            XCODE_ATTRIBUTE_ENABLE_HARDENED_RUNTIME "YES")\n',

            '    if (NOT IOS AND NOT TVOS AND NOT VISIONOS)\n'
            '        list(APPEND _apple_bundle_properties\n'
            '            XCODE_ATTRIBUTE_ENABLE_HARDENED_RUNTIME "YES")\n',
            1,
        ),
        # 5. The ad-hoc codesign-with-entitlements step is macOS-only.
        (
            '    if (NOT IOS AND NOT TVOS AND NOT "${CMAKE_GENERATOR}" STREQUAL "Xcode")\n',
            '    if (NOT IOS AND NOT TVOS AND NOT VISIONOS AND NOT "${CMAKE_GENERATOR}" STREQUAL "Xcode")\n',
            1,
        ),
        # 5b. Phase 2 (M1): compile the SwiftUI @main shell into the app under the
        #     Ninja generator. An ImmersiveSpace can only be declared by a SwiftUI
        #     App, so SwiftUI owns main() on visionOS (patch 0030 drops SDL_main.h
        #     and exposes dusk_engine_main; app/visionos/ calls it once the scene is
        #     live). No sibling compiles Swift under Ninja (they use -GXcode /
        #     XcodeGen, both of which break our Dawn-static OTA archive); this is the
        #     recipe proven in spikes/swift-ninja-probe -- the ONE missing ingredient
        #     is Swift's target triple (the C/C++ toolchain supplies none, so swiftc
        #     targets macOS and cannot load the stdlib). It belongs here because it
        #     is intrinsically part of teaching CMake to build for visionOS (it uses
        #     DUSK_VISIONOS_RESOURCE_DIR from edit #3), and folding it in keeps it
        #     from editing inside this patch's hunks as a separate overlay.
        (
            '    set_target_properties(dusklight PROPERTIES ${_apple_bundle_properties})\n',

            '    set_target_properties(dusklight PROPERTIES ${_apple_bundle_properties})\n'
            '\n'
            '    if (VISIONOS)\n'
            '        if (PLATFORM STREQUAL "SIMULATOR_VISIONOS")\n'
            '            set(CMAKE_Swift_COMPILER_TARGET "arm64-apple-xros${CMAKE_OSX_DEPLOYMENT_TARGET}-simulator")\n'
            '        else ()\n'
            '            set(CMAKE_Swift_COMPILER_TARGET "arm64-apple-xros${CMAKE_OSX_DEPLOYMENT_TARGET}")\n'
            '        endif ()\n'
            '        enable_language(Swift)\n'
            '        # Swift in its OWN static lib: a .swift source on the dusklight target\n'
            '        # inherits the game C warning flags (COMPILE_LANGUAGE genexes are ignored\n'
            '        # for Swift) and swiftc hard-errors. The flag-clean target sidesteps it.\n'
            '        add_library(dusk_visionswift STATIC "${DUSK_VISIONOS_RESOURCE_DIR}/DuskVisionApp.swift")\n'
            '        set_target_properties(dusk_visionswift PROPERTIES Swift_LANGUAGE_VERSION 5)\n'
            '        target_compile_options(dusk_visionswift PRIVATE\n'
            '            "$<$<COMPILE_LANGUAGE:Swift>:-import-objc-header;${DUSK_VISIONOS_RESOURCE_DIR}/Dusk-Bridging-Header.h>")\n'
            '        # ObjC shell (host VC boot, immersive-loop stubs, pre-main beacon).\n'
            '        target_sources(dusklight PRIVATE\n'
            '            "${DUSK_VISIONOS_RESOURCE_DIR}/DuskHostViewController.m"\n'
            '            "${DUSK_VISIONOS_RESOURCE_DIR}/DuskImmersive.m"\n'
            '            "${DUSK_VISIONOS_RESOURCE_DIR}/DuskLaunchBeacon.m")\n'
            '        target_include_directories(dusklight PRIVATE "${DUSK_VISIONOS_RESOURCE_DIR}")\n'
            '        target_compile_definitions(dusklight PRIVATE DUSK_SWIFT_MAIN=1)\n'
            '        # Swift @main supplies main(); force_load pulls it from the archive (a\n'
            '        # raw -force_load flag carries no build-order dep). The dusklight target\n'
            '        # has no Swift sources, so give it the Swift runtime path + rpath itself.\n'
            '        add_dependencies(dusklight dusk_visionswift)\n'
            '        target_link_libraries(dusklight PRIVATE\n'
            '            "-Wl,-force_load,$<TARGET_FILE:dusk_visionswift>"\n'
            '            "-framework CompositorServices"\n'
            '            "-framework ARKit"\n'
            '            "-framework AVFAudio")\n'
            '        target_link_options(dusklight PRIVATE\n'
            '            "SHELL:-L ${CMAKE_OSX_SYSROOT}/usr/lib/swift"\n'
            '            "SHELL:-Wl,-rpath,/usr/lib/swift")\n'
            '    endif ()\n',
            1,
        ),
        # 6. AppKit does not exist on visionOS -> this was the configure failure.
        (
            'if (APPLE AND NOT IOS AND NOT TVOS)\n'
            '    find_library(APPKIT_FRAMEWORK AppKit REQUIRED)\n',

            'if (APPLE AND NOT IOS AND NOT TVOS AND NOT VISIONOS)\n'
            '    find_library(APPKIT_FRAMEWORK AppKit REQUIRED)\n',
            1,
        ),
        # 7. visionOS is UIKit-family: take the iOS file-select dialog.
        (
            'if (IOS)\n'
            '    find_library(UIKIT_FRAMEWORK UIKit REQUIRED)\n',

            'if (IOS OR VISIONOS)\n'
            '    find_library(UIKIT_FRAMEWORK UIKit REQUIRED)\n',
            1,
        ),
        # 8. Skip the symgen exports/stub step on visionOS. See module docstring.
        (
            'if (APPLE)\n'
            '    include(cmake/AppleExports.cmake)\n'
            '    setup_apple_exports(dusklight)\n'
            'elseif (ANDROID)\n',

            'if (APPLE AND VISIONOS)\n'
            '    # symgen (pinned prebuilt, encounter/symgen v1.2.3) cannot read xrOS\n'
            '    # Mach-O objects: it reports "0 exports from 2053 objects" and then\n'
            '    # fails with "No symbols in dusklight_exports.exp". The exports list\n'
            '    # and the -bundle_loader stub exist only so *code mods* can link\n'
            '    # against the game; the game itself links fine without\n'
            '    # -exported_symbols_list. Skip the step rather than ship a silently\n'
            '    # empty exports list. Revisit by pointing SYMGEN_PATH at an\n'
            '    # xrOS-aware symgen build.\n'
            '    if (DUSK_ENABLE_CODE_MODS)\n'
            '        message(FATAL_ERROR\n'
            '            "dusklight: DUSK_ENABLE_CODE_MODS is not supported on visionOS yet "\n'
            '            "(symgen ${_SYMGEN_VERSION} emits no exports for xrOS objects). "\n'
            '            "Configure with -DDUSK_ENABLE_CODE_MODS=OFF.")\n'
            '    endif ()\n'
            '    message(STATUS "dusklight: skipping Apple exports/stub on visionOS (no code mods)")\n'
            'elseif (APPLE)\n'
            '    include(cmake/AppleExports.cmake)\n'
            '    setup_apple_exports(dusklight)\n'
            'elseif (ANDROID)\n',
            1,
        ),
    ]),
    # ---- cmake/DetectVersion.cmake ------------------------------------------
    ("cmake/DetectVersion.cmake", [
        # The Apple branch is guarded by `CMAKE_SYSTEM_NAME STREQUAL Darwin`, but
        # ios.toolchain.cmake sets CMAKE_SYSTEM_NAME to visionOS (and iOS/tvOS for
        # those), so that branch never runs for any embedded Apple platform and we
        # fall into the else, whose
        #     string(TOLOWER CMAKE_SYSTEM_NAME PLATFORM_NAME)
        # is missing its ${} and lowercases the *literal name*. The running app
        # logged the proof: "Platform: cmake_system_name".
        #
        # That is a pre-existing upstream bug which equally affects iOS and tvOS.
        # It is NOT fixed here: correcting the guard to `elseif (APPLE)` would
        # change PLATFORM_NAME for iOS/tvOS too, and the charter requires leaving
        # the other targets byte-for-byte alone. Test visionOS ahead of the Darwin
        # check instead, so only visionOS changes.
        (
            '    if (CMAKE_SYSTEM_NAME STREQUAL Windows)\n'
            '        set(PLATFORM_NAME win32)\n'
            '    elseif (CMAKE_SYSTEM_NAME STREQUAL Darwin)\n',

            '    if (CMAKE_SYSTEM_NAME STREQUAL Windows)\n'
            '        set(PLATFORM_NAME win32)\n'
            '    elseif (VISIONOS)\n'
            '        set(PLATFORM_NAME visionos)\n'
            '    elseif (CMAKE_SYSTEM_NAME STREQUAL Darwin)\n',
            1,
        ),
    ]),
]

chunks = []
for rel, edits in EDITS:
    src = VENDOR / rel
    orig = src.read_text()
    text = orig
    for old, new, want in edits:
        n = text.count(old)
        assert n == want, f"{rel}: expected {want} match(es), got {n} for:\n{old[:120]!r}"
        text = text.replace(old, new)
    if text == orig:
        continue
    with tempfile.NamedTemporaryFile("w", suffix=".a", delete=False) as fa, \
         tempfile.NamedTemporaryFile("w", suffix=".b", delete=False) as fb:
        fa.write(orig); fb.write(text); fa.flush(); fb.flush()
        r = subprocess.run(["diff", "-u", "--label", f"a/{rel}", "--label", f"b/{rel}",
                            fa.name, fb.name], capture_output=True)
    assert r.returncode == 1, f"{rel}: diff produced no change"
    chunks.append(r.stdout.decode())

out = ROOT / "overlay/patches/0001-dusk-cmake-visionos-platform.patch"
out.write_text(__doc__ + "\n" + "".join(chunks))
print(f"wrote {out} ({len(chunks)} file(s))")
