#!/usr/bin/env bash
set -euo pipefail
repo="${CINTER_WORKSPACE_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../../../.." && pwd -P)}/CinterForgeConverter"
builds="${CINTER_BUILDS_ROOT:-$(dirname "$repo")/Builds}"
[[ -x "$repo/CC" ]] || { echo "Missing canonical cfconverter lifecycle: $repo/CC" >&2; exit 1; }
exec "$repo/CC" stage linux --builds "$builds"
