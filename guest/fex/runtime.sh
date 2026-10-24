#!/usr/bin/env bash
# Standalone FEX userspace and portable Wine dependencies.
# Setup acquires the checked Wine archive. Build creates the amd64 rootfs.
# All working artifacts stay in the existing visible STEAMAC_WORK_DIR.
set -euo pipefail
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
STEAMAC_SOURCE_ROOT="$REPO"
. "$REPO/scripts/work-path.sh"
. "$REPO/scripts/config.env"
CACHE="$STEAMAC_WORK_DIR/DependencySources/Wine"
OUT="$STEAMAC_WORK_DIR/out/fex-userspace"
LOG="$STEAMAC_WORK_DIR/Logs/FEX"
ARCHIVE="$CACHE/$WINE_ARCHIVE"
mkdir -p "$CACHE" "$LOG" "$STEAMAC_WORK_DIR/out"
sha_check() { printf '%s  %s\n' "$WINE_SHA256" "$ARCHIVE" | shasum -a 256 -c -; }
case "${1:-}" in
  fetch)
    if ! test -f "$ARCHIVE" || ! sha_check >/dev/null 2>&1; then
      tmp="$ARCHIVE.partial"
      rm -f "$tmp"
      curl --fail --location --retry 3 --output "$tmp" "$WINE_URL"
      mv "$tmp" "$ARCHIVE"
    fi
    sha_check
    # Setup is an acquisition phase and must not depend on a running Docker
    # daemon. The amd64 base image is resolved by Docker when Build assembles
    # the RootFS, using the existing managed Colima backend.
    echo '[FEX userspace] Verified Wine archive; amd64 RootFS image is prepared during Build'
    ;;
  build)
    test -f "$ARCHIVE" && sha_check || { echo '[FEX userspace] Wine not acquired; rerun Hope setup' >&2; exit 1; }
    # Build uses the same managed Colima daemon as the existing Mesa/SteamOS
    # image pipeline; never fall back to the user's default Docker context.
    . "$REPO/scripts/xcode-docker-env.sh"
    # This is deliberately a separate, disposable Debian x86-64 rootfs, not
    # the SteamOS/ARM64 guest filesystem or Steam's pressure-vessel container.
    tmp="$STEAMAC_WORK_DIR/build/fex-userspace.tmp"
    mkdir -p "$(dirname "$tmp")"
    rm -rf "$tmp"
    mkdir -p "$tmp/RootFS" "$tmp/Wine"
    # Export as one regular file. Extracting directly into a macOS bind mount
    # from inside Linux can fail on symlinks/metadata with EACCES.
    docker run --rm --platform linux/amd64 -v "$tmp:/output" \
      "$FEX_ROOTFS_CONTAINER" /bin/bash -euc '
        export DEBIAN_FRONTEND=noninteractive
        apt-get update
        apt-get install -y --no-install-recommends \
          ca-certificates libstdc++6 libgcc-s1 libssl3t64 libasound2t64 \
          libglib2.0-0t64 libgtk2.0-0t64 libgtk-3-0t64 \
          libpangomm-1.4-1v5 libgtkmm-2.4-1t64 libatkmm-1.6-1v5 \
          libjack-jackd2-0 libpulse0 libdbus-1-3 libfontconfig1 libfreetype6 \
          libx11-6 libxext6 libxrender1 libxrandr2 libxi6 libxcursor1 \
          libxfixes3 libxcomposite1 libxinerama1 libxkbcommon0 \
          libgl1 libglx0 libegl1 libvulkan1 libudev1
        tar -C / --one-file-system --exclude=./proc --exclude=./sys \
          --exclude=./dev --exclude=./run --exclude=./tmp --exclude=./output \
          -cf /output/rootfs.tar .
      ' 2>&1 | tee "$LOG/rootfs-build.log"
    test -s "$tmp/rootfs.tar" || {
      echo '[FEX userspace] Docker did not export the rootfs archive' >&2; exit 1;
    }
    # Extract on the host, not through Docker's macOS bind mount. -o avoids
    # replaying container owner IDs onto the developer's persistent output.
    tar -xof "$tmp/rootfs.tar" -C "$tmp/RootFS"
    rm -f "$tmp/rootfs.tar"
    test -x "$tmp/RootFS/bin/sh" || test -x "$tmp/RootFS/usr/bin/bash" || {
      echo '[FEX userspace] Generated rootfs lacks shell' >&2; exit 1;
    }
    # Inspect archive paths rather than assuming a top-level Wine directory.
    tar -tf "$ARCHIVE" | grep -Eq '(^|/)bin/wine$' || {
      echo '[FEX userspace] Wine archive has no bin/wine' >&2; exit 1;
    }
    tar -xf "$ARCHIVE" -C "$tmp/Wine"
    wine=$(find "$tmp/Wine" -type f -path '*/bin/wine' -print -quit)
    test -n "$wine" || { echo '[FEX userspace] Wine executable missing after extraction' >&2; exit 1; }
    relative="${wine#"$tmp/Wine/"}"
    printf '%s\n' "$relative" > "$tmp/wine-executable.txt"
    # Ardour's x86-64 binary requires GLib before its own shared libraries
    # can load. The RootFS must contain this native amd64 SONAME.
    test -e "$tmp/RootFS/usr/lib/x86_64-linux-gnu/libglib-2.0.so.0" || {
      echo '[FEX userspace] x86-64 GLib runtime missing' >&2; exit 1;
    }
    test -e "$tmp/RootFS/usr/lib/x86_64-linux-gnu/libpangomm-1.4.so.1" || {
      echo '[FEX userspace] x86-64 Pangomm runtime missing' >&2; exit 1;
    }
    test -e "$tmp/RootFS/usr/lib/x86_64-linux-gnu/libvulkan.so.1" || {
      echo '[FEX userspace] x86 Vulkan loader missing' >&2; exit 1;
    }
    rm -rf "$OUT"
    mv "$tmp" "$OUT"
    echo "[FEX userspace] Built x86-64 RootFS and staged Wine at $OUT"
    ;;
  *) echo 'Usage: guest/fex/runtime.sh fetch|build' >&2; exit 64 ;;
esac
