#!/usr/bin/env bash
# Builds the guest Venus (virtio) Vulkan ICD for the Steam Frame SteamOS image:
#   aarch64 (native: Steam, Proton ARM64EC/WoW64 unix side, gamescope, zink)
#   x86_64 + i386 (FEX-emulated x86 apps; installed into the fex-mesa pressure-vessel provider),
#   plus the x86_64 fault reporter for emulated games (/usr/lib/steamac/x86_64/fault-report.so)
# Output: work/out/mesa-venus/ = tree rooted at guest "/" + MANIFEST.txt.
# Verification runs against the stock rootfs in a chroot: by default the signature- and
# sha256-verified OTA rootfs that `scripts/build-image.sh rootfs` caches in work/cache/rootfs/.
#
# Usage: guest/mesa/build.sh [--clean] [step...]
#   steps: fetch aarch64 x86 verify   (default: all, in that order)
#   --clean  drop the cached source volume and all outputs first
# Env overrides: ROOTFS_IMG (stock SteamOS rootfs image, btrfs).
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
STEAMAC_SOURCE_ROOT="$REPO"
. "$STEAMAC_SOURCE_ROOT/scripts/work-path.sh"
set -a
# shellcheck source=versions.env
source "$HERE/versions.env"
set +a

OUT=$STEAMAC_WORK_DIR/out/mesa-venus
WORK=$STEAMAC_WORK_DIR/build/mesa-venus
STEAMOS_BUILDID=$(. "$REPO/scripts/config.env" && echo "$STEAMOS_BUILDID")
ROOTFS_IMG=${ROOTFS_IMG:-$STEAMAC_WORK_DIR/cache/rootfs/$STEAMOS_BUILDID/rootfs.img}
SRC_VOLUME=steamac-mesa-src

clean=0
steps=()
for arg in "$@"; do
    case $arg in
        --clean) clean=1 ;;
        fetch|aarch64|x86|verify) steps+=("$arg") ;;
        *) echo "unknown argument: $arg" >&2; exit 64 ;;
    esac
