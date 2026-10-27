##!/usr/bin/env bash
## Build the developer-owned, patched macOS libkrun into the external work tree.
## Xcode links against out/host/lib and must never require a build in the source checkout.
#set -euo pipefail
#
#source_root="$(cd "$(dirname "$0")/.." && pwd)"
#export STEAMAC_ROOT="$source_root"
#source "$source_root/scripts/paths.sh"
#
#fork="${CINTERFORGE_LIBKRUN_SOURCE:-$(dirname "$source_root")/cinterforge-libkrun}"
#build_source="$STEAMAC_WORK_DIR/build/host-libkrun/src"
#prefix="$STEAMAC_WORK_DIR/out/host"
#log="$STEAMAC_WORK_DIR/build/host-libkrun/build.log"
#
#if [[ ! -f "$fork/Makefile" ]]; then
#    echo "error: Patched libkrun checkout is required at $fork" >&2
#    echo "error: Set CINTERFORGE_LIBKRUN_SOURCE to its actual location. Do not use unpatched system libkrun." >&2
#    exit 1
#fi
#
#source "$source_root/scripts/host-source-identity.sh"
#identity_file="$(dirname "$log")/source.sha256"
#virgl_lib="$prefix/lib/libvirglrenderer.1.dylib"
#[[ -s "$virgl_lib" ]] || { echo "error: virglrenderer must be built before libkrun: $virgl_lib" >&2; exit 1; }
#source_identity="$(cfos_combine_identity "$(cfos_source_identity "$fork")" "$(shasum -a 256 "$virgl_lib" | awk '{print $1}')" "$(shasum -a 256 "$0" | awk '{print $1}')")"
#
#if [[ -s "$prefix/lib/libkrun.1.dylib" && -s "$prefix/include/libkrun.h" && -s "$prefix/include/libkrun_display.h" && -s "$prefix/include/libkrun_input.h" && -s "$identity_file" ]] && \
#   [[ "$(cat "$identity_file")" == "$source_identity" ]]; then
#    if file "$prefix/lib/libkrun.1.dylib" | grep -q 'arm64'; then
#        [[ -e "$prefix/lib/libkrun.dylib" ]] || ln -s libkrun.1.dylib "$prefix/lib/libkrun.dylib"
#        echo "[Xcode] macOS arm64 libkrun already staged: $prefix/lib/libkrun.1.dylib"
#        exit 0
#    fi
#    echo "error: Existing libkrun is not macOS arm64: $prefix/lib/libkrun.1.dylib" >&2
#    exit 1
#fi
#
#command -v make >/dev/null || { echo 'error: make is required to build libkrun' >&2; exit 1; }
#command -v rsync >/dev/null || { echo 'error: rsync is required to stage libkrun sources' >&2; exit 1; }
#mkdir -p "$(dirname "$log")" "$prefix"
## Copy the authoritative patched checkout, never compile into ~/Source/cinterforge-libkrun.
#rsync -a --delete --exclude=.git --exclude=target --exclude=build "$fork/" "$build_source/"
#
## Match the existing CKrun API and GPU/input/blk/net requirements. Build output
## stays in external Builds, and installation must not write to /usr/local.
#export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
## CFOS Setup owns Homebrew installation. Xcode only consumes the
## provisioned pkg-config metadata; it must not invoke brew.
#command -v pkg-config >/dev/null || { echo "error: pkg-config missing; run CFOS Setup" >&2; exit 1; }
#epoxy_pc="/opt/homebrew/opt/libepoxy/lib/pkgconfig/epoxy.pc"
#[[ -f "$epoxy_pc" ]] || { echo "error: libepoxy metadata missing: $epoxy_pc; run CFOS Setup" >&2; exit 1; }
#export PKG_CONFIG_PATH="$(dirname "$epoxy_pc")${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
#pkg-config --exists epoxy || { echo "error: pkg-config cannot resolve epoxy; run CFOS Setup" >&2; exit 1; }
#echo "[Xcode] Host libepoxy: $(pkg-config --modversion epoxy)"
#
## The GPU-enabled rutabaga crate also requires the patched macOS
## virglrenderer. Prefer the already-installed dependency in our external
## host prefix; never accidentally link an unrelated Linux package.
#virgl_pc="$prefix/lib/pkgconfig/virglrenderer.pc"
#if [[ ! -f "$virgl_pc" ]]; then
#    echo "error: Patched macOS virglrenderer is not staged: $virgl_pc" >&2
#    echo "error: Build/install the cinterforge-virglrenderer fork into $prefix before building libkrun." >&2
#    echo "error: GPU=1 cannot compile without its virglrenderer headers, library, and pkg-config file." >&2
#    exit 1
#fi
#export PKG_CONFIG_PATH="$prefix/lib/pkgconfig:$PKG_CONFIG_PATH"
#if ! pkg-config --exists virglrenderer; then
#    echo "error: pkg-config cannot resolve staged virglrenderer: $virgl_pc" >&2
#    exit 1
#fi
#[[ -s "$prefix/lib/libvirglrenderer.1.dylib" ]] || {
#    echo "error: Patched libvirglrenderer.1.dylib missing from $prefix/lib" >&2
#    exit 1
#}
#file "$prefix/lib/libvirglrenderer.1.dylib" | grep -q 'arm64' || {
#    echo "error: Patched virglrenderer library is not macOS arm64" >&2
#    exit 1
#}
#echo "[Xcode] Host virglrenderer: $(pkg-config --modversion virglrenderer) ($prefix)"
#
## imago 0.2.3 requires Rust >= 1.87. CFOS Setup provides Rust 1.90.0,
## but Xcode may use Homebrew cargo without a ~/.cargo/bin rustup proxy.
## Resolve the actual binaries installed by rustup instead of requiring proxies.
#required_rust="1.90.0"
#if command -v rustup >/dev/null 2>&1; then
#    toolchain_cargo="$(rustup which --toolchain "$required_rust" cargo)" || {
#        echo "error: Cargo for Rust $required_rust unavailable; run CFOS Setup" >&2
#        exit 1
#    }
#    toolchain_rustc="$(rustup which --toolchain "$required_rust" rustc)" || {
#        echo "error: rustc for Rust $required_rust unavailable; run CFOS Setup" >&2
#        exit 1
#    }
#    [[ -x "$toolchain_cargo" && -x "$toolchain_rustc" ]] || {
#        echo "error: Rust $required_rust toolchain binaries are not executable" >&2
#        exit 1
#    }
#    export PATH="$(dirname "$toolchain_cargo"):$PATH"
#    export RUSTC="$toolchain_rustc"
#    export RUSTUP_TOOLCHAIN="$required_rust"
#else
#    command -v cargo >/dev/null || { echo "error: cargo missing; run CFOS Setup" >&2; exit 1; }
#    command -v rustc >/dev/null || { echo "error: rustc missing; run CFOS Setup" >&2; exit 1; }
#    rust_version="$(rustc --version | awk '{print $2}')"
#    if ! awk -v version="$rust_version" 'BEGIN { split(version, v, "."); exit !(v[1] > 1 || (v[1] == 1 && v[2] >= 87)) }'; then
#        echo "error: Rust >= 1.87 required by imago; found $rust_version; run CFOS Setup" >&2
#        exit 1
#    fi
#fi
#echo "[Xcode] Host Rust: $("${RUSTC:-rustc}" --version); Cargo: $(cargo --version)"
#
#(
#    cd "$build_source"
#    # Xcode exports GCC_VERSION as its Apple compiler identifier, while libkrun's
#    # Makefile uses GCC_VERSION for Debian bookworm's cross-compile libgcc package.
#    # Specify the Debian GCC major version for both make invocations so Xcode's
#    # unrelated setting cannot become a bogus libgcc-<Xcode identifier>-dev name.
#    make GCC_VERSION=12 GPU=1 INPUT=1 BLK=1 NET=1 SND=1 PREFIX="$prefix"
#    make install GCC_VERSION=12 GPU=1 INPUT=1 BLK=1 NET=1 SND=1 PREFIX="$prefix"
#) > "$log" 2>&1 || {
#    echo "error: libkrun build failed; last 35 log lines (full log: $log)" >&2
#    tail -n 35 "$log" >&2
#    exit 1
#}
#
#for header in libkrun.h libkrun_display.h libkrun_input.h; do
#    [[ -s "$prefix/include/$header" ]] || { echo "error: libkrun build did not install $header; see $log" >&2; exit 1; }
#done
#[[ -s "$prefix/lib/libkrun.1.dylib" ]] || { echo "error: libkrun build did not install libkrun.1.dylib; see $log" >&2; exit 1; }
#file "$prefix/lib/libkrun.1.dylib" | grep -q 'arm64' || { echo "error: Installed libkrun is not arm64; see $log" >&2; exit 1; }
## The linker resolves -lkrun, whereas the bundle copies libkrun.1.dylib.
#[[ -e "$prefix/lib/libkrun.dylib" ]] || ln -s libkrun.1.dylib "$prefix/lib/libkrun.dylib"
#printf '%s\n' "$source_identity" > "$identity_file"
#echo "[Xcode] macOS arm64 libkrun ready: $prefix/lib/libkrun.1.dylib"
