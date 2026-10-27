#!/usr/bin/env bash
# Test: run the currently available native provisioning self-test.
# This is NOT yet the complete DX9/OpenGL/SteamOS graphics regression suite.
# Sourced by ../CFOS.
cfos_test() {
  cfos_require_mac
  local configuration installed_app
  while IFS= read -r configuration; do
    installed_app="$(cfos_installed_app "$configuration")"
    if (( ! CFOS_DRY_RUN )); then cfos_validate_app "$installed_app"; fi
    cfos_run "$installed_app/Contents/MacOS/cfos-vm" --selftest-provision
    # Host Vulkan regression tests are explicit Test work, not Xcode Build gates.
    cfos_run "$CFOS_ROOT/host/kosmickrisp/build.sh" test
  done < <(cfos_configurations)
}
