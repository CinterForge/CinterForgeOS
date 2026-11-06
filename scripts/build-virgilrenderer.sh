#!/usr/bin/env bash
# Build the authoritative patched macOS virglrenderer fork for libkrun's GPU backend.
# Adapted from the supplied historical host/virglrenderer/build.sh: ownership now
# resides in scripts/ and the patched fork is an external source checkout.
set -euo pipefail

source_root="$(cd "$(dirname "$0")/.." && pwd)"
export STEAMAC_ROOT="$source_root"
source "$source_root/scripts/paths.sh"

fork="${CINTERFORGE_VIRGLRENDERER_SOURCE:-$(dirname "$source_root")/cinterforge-virglrenderer}"
work="$STEAMAC_WORK_DIR/build/host-virglrenderer"
src="$fork"
build="$work/build"
stage="$work/stage"
prefix="$STEAMAC_WORK_DIR/out/host"
log="$work/build.log"
lib="$prefix/lib/libvirglrenderer.1.dylib"
pc="$prefix/lib/pkgconfig/virglrenderer.pc"

[[ -f "$fork/meson.build" ]] || {
    echo "error: Patched virglrenderer checkout missing meson.build: $fork" >&2
    exit 1
}

source "$source_root/scripts/host-source-identity.sh"
identity_file="$work/source.sha256"
source_identity="$(cfos_combine_identity "$(cfos_source_identity "$fork")" "$(shasum -a 256 "$0" | awk '{print $1}')")"

# Reuse only complete, architecture-correct installations. Missing metadata or
# an incorrect architecture must trigger a rebuild rather than a false success.
if [[ -s "$lib" && -s "$pc" && -d "$prefix/include" && -s "$identity_file" ]] && \
   [[ "$(cat "$identity_file")" == "$source_identity" ]] && file "$lib" | grep -q 'arm64'; then
    echo "[Xcode] macOS arm64 virglrenderer already staged: $lib"
    exit 0
fi

export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
for tool in meson ninja pkg-config python3 clang install_name_tool otool codesign; do
    command -v "$tool" >/dev/null || { echo "error: $tool missing; run CFOS Setup" >&2; exit 1; }
done
# Homebrew provisioning and formula validation belong to CFOS Setup.
# Build only consumes the installed metadata and reports missing prerequisites.
epoxy_pc="/opt/homebrew/opt/libepoxy/lib/pkgconfig/epoxy.pc"
[[ -f "$epoxy_pc" ]] || {
    echo "error: libepoxy metadata missing: $epoxy_pc; run CFOS Setup" >&2
    exit 1
}
export PKG_CONFIG_PATH="$(dirname "$epoxy_pc")${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
pkg-config --exists epoxy || { echo 'error: pkg-config cannot resolve epoxy; run CFOS Setup' >&2; exit 1; }

# Build from the existing on-disk fork. Only build artifacts live in Builds.
mkdir -p "$work" "$prefix"
echo "[Xcode] Building virglrenderer from $src"

# Prepare the generator dependencies before resolving Meson subprojects.
if [[ ! -x "$work/venv/bin/python3" ]]; then
    python3 -m venv "$work/venv"
fi
"$work/venv/bin/python3" -m pip install 'pyyaml==6.0.3' 'mako==1.3.10' > "$work/python-deps.log" 2>&1 || {
    cat "$work/python-deps.log" >&2
    exit 1
}

# Resolve the fork's pinned Meson subproject directly. This may create
# dependency files in the checkout; it never duplicates the source repository.
wrap="$src/subprojects/venus-protocol.wrap"
[[ -f "$wrap" ]] || {
    echo "error: Patched virglrenderer is missing its venus-protocol.wrap: $fork/subprojects" >&2
    exit 1
}
wrap_directory="$(sed -n 's/^[[:space:]]*directory[[:space:]]*=[[:space:]]*//p' "$wrap" | head -n 1 | tr -d '\r')"
wrap_directory="${wrap_directory:-venus-protocol}"
[[ -n "$wrap_directory" && "$wrap_directory" != /* && "$wrap_directory" != *..* && "$wrap_directory" != */* ]] || {
    echo "error: Invalid venus-protocol wrap directory in $wrap: $wrap_directory" >&2
    exit 1
}

