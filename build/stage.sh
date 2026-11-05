#!/usr/bin/env bash
# Stage: install a complete versioned .app bundle, preserving the old version
# if copying or validation fails. Does NOT download/provision SteamOS disks.
# Sourced by ../CFOS.
# Stage the ONE authored SteamOS guest dispatcher from this repository. The
# previous reference to scripts/steam/stage_external.py was an orphaned path:
# do not recreate another Python packager for files already under dispatch/.
cfos_stage_guest_commands() {
  local src="$CFOS_ROOT/dispatch/external"
  local external="$CFOS_WORKSPACE/Builds/CreativeSuite/Staged/mac/cinterforge-os/External/Current"
  local apps destination temporary
  [[ -s "$src/dispatch/cinter-commands.sh" && -d "$src/installl" && -d "$src/launch" ]] || {
    echo "CFOS: authored guest dispatcher missing: $src" >&2; return 1;
  }
  if (( CFOS_DRY_RUN )); then
    cfos_run mkdir -p "$external/TestScripts"
    cfos_run cp "$src/dispatch/cinter-commands.sh" "$external/CinterDispatcher"
    cfos_run rsync -a "$src/" "$external/TestScripts/"
    return 0
  fi
  # A published product must be an exact copy of the staged guest commands.
  # Build and compare in a sibling folder first; don't replace a valid Stage
  # until all authored files have copied successfully.
  mkdir -p "$(dirname "$external")"
  temporary="${external}.staging.$$"
  [[ ! -e "$temporary" ]] || { echo "CFOS: unreconciled guest staging: $temporary" >&2; return 1; }
  mkdir -p "$temporary/TestScripts"
  if ! cp "$src/dispatch/cinter-commands.sh" "$temporary/CinterDispatcher" \
       || ! rsync -a --exclude '.DS_Store' "$src/" "$temporary/TestScripts/" \
       || ! cmp -s "$src/dispatch/cinter-commands.sh" "$temporary/CinterDispatcher"; then
    rm -rf -- "$temporary"
    return 1
  fi
  chmod 755 "$temporary/CinterDispatcher"
  # Some source files carry 0644 permissions in the checkout. The installed
  # SteamOS dispatcher execs these scripts directly, so Stage must make the
  # actual published executable entry points executable rather than assume
  # that Git transport retained their executable bits.
  local entry
  for entry in \
      dispatch/cinter-commands.sh \
      installl/install-unforked.sh \
      launch/launch-unforked.sh \
      run/run-linux-amd64.sh \
      wine/cinterforge-wine.sh \
      inspect/inspect-unforked-windows.py; do
    [[ -s "$temporary/TestScripts/$entry" ]] || {
      echo "CFOS: required guest command not present: $entry" >&2
      rm -rf -- "$temporary"
      return 1
    }
    chmod 755 "$temporary/TestScripts/$entry"
  done
  if [[ -e "$external" ]]; then
    echo "CFOS: replacing previously staged guest commands: $external"
    local backup="${external}.previous.$$"
    mv "$external" "$backup" || { rm -rf "$temporary"; return 1; }
    if ! mv "$temporary" "$external"; then mv "$backup" "$external"; return 1; fi
    rm -rf "$backup"
  else
    mv "$temporary" "$external"
  fi
  apps="$(cfos_steamapps)/common/CinterForge/Applications" || return 1
  destination="$apps/Test"
  mkdir -p "$destination/Dispatcher" "$destination/Scripts"
  # Keep the authored external commands in the persistent SteamOS volume;
  # do not alter the immutable guest image or other application folders.
  cp "$external/CinterDispatcher" "$destination/Dispatcher/Cinter"
  rsync -a --exclude '.DS_Store' "$external/TestScripts/" "$destination/Scripts/"
  echo "CFOS: staged SteamOS upstream install/launch/test commands to $destination"
}

