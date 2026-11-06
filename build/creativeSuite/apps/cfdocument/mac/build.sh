#!/bin/sh
# Native upstream macOS source compilation; no prebuilt acquisition.
set -eu
: "${CINTER_SCRIPTS_ROOT:?}"
exec python3 -u "$CINTER_SCRIPTS_ROOT/shared/mac/mac_source_pipeline.py" build cfdocument
