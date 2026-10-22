#!/bin/sh
# Steamac build products are external to the source checkout.
# This file is sourced after the caller resolves its repository root.
if [ -z "${STEAMAC_WORK_DIR:-}" ]; then
    STEAMAC_WORK_DIR="$(cd "$STEAMAC_SOURCE_ROOT/.." && pwd)/Builds/CreativeSuite/Built/cinterforge-os/mac/Steamac/work"
fi
export STEAMAC_WORK_DIR
STEAMAC_SWIFT_BUILD_DIR="${STEAMAC_SWIFT_BUILD_DIR:-$(dirname "$STEAMAC_WORK_DIR")/SwiftPM}"
export STEAMAC_SWIFT_BUILD_DIR
# Move any old source-local caches into the external workspace exactly once.
# No symlink or generated directory is left in the checkout.
if [ -d "$STEAMAC_SOURCE_ROOT/work" ] && [ ! -L "$STEAMAC_SOURCE_ROOT/work" ]; then
    mkdir -p "$(dirname "$STEAMAC_WORK_DIR")"
    if [ ! -e "$STEAMAC_WORK_DIR" ]; then
        mv "$STEAMAC_SOURCE_ROOT/work" "$STEAMAC_WORK_DIR"
    else
        cp -R "$STEAMAC_SOURCE_ROOT/work/." "$STEAMAC_WORK_DIR/"
        rm -rf "$STEAMAC_SOURCE_ROOT/work"
    fi
fi
if [ -d "$STEAMAC_SOURCE_ROOT/host/launcher/.build" ] && [ ! -L "$STEAMAC_SOURCE_ROOT/host/launcher/.build" ]; then
    mkdir -p "$(dirname "$STEAMAC_SWIFT_BUILD_DIR")"
    if [ ! -e "$STEAMAC_SWIFT_BUILD_DIR" ]; then
        mv "$STEAMAC_SOURCE_ROOT/host/launcher/.build" "$STEAMAC_SWIFT_BUILD_DIR"
    else
        cp -R "$STEAMAC_SOURCE_ROOT/host/launcher/.build/." "$STEAMAC_SWIFT_BUILD_DIR/"
        rm -rf "$STEAMAC_SOURCE_ROOT/host/launcher/.build"
    fi
fi
mkdir -p "$STEAMAC_WORK_DIR" "$STEAMAC_SWIFT_BUILD_DIR"
