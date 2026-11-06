#!/bin/sh
# cfvideo / win: prebuilt acquisition until native source build is implemented.
set -eu
: "${CINTER_SCRIPTS_ROOT:?}"
exec "$CINTER_SCRIPTS_ROOT/env/stage_prebuilt.sh" "cfvideo" "win"
