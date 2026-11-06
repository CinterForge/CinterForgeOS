#!/bin/sh
# cfpaint / win: prebuilt acquisition until native source build is implemented.
set -eu
: "${CINTER_SCRIPTS_ROOT:?}"
exec "$CINTER_SCRIPTS_ROOT/env/build_prebuilt.sh" "cfpaint" "win"
