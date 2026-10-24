#!/usr/bin/env bash
# Acquire FEX on the Mac during setup; compile in the existing Linux/arm64 Docker facility.
# Only the installed ARM64 ELF files are injected into the disposable SteamOS layer.
set -euo pipefail
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
STEAMAC_SOURCE_ROOT="$REPO"
. "$REPO/scripts/work-path.sh"
. "$REPO/scripts/config.env"
SOURCE="$STEAMAC_WORK_DIR/DependencySources/FEX"
BUILD="$STEAMAC_WORK_DIR/build/fex"
DEST="$STEAMAC_WORK_DIR/out/fex"
LOGS="$STEAMAC_WORK_DIR/Logs/FEX"
IMAGE="${FEX_BUILD_IMAGE:-cinterforgeos-fex-build:2610}"
mkdir -p "$(dirname "$SOURCE")" "$BUILD" "$LOGS" "$STEAMAC_WORK_DIR/out"
case "${1:-}" in
  fetch)
    if [[ ! -d "$SOURCE/.git" ]]; then
      git clone --recurse-submodules "$FEX_REPO" "$SOURCE"
    fi
    git -C "$SOURCE" fetch --tags origin "$FEX_REF"
    git -C "$SOURCE" checkout --detach "$FEX_COMMIT"
    git -C "$SOURCE" submodule update --init --recursive
    actual=$(git -C "$SOURCE" rev-parse HEAD)
    [[ "$actual" == "$FEX_COMMIT" ]] || { echo "FEX source mismatch: $actual" >&2; exit 1; }
    echo "[FEX] pinned source $actual"
    ;;
  build)
    [[ -d "$SOURCE/.git" ]] || { echo "FEX source missing: run guest/fex/build.sh fetch" >&2; exit 1; }
    [[ "$(git -C "$SOURCE" rev-parse HEAD)" == "$FEX_COMMIT" ]] || { echo 'FEX source does not match pinned commit' >&2; exit 1; }
    git -C "$SOURCE" submodule status --recursive | awk '$1 ~ /^[-+U]/ {bad=1; print "FEX invalid submodule: " $0} END {exit bad}'
    # Reuse the existing managed Colima daemon (and start its existing
    # profile if stopped), rather than relying on ambient Docker settings.
    . "$REPO/scripts/xcode-docker-env.sh"
    docker build --platform linux/arm64 -t "$IMAGE" "$REPO/guest/fex"
    mkdir -p "$DEST"
    # /src read-only, /work and /dest writable; DESTDIR is absolute as required by FEX.
    docker run --rm --platform linux/arm64 \
      -v "$SOURCE:/src:ro" -v "$BUILD:/work" -v "$DEST:/dest" \
      "$IMAGE" /bin/bash -euc '
        export CC=clang CXX=clang++
        cmake -S /src -B /work -G Ninja -DCMAKE_BUILD_TYPE=Release \
          -DCMAKE_INSTALL_PREFIX=/usr -DUSE_LINKER=lld -DENABLE_LTO=ON
        cmake --build /work --parallel "$(nproc)"
        DESTDIR=/dest cmake --install /work
        test -x /dest/usr/bin/FEX
        file /dest/usr/bin/FEX
        readelf -h /dest/usr/bin/FEX | grep -q "AArch64"
      ' 2>&1 | tee "$LOGS/build.log"
    echo "[FEX] installed to $DEST (host-side Linux ARM64 artifact)"
    ;;
  *) echo 'Usage: guest/fex/build.sh fetch|build' >&2; exit 64 ;;
esac
