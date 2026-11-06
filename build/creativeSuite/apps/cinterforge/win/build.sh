#!/usr/bin/env bash
set -euo pipefail
: "${CINTER_SCRIPTS_ROOT:?}"
# Windows CinterForge is acquired from the published artifact, not compiled.
# The caller must supply the actual published Cloudflare URL and SHA-256.
if [[ -z "${CINTER_APP_URL:-}" && -z "${CINTER_APP_ARCHIVE:-}" ]]; then
  echo 'ERROR: CinterForge Windows requires a published Cloudflare artifact URL (CINTER_APP_URL, CINTER_APP_SHA256, CINTER_APP_FORMAT) or a verified local archive (CINTER_APP_ARCHIVE).' >&2
  exit 2
fi
exec bash "$CINTER_SCRIPTS_ROOT/env/build_prebuilt.sh" cinterforge win
