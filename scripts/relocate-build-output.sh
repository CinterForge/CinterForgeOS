#!/usr/bin/env bash
set -euo pipefail
STEAMAC_ROOT=$(cd "$(dirname "$0")/.." && pwd)
export STEAMAC_ROOT
. "$STEAMAC_ROOT/scripts/paths.sh"
mkdir -p "$STEAMAC_WORK_DIR" "$(dirname "$STEAMAC_SWIFT_BUILD_DIR")"
move_dir() {
  local from="$1" to="$2"
  if [[ ! -e "$from" ]]; then return; fi
  if [[ -e "$to" ]] && [[ -n "$(ls -A "$to" 2>/dev/null)" ]]; then
    echo "Refusing to merge $from into nonempty $to; reconcile existing outputs first" >&2; exit 1
  fi
  if [[ -d "$to" ]]; then rmdir "$to"; fi
  mv "$from" "$to"
  echo "Moved $from -> $to"
}
# Preserve old work tree (especially persistent SteamOS disk) verbatim.
move_dir "$STEAMAC_ROOT/work" "$STEAMAC_WORK_DIR"
move_dir "$STEAMAC_ROOT/host/launcher/.build" "$STEAMAC_SWIFT_BUILD_DIR"
echo "Repository build artifacts moved outside steamac."
