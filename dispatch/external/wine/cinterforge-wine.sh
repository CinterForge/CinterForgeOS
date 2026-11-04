#!/bin/sh
# Portable x86-64 Wine via CinterForgeOS standalone FEX (not Steam's Proton).
set -eu
base=/usr/share/cinterforge/fex
[ -f "$base/wine-executable.txt" ] || { echo 'CinterForgeOS Wine payload missing' >&2; exit 1; }
relative=$(cat "$base/wine-executable.txt")
case "$relative" in /*|*..*) echo 'Invalid staged Wine path' >&2; exit 1 ;; esac
wine="$base/Wine/$relative"
[ -f "$wine" ] || { echo "Wine binary missing: $wine" >&2; exit 1; }
[ -x /usr/bin/FEX ] || { echo 'Standalone FEX missing' >&2; exit 1; }
FEX_ROOTFS="$base/RootFS"
[ -x "$FEX_ROOTFS/usr/bin/bash" ] || { echo "FEX RootFS missing: $FEX_ROOTFS" >&2; exit 1; }
export FEX_ROOTFS
exec /usr/bin/FEX "$wine" "$@"
