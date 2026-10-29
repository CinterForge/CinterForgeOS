#!/usr/bin/env bash
# Installer workflow; run from the repository root or via the .app bundle.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$ROOT"
trap 'status=$?; if (( status != 0 )); then printf "\nCinterForgeOS installation FAILED (exit %s).\n" "$status"; fi' EXIT
# The flame owns the interactive terminal; each phase's complete, unfiltered
# output is also recorded outside the source tree for postmortem diagnostics.
# CI/noninteractive invocations retain normal stdout/stderr from CFOS.
fire="$ROOT/scripts/steam/fire.py"
# Keep the installer log beside the staged app so it is discoverable by users.
steamapps="$(/usr/bin/python3 "$ROOT/scripts/resolve-steamapps.py")"
install_dir="${CFOS_INSTALL_ROOT:-$steamapps/common/CinterForge/Applications/macOS/CinterForgeOS}/2026"
installed="$install_dir/CinterForge OS.app"
mkdir -p "$install_dir"
log_file="$install_dir/CFOS Installer.log"
# Truncate for each installation; preserve all phases in the same log.
: > "$log_file"
printf 'CinterForgeOS installer started: %s\n' "$(date)" >> "$log_file"
if [[ -t 1 && -t 2 && "${TERM:-dumb}" != dumb && "${CFOS_PLAIN_OUTPUT:-0}" != 1 ]]; then
  [[ -f "$fire" ]] || { echo "Missing installer display: $fire" >&2; exit 1; }
  command -v python3 >/dev/null || { echo "Python 3 is required for installer display" >&2; exit 1; }
  printf 'Installer output log: %s\n' "$log_file"
  for phase in setup build stage; do
    case "$phase" in
      setup) stage='PREPARING ENVIRONMENT' ;;
      build) stage='BUILDING CINTERFORGEOS' ;;
      stage) stage='INSTALLING CINTERFORGEOS' ;;
    esac
    printf "\n===== CinterForgeOS %s (Release) =====\n" "$phase" >> "$log_file"
    python3 "$fire" --stage "$stage" --log "$log_file" -- /bin/bash ./CFOS "$phase" cinterforge-os mac Release
  done
else
  for phase in setup build stage; do
    printf '\n===== CinterForgeOS %s (Release) =====\n' "$phase" | tee -a "$log_file"
    /bin/bash ./CFOS "$phase" cinterforge-os mac Release 2>&1 | tee -a "$log_file"
  done
fi
[[ -d "$installed" ]] || { echo "Staged application missing: $installed" >&2; exit 1; }
printf '\nCinterForgeOS staged successfully: %s\n' "$installed" | tee -a "$log_file"
printf '\nSteam integration: CinterForge OS registered as a Non-Steam Game by Stage.\n' | tee -a "$log_file"
# Steam shortcut registration is handled by our fork during Stage.
if [[ "${CFOS_OPEN_STEAM:-1}" == 1 ]]; then
  /usr/bin/open -a Steam || true
  /usr/bin/open -R "$installed" || true
fi
printf '\nInstallation complete.\n' | tee -a "$log_file"
