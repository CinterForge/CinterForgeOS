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
# macOS may report ENOTEMPTY while recursively deleting the large extracted
# Debian rootfs. Retry only that disposable output removal, and verify that
# the directory is gone before promoting a freshly built userspace. Never
# reuse or partially overlay an older RootFS.
remove_disposable_tree() {
  local path="$1" attempt
  for attempt in 1 2 3 4 5; do
    [[ ! -e "$path" && ! -L "$path" ]] && return 0
    if rm -rf "$path" && [[ ! -e "$path" && ! -L "$path" ]]; then
      return 0
    fi
    echo "[FEX userspace] Cleanup attempt $attempt incomplete: $path" >&2
    sleep 1
  done
  echo "[FEX userspace] ERROR: Unable to remove disposable build directory: $path" >&2
  return 1
}
sha_check() { printf '%s  %s\n' "$WINE_SHA256" "$ARCHIVE" | shasum -a 256 -c -; }
# Rebuild only the AMD64 GLX diagnostic when its authored source changes.
# The stamp is embedded in the executable so stale generated RootFS copies
# cannot satisfy readiness merely by containing a runnable old probe.
probe_source_sha() { shasum -a 256 "$REPO/guest/fex/glx-probe.c" | awk '{print $1}'; }
build_glx_probe() {
  local destination="$1" probe_sha probe_output
  probe_sha=$(probe_source_sha)
  probe_output="$STEAMAC_WORK_DIR/build/fex-glx-probe-output"
  remove_disposable_tree "$probe_output"
  mkdir -p "$probe_output"
  docker run --rm --platform linux/amd64 \
    -e PROBE_SOURCE_SHA="$probe_sha" \
    -v "$REPO/guest/fex/glx-probe.c:/src/glx-probe.c:ro" \
    -v "$probe_output:/output" \
    "$FEX_ROOTFS_CONTAINER" /bin/bash -euc '
      export DEBIAN_FRONTEND=noninteractive
      apt-get update
      apt-get install -y --no-install-recommends gcc libc6-dev libgl-dev libx11-dev file
      gcc -std=c11 -O2 -Wall -Wextra \
        -DCINTERFORGE_GLX_PROBE_SOURCE_SHA=\"$PROBE_SOURCE_SHA\" \
        -o /output/cinterforge-glx-probe /src/glx-probe.c -lGL -lX11
      file /output/cinterforge-glx-probe | grep -Eq "x86-64|x86_64"
    ' 2>&1 | tee "$LOG/glx-probe-build.log"
  strings "$probe_output/cinterforge-glx-probe" | grep -Fx "CINTERFORGE_GLX_PROBE_SHA=$probe_sha" >/dev/null || {
    echo '[FEX userspace] ERROR: GLX probe source fingerprint missing from binary' >&2
    exit 1
  }
  install -m 0755 "$probe_output/cinterforge-glx-probe" "$destination"
  remove_disposable_tree "$probe_output"
  echo "[FEX userspace] GLX probe built from source SHA $probe_sha"
}
case "${1:-}" in
  probe)
    # Called by the normal Xcode layer build when only glx-probe.c changed.
    # Never create a partial RootFS or silently reuse a stale diagnostic.
    [[ -d "$OUT/RootFS" && -x "$OUT/RootFS/usr/bin/bash" ]] || {
      echo '[FEX userspace] ERROR: No built AMD64 RootFS; run userspace build first' >&2
      exit 1
    }
    . "$REPO/scripts/xcode-docker-env.sh"
    build_glx_probe "$OUT/RootFS/usr/bin/cinterforge-glx-probe"
    ;;
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
    remove_disposable_tree "$tmp"
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
          libsm6 libice6 libxcb1 libxcb-glx0 libxcb-dri2-0 libxcb-dri3-0 \
          libxcb-present0 libxcb-shm0 libxshmfence1 \
          libgl1 libglx0 libegl1 libvulkan1 libudev1 \
          libgl1-mesa-dri mesa-vulkan-drivers mesa-utils vulkan-tools
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
    # These AMD64 tools deliberately live inside FEX's rootfs. Running the
    # ARM64 SteamOS /usr/bin/glxinfo from FEXBash gives a false architecture
    # signal, as observed during Superposition debugging.
    for probe in glxinfo vulkaninfo; do
      test -x "$tmp/RootFS/usr/bin/$probe" || {
        echo "[FEX userspace] missing x86-64 graphics probe: $probe" >&2
        exit 1
      }
      file -L "$tmp/RootFS/usr/bin/$probe" | grep -Eq 'x86-64|x86_64' || {
        echo "[FEX userspace] graphics probe is not AMD64: $probe" >&2
        exit 1
      }
    done
    # The loader alone is not a graphics driver. Require actual AMD64 Mesa
    # OpenGL drivers and Vulkan ICDs as part of the prepared offline image.
    test -d "$tmp/RootFS/usr/lib/x86_64-linux-gnu/dri" || {
      echo '[FEX userspace] AMD64 Mesa DRI drivers are missing' >&2; exit 1;
    }
    test -d "$tmp/RootFS/usr/share/vulkan/icd.d" || {
      echo '[FEX userspace] AMD64 Vulkan ICD metadata is missing' >&2; exit 1;
    }
    compgen -G "$tmp/RootFS/usr/share/vulkan/icd.d/*.json" >/dev/null || {
      echo '[FEX userspace] AMD64 Vulkan ICD database is empty' >&2; exit 1;
    }
    for dependency in libSM.so.6 libICE.so.6; do
      test -e "$tmp/RootFS/usr/lib/x86_64-linux-gnu/$dependency" || {
        echo "[FEX userspace] missing x86-64 GUI dependency: $dependency" >&2
        exit 1
      }
    done
    # The probe is source-fingerprinted; the layer build can update it
    # independently without rebuilding the multi-gigabyte Debian RootFS.
    build_glx_probe "$tmp/RootFS/usr/bin/cinterforge-glx-probe"
    remove_disposable_tree "$OUT"
    mv "$tmp" "$OUT"
    echo "[FEX userspace] Built x86-64 RootFS and staged Wine at $OUT"
    ;;
  *) echo 'Usage: guest/fex/runtime.sh fetch|build|probe' >&2; exit 64 ;;
esac
