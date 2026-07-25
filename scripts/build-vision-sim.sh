#!/bin/bash
# Build Dusklight for the visionOS SIMULATOR (arm64). Phase 1 of the Vision Pro
# port (VISION-PRO-GUIDE.md). Mirrors the upstream `ios-default` preset with the
# visionOS deltas:
#   PLATFORM        OS64 -> SIMULATOR_VISIONOS   (leetal toolchain: xrsimulator)
#   DEPLOYMENT      14.0 -> 2.0                  (xrOS 2.0; Phase 2 needs it)
#   Rust_CARGO_TARGET  aarch64-apple-ios -> aarch64-apple-visionos-sim
#   Rust toolchain  stable -> nightly            (visionOS is a Tier-3 Rust target;
#                                                 same reason upstream CI uses
#                                                 nightly for tvOS)
#   AURORA_DAWN_PROVIDER -> vendor               (no prebuilt Dawn package for
#                                                 xros; encounter/dawn ships
#                                                 ios-arm64 but not visionos)
# The iOS/tvOS/macOS targets are untouched.
#
# CMAKE_IGNORE_PREFIX_PATH also lists Miniforge3: aurora does a bare
# find_package(fmt)/find_package(zstd) and will happily resolve them to host
# *macOS dylibs* from any conda-ish prefix on PATH, which then fail the link
# with "building for visionOS-simulator, but linking in dylib built for macOS".
# Upstream's ios preset only ignores /opt/homebrew because CI has no conda.
# Ignoring the prefix forces aurora down its FetchContent path, which builds
# fmt/zstd for xros correctly.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENDOR="$ROOT/vendor/dusklight"
BUILD="$ROOT/spikes/vision-sim-build"

[[ -d "$VENDOR/.git" ]] || { echo "FATAL: vendor missing" >&2; exit 1; }
[[ -x "$ROOT/scripts/apply-overlay.sh" ]] && "$ROOT/scripts/apply-overlay.sh"

# Corrosion drives cargo for nod (Rust GameCube disc reader, AURORA_ENABLE_DVD=ON).
# visionOS std ships prebuilt on nightly, so no -Z build-std is required.
rustup target list --toolchain nightly --installed | grep -q aarch64-apple-visionos-sim || {
    rustup target add --toolchain nightly aarch64-apple-visionos-sim
}

cmake --no-warn-unused-cli -S "$VENDOR" -B "$BUILD" -GNinja \
    -DCMAKE_TOOLCHAIN_FILE="$VENDOR/ios.toolchain.cmake" \
    -DPLATFORM=SIMULATOR_VISIONOS \
    -DDEPLOYMENT_TARGET=2.0 \
    -DENABLE_BITCODE=NO \
    -DENABLE_ARC=NO \
    -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    -DBUILD_SHARED_LIBS=NO \
    -DCMAKE_INSTALL_PREFIX="$BUILD/install" \
    -DCMAKE_DISABLE_FIND_PACKAGE_PkgConfig=ON \
    -DCMAKE_IGNORE_PREFIX_PATH="/opt/homebrew;$HOME/Miniforge3" \
    -DRust_CARGO_TARGET=aarch64-apple-visionos-sim \
    -DRust_TOOLCHAIN=nightly \
    -DRust_RUSTUP_INSTALL_MISSING_TARGET=ON \
    -DAURORA_DAWN_PROVIDER=vendor \
    -DAURORA_DAWN_LINKAGE=static \
    -DDAWN_BUILD_PROTOBUF=OFF \
    -DTINT_BUILD_IR_BINARY=OFF \
    -DDUSK_VISIONOS_RESOURCE_DIR="$ROOT/app/visionos" \
    "$@"

cmake --build "$BUILD" --parallel "$(sysctl -n hw.ncpu)"
echo "configure+build finished"
