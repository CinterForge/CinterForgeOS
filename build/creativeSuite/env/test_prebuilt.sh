#!/bin/sh
set -eu
app=$1 platform=$2
: "${CINTER_APPLICATIONS_ROOT:?}"
target="$CINTER_APPLICATIONS_ROOT/Applications/$platform/$app"
[ -d "$target" ] && [ -n "$(find "$target" -type f -print -quit)" ] || { echo "No staged application: $app/$platform" >&2; exit 4; }
printf 'CINTER_CREATIVE_SUITE_ARTIFACT_PRESENT app=%s platform=%s (execution not verified)\n' "$app" "$platform"
