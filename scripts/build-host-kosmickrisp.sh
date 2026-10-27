#!/usr/bin/env bash
# Xcode Build: compile and verify the pinned, patched Metal 4 Vulkan driver.
# The authoritative recipe and feature probes live in host/kosmickrisp/build.sh.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
export STEAMAC_ROOT="$root"
"$root/host/kosmickrisp/build.sh"
source "$root/scripts/paths.sh"
lib="$STEAMAC_WORK_DIR/out/host/lib/libvulkan_kosmickrisp.dylib"
[[ -s "$lib" ]] || { echo "error: KosmicKrisp build completed without required $lib" >&2; exit 1; }
echo "[Xcode] KosmicKrisp ready: $lib"
