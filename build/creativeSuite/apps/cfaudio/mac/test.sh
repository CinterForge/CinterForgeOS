#!/usr/bin/env bash
# macOS launch smoke: open the staged, installed Ardour application.
set -euo pipefail
[[ "$(uname -s)" == Darwin ]] || { echo "Ardour mac test requires macOS" >&2; exit 1; }
mode="${1:-gui}"
[[ "$mode" == gui ]] || { echo "Ardour mac test supports GUI launch only" >&2; exit 2; }
# Resolve the active CinterForge Steam library via the same authoritative
# Steam libraryfolders.vdf discovery used by staging (including external disks).
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
steamapps="$(python3 "$script_dir/../../../lifecycle.py" resolve-steamapps cfaudio mac)"
install_root="$steamapps/common/CinterForge/Applications/macOS/CinterForge Audio"
[[ -d "$install_root" ]] || { echo "Not installed: $install_root (run stage first)" >&2; exit 1; }
if [[ -n "${CINTER_INSTALL_VERSION:-${CINTER_APP_VERSION:-}}" ]]; then
  version="${CINTER_INSTALL_VERSION:-${CINTER_APP_VERSION}}"
  [[ "$version" != *'/'* && "$version" != . && "$version" != .. ]] || { echo "Invalid app version: $version" >&2; exit 2; }
  installed="$install_root/$version"
else
  # Prefer the newest staged version; don't launch anything from Builds.
  installed="$(python3 - "$install_root" <<'PYVERS'
from pathlib import Path
import re
import sys
root = Path(sys.argv[1])
versions = [p for p in root.iterdir() if p.is_dir() and not p.name.startswith('.')]
def key(path):
    return tuple(int(n) for n in re.findall(r'\d+', path.name))
print(max(versions, key=key) if versions else '')
PYVERS
)"
fi
[[ -d "$installed" ]] || { echo "Not installed: $installed (run stage first)" >&2; exit 1; }
shopt -s nullglob
apps=("$installed"/*.app)
[[ ${#apps[@]} -eq 1 ]] || { echo "Expected exactly one installed Ardour .app in $installed" >&2; exit 1; }
[[ -f "${apps[0]}/Contents/Info.plist" ]] || { echo "Incomplete app: ${apps[0]}" >&2; exit 1; }
echo "[cfaudio][mac][test] Launching ${apps[0]}"
/usr/bin/open -n -a "${apps[0]}"
