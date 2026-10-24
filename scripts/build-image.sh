#!/bin/bash
# Build the guest disk artifacts for steamac (run on the macOS host).
#
#   work/out/steamos.img          raw sparse GPT disk  -> guest /dev/vda
#   work/out/initramfs.cpio.gz    boot stage (busybox + guest/initramfs/init)
#   work/out/steamac-layer.img    read-only erofs /usr layer -> guest /dev/vdb
#                                 (includes guest/progress-agent, built in Rust)
#
# All loop/mkfs/btrfs work runs in a privileged linux/arm64 OrbStack container
# built from scripts/builder/Dockerfile (pinned). Idempotent: the verified
# rootfs reconstruction is cached in work/cache/, everything else is rebuilt.
#
# Usage: scripts/build-image.sh [step...]
#   steps: builder rootfs initramfs fex userspace layer disk check   (default: all, in order)
# Env:   HOME_SIZE_GIB (default 64), STEAMOS_PASSWORD (default "steamos"),
#        FORCE_DISK=1 to rebuild steamos.img even if it exists (it holds user
#        state once booted, so it is never overwritten silently),
#        ALLOW_NO_VENUS=1 to build a test layer without work/out/mesa-venus.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
STEAMAC_SOURCE_ROOT="$REPO"
. "$STEAMAC_SOURCE_ROOT/scripts/work-path.sh"
WORK=$STEAMAC_WORK_DIR
. "$REPO/scripts/config.env"

# Receipts are taken on the host: containers do not need Git/Python installed.
artifacts() { python3 "$REPO/scripts/guest-artifacts.py" "$@"; }

