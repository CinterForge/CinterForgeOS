#!/bin/sh
# code-oss / mac: prebuilt acquisition until native source build is implemented.
set -eu
: "${CINTER_SCRIPTS_ROOT:?}"
exec sh "$CINTER_SCRIPTS_ROOT/env/build_prebuilt.sh" "code-oss" "mac"
