#!/usr/bin/env bash
set -euo pipefail
app='/run/steamapps/common/CinterForge/Applications/Windows/Heaven/2026/app'
exe="$app/bin/browser_x86.exe"
proton="$HOME/.local/share/Steam/steamapps/common/Proton 11.0 (ARM64)/proton"
if [[ ! -f "$exe" ]]; then
  echo "Windows Heaven executable missing: $exe" >&2
  exit 1
fi
if [[ ! -f "$proton" ]]; then
  echo "Proton runtime missing: $proton" >&2
  exit 1
fi
export STEAM_COMPAT_CLIENT_INSTALL_PATH="$HOME/.local/share/Steam"
export STEAM_COMPAT_DATA_PATH="$HOME/.local/share/cinterforge/proton-heaven"
export ENABLE_GAMESCOPE_WSI=0 DISABLE_GAMESCOPE_WSI=1
mkdir -p "$STEAM_COMPAT_DATA_PATH"
cd "$app/bin"
exec "$proton" waitforexitandrun "$exe" -config ../data/launcher/launcher.xml
