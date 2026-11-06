#!/bin/sh
# Compile supplied cfphoto source with pinned Tauri/Rust toolchain.
set -eu
: "${CINTER_SCRIPTS_ROOT:?}"
exec python3 -u "$CINTER_SCRIPTS_ROOT/shared/mac/mac_source_pipeline.py" build cfphoto
