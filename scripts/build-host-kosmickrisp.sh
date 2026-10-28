#!/usr/bin/env bash
# Xcode Build: compile and verify the pinned, patched Metal 4 Vulkan driver.
# The authoritative recipe and feature probes live in host/kosmickrisp/build.sh.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
export STEAMAC_ROOT="$root"
# Xcode does not inherit Setup's PATH. Resolve the same external workspace
# first, then force all Mesa/Python probes to use Setup's managed Python.
source "$root/scripts/paths.sh"
cfos_python="$STEAMAC_WORK_DIR/tools/python/bin/python3"
if [[ ! -x "$cfos_python" ]] || ! "$cfos_python" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 11))' 2>/dev/null; then
    echo "CFOS: Setup-managed Python 3.11 unavailable: $cfos_python" >&2
    echo "Run ./CFOS setup cinterforge-os mac Release; do not install global Python." >&2
    exit 1
fi
export CFOS_PYTHON="$cfos_python"
export PATH="$(dirname "$cfos_python"):$PATH"
echo "[Xcode] Mesa Python: $cfos_python ($("$cfos_python" --version 2>&1))"
/bin/sh "$root/host/kosmickrisp/build.sh"
lib="$STEAMAC_WORK_DIR/out/host/lib/libvulkan_kosmickrisp.dylib"
[[ -s "$lib" ]] || { echo "error: KosmicKrisp build completed without required $lib" >&2; exit 1; }
echo "[Xcode] KosmicKrisp ready: $lib"
