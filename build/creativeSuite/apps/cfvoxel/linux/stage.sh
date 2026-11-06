#!/usr/bin/env bash
# Stage is owned by creativeSuite/lifecycle.py; do not install or overwrite anything here.
set -euo pipefail
printf '%s\n' 'CinterForge blockbench: ./Cinter stage uses the shared ZIP-backed lifecycle; no legacy ROOT/DEBIAN stage.'


#!/bin/sh
set -eu
: "${ROOT:?}" "${DEBIAN:?}"
source_dir="$ROOT/Installed/Blockbench"
target="$ROOT/Applications/Linux/Blockbench"
mkdir -p "$target"
# Copy only an acquired application, never synthesize a success marker.
if [ -n "$(find "$source_dir" -mindepth 1 -print -quit 2>/dev/null)" ]; then
  cp -a "$source_dir/." "$target/"
elif [ -n "$(find "$DEBIAN/usr/bin" -maxdepth 1 -name 'nonexistent-package' -print -quit 2>/dev/null)" ]; then
  # Debian packages remain in persistent ext4; record actual installed executable.
  printf '%s\n' '/usr/bin/' > "$target/debian-executable.txt"
else
  echo 'CINTER_CREATIVE_SUITE_ERROR no-acquired-app-to-stage Blockbench' >&2
  exit 3
fi
echo 'CINTER_CREATIVE_SUITE_STAGED app=Blockbench'
