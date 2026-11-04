#!/bin/sh
# Cinter baseline [linux|win|all]
set -eu

scripts_directory=/run/steamapps/common/CinterForge/Applications/Test/Scripts
exec "$scripts_directory/baseline/baseline-environments.py" "$@"
