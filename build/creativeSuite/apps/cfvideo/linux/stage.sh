#!/bin/sh
set -eu
: "${ROOT:?}" "${DEBIAN:?}"
source_dir="$ROOT/Installed/Kdenlive"
target="$ROOT/Applications/Linux/Kdenlive"
mkdir -p "$target"
# Copy only an acquired application, never synthesize a success marker.
if [ -n "$(find "$source_dir" -mindepth 1 -print -quit 2>/dev/null)" ]; then
  cp -a "$source_dir/." "$target/"
elif [ -n "$(find "$DEBIAN/usr/bin" -maxdepth 1 -name 'cfvideo' -print -quit 2>/dev/null)" ]; then
  # Debian packages remain in persistent ext4; record actual installed executable.
  printf '%s\n' '/usr/bin/cfvideo' > "$target/debian-executable.txt"
else
  echo 'CINTER_CREATIVE_SUITE_ERROR no-acquired-app-to-stage Kdenlive' >&2
  exit 3
fi
echo 'CINTER_CREATIVE_SUITE_STAGED app=Kdenlive'
