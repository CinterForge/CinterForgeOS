#!/bin/sh
# Steamac build products are external to the source checkout.
# Xcode build phases do not inherit the interactive shell PATH.  Use the native
# Apple Silicon Homebrew prefix, never the Intel /usr/local brew selected by an
# x86_64/Rosetta shell. Setup installs packages; this only discovers tools.
if [ "$(uname -s)" = Darwin ] && [ "$(uname -m)" = arm64 ]; then
    PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
    export PATH
    if [ ! -x /opt/homebrew/bin/brew ]; then
        echo "CFOS: native arm64 Homebrew missing at /opt/homebrew/bin/brew; setup cannot use Intel /usr/local Homebrew." >&2
        return 1 2>/dev/null || exit 1
    fi
fi
# This file is sourced after the caller resolves its repository root.
if [ -z "${STEAMAC_WORK_DIR:-}" ]; then
    STEAMAC_WORK_DIR="$(cd "$STEAMAC_SOURCE_ROOT/.." && pwd)/Builds/CreativeSuite/Built/cinterforge-os/mac/CFOS/work"
fi
export STEAMAC_WORK_DIR
STEAMAC_SWIFT_BUILD_DIR="${STEAMAC_SWIFT_BUILD_DIR:-$(dirname "$STEAMAC_WORK_DIR")/SwiftPM}"
export STEAMAC_SWIFT_BUILD_DIR
# Never automatically move or copy source-local build data. The generated
# tree may exceed 80 GB; users must reconcile legacy folders explicitly.
if [ -d "$STEAMAC_SOURCE_ROOT/work" ] || [ -d "$STEAMAC_SOURCE_ROOT/host/launcher/.build" ]; then
    echo "ERROR: Legacy build outputs exist inside the checkout. Move them to $STEAMAC_WORK_DIR and $STEAMAC_SWIFT_BUILD_DIR before proceeding." >&2
    return 1 2>/dev/null || exit 1
fi
mkdir -p "$STEAMAC_WORK_DIR" "$STEAMAC_SWIFT_BUILD_DIR"
