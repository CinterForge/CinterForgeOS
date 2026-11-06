#!/usr/bin/env bash
# Self-contained Creative Suite build adapter for code-oss; no legacy pipeline calls.
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$HERE/../../../shared/linux/linux_app_build.py" code-oss
