#!/usr/bin/env bash
# No real installed-application smoke contract has been implemented for this app.
set -euo pipefail
platform="${1:?Usage: test.sh <linux|mac|win> [headless|gui]}"
mode="${2:-gui}"
echo "ERROR: Installed application smoke test unsupported: app=code-oss platform=$platform mode=$mode" >&2
exit 1
