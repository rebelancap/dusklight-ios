#!/bin/bash
# Reconstruct the vendored engine tree, then apply the overlay. Run this once
# after cloning the repo — vendor/ is deliberately NOT committed (it is upstream's
# code; we keep only the pin + our overlay patches).
#
#   scripts/bootstrap.sh
#
# Clones upstream Dusklight at the pinned commit and its `aurora` + `borealis`
# submodules at the recorded pins, then applies overlay/patches/ via scripts/apply-overlay.sh. Dawn is
# fetched by CMake at build time (AURORA_DAWN_PROVIDER=vendor), not here.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENDOR="$ROOT/vendor/dusklight"

DUSK_REPO="https://github.com/TwilitRealm/dusklight.git"
DUSK_PIN="34a144a493b7f57adeb4034d382317c826eb5c8d"      # v2.0.3-2-g34a144a493 (upstream main, CI green, 2026-10-10)
AURORA_PIN="3227d76c60e1e782ca576610bce61c9e7744d8be"    # = the submodule commit DUSK_PIN records
BOREALIS_PIN="4ac5e7052a8c49a122f8d57f626b5c75c5ca6968"  # = the submodule commit DUSK_PIN records

if [[ -d "$VENDOR/.git" ]]; then
    echo "vendor already present ($VENDOR) — skipping clone"
else
    echo "cloning upstream Dusklight @ ${DUSK_PIN:0:12} ..."
    git clone "$DUSK_REPO" "$VENDOR"
    git -C "$VENDOR" checkout --detach "$DUSK_PIN"
    # Only the submodules the visionOS build compiles. mods/cosmetics and
    # mods/randomizer are add_subdirectory()d only with DUSK_ENABLE_CODE_MODS=ON,
    # which visionOS forces OFF (D-005), so they are not fetched.
    git -C "$VENDOR" submodule update --init --recursive extern/aurora extern/borealis
    # Belt-and-suspenders: pin them explicitly to the recorded commits.
    git -C "$VENDOR/extern/aurora" checkout --detach "$AURORA_PIN"
    git -C "$VENDOR/extern/borealis" checkout --detach "$BOREALIS_PIN"
fi

"$ROOT/scripts/apply-overlay.sh"
echo "bootstrap complete — vendor ready, overlay applied. Build with scripts/build-visionos.sh"
