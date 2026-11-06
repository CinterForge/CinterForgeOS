#!/usr/bin/env bash
set -euo pipefail
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mode="${1:?Usage: test.sh <headless|gui>}"
case "$mode" in
  headless)
    : "${CINTER_HOPE_ROOT:?Cinter must provide CINTER_HOPE_ROOT for Linux Debug tests}"
    version="${CINTER_INSTALL_VERSION:-${CINTER_APP_VERSION:-}}"
    [[ -n "$version" ]] || { echo 'ERROR: Cinter must resolve the installed Ardour version' >&2; exit 2; }
    exec python3 -u "$here/../../../shared/linux/headless_installed_smoke.py" cfaudio \
      --version "$version" --exe-glob 'lib/ardour9/ardour-[0-9]*'
    ;;
  gui)
    exec python3 -u "$here/../../../shared/installed_guest_smoke.py" linux \
      'CinterForge Audio' 'cfaudio' --mode gui --exe 'bin/ardour9'
    ;;
  *)
    echo "ERROR: Ardour Linux test mode must be headless (Debug) or gui (Release), got: $mode" >&2
    exit 2
    ;;
esac
