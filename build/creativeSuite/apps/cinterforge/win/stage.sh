#!/usr/bin/env bash
set -euo pipefail
: "${CINTER_SCRIPTS_ROOT:?}"
exec bash "$CINTER_SCRIPTS_ROOT/env/stage_prebuilt.sh" cinterforge win
