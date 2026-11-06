#!/usr/bin/env bash
set -euo pipefail
platform="${1:?Usage: test.sh <linux|mac|win> [headless|gui]}"
mode="${2:-headless}"
[[ "$mode" == "headless" ]] || { echo 'Converter CLI tests are headless; GUI acceptance belongs to the integrated WebApp' >&2; exit 2; }
repo="${CINTER_WORKSPACE_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../../../.." && pwd -P)}/CinterForgeConverter"
exec "$repo/CC" test "$platform" --builds "${CINTER_BUILDS_ROOT:-$(dirname "$repo")/Builds}"
