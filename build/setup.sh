#!/usr/bin/env bash
# Setup: prepare host toolchains, private Colima profile, and guest sources.
# Sourced by ../CFOS. Nothing in this file runs until cfos_setup is called.
cfos_setup() {
  cfos_require_mac
  # Never install host dependencies through Intel Homebrew under Rosetta.
  # This also makes Setup and Xcode build scripts resolve the same formulae.
  if (( ! CFOS_DRY_RUN )); then
    [[ -x /opt/homebrew/bin/brew ]] || {
      echo 'CFOS: missing native Apple Silicon Homebrew at /opt/homebrew/bin/brew (do not use /usr/local Intel Homebrew).' >&2
      return 1
    }
    export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
    [[ "$(brew --prefix)" == /opt/homebrew ]] || {
      echo "CFOS: wrong Homebrew prefix: $(brew --prefix); expected /opt/homebrew" >&2
      return 1
    }
  fi
  if (( ! CFOS_DRY_RUN )); then
    command -v brew >/dev/null || {
      echo 'CFOS: install Apple Silicon Homebrew first.' >&2
      return 1
    }
  fi

  # Install MoltenVK repro/bench shader tools here, never inside Xcode build
  # scripts. The formulae below include glslang, spirv-tools and vulkan-loader.
  # Keep the dependency list explicit: reviewers can see what we require.
  local formula
  local formulae=(
    dtc xz lld pkgconf libepoxy meson ninja llvm
    spirv-llvm-translator spirv-tools spirv-headers spirv-cross
    vulkan-loader glslang cmake go rustup colima docker
  )
  for formula in "${formulae[@]}"; do
    if (( CFOS_DRY_RUN )) || ! brew list --versions "$formula" >/dev/null 2>&1; then
      cfos_run brew install "$formula"
    fi
  done

  # Provision pinned Python into the external CFOS build tree, never Homebrew
  # or a user-global Python directory. uv itself is installed locally as well.
  local python_home="$CFOS_BUILD_ROOT/CFOS/work/tools/python"
  local uv_home="$CFOS_BUILD_ROOT/CFOS/work/tools/uv"
  local managed_python="$python_home/bin/python3"
  if (( CFOS_DRY_RUN )); then
    cfos_run mkdir -p "$python_home" "$uv_home"
  else
    mkdir -p "$python_home/bin" "$uv_home/bin"
    if [[ ! -x "$uv_home/bin/uv" ]]; then
      command -v curl >/dev/null || { echo 'CFOS: curl required to bootstrap isolated Python' >&2; return 1; }
      echo "CFOS: installing isolated uv into $uv_home"
      local uv_installer="$uv_home/install.sh"
      curl -fsSL https://astral.sh/uv/install.sh -o "$uv_installer" || return 1
      env UV_INSTALL_DIR="$uv_home/bin" UV_NO_MODIFY_PATH=1 sh "$uv_installer" || return 1
    fi
    [[ -x "$uv_home/bin/uv" ]] || { echo 'CFOS: isolated uv installation failed' >&2; return 1; }
    if [[ ! -x "$managed_python" ]] || ! "$managed_python" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 11))' 2>/dev/null; then
      echo "CFOS: installing isolated Python 3.11 into $python_home"
      UV_PYTHON_INSTALL_DIR="$python_home/managed" "$uv_home/bin/uv" python install 3.11 || return 1
      local python_found
      python_found="$(UV_PYTHON_INSTALL_DIR="$python_home/managed" "$uv_home/bin/uv" python find --managed-python 3.11)" || return 1
      [[ -x "$python_found" ]] || { echo "CFOS: Python 3.11 unavailable: $python_found" >&2; return 1; }
      ln -sfn "$python_found" "$managed_python"
    fi
    export CFOS_PYTHON="$managed_python"
    export PATH="$python_home/bin:$PATH"
    echo "CFOS: using isolated Python: $CFOS_PYTHON"
  fi

  # Homebrew packages can be installed but their executables hidden from
  # Xcode's sanitized PATH.  Validate the actual shader tools in Setup.
  if (( ! CFOS_DRY_RUN )); then
    local brew_bin tool
    brew_bin="$(brew --prefix)/bin"
    for tool in meson ninja pkg-config glslangValidator spirv-as spirv-link; do
      if [[ ! -x "$brew_bin/$tool" ]] && ! command -v "$tool" >/dev/null 2>&1; then
        echo "CFOS: Setup missing $tool (Homebrew prefix: $(brew --prefix))" >&2
        return 1
      fi
    done
  fi

  # Homebrew's rustup formula provides the rustup executable directly.
  # Do not assume a rustup-init binary exists in the formula keg.
  # Provision the pinned toolchain in Setup, not during Xcode builds.
  if (( ! CFOS_DRY_RUN )); then
    local rustup_bin pinned_cargo pinned_rustc
    rustup_bin="$(brew --prefix)/bin/rustup"
    [[ -x "$rustup_bin" ]] || {
      echo "CFOS: rustup executable missing: $rustup_bin" >&2
      return 1
    }
    export PATH="$(brew --prefix)/bin:$HOME/.cargo/bin:$PATH"
    "$rustup_bin" toolchain install 1.90.0 --profile minimal || return 1
    "$rustup_bin" default 1.90.0 || return 1
    pinned_cargo="$("$rustup_bin" which --toolchain 1.90.0 cargo)" || return 1
    pinned_rustc="$("$rustup_bin" which --toolchain 1.90.0 rustc)" || return 1
    [[ -x "$pinned_cargo" && -x "$pinned_rustc" ]] || {
      echo 'CFOS: pinned Rust 1.90.0 cargo/rustc binaries unavailable' >&2
      return 1
    }
    export PATH="$(dirname "$pinned_cargo"):$PATH"
  else
    cfos_run rustup toolchain install 1.90.0 --profile minimal
  fi

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

  # Required maintained forks live beside CinterForgeOS, regardless of drive.
  # Never replace an existing checkout or silently accept an unrelated origin.
  local repo_name checkout remote answer branch
  local required_repos=(
    FEX
    cinterforge-kosmickrisp-mesa
    cinterforge-libkrun
    cinterforge-moltenvk
    cinterforge-spirv-cross
    cinterforge-virglrenderer
  )
  for repo_name in "${required_repos[@]}"; do
    if [[ "$repo_name" == FEX ]]; then
      checkout="${FEX_FORK_CHECKOUT:-$CFOS_WORKSPACE/FEX}"
      remote='https://github.com/CinterForge/FEX.git'
      branch='cinterforge/cinterforge'
    else
      checkout="$CFOS_WORKSPACE/$repo_name"
      remote="https://github.com/CinterForge/$repo_name.git"
      branch='cinterforge/cinterforge'
    fi
    if [[ ! -d "$checkout/.git" ]]; then
      if [[ -e "$checkout" ]]; then
        echo "CFOS: required repository path exists but is not a Git checkout: $checkout" >&2
        return 1
      fi
      echo "CFOS: required repository missing: $checkout" >&2
      echo "CFOS: clone $remote${branch:+ (branch $branch)}?" >&2
      if (( CFOS_DRY_RUN )); then
        cfos_run git clone ${branch:+--branch "$branch"} "$remote" "$checkout"
        continue
      fi
      # Setup may be invoked by a wrapper that redirects stdin. Prefer the
      # controlling terminal so an interactive user can still approve cloning.
      # Never assume consent in unattended runs.
      if [[ -r /dev/tty && -w /dev/tty ]]; then
        printf 'Clone repository and continue? [y/N]: ' > /dev/tty
        IFS= read -r answer < /dev/tty || return 1
      elif [[ -t 0 ]]; then
        read -r -p 'Clone repository and continue? [y/N]: ' answer || return 1
      else
        echo 'CFOS: no interactive terminal available; stopping without cloning.' >&2
        return 1
      fi
      case "$answer" in
        y|Y|yes|YES|Yes) ;;
        *) echo 'CFOS: repository clone declined; stopping Setup.' >&2; return 1 ;;
      esac
      if [[ -n "$branch" ]]; then
        cfos_run git clone --recurse-submodules --branch "$branch" "$remote" "$checkout" || return 1
      else
        cfos_run git clone --recurse-submodules "$remote" "$checkout" || return 1
      fi
    fi
    if (( ! CFOS_DRY_RUN )); then
      [[ -d "$checkout/.git" ]] || { echo "CFOS: invalid checkout: $checkout" >&2; return 1; }
      local actual_origin
      actual_origin="$(git -C "$checkout" remote get-url origin)" || return 1
      case "$actual_origin" in
        "https://github.com/CinterForge/$repo_name"|"https://github.com/CinterForge/$repo_name.git"|"git@github.com:CinterForge/$repo_name.git") ;;
        *) echo "CFOS: unexpected origin for $repo_name: $actual_origin" >&2; return 1 ;;
      esac
      if [[ "$(git -C "$checkout" symbolic-ref --quiet --short HEAD)" != "$branch" ]]; then
        echo "CFOS: $repo_name must be on branch $branch (currently $(git -C "$checkout" branch --show-current))" >&2
        # Never discard local modifications, staged changes, or untracked files.
        if [[ -n "$(git -C "$checkout" status --porcelain --untracked-files=all)" ]]; then
          echo "CFOS: $repo_name has local changes; refusing automatic branch checkout." >&2
          return 1
        fi
        if [[ -r /dev/tty && -w /dev/tty ]]; then
          printf 'Check out required branch and continue? [y/N]: ' > /dev/tty
          IFS= read -r answer < /dev/tty || return 1
        elif [[ -t 0 ]]; then
          read -r -p 'Check out required branch and continue? [y/N]: ' answer || return 1
        else
          echo 'CFOS: no interactive terminal available; stopping without switching branches.' >&2
          return 1
        fi
        case "$answer" in
          y|Y|yes|YES|Yes) ;;
          *) echo 'CFOS: branch checkout declined; stopping Setup.' >&2; return 1 ;;
        esac
        git -C "$checkout" fetch origin "$branch" || return 1
        if git -C "$checkout" show-ref --verify --quiet "refs/heads/$branch"; then
          git -C "$checkout" switch "$branch" || return 1
        else
          git -C "$checkout" switch --track -c "$branch" "origin/$branch" || return 1
        fi
        [[ "$(git -C "$checkout" symbolic-ref --quiet --short HEAD)" == "$branch" ]] || {
          echo "CFOS: failed to select required branch $branch for $repo_name" >&2
          return 1
        }
      fi
    fi
  done
  cfos_run bash "$CFOS_ROOT/guest/fex/build.sh" fetch
  cfos_run bash "$CFOS_ROOT/guest/fex/runtime.sh" fetch
  echo 'CFOS: Setup prepared source prerequisites; the build phase compiles the application.'
}