steps=("$@")
[[ ${#steps[@]} -gt 0 ]] || steps=(builder rootfs initramfs fex userspace layer disk check)

mkdir -p "$WORK/out" "$WORK/cache"

# An existing Docker tag is not proof it points to our Debian builder. In
# particular the managed Colima daemon may have a stale/wrong tag with no
# /bin/bash (OCI exit 127). Validate the executable, then rebuild the pinned
# builder image using the existing authored Dockerfile if required.
ensure_builder() {
    if docker run --rm --platform linux/arm64 --entrypoint /bin/bash \
        "$BUILDER_IMAGE" -c 'command -v mkfs.erofs >/dev/null && command -v install >/dev/null' \
        >/dev/null 2>&1; then
        return
    fi
    echo "[steamac] Builder image unavailable or invalid; rebuilding $BUILDER_IMAGE" >&2
    docker build --platform linux/arm64 -t "$BUILDER_IMAGE" \
        --build-arg BUILDER_BASE="$BUILDER_BASE" \
        --build-arg DEBIAN_SNAPSHOT="$DEBIAN_SNAPSHOT" \
        --build-arg DESYNC_VERSION="$DESYNC_VERSION" \
        --build-arg DESYNC_SHA256="$DESYNC_SHA256" \
        "$REPO/scripts/builder"
    docker run --rm --platform linux/arm64 --entrypoint /bin/bash \
        "$BUILDER_IMAGE" -c 'command -v mkfs.erofs >/dev/null && command -v install >/dev/null' \
        || { echo "[steamac] ERROR: builder image lacks required tools" >&2; exit 1; }
}

run_in_builder() {
    ensure_builder
    # /src: repo (read-only), /work: work dir. --privileged for loop devices.
    docker run --rm --privileged --platform linux/arm64 \
        -e HOME_SIZE_GIB -e STEAMOS_PASSWORD -e FORCE_DISK -e ALLOW_NO_VENUS -e SOURCE_DATE_EPOCH \
        -v "$REPO:/src:ro" -v "$WORK:/work" \
        "$BUILDER_IMAGE" /bin/bash "$@"
}

for step in "${steps[@]}"; do
    echo "=== step: $step"
    case $step in
        builder)
            docker build --platform linux/arm64 -t "$BUILDER_IMAGE" \
                --build-arg BUILDER_BASE="$BUILDER_BASE" \
                --build-arg DEBIAN_SNAPSHOT="$DEBIAN_SNAPSHOT" \
                --build-arg DESYNC_VERSION="$DESYNC_VERSION" \
                --build-arg DESYNC_SHA256="$DESYNC_SHA256" \
                "$REPO/scripts/builder"
            ;;
        rootfs)    run_in_builder /src/scripts/steps/10-rootfs.sh ;;
        initramfs)
            inputs=$(artifacts begin initramfs)
            run_in_builder /src/scripts/steps/20-initramfs.sh
            artifacts finish initramfs "$inputs"
            ;;
        fex)      "$REPO/guest/fex/build.sh" fetch
                  "$REPO/guest/fex/build.sh" build ;;
        userspace) bash "$REPO/guest/fex/runtime.sh" build ;;
        layer)
            # Fetch the publisher artwork referenced by Hope's authoritative CSV.
            # The 16 original/installer PNGs become normal fingerprinted layer inputs.
            # Never package generic placeholder icons when acquisition fails.
            # Publisher-icon conversion requires Pillow. Xcode's system Python
            # does not provide it; provision a dedicated environment in the
            # external disposable Build work tree, never in the source checkout.
            icon_venv="$WORK/venv-creative-suite-icons"
            icon_python="$icon_venv/bin/python3"
            if [[ ! -x "$icon_python" ]]; then
                python3 -m venv "$icon_venv" || {
                    echo "[icons] ERROR: could not create icon-processing Python environment: $icon_venv" >&2
                    exit 1
                }
            fi
            if ! "$icon_python" -c 'from PIL import Image, ImageDraw, ImageOps; from PIL.PngImagePlugin import PngInfo' 2>/dev/null; then
                echo '[icons] Installing Pillow in the external build environment...'
                "$icon_python" -m pip install --disable-pip-version-check 'Pillow==11.3.0' \
                    > "$WORK/creative-suite-icon-dependencies.log" 2>&1 || {
                    cat "$WORK/creative-suite-icon-dependencies.log" >&2
                    echo '[icons] ERROR: failed to prepare Pillow; see creative-suite-icon-dependencies.log' >&2
                    exit 1
                }
            fi
            # Require the correct importer source. An older script previously
            # finished successfully while silently installing Scribus's 16px
            # website favicon. Fail early rather than shipping incorrect art.
            if ! grep -Fqx "IMPORTER_REVISION = 'v8'" "$REPO/scripts/import-creative-suite-icons.py"; then
                echo '[icons] ERROR: outdated icon importer in active source checkout (Scribus needs v8).' >&2
                echo "[icons] ERROR: replace $REPO/scripts/import-creative-suite-icons.py with the file from this patch." >&2
                exit 1
            fi
            echo "[icons] CinterForgeOS layer: invoking $REPO/scripts/import-creative-suite-icons.py"
            "$icon_python" -u "$REPO/scripts/import-creative-suite-icons.py"
            echo "[icons] CinterForgeOS layer: icon import completed"
            # Validate every authored KDE input on the host before starting
            # the expensive guest build. Docker bind-mounts this exact tree.
            missing=0
            for platform in linux windows; do
                for flavor in built unforked install; do
                    for app in gimp krita inkscape scribus blender kdenlive ardour rapidraw; do
                        desktop="$REPO/guest/layer/usr/share/applications/cinterforge-$flavor-$platform-$app.desktop"
                        if [[ ! -s "$desktop" ]]; then
                            echo "[steamac] ERROR: missing KDE launcher source: $desktop" >&2
                            missing=1
                        fi
                    done
                done
            done
            for group in cinterforge-linux cinterforge-windows linux-unforked windows-unforked linux-install windows-install; do
                directory="$REPO/guest/layer/usr/share/desktop-directories/$group.directory"
                if [[ ! -s "$directory" ]]; then
                    echo "[steamac] ERROR: missing KDE menu directory: $directory" >&2
                    missing=1
                fi
            done
            if [[ ! -s "$REPO/guest/layer/usr/share/cinterforge/kde/cinterforge-unforked.menu" ]]; then
                echo '[steamac] ERROR: missing KDE menu source' >&2
                missing=1
            fi
            (( missing == 0 )) || exit 1
            # The Xcode target invokes `builder layer` directly. The layer
            # requires standalone FEX and its x86-64 RootFS/Wine inputs, so
            # prepare missing inputs before the layer receipt is calculated.
            # Build only: source/archive acquisition remains Hope Setup's job.
            if [[ ! -s "$WORK/out/fex/usr/bin/FEX" ]]; then
                echo "[steamac] Missing standalone FEX: building ARM64 runtime"
                "$REPO/guest/fex/build.sh" build
            fi
            # Keep this prerequisite check aligned with runtime.sh's verified
            # x86-64 libraries. A pre-Pangomm RootFS must not be reused.
            if [[ ! -s "$WORK/out/fex-userspace/wine-executable.txt" || \
                  ! -x "$WORK/out/fex-userspace/RootFS/usr/bin/bash" || \
                  ! -e "$WORK/out/fex-userspace/RootFS/usr/lib/x86_64-linux-gnu/libglib-2.0.so.0" || \
                  ! -e "$WORK/out/fex-userspace/RootFS/usr/lib/x86_64-linux-gnu/libpangomm-1.4.so.1" ]]; then
                echo "[steamac] Missing FEX userspace: building x86-64 RootFS/Wine"
                bash "$REPO/guest/fex/runtime.sh" build
            fi
            if [[ ${ALLOW_NO_VENUS:-} == 1 ]]; then
                # Test-only layers must never acquire a release receipt.
                artifacts invalidate layer
            else
                inputs=$(artifacts begin layer)
            fi
            # guest/progress-agent (Rust, static musl) in the pinned rust container,
            # then the erofs layer (which installs the binary).
            docker run --rm --platform linux/arm64 \
                -e SOURCE_DATE_EPOCH \
                -v "$REPO:/src:ro" -v "$WORK:/work" \
                "$RUST_IMAGE" /bin/sh /src/scripts/steps/25-progress-agent.sh
            run_in_builder /src/scripts/steps/30-layer.sh
            if [[ ${ALLOW_NO_VENUS:-} != 1 ]]; then
                artifacts finish layer "$inputs"
            fi
            ;;
        disk)      run_in_builder /src/scripts/steps/40-disk.sh ;;
        check)     run_in_builder /src/scripts/steps/50-check.sh ;;
        *) echo "unknown step $step" >&2; exit 2 ;;
    esac
done
