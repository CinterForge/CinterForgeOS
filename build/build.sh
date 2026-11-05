#!/usr/bin/env bash
# Build: use the existing Xcode scheme. Do not invent a parallel compiler.
# Sourced by ../CFOS.
cfos_build() {
  if [[ "$CFOS_PLATFORM" == linux ]]; then
    cfos_linux build
    return $?
  fi
  cfos_require_mac
  # Re-check the Ubuntu receipt when Build is invoked without Setup.
  cfos_linux verify || return 1
  if (( CFOS_DRY_RUN )); then
    source "$CFOS_ROOT/scripts/colima-runtime.sh"
    cfos_colima_environment || return 1
    local build_memory_gib
    build_memory_gib="$(cfos_colima_build_memory_gib)" || return 1
    cfos_run colima start --profile "$CFOS_COLIMA_PROFILE" --memory "$build_memory_gib" --activate=false
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
    # The CFOS launcher Home desktop uses the SAME authored Linux OS wallpaper.
    # Copy before the final Ubuntu adapter bundle/signing operation; do not
    # edit the Xcode project or sign a bundle before changing its resources.
    local wallpaper="$CFOS_ROOT/resources/html/assets/backgrounds/cinterforge-linux-1920x1080.png"
    [[ -f "$wallpaper" ]] || { echo "CFOS: missing Desktop wallpaper: $wallpaper" >&2; return 1; }
    cfos_run cp "$wallpaper" "$(cfos_built_app "$configuration")/Contents/Resources/CinterForgeOS-Desktop.png" || return 1
    # Package the single CFOS-owned Session console for Swift/WKWebView and
    # publish its checked consumer product under sibling Builds. Ubuntu's bundle
    # step signs the complete .app after both resources have been copied.
    cfos_run python3 -u "$CFOS_ROOT/scripts/session_console.py" build \
      --repo-root "$CFOS_ROOT" --product "$(cfos_built_app "$configuration")" || return 1
    # The minimal WebApp HTTP/API handler belongs to the sibling WebApp repo.
    # CFOS bundles the verified product before final macOS code signing.
    cfos_run python3 -u "$CFOS_WORKSPACE/CinterForgeWebApp/runtime/package.py" build \
      --product "$(cfos_built_app "$configuration")" || return 1
    # Package the already-tested host Ubuntu adapter *before* validating the
    # signed product. This is part of Build, not an after-the-fact Stage patch.
    cfos_run python3 -u "$CFOS_ROOT/build/linux/ubuntu_pipeline.py" bundle \
      --repo-root "$CFOS_ROOT" --configuration Release \
      --product "$(cfos_built_app "$configuration")" || return 1
    if (( ! CFOS_DRY_RUN )); then
      cfos_validate_app "$(cfos_built_app "$configuration")"
    fi
  done < <(cfos_configurations)
}