done
[ ${#steps[@]} -gt 0 ] || steps=(fetch aarch64 x86 verify)

if [ "$clean" = 1 ]; then
    docker volume rm -f "$SRC_VOLUME" >/dev/null
    rm -rf "$OUT" "$WORK"
fi
mkdir -p "$OUT" "$WORK"

artifacts() { python3 "$REPO/scripts/guest-artifacts.py" "$@"; }
# Keep successful receipts until their inputs or outputs actually change.
# In particular, editing a guest-layer script must not rebuild Mesa.
receipt_ok() { artifacts check "$1" >/dev/null 2>&1; }
# Docker Desktop/Colima may not expose external macOS volumes to its daemon.
# Keep the normal bind-mount path when available, otherwise transfer the
# authored scripts and build outputs through Docker's copy API.
mesa_docker() {
    local -a args=("$@") create_args=() copies=()
    local arg host dest mode cid status=0 copy_status=0
    local i=0 needs_copy=0
    # A visible /scripts mount alone does not prove that /work and /out
    # bind to the host.  Colima can expose one path but not another; that
    # previously let vkprobe compile inside a throwaway container and vanish.
    # Test both directions before choosing the direct Docker path.
    local marker="mesa-mount-$$-${RANDOM}"
    printf '%s\n' "$marker" > "$WORK/$marker"
    printf '%s\n' "$marker" > "$OUT/$marker"
    if ! docker run --rm --platform linux/arm64 \
        -v "$HERE/container:/scripts:ro" -v "$WORK:/work" -v "$OUT:/out" \
        --entrypoint /bin/sh "$TOOLS_IMAGE" -ec '
            test -f /scripts/fetch-mesa.sh
            test "$(cat "/work/$1")" = "$1"
            test "$(cat "/out/$1")" = "$1"
            printf "%s\n" "$1" > "/work/$1.reply"
            printf "%s\n" "$1" > "/out/$1.reply"
        ' sh "$marker" >/dev/null 2>&1 ||
        [[ $(cat "$WORK/$marker.reply" 2>/dev/null) != "$marker" ]] ||
        [[ $(cat "$OUT/$marker.reply" 2>/dev/null) != "$marker" ]]; then
        needs_copy=1
    fi
    rm -f "$WORK/$marker" "$OUT/$marker" "$WORK/$marker.reply" "$OUT/$marker.reply"
    if (( ! needs_copy )); then
        docker run "${args[@]}"
        return
    fi
    while (( i < ${#args[@]} )); do
        arg=${args[i]}
        if [[ $arg == -v ]]; then
            (( i += 1 ))
            arg=${args[i]}
            host=${arg%%:*}
            dest=${arg#*:}; dest=${dest%%:*}
            if [[ $host == /* ]]; then
                copies+=("$host" "$dest")
            else
                create_args+=(-v "$arg")
            fi
        elif [[ $arg != --rm ]]; then
            create_args+=("$arg")
        fi
        (( i += 1 ))
    done
    cid=$(docker create "${create_args[@]}") || return 1
    echo '[mesa] Docker bind mounts failed round-trip validation; copying inputs and outputs through Docker' >&2
    for (( i=0; i<${#copies[@]}; i+=2 )); do
        host=${copies[i]}; dest=${copies[i+1]}
        if [[ -d $host ]]; then
            docker cp "$host/." "$cid:$dest/" || status=1
        else
            docker cp "$host" "$cid:$dest" || status=1
        fi
    done
    if (( ! status )); then docker start -a "$cid" || status=$?; fi
    # Preserve generated build products even when the command fails.
    for (( i=0; i<${#copies[@]}; i+=2 )); do
        host=${copies[i]}; dest=${copies[i+1]}
        if [[ $dest == /out || $dest == /work ]]; then
            mkdir -p "$host"
            docker cp "$cid:$dest/." "$host/" || copy_status=1
        fi
    done
    docker rm -f "$cid" >/dev/null 2>&1 || true
    (( copy_status == 0 )) || return 1
    return "$status"
}

fetched=0
fetch_sources() {
    mesa_docker --rm --platform linux/arm64 \
        -e MESA_URL -e MESA_COMMIT -e VENUS_PROTOCOL_URL -e VENUS_PROTOCOL_COMMIT \
        -v "$SRC_VOLUME:/src" -v "$HERE/container:/scripts:ro" \
        "$TOOLS_IMAGE" sh /scripts/fetch-mesa.sh
    fetched=1
}

log() { printf '\n=== %s\n' "$*"; }

for step in "${steps[@]}"; do
    case $step in
    fetch)
        if receipt_ok mesa-aarch64 && receipt_ok mesa-x86; then
            log "Mesa architecture artifacts valid; source fetch unnecessary"
        else
            log "fetch mesa $MESA_COMMIT"
            fetch_sources
        fi
        ;;
    aarch64)
        if receipt_ok mesa-aarch64; then
            log "reuse verified aarch64 Mesa output (no compilation)"
            continue
        fi
        log "build aarch64 (log: $WORK/build-aarch64.log)"
        artifacts invalidate mesa
        inputs=$(artifacts begin mesa-aarch64)
        [[ $fetched == 1 ]] || fetch_sources
        rm -rf "$OUT/usr/lib/libvulkan_virtio.so" "$OUT/usr/share/vulkan"
        mesa_docker --rm --platform linux/arm64 \
            -v "$SRC_VOLUME:/src:ro" -v "$HERE/container:/scripts:ro" -v "$OUT:/out" -v "$WORK:/work" \
            "$AARCH64_IMAGE" bash /scripts/build-aarch64.sh 2>&1 | tee "$WORK/build-aarch64.log"
        artifacts finish mesa-aarch64 "$inputs"
        ;;
    x86)
        if receipt_ok mesa-x86; then
            log "reuse verified x86_64 + i386 Mesa outputs (no compilation)"
            continue
        fi
        log "build x86_64 + i386 (log: $WORK/build-x86.log)"
        artifacts invalidate mesa
        inputs=$(artifacts begin mesa-x86)
        [[ $fetched == 1 ]] || fetch_sources
        rm -rf "$OUT/usr/share/guestos" "$OUT/usr/lib/steamac"
        mesa_docker --rm --platform linux/amd64 \
            -e STEAMOS_X86_MIRROR -e STEAMOS_X86_BRANCH -e FEX_PROVIDER -e HOLO_KEYRING_PKG -e HOLO_KEYRING_SHA256 \
            -e LIBDISPLAY_INFO_PKG -e LIBDISPLAY_INFO_SHA256 \
            -e LIB32_LIBDISPLAY_INFO_PKG -e LIB32_LIBDISPLAY_INFO_SHA256 \
            -v "$SRC_VOLUME:/src:ro" -v "$HERE/container:/scripts:ro" -v "$OUT:/out" -v "$WORK:/work" \
            "$X86_IMAGE" bash /scripts/build-x86.sh 2>&1 | tee "$WORK/build-x86.log"
        artifacts finish mesa-x86 "$inputs"
        ;;
    verify)
        if receipt_ok mesa; then
            log "reuse verified Mesa release tree (no verification rebuild)"
            continue
        fi
        log "manifest + verify against $ROOTFS_IMG (log: $WORK/verify.log)"
        inputs=$(artifacts begin mesa)
        [ -f "$ROOTFS_IMG" ] || { echo "missing stock rootfs image: $ROOTFS_IMG" >&2; exit 1; }
        # An override may be useful for diagnostics, but cannot produce a release
        # receipt for a different rootfs than the one pinned in config.env.
        rootfs_sha=$(. "$REPO/scripts/config.env" && echo "$STEAMOS_ROOTFS_SHA256")
        echo "$rootfs_sha  $ROOTFS_IMG" | shasum -a 256 -c -
        mesa_docker --rm --platform linux/arm64 --privileged \
            -e MESA_URL -e MESA_COMMIT -e VENUS_PROTOCOL_COMMIT -e AARCH64_IMAGE -e X86_IMAGE \
            -e STEAMOS_X86_MIRROR -e STEAMOS_X86_BRANCH -e FEX_PROVIDER \
            -v "$ROOTFS_IMG:/rootfs.img:ro" -v "$HERE/container:/scripts:ro" -v "$OUT:/out" -v "$WORK:/work" \
            "$TOOLS_IMAGE" sh -c 'sh /scripts/manifest.sh && sh /scripts/verify.sh' 2>&1 | tee "$WORK/verify.log"
        artifacts finish mesa "$inputs"
        ;;
    esac
done
log "done: $OUT"
