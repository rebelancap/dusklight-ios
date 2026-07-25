#!/bin/bash
# Build Dusklight for visionOS DEVICE (arm64, xrOS). Sibling of
# build-vision-sim.sh; the deltas from the simulator build are:
#   PLATFORM           SIMULATOR_VISIONOS -> VISIONOS      (sysroot xrsimulator -> xros)
#   Rust_CARGO_TARGET  aarch64-apple-visionos-sim -> aarch64-apple-visionos
# Everything else (nightly Rust for the Tier-3 target, vendored Dawn, the
# Miniforge prefix exclusion, the out-of-tree resource dir) is identical and
# documented in build-vision-sim.sh / DECISIONS.md.
#
# This is the build OTA needs; the simulator build cannot be installed on a
# headset. Codesigning is deliberately NOT done here -- the charter says publish
# to READY and let the user decide install order, so signing/packaging belongs in a
# publish script, not the compile step.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENDOR="$ROOT/vendor/dusklight"
BUILD="$ROOT/spikes/vision-device-build"

[[ -d "$VENDOR/.git" ]] || { echo "FATAL: vendor missing" >&2; exit 1; }
"$ROOT/scripts/apply-overlay.sh"

rustup target list --toolchain nightly --installed | grep -q '^aarch64-apple-visionos$' || {
    rustup target add --toolchain nightly aarch64-apple-visionos
}

cmake --no-warn-unused-cli -S "$VENDOR" -B "$BUILD" -GNinja \
    -DCMAKE_TOOLCHAIN_FILE="$VENDOR/ios.toolchain.cmake" \
    -DPLATFORM=VISIONOS \
    -DDEPLOYMENT_TARGET=2.0 \
    -DENABLE_BITCODE=NO \
    -DENABLE_ARC=NO \
    -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    -DBUILD_SHARED_LIBS=NO \
    -DCMAKE_INSTALL_PREFIX="$BUILD/install" \
    -DCMAKE_DISABLE_FIND_PACKAGE_PkgConfig=ON \
    -DCMAKE_IGNORE_PREFIX_PATH="/opt/homebrew;$HOME/Miniforge3" \
    -DRust_CARGO_TARGET=aarch64-apple-visionos \
    -DRust_TOOLCHAIN=nightly \
    -DRust_RUSTUP_INSTALL_MISSING_TARGET=ON \
    -DAURORA_DAWN_PROVIDER=vendor \
    -DAURORA_DAWN_LINKAGE=static \
    -DDAWN_BUILD_PROTOBUF=OFF \
    -DTINT_BUILD_IR_BINARY=OFF \
    -DDUSK_VISIONOS_RESOURCE_DIR="$ROOT/app/visionos" \
    "$@"

cmake --build "$BUILD" --parallel "$(sysctl -n hw.ncpu)"

APP="$BUILD/Dusklight.app"
[[ -d "$APP" ]] || { echo "FATAL: expected app at $APP" >&2; exit 1; }

# Stamp the marketing version from ROOT/VERSION onto the built bundle. The CMake
# build derives its version from upstream git tags (DetectVersion.cmake -> ~1.4.x);
# SideStore matches updates on CFBundleShortVersionString, so the public IPA must
# report OUR marketing version, not upstream's. VERSION is the single source of
# truth -- dev OTA and the public release share it (dev version numbers are
# throwaway per the release plan; bump VERSION for a real release).
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION" 2>/dev/null || true)"
if [[ -n "${VERSION:-}" ]]; then
    /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$APP/Info.plist"
    /usr/libexec/PlistBuddy -c "Set :CFBundleVersion $VERSION" "$APP/Info.plist"
    echo "stamped marketing version: $VERSION"
fi
echo
vtool -show-build-version "$APP/Dusklight" | grep -E "platform|minos|sdk"
lipo -info "$APP/Dusklight"
echo "built (visionOS device): $APP"
