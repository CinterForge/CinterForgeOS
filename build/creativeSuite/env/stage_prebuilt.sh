#!/bin/sh
set -eu
app=$1 platform=$2
: "${CINTER_APPLICATIONS_ROOT:?}"
source="$CINTER_APPLICATIONS_ROOT/Acquired/$platform/$app"
target="$CINTER_APPLICATIONS_ROOT/Applications/$platform/$app"
[ -d "$source" ] && [ -n "$(find "$source" -mindepth 1 -print -quit)" ] || { echo "No acquired application: $app/$platform" >&2; exit 3; }
mkdir -p "$target"
cp -R "$source/." "$target/"
printf 'CINTER_CREATIVE_SUITE_PREBUILT_STAGED app=%s platform=%s\n' "$app" "$platform"
