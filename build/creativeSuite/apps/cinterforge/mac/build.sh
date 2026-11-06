#!/usr/bin/env bash
set -euo pipefail
# Native Desktop's macOS build is implemented once in the sibling Server.
# CFOS supplies the tested Ubuntu adapter; the Desktop owns its Xcode project.
config="${CINTER_BUILD_CONFIGURATION:-Debug}"
case "$config" in Debug|Release) ;; *) echo "ERROR: Unsupported Desktop configuration: $config" >&2; exit 2 ;; esac
[[ "$(uname -s)" == Darwin ]] || { echo 'ERROR: Native Desktop macOS builds require macOS.' >&2; exit 2; }
server_root="${CINTER_SERVER_ROOT:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../../../../.." && pwd -P)}"
builder="$server_root/cf/build_cinterforge/build_cinterforge.sh"
[[ -f "$builder" ]] || { echo "ERROR: Desktop builder missing in CinterForgeServer: $builder" >&2; exit 1; }
exec bash "$builder" "$config"
