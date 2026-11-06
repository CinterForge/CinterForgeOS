#!/bin/sh
# Ardour macOS lifecycle entry point. Do not reference removed shared/cfaudio scripts.
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BUILDER="$SCRIPT_DIR/build.py"
if [ ! -f "$BUILDER" ]; then
    printf '[cfaudio][mac] ERROR: expected build recipe: %s\n' "$BUILDER" >&2
    exit 2
fi
if [ "${1:-}" = "--target" ]; then
    if [ "$#" -lt 2 ]; then
        echo '[cfaudio][mac] ERROR: --target requires a value' >&2
        exit 2
    fi
    TARGET=$2
    shift 2
else
    TARGET=mac
fi
printf '[cfaudio][mac] RUN %s --target %s\n' "$BUILDER" "$TARGET"
exec python3 -u "$BUILDER" --target "$TARGET" "$@"
