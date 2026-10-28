#!/usr/bin/env bash
# Installer workflow; run from the repository root or via the .app bundle.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$ROOT"
trap 'status=$?; if (( status != 0 )); then printf "\nCinterForgeOS installation FAILED (exit %s).\n" "$status"; fi' EXIT
for phase in setup build stage; do
  printf '\n===== CinterForgeOS %s (Release) =====\n' "$phase"
  /bin/bash ./CFOS "$phase" cinterforge-os mac Release
done
installed="$HOME/Library/Application Support/Steam/steamapps/common/CinterForge/Applications/macOS/CinterForgeOS/2026/CinterForge OS.app"
[[ -d "$installed" ]] || { echo "Staged application missing: $installed" >&2; exit 1; }
printf '\nCinterForgeOS staged successfully: %s\n' "$installed"
printf '\nSteam integration: Steam officially supports Add a Non-Steam Game.\n'
printf 'In Steam: Games > Add a Non-Steam Game to My Library, then Browse to the staged application.\n'
# Steam has no supported public API to register arbitrary local shortcuts.
# Never mutate Steam userdata/shortcuts.vdf behind the client's back.
if [[ "${CFOS_OPEN_STEAM:-1}" == 1 ]]; then
  /usr/bin/open -a Steam || true
  /usr/bin/open -R "$installed" || true
fi
printf '\nInstallation complete.\n'
