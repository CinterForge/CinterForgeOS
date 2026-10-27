#!/usr/bin/env bash
# Xcode's environment is not an interactive shell. Select the existing
# CinterForge managed Colima daemon explicitly, never ~/.colima or default Docker.
# This file is sourced by Xcode's bundle phase, after STEAMAC_SOURCE_ROOT is set.
if [[ -z "${STEAMAC_SOURCE_ROOT:-}" ]]; then
  echo 'error: STEAMAC_SOURCE_ROOT must be set before loading xcode-docker-env.sh' >&2
  return 1
fi
_CINTER_WORKSPACE="$(cd "$STEAMAC_SOURCE_ROOT/.." && pwd -P)"
# Use the same *physical*, short Colima home as build/setup.sh.
# A /tmp symlink to the long path is not sufficient: Lima resolves symlinks
# when constructing its Unix socket paths.
_CINTER_COLIMA_HOME="$_CINTER_WORKSPACE/Builds/CFOS/Colima"
_CINTER_COLIMA_VIEW="$_CINTER_WORKSPACE/Builds/CreativeSuite/Built/cinterforge-os/mac/CFOS/Colima"
_CINTER_PROFILE='cinterforge-linux'
_CINTER_SOCKET="$_CINTER_COLIMA_HOME/$_CINTER_PROFILE/docker.sock"
if [[ ! -d "$_CINTER_COLIMA_HOME" ]]; then
  echo "error: Managed Colima home missing: $_CINTER_COLIMA_HOME; run ./CFOS setup first." >&2
  return 1
fi
if [[ ! -L "$_CINTER_COLIMA_VIEW" || "$(readlink "$_CINTER_COLIMA_VIEW")" != "$_CINTER_COLIMA_HOME" ]]; then
  echo "error: Colima build-folder link is missing or incorrect: $_CINTER_COLIMA_VIEW; run ./CFOS setup first." >&2
  return 1
fi
export COLIMA_HOME="$_CINTER_COLIMA_HOME"
export LIMA_HOME="$_CINTER_COLIMA_HOME/_lima"
export COLIMA_CACHE_HOME="$_CINTER_WORKSPACE/Builds/CreativeSuite/Built/cinterforge-os/mac/CFOS/Cache/Colima"
export DOCKER_CONFIG="$_CINTER_WORKSPACE/Builds/CreativeSuite/Built/cinterforge-os/mac/CFOS/Docker"
export DOCKER_HOST="unix://$_CINTER_SOCKET"
unset DOCKER_CONTEXT
printf '[Xcode Docker] Managed profile: %s\n[Xcode Docker] Socket: %s\n' "$_CINTER_PROFILE" "$_CINTER_SOCKET"
if (( ${#_CINTER_COLIMA_HOME} + ${#_CINTER_PROFILE} + 36 >= 104 )); then
  echo "error: Managed Colima home is too long for Lima sockets: $_CINTER_COLIMA_HOME" >&2
  return 1
fi
if ! command -v docker >/dev/null 2>&1 || ! command -v colima >/dev/null 2>&1; then
  echo 'error: docker and colima must be installed and available to Xcode.' >&2
  return 1
fi
if [[ ! -S "$_CINTER_SOCKET" ]]; then
  echo "[Xcode Docker] Managed daemon not running; starting existing $_CINTER_PROFILE profile"
  if ! colima start --profile "$_CINTER_PROFILE" --activate=false; then
    echo "error: Could not start existing managed Colima profile $_CINTER_PROFILE; preserved VM data." >&2
    return 1
  fi
fi
if [[ ! -S "$_CINTER_SOCKET" ]]; then
  echo "error: Managed Colima socket still missing: $_CINTER_SOCKET" >&2
  return 1
fi
if ! docker info --format '[Xcode Docker] Docker server: {{.ServerVersion}}'; then
  echo "error: Docker daemon unavailable at $DOCKER_HOST" >&2
  return 1
fi
unset _CINTER_WORKSPACE _CINTER_COLIMA_HOME _CINTER_COLIMA_VIEW _CINTER_PROFILE _CINTER_SOCKET
