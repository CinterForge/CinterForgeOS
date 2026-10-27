#!/usr/bin/env bash
# Setup: prepare host toolchains, private Colima profile, and guest sources.
# Sourced by ../CFOS. Nothing in this file runs until cfos_setup is called.
cfos_setup() {
  cfos_require_mac
  if (( ! CFOS_DRY_RUN )); then
    command -v brew >/dev/null || {
      echo 'CFOS: install Apple Silicon Homebrew first.' >&2
      return 1
    }
  fi

  # Keep the dependency list explicit: reviewers can see what we require.
  local formula
  local formulae=(
    dtc xz lld pkgconf libepoxy meson ninja llvm
    spirv-llvm-translator spirv-tools spirv-headers spirv-cross
    vulkan-loader glslang python cmake go rustup-init colima docker
  )
  for formula in "${formulae[@]}"; do
    if (( CFOS_DRY_RUN )) || ! brew list --versions "$formula" >/dev/null 2>&1; then
      cfos_run brew install "$formula"
    fi
  done

  # Validate binaries, not just Homebrew package metadata.
  if (( ! CFOS_DRY_RUN )); then
    local tool
    for tool in git make xcodebuild xcrun cargo go python3 docker colima; do
      command -v "$tool" >/dev/null || {
        echo "CFOS: missing required tool: $tool" >&2
        return 1
      }
    done
  fi

  # Lima's socket paths have a hard macOS length limit.  A symlink from
  # /tmp to a deep directory does NOT solve this: Lima resolves it to the
  # physical path.  The Colima home must itself have a short physical path.
  #
  # Keep persistent VM data in Builds/CFOS/Colima.  Expose that same directory
  # in the per-application build tree with a symlink for Finder convenience.
  # There is one authoritative Colima home, not a chain of fallback paths.
  local state_root="$CFOS_BUILD_ROOT/CFOS"
  local colima_home="$CFOS_WORKSPACE/Builds/CFOS/Colima"
  local colima_view="$state_root/Colima"
  local colima_profile='cinterforge-linux'

  # Fail visibly if an older *real* directory still occupies the intended
  # display path.  Its VM may contain user data; never overwrite or merge it.
  if [[ -e "$colima_view" && ! -L "$colima_view" ]]; then
    echo "CFOS: Colima must be moved out of: $colima_view" >&2
    echo "CFOS: Move it to: $colima_home" >&2
    echo 'CFOS: Stop Colima before moving its files. Setup will create the link.' >&2
    return 1
  fi
  if [[ -L "$colima_view" && "$(readlink "$colima_view")" != "$colima_home" ]]; then
    echo "CFOS: Colima view points to the wrong location: $colima_view" >&2
    return 1
  fi

  cfos_run mkdir -p "$state_root" "$colima_home"
  if [[ ! -L "$colima_view" ]]; then
    cfos_run ln -s "$colima_home" "$colima_view"
  fi

  # Check the *real* Lima socket path before trying to create a VM.
  # Avoid accidentally generating another unstartable Lima configuration.
  local host_agent="$colima_home/_lima/colima-$colima_profile/ha.sock"
  if (( ${#host_agent} >= 104 )); then
    echo "CFOS: Lima host-agent socket path is too long: $host_agent" >&2
    return 1
  fi
  export COLIMA_HOME="$colima_home"
  export LIMA_HOME="$colima_home/_lima"
  export COLIMA_CACHE_HOME="$state_root/Cache/Colima"
  export DOCKER_CONFIG="$state_root/Docker"
  if (( CFOS_DRY_RUN )) || ! colima status --profile "$colima_profile" >/dev/null 2>&1; then
    cfos_run colima start --profile "$colima_profile" --arch aarch64 --vm-type vz --activate=false
  fi

  # Current limitation: the maintained FEX fork must already be checked out.
  # Do not silently switch to upstream or an unrelated checkout.
  local fex_checkout="${FEX_FORK_CHECKOUT:-$HOME/Source/FEX}"
  if (( ! CFOS_DRY_RUN )) && [[ ! -d "$fex_checkout/.git" ]]; then
    echo "CFOS: maintained FEX fork missing at $fex_checkout" >&2
    echo 'Clone the CinterForge/FEX cinterforge branch or set FEX_FORK_CHECKOUT.' >&2
    return 1
  fi
  cfos_run bash "$CFOS_ROOT/guest/fex/build.sh" fetch
  cfos_run bash "$CFOS_ROOT/guest/fex/runtime.sh" fetch
  echo 'CFOS: Setup prepared source prerequisites; the build phase compiles the application.'
}
