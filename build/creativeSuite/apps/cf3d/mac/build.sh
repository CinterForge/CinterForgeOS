#!/bin/sh
# Native Blender source build: dependencies must have passed Setup.
set -eu
exec python3 -u "$(dirname -- "$0")/build.py"
