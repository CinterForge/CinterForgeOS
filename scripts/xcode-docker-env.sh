#!/usr/bin/env bash
# Xcode's environment is not an interactive shell. Select the existing
# CinterForge managed Colima daemon explicitly, never ~/.colima or default Docker.
# This file is sourced by Xcode's bundle phase, after STEAMAC_SOURCE_ROOT is set.
if [[ -z "${STEAMAC_SOURCE_ROOT:-}" ]]; then
  echo 'error: STEAMAC_SOURCE_ROOT must be set before loading xcode-docker-env.sh' >&2
  return 1
fi
# Use exactly the same activation and paths as ./CFOS build.
source "$STEAMAC_SOURCE_ROOT/scripts/colima-runtime.sh"
cfos_colima_activate || return 1
printf '[Xcode Docker] Managed profile: cinterforge-linux\n[Xcode Docker] Socket: %s\n' "$DOCKER_HOST"
docker info --format '[Xcode Docker] Docker server: {{.ServerVersion}}' || return 1
