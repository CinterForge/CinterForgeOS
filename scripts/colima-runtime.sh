#!/usr/bin/env bash
# Shared CFOS Docker configuration and reboot-safe runtime activation.
# Setup provisions the profile; Build and Xcode activate it on demand.
cfos_colima_environment() {
  local root="${CFOS_ROOT:-${STEAMAC_SOURCE_ROOT:-}}"
  [[ -n "$root" ]] || { echo 'CFOS: source root not set' >&2; return 1; }
  root="$(cd "$root" && pwd -P)" || return 1
  local workspace="$(dirname "$root")"
  export COLIMA_HOME="$workspace/Builds/CFOS/Colima"
  export LIMA_HOME="$COLIMA_HOME/_lima"
  local agent_socket="$LIMA_HOME/colima-cinterforge-linux/ha.sock"
  if (( ${#agent_socket} >= 104 )); then
    echo "CFOS: Lima host-agent socket path too long: $agent_socket" >&2
    return 1
  fi
  export COLIMA_CACHE_HOME="$workspace/Builds/CreativeSuite/Built/cinterforge-os/mac/CFOS/Cache/Colima"
  export DOCKER_CONFIG="$workspace/Builds/CreativeSuite/Built/cinterforge-os/mac/CFOS/Docker"
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
  local start_output
  # Lima may append to, truncate, or replace its stderr log at startup.
  # Keep the previous log separately so only THIS attempt can authorize
  # clearing a stale self-attachment (never infer it from historical errors).
  if [[ -f "$log" ]]; then
    mv -- "$log" "$log.cfos-previous" || return 1
  fi
  start_output="$(mktemp)" || return 1
  if colima start --profile "$profile" --activate=false "$@" >"$start_output" 2>&1; then
    cat "$start_output"
    rm -f "$start_output"
    return 0
  fi
  cat "$start_output" >&2
  rm -f "$start_output"
  if [[ -f "$log" ]] &&
     grep -Fq "failed to attach disk \`$disk\`: in use by instance \`colima-$profile\`" "$log" &&
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

cfos_colima_activate() {
  cfos_colima_environment || return 1
  command -v docker >/dev/null && command -v colima >/dev/null || {
    echo 'CFOS: docker/colima missing; run ./CFOS setup cinterforge-os mac Release' >&2; return 1;
  }
  [[ -d "$COLIMA_HOME" ]] || {
    echo 'CFOS: managed Colima not configured; run ./CFOS setup cinterforge-os mac Release' >&2; return 1;
  }
  if docker info >/dev/null 2>&1; then return 0; fi
  echo 'CFOS: activating managed Colima profile after shutdown/reboot'
  cfos_colima_start || return 1
  local attempt
  for ((attempt=0; attempt<30; attempt++)); do
    if docker info >/dev/null 2>&1; then return 0; fi
    sleep 2
  done
  echo "CFOS: Docker unavailable at $DOCKER_HOST" >&2
  return 1
}
