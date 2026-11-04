#!/bin/sh
# Cinter test [arguments...] — existing inspection behavior, externally editable.
set -eu

scripts_directory=/run/steamapps/common/CinterForge/Applications/Test/Scripts
exec "$scripts_directory/dispatch/cinter-commands.sh" test "$@"
