#!/usr/bin/env bash
# Xcode Build: compile and verify the pinned, patched MoltenVK host driver.
# The authoritative recipe and feature probes live in host/moltenvk/build.sh.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
export STEAMAC_ROOT="$root"
/bin/sh "$root/host/moltenvk/build.sh"
source "$root/scripts/paths.sh"
lib="$STEAMAC_WORK_DIR/out/host/lib/libMoltenVK.dylib"
[[ -s "$lib" ]] || { echo "error: MoltenVK build completed without required $lib" >&2; exit 1; }
echo "[Xcode] MoltenVK ready: $lib"
