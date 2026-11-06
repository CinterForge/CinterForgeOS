#!/usr/bin/env bash
# Test: run the currently available native provisioning self-test.
# This is NOT yet the complete DX9/OpenGL/SteamOS graphics regression suite.
# Sourced by ../CFOS.
cfos_test() {
  if [[ "$CFOS_PLATFORM" == linuxkit ]]; then
    # CFOS owns the VM and test orchestration. CinterForge supplies the
    # official Bedrock acquisition and existing console/network smoke.
    cfos_require_mac || return $?
    cfos_run python3 -u "$CFOS_ROOT/build/linux/test_linuxkit.py" --repo-root "$CFOS_ROOT"
    return $?
  fi
  if [[ "$CFOS_PLATFORM" == linux ]]; then
    cfos_linux test
    return $?
  fi
  cfos_require_mac
  local configuration installed_app
  while IFS= read -r configuration; do
    installed_app="$(cfos_installed_app "$configuration")"
    if (( ! CFOS_DRY_RUN )); then cfos_validate_app "$installed_app"; fi
    cfos_linux verify || return 1
    cfos_run python3 -u "$installed_app/Contents/Resources/Ubuntu/ubuntu_adapter.py" test || return 1
    cfos_run "$installed_app/Contents/MacOS/cfos-vm" --selftest-provision
    # Host Vulkan regression tests are explicit Test work, not Xcode Build gates.
    cfos_run "$CFOS_ROOT/host/kosmickrisp/build.sh" test
  done < <(cfos_configurations)
}
