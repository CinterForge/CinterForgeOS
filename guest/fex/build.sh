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
# CinterForge's editable fork is the authored source. Setup selects a commit;
# Build consumes that same commit from the external, visible dependency checkout.
# A changing branch cannot silently change a release between Setup and Build.
FEX_FORK_CHECKOUT="${FEX_FORK_CHECKOUT:-$(dirname "$REPO")/FEX}"
FEX_SOURCE_ID="$STEAMAC_WORK_DIR/DependencySources/FEX.selected-commit"
FEX_BUILD_ID="$STEAMAC_WORK_DIR/out/fex.source-commit"
resolve_fex_commit() {
  [[ -d "$FEX_FORK_CHECKOUT/.git" ]] || {
    echo "[FEX] CinterForge fork missing: $FEX_FORK_CHECKOUT (clone the public CinterForge/FEX cinterforge/cinterforge branch)" >&2; exit 1;
  }
  local branch origin_url sha
  branch=$(git -C "$FEX_FORK_CHECKOUT" symbolic-ref --quiet --short HEAD) || {
    echo '[FEX] fork checkout must be on cinterforge/cinterforge branch' >&2; exit 1;
  }
  [[ "$branch" == cinterforge/cinterforge ]] || {
    echo "[FEX] unexpected branch $branch (expected cinterforge/cinterforge)" >&2; exit 1;
  }
  [[ -z "$(git -C "$FEX_FORK_CHECKOUT" status --porcelain --untracked-files=normal)" ]] || {
    echo '[FEX] fork has uncommitted/untracked files; commit the FEX fix first' >&2; exit 1;
  }
  origin_url=$(git -C "$FEX_FORK_CHECKOUT" remote get-url origin)
  [[ "$origin_url" == *CinterForge/FEX* ]] || {
    echo "[FEX] fork origin must be CinterForge/FEX, got $origin_url" >&2; exit 1;
  }
  sha=$(git -C "$FEX_FORK_CHECKOUT" rev-parse --verify HEAD)
  # Exact commit is captured at Setup and used for every subsequent Build.
  printf '%s\n' "$sha"
}
case "${1:-}" in
  fetch)
    selected=$(resolve_fex_commit)
    if [[ ! -d "$SOURCE/.git" ]]; then
      git clone --recurse-submodules "$FEX_FORK_CHECKOUT" "$SOURCE"
    fi
    # Setup alone updates the external dependency checkout. Build never
    # fetches source or follows a moving branch.
    git -C "$SOURCE" fetch "$FEX_FORK_CHECKOUT" "$selected"
    git -C "$SOURCE" checkout --detach "$selected"
    git -C "$SOURCE" submodule update --init --recursive
    actual=$(git -C "$SOURCE" rev-parse HEAD)
    [[ "$actual" == "$selected" ]] || { echo "FEX source mismatch: $actual" >&2; exit 1; }
    printf '%s\n' "$actual" > "$FEX_SOURCE_ID"
    echo "[FEX] setup selected CinterForge/FEX cinterforge/cinterforge commit $actual"
    ;;
  build)
    [[ -d "$SOURCE/.git" ]] || { echo '[FEX] Setup incomplete: external source checkout missing. Run ./CFOS setup cinterforge-os mac' >&2; exit 1; }
    [[ -s "$FEX_SOURCE_ID" ]] || { echo '[FEX] Setup incomplete: selected source commit missing. Run ./CFOS setup cinterforge-os mac' >&2; exit 1; }
    selected=$(cat "$FEX_SOURCE_ID")
    # Fail before Docker if Setup selected a checkout without the FEX build root.
    [[ -f "$SOURCE/CMakeLists.txt" ]] || {
      echo "[FEX] ERROR: Setup source has no root CMakeLists.txt: $SOURCE" >&2
      echo '[FEX] Rerun CFOS Setup to populate the complete FEX fork checkout.' >&2
      exit 1
    }
    [[ "$(git -C "$SOURCE" rev-parse HEAD)" == "$selected" ]] || { echo '[FEX] source differs from Setup-selected commit' >&2; exit 1; }
    [[ -z "$(git -C "$SOURCE" status --porcelain --untracked-files=normal)" ]] || { echo '[FEX] build source is dirty' >&2; exit 1; }
    git -C "$SOURCE" submodule status --recursive | awk '$1 ~ /^[-+U]/ {bad=1; print "FEX invalid submodule: " $0} END {exit bad}'
    # Reuse the existing managed Colima daemon (and start its existing
    # profile if stopped), rather than relying on ambient Docker settings.
    . "$REPO/scripts/xcode-docker-env.sh"
    docker build --platform linux/arm64 -t "$IMAGE" "$REPO/guest/fex"
    # Never allow libraries from an older build to satisfy validation for a
    # fresh FEX build. Stage into a clean directory, promote only on success.
    staging="$STEAMAC_WORK_DIR/out/fex.building"
    rm -rf "$staging"
    mkdir -p "$staging"
    # /src read-only, /work and /dest writable; DESTDIR is absolute as required by FEX.
    # Colima's daemon cannot always bind-mount external macOS volumes.
    # Probe the actual FEX source, not merely whether Docker itself works.
    if docker run --rm --platform linux/arm64 --mount "type=bind,source=$SOURCE,target=/src,readonly" \
        "$IMAGE" /bin/bash -c 'test -f /src/CMakeLists.txt' >/dev/null 2>&1; then
      docker run --rm --platform linux/arm64 \
        --mount "type=bind,source=$SOURCE,target=/src,readonly" --mount "type=bind,source=$BUILD,target=/work" --mount "type=bind,source=$staging,target=/dest" \
        "$IMAGE" /bin/bash -euc '
        export CC=clang CXX=clang++
        cmake -S /src -B /work -G Ninja -DCMAKE_BUILD_TYPE=Release \
          -DCMAKE_INSTALL_PREFIX=/usr -DUSE_LINKER=lld -DENABLE_LTO=ON \
          -DBUILD_THUNKS=ON
        cmake --build /work --parallel "$(nproc)"
        DESTDIR=/dest cmake --install /work
        test -x /dest/usr/bin/FEX
        file /dest/usr/bin/FEX
        readelf -h /dest/usr/bin/FEX | grep -q "AArch64"
        # FEX installs host and guest thunk libraries in different canonical
        # directories; the JSON database is not a substitute for either.
        host=/dest/usr/lib/aarch64-linux-gnu/fex-emu/HostThunks
        guest=/dest/usr/share/fex-emu/GuestThunks
        for library in libGL-host.so libvulkan-host.so; do
          test -s "$host/$library" || {
            echo "[FEX] required ARM64 host thunk missing: $host/$library" >&2
            exit 1
          }
          readelf -h "$host/$library" | grep -q "AArch64" || {
            echo "[FEX] wrong architecture for host thunk: $host/$library" >&2
            exit 1
          }
        done
        for library in libGL-guest.so libvulkan-guest.so; do
          test -s "$guest/$library" || {
            echo "[FEX] required AMD64 guest thunk missing: $guest/$library" >&2
            exit 1
          }
          readelf -h "$guest/$library" | grep -q "Advanced Micro Devices X86-64" || {
            echo "[FEX] wrong architecture for guest thunk: $guest/$library" >&2
            exit 1
          }
        done
      ' 2>&1 | tee "$LOGS/build.log"
    else
      echo '[FEX] Docker daemon cannot see external source; copying FEX inputs into container'
      cid=$(docker create --platform linux/arm64 "$IMAGE" /bin/bash -euc '
        export CC=clang CXX=clang++
        cmake -S /src -B /work -G Ninja -DCMAKE_BUILD_TYPE=Release \
          -DCMAKE_INSTALL_PREFIX=/usr -DUSE_LINKER=lld -DENABLE_LTO=ON \
          -DBUILD_THUNKS=ON
        cmake --build /work --parallel "$(nproc)"
        DESTDIR=/dest cmake --install /work
        test -x /dest/usr/bin/FEX
        file /dest/usr/bin/FEX
        readelf -h /dest/usr/bin/FEX | grep -q "AArch64"
        # FEX installs host and guest thunk libraries in different canonical
        # directories; the JSON database is not a substitute for either.
        host=/dest/usr/lib/aarch64-linux-gnu/fex-emu/HostThunks
        guest=/dest/usr/share/fex-emu/GuestThunks
        for library in libGL-host.so libvulkan-host.so; do
          test -s "$host/$library" || {
            echo "[FEX] required ARM64 host thunk missing: $host/$library" >&2
            exit 1
          }
          readelf -h "$host/$library" | grep -q "AArch64" || {
            echo "[FEX] wrong architecture for host thunk: $host/$library" >&2
            exit 1
          }
        done
        for library in libGL-guest.so libvulkan-guest.so; do
          test -s "$guest/$library" || {
            echo "[FEX] required AMD64 guest thunk missing: $guest/$library" >&2
            exit 1
          }
          readelf -h "$guest/$library" | grep -q "Advanced Micro Devices X86-64" || {
            echo "[FEX] wrong architecture for guest thunk: $guest/$library" >&2
            exit 1
          }
        done
      ')
      # docker cp into an absent destination can nest the checkout under /src.
      # Explicitly create the three mount-equivalent directories first.
      # Copy into explicit container paths, then verify the CMake root before build.
      if ! docker cp "$SOURCE/." "$cid:/src" ||
         ! docker cp "$BUILD/." "$cid:/work"; then
        docker rm -f "$cid" >/dev/null 2>&1 || true
        echo '[FEX] failed to transfer source/build inputs to Docker' >&2
        exit 1
      fi
      # Confirm copied files are at the path CMake will use.
      if ! docker cp "$cid:/src/CMakeLists.txt" "$LOGS/FEX-CMakeLists.probe" >/dev/null 2>&1; then
        docker rm -f "$cid" >/dev/null 2>&1 || true
        echo '[FEX] ERROR: Docker copy did not place CMakeLists.txt at /src/CMakeLists.txt' >&2
        exit 1
      fi
      rm -f "$LOGS/FEX-CMakeLists.probe"
      set +e
      docker start -a "$cid" 2>&1 | tee "$LOGS/build.log"
      status=${PIPESTATUS[0]}
      set -e
      if (( status == 0 )); then
        docker cp "$cid:/work/." "$BUILD/" || status=1
        docker cp "$cid:/dest/." "$staging/" || status=1
      fi
      docker rm -f "$cid" >/dev/null 2>&1 || true
      (( status == 0 )) || { echo "[FEX] container build failed (exit=$status)" >&2; exit 1; }
    fi
    rm -rf "$DEST"
    mv "$staging" "$DEST"
    printf '%s\n' "$selected" > "$FEX_BUILD_ID"
    echo "[FEX] installed to $DEST (host-side Linux ARM64 artifact; commit $selected)"
    ;;
  *) echo 'Usage: guest/fex/build.sh fetch|build' >&2; exit 64 ;;
esac
