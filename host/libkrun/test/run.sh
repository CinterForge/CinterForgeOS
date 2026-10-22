#!/bin/sh
# Build, sign (com.apple.security.hypervisor) and run the libkrun configuration smoke
# test against work/out/host.
set -eu

here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../../.." && pwd)
STEAMAC_SOURCE_ROOT="$root"
. "$STEAMAC_SOURCE_ROOT/scripts/work-path.sh"
out=$STEAMAC_WORK_DIR/out/host
bin=$STEAMAC_WORK_DIR/build/host-libkrun/smoke

mkdir -p "$(dirname "$bin")"
clang -std=c11 -Wall -Wextra -Werror -o "$bin" "$here/smoke.c" \
	-I"$out/include" -L"$out/lib" -lkrun -Wl,-rpath,"$out/lib"
codesign --force -s - --entitlements "$here/hypervisor.entitlements" "$bin"
"$bin"
