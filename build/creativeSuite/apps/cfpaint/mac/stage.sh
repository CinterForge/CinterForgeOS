#!/bin/sh
# cfpaint / mac: prebuilt acquisition until native source build is implemented.
set -eu
: "${CINTER_SCRIPTS_ROOT:?}"
exec sh "$CINTER_SCRIPTS_ROOT/env/stage_prebuilt.sh" "cfpaint" "mac"
