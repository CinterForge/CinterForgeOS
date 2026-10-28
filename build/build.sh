#!/usr/bin/env bash
# Build: use the existing Xcode scheme. Do not invent a parallel compiler.
# Sourced by ../CFOS.
cfos_build() {
  cfos_require_mac
  if (( CFOS_DRY_RUN )); then
    cfos_run colima start --profile cinterforge-linux --activate=false
  else
    source "$CFOS_ROOT/scripts/colima-runtime.sh"
    cfos_colima_activate || return 1
  fi
  local configuration
  while IFS= read -r configuration; do
    cfos_run xcodebuild \
      -project "$CFOS_ROOT/CinterForgeOS.xcodeproj" \
      -scheme CinterForgeOS \
      -configuration "$configuration" \
      -derivedDataPath "$CFOS_BUILD_ROOT/DerivedData" \
      build
    # Build success alone does not guarantee the expected product location.
    if (( ! CFOS_DRY_RUN )); then
      cfos_validate_app "$(cfos_built_app "$configuration")"
    fi
  done < <(cfos_configurations)
}
