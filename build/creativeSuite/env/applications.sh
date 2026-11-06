#!/bin/sh
set -eu
script_dir=/usr/local/lib/cinter-creative-suite/apps/$app/linux
case "$operation" in
  build|build-cfaudio|install)
    # build-cfaudio retains its job name but now uses per-app build scripts.
    "$script_dir/build.sh"
    "$script_dir/stage.sh"
    ;;
  stage) "$script_dir/stage.sh" ;;
  smoke|test) "$script_dir/../test/test.sh" linux "${CINTER_TEST_MODE:-headless}" ;;
  *) echo "CINTER_CREATIVE_SUITE_ERROR unsupported-operation $operation" >&2; finish 73 ;;
esac
finish 0
