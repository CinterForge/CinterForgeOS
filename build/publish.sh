#!/usr/bin/env bash
# Package an installed CFOS Release and its independently staged guest commands.
# Publication of these archives to a remote destination remains separately authorized.
cfos_publish() {
  [[ "$CFOS_CONFIGURATION" == Release ]] || { echo 'CFOS: Publish requires Release.' >&2; return 2; }
  local output="$CFOS_WORKSPACE/Builds/CreativeSuite/Zipped"
  if [[ "$CFOS_PLATFORM" == linux ]]; then
    local built="$CFOS_WORKSPACE/Builds/CreativeSuite/Built/cinterforge-os/linux/2026/Ubuntu/Ubuntu-24.04-amd64-rootfs.tar"
    local installed
    if (( ! CFOS_DRY_RUN )); then
      local steamapps
      steamapps="$(cfos_steamapps)" || return 1
      installed="$steamapps/common/CinterForge/Applications/Linux/CinterForgeOS/2026/Ubuntu/Distro/Ubuntu-24.04-amd64-rootfs.tar"
      [[ -s "$built" && -s "$installed" ]] || { echo 'CFOS: Linux Build and Stage must complete before Publish.' >&2; return 1; }
      cmp -s "$built" "$installed" || { echo 'CFOS: installed Linux archive does not match Build.' >&2; return 1; }
    fi
    cfos_run mkdir -p "$output"
    cfos_run cp "$built" "$output/CinterForgeOS-linux-amd64-2026.tar"
  else
    local app external="${CFOS_WORKSPACE}/Builds/CreativeSuite/Staged/mac/cinterforge-os/External/Current"
    local archive="$output/CinterForgeOS-mac-2026.zip"
    if (( CFOS_DRY_RUN )); then
      cfos_run ditto -c -k '<staged CFOS Release app and external dispatcher>' "$archive"
    else
      app="$(cfos_installed_app Release)" || return 1
      cfos_validate_app "$app" || return 1
      [[ -s "$external/CinterDispatcher" && -d "$external/TestScripts" ]] || {
        echo 'CFOS: Stage must install the external guest dispatcher and scripts before Publish.' >&2
        return 1
      }
      mkdir -p "$output"
      local temporary
      temporary="$(mktemp -d "$output/CFOS-mac-release-XXXXXX")" || return 1
      # Distribution layout matches persistent Applications ownership; the
      # installer can place the app and guest scripts without a source checkout.
      mkdir -p "$temporary/Applications/macOS/CinterForgeOS/2026" \
               "$temporary/Applications/Test/Dispatcher" \
               "$temporary/Applications/Test/Scripts"
      if ! ditto "$app" "$temporary/Applications/macOS/CinterForgeOS/2026/CinterForge OS.app" \
          || ! cp "$external/CinterDispatcher" "$temporary/Applications/Test/Dispatcher/Cinter" \
          || ! ditto "$external/TestScripts" "$temporary/Applications/Test/Scripts" \
          || ! ditto -c -k "$temporary" "$archive"; then
        rm -rf -- "$temporary"
        echo 'CFOS: macOS package assembly failed.' >&2
        return 1
      fi
      rm -rf -- "$temporary"
    fi
  fi
  echo "CFOS: Prepared local Release in $output; remote publication requires explicit authorization."
}
