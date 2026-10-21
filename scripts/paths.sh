#!/usr/bin/env bash
# Shared host-side output paths. Keep the steamac checkout source-only.
# Override STEAMAC_BUILD_ROOT for an alternate external build volume.
STEAMAC_BUILD_ROOT="${STEAMAC_BUILD_ROOT:-$(dirname "$STEAMAC_ROOT")/Builds/CreativeSuite/Built/cinterforge-os/mac/Steamac}"
export STEAMAC_BUILD_ROOT
STEAMAC_WORK_DIR="${STEAMAC_WORK_DIR:-$STEAMAC_BUILD_ROOT/work}"
STEAMAC_SWIFT_BUILD_DIR="${STEAMAC_SWIFT_BUILD_DIR:-$STEAMAC_BUILD_ROOT/SwiftPM}"
export STEAMAC_WORK_DIR STEAMAC_SWIFT_BUILD_DIR
