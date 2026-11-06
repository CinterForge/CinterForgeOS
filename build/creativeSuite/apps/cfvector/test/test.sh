#!/usr/bin/env bash
# Shared installed-application smoke contract for cfvector.
# The existing Linux guest runner creates and validates real native/export files.
# Platform-specific launch and environment preparation stay outside this test.
set -euo pipefail
platform="${1:?Usage: test.sh <linux|mac|win> [headless|gui]}"
mode="${2:-gui}"
case "$platform:$mode" in
  linux:headless)
    if [[ "$(uname -s)" != Darwin ]]; then
      echo 'ERROR: Linux guest smoke requires the macOS LinuxKit host.' >&2
      exit 1
    fi
    : "${CINTER_HOPE_ROOT:?CINTER_HOPE_ROOT is required}"
    : "${CINTER_SCRIPTS_ROOT:?CINTER_SCRIPTS_ROOT is required}"
    python_bin="${CINTER_HOPE_ROOT}/../Builds/cinterenv/bin/python"
    smoke="${CINTER_SCRIPTS_ROOT}/shared/linux/linux_smoke_tests.py"
    guest="${CINTER_SCRIPTS_ROOT}/shared/linux/linux_guest.py"
    if [[ ! -x "$python_bin" || ! -f "$smoke" || ! -f "$guest" ]]; then
      echo 'ERROR: Installed Linux smoke runner or Python environment missing.' >&2
      exit 1
    fi
    exec "$python_bin" -u "$smoke" --repo-root "$CINTER_HOPE_ROOT" --guest-runner "$guest" --skip-prepare --application 'Inkscape'
    ;;
  linux:gui|mac:gui|win:gui|mac:headless|win:headless)
    echo "ERROR: Real installed smoke test is not implemented for cfvector platform=$platform mode=$mode." >&2
    exit 1
    ;;
  *)
    echo "ERROR: Unsupported smoke target: platform=$platform mode=$mode" >&2
    exit 2
    ;;
esac
