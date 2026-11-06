#!/usr/bin/env bash
set -euo pipefail
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mode="${1:-gui}"
[[ "$mode" == gui ]] || { echo "ERROR: installed Ardour guest GUI smoke supports gui only" >&2; exit 2; }
exec python3 "$here/../../cinterforge-os/test/installed_guest_smoke.py" win 'CinterForge Audio' 'cfaudio' --mode "$mode" --exe-glob "lib/ardour9/ardour-[0-9]*.exe"
