#!/usr/bin/env bash
# Shared CFOS Docker configuration and reboot-safe runtime activation.
# Setup provisions the profile; Build and Xcode activate it on demand.
cfos_colima_environment() {
  local root="${CFOS_ROOT:-${STEAMAC_SOURCE_ROOT:-}}"
  [[ -n "$root" ]] || { echo 'CFOS: source root not set' >&2; return 1; }
  root="$(cd "$root" && pwd -P)" || return 1
  local workspace="$(dirname "$root")"
  export CFOS_SHARED_BUILDS="$workspace/Builds"
  export COLIMA_HOME="$CFOS_SHARED_BUILDS/CFOS/Colima"
  export LIMA_HOME="$COLIMA_HOME/_lima"
  local agent_socket="$LIMA_HOME/colima-cinterforge-linux/ha.sock"
  if (( ${#agent_socket} >= 104 )); then
    echo "CFOS: Lima host-agent socket path too long: $agent_socket" >&2
    return 1
  fi
  export COLIMA_CACHE_HOME="$CFOS_SHARED_BUILDS/CreativeSuite/Built/cinterforge-os/mac/CFOS/Cache/Colima"
  export DOCKER_CONFIG="$CFOS_SHARED_BUILDS/CreativeSuite/Built/cinterforge-os/mac/CFOS/Docker"
  export DOCKER_HOST="unix://$COLIMA_HOME/cinterforge-linux/docker.sock"
  unset DOCKER_CONTEXT
}
# Lima records an exclusive owner for an additional disk. After the earlier
# CFOS path migrations, the disk can retain an owner equal to this *stopped*
# instance. This is a metadata ownership failure, not disk corruption.
# Never unlock a disk owned by a running instance or another profile.
cfos_colima_start() {
  local profile=cinterforge-linux
  local disk="colima-$profile"
  local log="$LIMA_HOME/colima-$profile/ha.stderr.log"
  local start_output log_bytes=0
  [[ ! -f "$log" ]] || log_bytes=$(wc -c < "$log")
  start_output="$(mktemp)" || return 1
  if colima start --profile "$profile" --activate=false "$@" >"$start_output" 2>&1; then
    cat "$start_output"
    rm -f "$start_output"
    return 0
  fi
  cat "$start_output" >&2
  rm -f "$start_output"
  if [[ -f "$log" ]] &&
     tail -c "+$((log_bytes + 1))" "$log" | grep -Fq "failed to attach disk \`$disk\`: in use by instance \`colima-$profile\`" &&
     ! colima status --profile "$profile" >/dev/null 2>&1; then
    echo "CFOS: Lima reports a stale self-attachment for stopped $profile; releasing its disk ownership" >&2
    if ! command -v limactl >/dev/null 2>&1; then
      echo 'CFOS: limactl unavailable; cannot repair disk ownership' >&2
      return 1
    fi
    # Disk data is not removed. The unlock only clears Lima's stale owner.
    limactl disk unlock "$disk" || return 1
    colima start --profile "$profile" --activate=false "$@" || return 1
    return 0
  fi
  echo "CFOS: Colima failed to start; inspect $log" >&2
  return 1
}

# LLVM/Mesa image extraction requires more space than the original 20 GiB VM.
# Colima's --disk flag does not resize an already-created VZ/Lima instance.
# This profile is disposable build infrastructure: recreate only this managed
# profile at the requested size, rather than repeatedly restarting an undersized
# filesystem. Authored source and staged outputs live outside Colima in Builds.
cfos_colima_ensure_disk() {
  local profile=cinterforge-linux target_gib=96
  local capacity_kib mount_root
  capacity_kib="$(colima ssh --profile "$profile" -- df -Pk /var/lib/containerd 2>/dev/null | awk 'NR==2 {print $2}')" || return 1
  [[ "$capacity_kib" =~ ^[0-9]+$ ]] || {
    echo 'CFOS: unable to measure managed containerd filesystem capacity' >&2
    return 1
  }
  # ext4 reserves metadata, so a 96 GiB disk exposes slightly less than 96 GiB.
  # 80 GiB is ample evidence that the new 96 GiB disk is mounted.
  if (( capacity_kib >= 80 * 1024 * 1024 )); then return 0; fi
  mount_root="$(dirname "${CFOS_ROOT:-${STEAMAC_SOURCE_ROOT:-}}")"
  [[ -d "$mount_root/CinterForgeOS" && -d "$mount_root/Builds" ]] || {
    echo "CFOS: cannot safely recreate managed VM: invalid workspace $mount_root" >&2
    return 1
  }
  echo "CFOS: managed container filesystem is $((capacity_kib / 1024 / 1024)) GiB; replacing undersized disposable Colima profile with ${target_gib} GiB"
  echo 'CFOS: Docker images and container caches in this managed profile will be rebuilt; source and Builds outputs are preserved'
  colima stop --profile "$profile" || return 1
  colima delete --profile "$profile" --force || return 1
  cfos_colima_start --arch aarch64 --vm-type vz --disk "$target_gib" --mount "$mount_root:w" || return 1
  capacity_kib="$(colima ssh --profile "$profile" -- df -Pk /var/lib/containerd 2>/dev/null | awk 'NR==2 {print $2}')" || return 1
  [[ "$capacity_kib" =~ ^[0-9]+$ ]] && (( capacity_kib >= 80 * 1024 * 1024 )) || {
    echo "CFOS: new managed Colima filesystem is still too small (${capacity_kib:-unknown} KiB); refusing to build" >&2
    return 1
  }
  echo "CFOS: managed containerd filesystem ready: $((capacity_kib / 1024 / 1024)) GiB"
}

cfos_colima_activate() {
  cfos_colima_environment || return 1
  command -v docker >/dev/null && command -v colima >/dev/null || {
    echo 'CFOS: docker/colima missing; run ./CFOS setup cinterforge-os mac Release' >&2; return 1;
  }
  [[ -d "$COLIMA_HOME" ]] || {
    echo 'CFOS: managed Colima not configured; run ./CFOS setup cinterforge-os mac Release' >&2; return 1;
  }
  if docker info >/dev/null 2>&1; then
    cfos_colima_ensure_disk || return 1
    return 0
  fi
  echo 'CFOS: activating managed Colima profile after shutdown/reboot'
  cfos_colima_start || return 1
  local attempt
  for ((attempt=0; attempt<30; attempt++)); do
    if docker info >/dev/null 2>&1; then
      cfos_colima_ensure_disk || return 1
      return 0
    fi
    sleep 2
  done
  echo "CFOS: Docker unavailable at $DOCKER_HOST" >&2
  return 1
}