# An incomplete extraction must never short-circuit Meson's wrap resolver.
if [[ -d "$src/subprojects/$wrap_directory" && ! -f "$src/subprojects/$wrap_directory/meson.build" ]]; then
    echo "[Xcode] Removing incomplete staged Venus subproject: $wrap_directory"
    rm -rf "$src/subprojects/$wrap_directory"
fi
if [[ ! -f "$src/subprojects/$wrap_directory/meson.build" ]]; then
    echo "[Xcode] Resolving Venus protocol using the patched fork's pinned wrap"
    PATH="$work/venv/bin:$PATH" meson subprojects download --sourcedir "$src" venus-protocol
fi
[[ -f "$src/subprojects/$wrap_directory/meson.build" ]] || {
    echo "error: Venus protocol wrap did not provide meson.build: $src/subprojects/$wrap_directory" >&2
    exit 1
}

# Preserve the Venus, dynamically loaded Vulkan driver and in-process render
# server configuration of the supplied historical build recipe.
rm -rf "$build" "$stage"
export MACOSX_DEPLOYMENT_TARGET=15.0
(
    export PATH="$work/venv/bin:$PATH"
    meson setup "$build" "$src" \
        --prefix="$prefix" --libdir=lib --buildtype=release \
        -Dvenus=true -Dvulkan-dload=true \
        -Drender-server-mode=thread -Drender-server-worker=thread \
        -Dneptune=false -Dvtest=false -Dtests=false -Dvideo=false \
        -Dcheck-gl-errors=false
    ninja -C "$build"
    DESTDIR="$stage" ninja -C "$build" install
) > >(tee "$log") 2> >(tee -a "$log" >&2)

staged="$stage$prefix"
staged_lib="$staged/lib/libvirglrenderer.1.dylib"
staged_pc="$staged/lib/pkgconfig/virglrenderer.pc"
[[ -s "$staged_lib" && -s "$staged_pc" ]] || {
    echo "error: virglrenderer install missing expected dylib or pkg-config metadata; see $log" >&2
    exit 1
}
file "$staged_lib" | grep -q 'arm64' || { echo 'error: built virglrenderer is not macOS arm64' >&2; exit 1; }
install_name_tool -id @rpath/libvirglrenderer.1.dylib "$staged_lib"
if otool -L "$staged_lib" | grep -qiE 'molten|vulkan'; then
    echo 'error: virglrenderer must load the Vulkan driver dynamically, not link it' >&2
    exit 1
fi
if grep -qiE 'vulkan|molten' "$staged_pc"; then
    echo 'error: virglrenderer.pc unexpectedly depends on a Vulkan driver' >&2
    exit 1
fi
codesign --force --sign - "$staged_lib"

# Move staged files to the durable host prefix; no source checkout or app bundle
# is modified. Preserve symlinks exactly as Meson installed them.
while IFS= read -r -d '' entry; do
    relative="${entry#"$staged"/}"
    mkdir -p "$(dirname "$prefix/$relative")"
    mv -f "$entry" "$prefix/$relative"
done < <(find "$staged" \( -type f -o -type l \) -print0)
rm -rf "$stage"

[[ -s "$lib" && -s "$pc" ]] || { echo 'error: final host virglrenderer installation incomplete' >&2; exit 1; }
if [[ ! -e "$prefix/lib/libvirglrenderer.dylib" ]]; then
    ln -s libvirglrenderer.1.dylib "$prefix/lib/libvirglrenderer.dylib"
fi
PKG_CONFIG_PATH="$prefix/lib/pkgconfig:$PKG_CONFIG_PATH" pkg-config --exists virglrenderer || {
    echo 'error: staged virglrenderer.pc is not resolvable' >&2
    exit 1
}
printf '%s\n' "$source_identity" > "$identity_file"
echo "[Xcode] macOS arm64 virglrenderer ready: $lib"
otool -L "$lib"
