#!/usr/bin/env bash
set -euo pipefail
app='/run/steamapps/common/CinterForge/Applications/Linux/Superposition/2026/Unigine_Heaven-4.0'
if [[ ! -f "$app/heaven" ]]; then
  echo "Linux Heaven executable missing: $app/heaven" >&2
  exit 1
fi
if ! command -v FEXBash >/dev/null 2>&1; then
  echo 'FEXBash is unavailable in the SteamOS guest PATH' >&2
  exit 1
fi
export ENABLE_GAMESCOPE_WSI=0 DISABLE_GAMESCOPE_WSI=1
cd "$app"
exec FEXBash -c './heaven'
