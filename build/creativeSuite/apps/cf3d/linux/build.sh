#!/usr/bin/env bash
# Blender Linux AMD64 is compiled from the supplied source, never a release archive.
set -euo pipefail
exec python3 -u "$(dirname -- "$0")/build.py"
