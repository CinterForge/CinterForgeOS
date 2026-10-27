#!/usr/bin/env bash
# Steamac source checkout contains source only. Generated data belongs in the
# external, unversioned CinterForgeOS Build workspace (never under the checkout).
[[ -n ${STEAMAC_ROOT:-} ]] || { echo 'STEAMAC_ROOT is required before sourcing scripts/paths.sh' >&2; return 1 2>/dev/null || exit 1; }
STEAMAC_BUILD_ROOT="${STEAMAC_BUILD_ROOT:-$(dirname "$STEAMAC_ROOT")/Builds/CreativeSuite/Built/cinterforge-os/mac/CFOS}"
STEAMAC_WORK_DIR="${STEAMAC_WORK_DIR:-$STEAMAC_BUILD_ROOT/work}"
STEAMAC_SWIFT_BUILD_DIR="${STEAMAC_SWIFT_BUILD_DIR:-$STEAMAC_BUILD_ROOT/SwiftPM}"
# Reject inherited configuration that would silently recreate gigabytes in Git.
case "$STEAMAC_WORK_DIR/" in
    "$STEAMAC_ROOT/"*) echo "ERROR: STEAMAC_WORK_DIR points inside source: $STEAMAC_WORK_DIR" >&2; return 1 2>/dev/null || exit 1 ;;
esac
case "$STEAMAC_SWIFT_BUILD_DIR/" in
    "$STEAMAC_ROOT/"*) echo "ERROR: STEAMAC_SWIFT_BUILD_DIR points inside source: $STEAMAC_SWIFT_BUILD_DIR" >&2; return 1 2>/dev/null || exit 1 ;;
esac
if [[ -e "$STEAMAC_ROOT/work" || -L "$STEAMAC_ROOT/work" ]]; then
    echo "ERROR: Generated work directory remains in Steamac source: $STEAMAC_ROOT/work" >&2
    echo "Move/remove it after confirming the external Build workspace: $STEAMAC_WORK_DIR" >&2
    return 1 2>/dev/null || exit 1
fi
export STEAMAC_BUILD_ROOT STEAMAC_WORK_DIR STEAMAC_SWIFT_BUILD_DIR
