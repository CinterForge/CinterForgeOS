#!/usr/bin/env bash
set -euo pipefail
platform="${1:?Usage: test.sh <mac|win|linux> [gui|headless]}"
mode="${2:-gui}"
case "$platform:$mode" in
  mac:gui|win:gui|linux:gui|mac:headless|win:headless|linux:headless)
    echo "ERROR: Installed CinterForge functional smoke test not implemented for platform=$platform mode=$mode." >&2
    exit 3 ;;
  *) echo "ERROR: Unsupported CinterForge smoke target: $platform:$mode" >&2; exit 2 ;;
esac
