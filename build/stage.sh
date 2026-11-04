#!/usr/bin/env bash
# Stage: install a complete versioned .app bundle, preserving the old version
# if copying or validation fails. Does NOT download/provision SteamOS disks.
# Sourced by ../CFOS.
cfos_stage() {
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

  # The CFOS host Stage must install the external Cinter command tree as well
  # as the signed app. Reuse CinterForge's existing Stage ownership and
  # preservation rules; never create another dispatcher or guest helper.
  local lifecycle="$CFOS_WORKSPACE/CinterForge/CinterForgeServer/cinter/build_cinterforge/creativeSuite/lifecycle.py"
  local external="$CFOS_WORKSPACE/CinterForge/CinterForgeServer/cinter/build_cinterforge/creativeSuite/apps/cinterforge-os/external"
  if [[ ! -f "$lifecycle" || ! -f "$external/dispatch/cinter-dispatcher.sh" ]]; then
    echo 'CFOS: missing sibling CinterForge external Cinter Stage sources.' >&2
    return 1
  fi
  if (( CFOS_DRY_RUN )); then
    echo "CFOS: would stage external Cinter dispatcher and scripts from $external"
  else
    CFOS_LIFECYCLE="$lifecycle" CFOS_EXTERNAL="$external" CFOS_BUILD_ROOT="$CFOS_BUILD_ROOT" \
      /usr/bin/python3 - <<'PYTHON'
import importlib.util
import os
from pathlib import Path
import shutil
import tempfile

lifecycle_path = Path(os.environ['CFOS_LIFECYCLE'])
external = Path(os.environ['CFOS_EXTERNAL'])
build_root = Path(os.environ['CFOS_BUILD_ROOT'])
spec = importlib.util.spec_from_file_location('cinter_cfos_lifecycle', lifecycle_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
build_root.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='CinterExternalStage-', dir=build_root) as temp:
    staged = Path(temp)
    shutil.copy2(external / 'dispatch/cinter-dispatcher.sh', staged / 'CinterDispatcher')
    scripts = staged / 'TestScripts'
    for source in external.rglob('*'):
        if not source.is_file() or source.name == 'cinter-dispatcher.sh':
            continue
        relative = source.relative_to(external)
        if any(part.startswith('.') or part == '__pycache__' for part in relative.parts):
            continue
        target = scripts / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    module.provision_cinterforge_os_dispatcher(staged)
    module.provision_cinterforge_os_test_scripts(staged)
PYTHON
  fi
}
