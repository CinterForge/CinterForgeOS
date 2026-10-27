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
    # App bundles contain no managed SteamOS user disks: Swift manages those.
    if [[ -e "$backup" ]]; then rm -rf -- "$backup"; fi
    echo "CFOS: staged $installed_app"
  done < <(cfos_configurations)
}
