#!/usr/bin/env bash
# Run Blender installed smoke tests, never source/build-tree binaries.
set -euo pipefail
platform="${1:?Usage: test.sh <mac|linux|win> <gui|headless>}"
mode="${2:-gui}"
: "${CINTER_SCRIPTS_ROOT:?CINTER_SCRIPTS_ROOT required}"
case "$platform:$mode" in
  mac:gui|mac:headless)
    exec python3 -u "$CINTER_SCRIPTS_ROOT/apps/cf3d/test/mac_smoke.py" "$mode"
    ;;
  linux:gui)
    exec python3 -u "$CINTER_SCRIPTS_ROOT/shared/installed_guest_smoke.py" linux 'CinterForge 3D' cf3d --exe cf3d
    ;;
  win:gui)
    exec python3 -u "$CINTER_SCRIPTS_ROOT/shared/installed_guest_smoke.py" win 'CinterForge 3D' cf3d --exe-glob '**/cf3d.exe'
    ;;
  linux:headless)
    echo 'Linux Debug headless is dispatched through the installed CFOS Ubuntu smoke runner.' >&2
    exit 2
    ;;
  *) echo "Unsupported Blender smoke target: $platform/$mode" >&2; exit 2 ;;
esac
