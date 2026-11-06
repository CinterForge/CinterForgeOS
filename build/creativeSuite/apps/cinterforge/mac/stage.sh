#!/usr/bin/env bash
set -euo pipefail
: "${CINTER_APPLICATIONS_ROOT:?CINTER_APPLICATIONS_ROOT is required}"
config="${CINTER_BUILD_CONFIGURATION:-Debug}"
case "$config" in Debug|Release) ;; *) echo "ERROR: Unsupported configuration: $config" >&2; exit 2 ;; esac
root="$(cd "${CINTER_SCRIPTS_ROOT:?CINTER_SCRIPTS_ROOT is required}/../../../../.." && pwd)"
# Builds is a sibling of Cinter, not a source directory.
product="$(dirname "$root")/Builds/DerivedData/$config/CinterForge.app"
[[ -d "$product/Contents/MacOS" ]] || { echo "ERROR: Built CinterForge application missing: $product" >&2; exit 1; }
for helper in ubuntu_adapter.py managed_backend.py headless_contract.sh; do
  [[ -f "$product/Contents/Resources/Ubuntu/$helper" ]] || {
    echo "ERROR: Desktop build is missing bundled CFOS Ubuntu/$helper: $product" >&2
    exit 1
  }
done
target="$CINTER_APPLICATIONS_ROOT/Applications/mac/cinterforge/$config/CinterForge.app"
mkdir -p "$(dirname "$target")"
rm -rf "$target"
cp -R "$product" "$target"
echo "CINTER_CREATIVE_SUITE_STAGED app=cinterforge platform=mac configuration=$config path=$target"