cfos_stage() {
  if [[ "$CFOS_PLATFORM" == linux ]]; then
    cfos_linux stage
    return $?
  fi
  cfos_require_mac
  local configuration source_app installed_app temporary backup
  while IFS= read -r configuration; do
    source_app="$(cfos_built_app "$configuration")"
    installed_app="$(cfos_installed_app "$configuration")"
    if (( ! CFOS_DRY_RUN )); then cfos_validate_app "$source_app"; fi
    cfos_run mkdir -p "$(dirname "$installed_app")"
    if (( CFOS_DRY_RUN )); then
      cfos_run ditto "$source_app" "$installed_app"
      continue
    fi

    temporary="${installed_app}.staging.$$"
    backup="${installed_app}.previous.$$"
    # Fail rather than overwrite another operation's temporary files.
    if [[ -e "$temporary" || -e "$backup" ]]; then
      echo 'CFOS: previous staging transaction still exists; refusing to overwrite.' >&2
      return 1
    fi
    if ! ditto "$source_app" "$temporary"; then
      rm -rf -- "$temporary"
      echo 'CFOS: failed copying application; existing installation unchanged.' >&2
      return 1
    fi
    if ! cfos_validate_app "$temporary"; then
      rm -rf -- "$temporary"
      return 1
    fi
    if [[ -e "$installed_app" ]]; then
      mv "$installed_app" "$backup"
    fi
    if ! mv "$temporary" "$installed_app"; then
      if [[ -e "$backup" ]]; then mv "$backup" "$installed_app"; fi
      echo 'CFOS: installation failed; previous application restored.' >&2
      return 1
    fi
    # The new installation is committed only after it passes validation.
    # Remove the transaction backup and stale backups left by interrupted
    # successful stages. Never touch another application's files or Ubuntu.
    if ! cfos_validate_app "$installed_app"; then
      echo 'CFOS: installed application failed validation; restoring previous version.' >&2
      rm -rf -- "$installed_app"
      if [[ -d "$backup" && ! -L "$backup" ]]; then mv -- "$backup" "$installed_app"; fi
      return 1
    fi
    if [[ -d "$backup" && ! -L "$backup" ]]; then
      rm -rf -- "$backup" || { echo "CFOS: could not remove transaction backup: $backup" >&2; return 1; }
    fi
    # Only clean backups for this exact installed app, and only when the
    # canonical installed bundle is valid. Do not follow symbolic links.
    local old_backup
    for old_backup in "${installed_app}.previous."*; do
      [[ -e "$old_backup" || -L "$old_backup" ]] || continue
      if [[ -d "$old_backup" && ! -L "$old_backup" && "${old_backup##*.}" =~ ^[0-9]+$ ]]; then
        echo "CFOS: removing stale installation backup: $old_backup"
        rm -rf -- "$old_backup" || { echo "CFOS: could not remove stale backup: $old_backup" >&2; return 1; }
      else
        echo "CFOS: leaving unrecognized backup entry untouched: $old_backup" >&2
      fi
    done
    echo "CFOS: staged $installed_app"
    # Prefer the editable CinterForge fork. The installer must still work when
    # that sibling checkout is absent or has not received the macOS changes.
    local steam_register="$CFOS_WORKSPACE/cinterforge-steamtinkerlaunch/mac/register_nonsteam.py"
    local steam_dialog="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/Install CinterForgeOS.app/Contents/Resources/UI/steam_dialogs.applescript"
    if [[ ! -f "$steam_dialog" ]]; then
      echo "CFOS: missing Steam dialog script: $steam_dialog" >&2
      return 1
    fi
    local steam_artwork="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/Install CinterForgeOS.app/Contents/Resources/UI/SteamArtwork"
    [[ -f "$steam_artwork/hero.png" && -f "$steam_artwork/logo.png" ]] || { echo 'CFOS: missing Steam-specific hero/logo artwork' >&2; return 1; }
    local -a steam_user_args=()
    if [[ -n "${CFOS_STEAM_USER_ID:-}" ]]; then steam_user_args=(--steam-user "$CFOS_STEAM_USER_ID"); fi
    # Always use the checked-in registrar: the sibling fork can be stale and
    # the old embedded fallback used the wrong Steam artwork identifier.
    local bundled_register="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/steam/register_nonsteam.py"
    [[ -f "$bundled_register" ]] || { echo "CFOS: missing Steam registrar: $bundled_register" >&2; return 1; }
    CFOS_STEAM_ARTWORK_DIR="$steam_artwork" CFOS_STEAM_DIALOG_SCRIPT="$steam_dialog" cfos_run /usr/bin/python3 "$bundled_register" --app "$installed_app" --restart-steam "${steam_user_args[@]}"
  done < <(cfos_configurations)

  # Both Linux and Windows publisher install commands are distributed with
  # the installed SteamOS product, independent of the CinterForge forks.
  cfos_stage_guest_commands || return 1
}
