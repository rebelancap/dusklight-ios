#!/bin/bash
# Reconstruct the vendored engine tree, then apply the overlay. Run this once
# after cloning the repo — vendor/ is deliberately NOT committed (it is upstream's
# code; we keep only the pin + our overlay patches).
#
#   scripts/bootstrap.sh
#
# Clones upstream Dusklight at the pinned commit and its `aurora` submodule at the
# recorded pin, then applies overlay/patches/ via scripts/apply-overlay.sh. Dawn is
# fetched by CMake at build time (AURORA_DAWN_PROVIDER=vendor), not here.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENDOR="$ROOT/vendor/dusklight"

DUSK_REPO="https://github.com/TwilitRealm/dusklight.git"
DUSK_PIN="0f2a00cd1f559e7d2c719091e8b1ece372e0f918"      # v1.4.1-82-g0f2a00cd1f
AURORA_PIN="1dde08fa0d0030133788a6250a81c8b9c44f246f"

if [[ -d "$VENDOR/.git" ]]; then
    echo "vendor already present ($VENDOR) — skipping clone"
else
    echo "cloning upstream Dusklight @ ${DUSK_PIN:0:12} ..."
    git clone "$DUSK_REPO" "$VENDOR"
    git -C "$VENDOR" checkout --detach "$DUSK_PIN"
    git -C "$VENDOR" submodule update --init --recursive
    # Belt-and-suspenders: pin aurora explicitly to the recorded commit.
    git -C "$VENDOR/extern/aurora" checkout --detach "$AURORA_PIN"
fi

"$ROOT/scripts/apply-overlay.sh"
echo "bootstrap complete — vendor ready, overlay applied. Build with scripts/build-visionos.sh"
