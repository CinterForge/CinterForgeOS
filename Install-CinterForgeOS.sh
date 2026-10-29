#!/usr/bin/env bash
# Install CinterForgeOS using the existing CFOS lifecycle:
#   setup -> build -> stage
#
# Run this from anywhere, or launch it through "Install CinterForgeOS.app".
# The graphical launcher sets CFOS_INSTALL_DIALOG=1 to request Steam account
# selection. The normal command-line path does not show that dialog.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$ROOT"

show_installer_terminal() {
  # The graphical launcher uses Terminal so it has the same filesystem access
  # as the working command-line installer. Make that window less distracting.
  if [[ "${CFOS_INSTALL_DIALOG:-0}" == 1 && -t 1 ]]; then
    printf '\033]0;CinterForgeOS Installer\007'            # Window title
    printf '\033]11;#080808\007\033]10;#f5f0e8\007'  # Dark theme
    printf '\033[2J\033[H\033[?25l'                 # Clear, hide cursor
  fi
}

restore_terminal_on_exit() {
  local exit_status=$?
  printf '\033[?25h'  # Always restore the cursor, including on failure.
  if (( exit_status != 0 )); then
    printf '\nCinterForgeOS installation FAILED (exit %s).\n' "$exit_status"
  fi
}

choose_steam_account_if_requested() {
  [[ "${CFOS_INSTALL_DIALOG:-0}" == 1 ]] || return 0

  local register_script="$ROOT/scripts/steam/register_nonsteam.py"
  local dialog_script="$ROOT/Install CinterForgeOS.app/Contents/Resources/UI/steam_dialogs.applescript"
  local preflight account_id

  if [[ ! -f "$register_script" || ! -f "$dialog_script" ]]; then
    echo 'Missing Steam installer dialog components' >&2
    return 1
  fi

  # Ask the user which Steam account to use. This is a preflight only;
  # registration itself happens in CFOS stage, not here.
  if ! preflight=$(CFOS_STEAM_DIALOG_SCRIPT="$dialog_script" \
      /usr/bin/python3 "$register_script" --preflight --install-dialog 2>&1); then
    if [[ "$preflight" == *"Installation cancelled"* ]]; then
      exit 0
    fi
    printf 'Steam installer preflight failed: %s\n' "$preflight" >&2
    return 1
  fi

  account_id=$(printf '%s\n' "$preflight" | \
    /usr/bin/sed -n 's/^CFOS_STEAM_ACCOUNT_ID=\([0-9][0-9]*\)$/\1/p' | \
    /usr/bin/tail -n 1)
  if [[ -z "$account_id" ]]; then
    echo 'No Steam account selected; installation did not start.' >&2
    return 1
  fi
  export CFOS_STEAM_USER_ID="$account_id"
}

use_flame_display() {
  [[ -t 1 && -t 2 && "${TERM:-dumb}" != dumb && "${CFOS_PLAIN_OUTPUT:-0}" != 1 ]]
}

run_installation_phases() {
  local phase stage
  local fire="$ROOT/scripts/steam/fire.py"

  if use_flame_display; then
    [[ -f "$fire" ]] || { echo "Missing installer display: $fire" >&2; return 1; }
    command -v python3 >/dev/null || { echo 'Python 3 is required for installer display' >&2; return 1; }
    printf 'Installer output log: %s\n' "$log_file"
  fi

  for phase in setup build stage; do
    if use_flame_display; then
      case "$phase" in
        setup) stage='PREPARING ENVIRONMENT' ;;
        build) stage='BUILDING CINTERFORGEOS' ;;
        stage) stage='INSTALLING CINTERFORGEOS' ;;
      esac
      printf '\n===== CinterForgeOS %s (Release) =====\n' "$phase" >> "$log_file"
      # fire.py displays live progress and records the full output to the log.
      python3 "$fire" --stage "$stage" --log "$log_file" -- \
        /bin/bash ./CFOS "$phase" cinterforge-os mac Release
    else
      # Noninteractive runs keep normal output, while tee records it as well.
      printf '\n===== CinterForgeOS %s (Release) =====\n' "$phase" | tee -a "$log_file"
      /bin/bash ./CFOS "$phase" cinterforge-os mac Release 2>&1 | tee -a "$log_file"
    fi
  done
}

finish_installation() {
  if [[ ! -d "$installed" ]]; then
    echo "Staged application missing: $installed" >&2
    return 1
  fi

  printf '\nCinterForgeOS staged successfully: %s\n' "$installed" | tee -a "$log_file"
  printf '\nSteam integration: CinterForge OS registered as a Non-Steam Game by Stage.\n' | tee -a "$log_file"

  # Opening Steam and revealing the app are conveniences, not requirements.
  if [[ "${CFOS_OPEN_STEAM:-1}" == 1 ]]; then
    /usr/bin/open -a Steam || true
    /usr/bin/open -R "$installed" || true
  fi
  printf '\nInstallation complete.\n' | tee -a "$log_file"
}

main() {
  local steamapps install_dir

  show_installer_terminal
  choose_steam_account_if_requested

  # Put the log beside the installed application, not in the source tree.
  steamapps="$(/usr/bin/python3 "$ROOT/scripts/resolve-steamapps.py")"
  install_dir="${CFOS_INSTALL_ROOT:-$steamapps/common/CinterForge/Applications/macOS/CinterForgeOS}/2026"
  installed="$install_dir/CinterForge OS.app"
  mkdir -p "$install_dir"
  log_file="$install_dir/CFOS Installer.log"
  : > "$log_file"  # Each run gets a fresh log containing all three phases.
  printf 'CinterForgeOS installer started: %s\n' "$(date)" >> "$log_file"

  run_installation_phases
  finish_installation
}

trap restore_terminal_on_exit EXIT
main "$@"
