#!/bin/sh
# Build the steamac guest kernel (raw arm64 Image, all drivers built in).
#
#   guest/kernel/build.sh          build/refresh work/out/Image + work/out/kernel.config
#   guest/kernel/build.sh clean    drop the docker build volume (next build is from scratch)
#
# Source: Linux $KVER from kernel.org (sha256 pinned), patches/ applied in order.
# Config: config/00-base-libkrunfw-aarch64.config + 10-frame-userspace.config +
#         20-steamac.config, olddefconfig, every =m promoted to =y, then
#         check-config.sh must pass. The resulting full config is copied to
#         guest/kernel/config-steamac.
# The build runs natively in an arm64 OrbStack/Docker container inside a docker volume
# (the repo lives on a case-insensitive APFS volume, unusable for a kernel tree).
# Verify a built Image under QEMU with guest/kernel/smoke-test.sh.
#
# Patches (patches/, applied with fuzz 0):
#   0001      drm/virtio: place host-visible blobs at blob_alignment. Upstream 7.2
#             (7b5121c3374e, 47248e0d8264, 6bd7e82e2653) only rejects unaligned sizes;
#             virtio_gpu_vram_map() still used drm_mm_insert_node() without alignment.
#             Partial mmap (libkrunfw 0022) is already upstream in 7.2
#             (virtio_gpu_vram_mmap honours vm_pgoff with an overflow check).
#   0002-0005 Apple TSO memory model control: PR_{GET,SET}_MEM_MODEL, used by FEX.
#             These come from AsahiLinux/linux bits/220-tso (3da9f89f..ee9adecf, rebased
#             on v7.2). They are the same series as libkrunfw 0014-0017, whose 6.12
#             versions do not apply to 7.2. libkrunfw 0013 is not needed on 7.2 (the
#             capability uses ARM64_CPUCAP_EARLY_LOCAL_CPU_FEATURE). The series is
#             harmless when unsupported: the TSO capability needs an Apple MIDR
#             implementer, AIDR_EL1 bit 9 and a read-back test showing ACTLR_EL1.TSO is
#             writable; otherwise the prctl returns -EINVAL. libkrunfw ships the same
#             code (ARM64_MEMORY_MODEL_CONTROL=y) for libkrun on macOS.
#   0006      ALSA: virtio: clear the fill level of every period in ops->prepare(). Upstream
#             (still in 7.3) keeps the length of a period that was partly filled when the
#             stream stopped; after the restart that period is never sent to the device
#             and playback stalls ~0.6 s later (PipeWire restarting after an xrun).
#   0007      ALSA: virtio: report an xrun when the device completed a whole buffer since the
#             last pointer callback. Upstream reports the wrapped position only, so a buffer
#             completed while the guest was busy looks like no progress (pcm_indirect and the
#             PCM core both lose it) and playback stalls for good with a full buffer.
#   Not taken from libkrunfw: 0018 fence passing (claims feature bit 5 = BLOB_ALIGNMENT),
#   0003-0012 vsock dgram/TSI (we use virtio-net), 0019 compat input (muvm-specific),
#   0023 hard-coded 64K placement (superseded by 0001).
#
# Regenerating config/10-frame-userspace.config (only when the Frame config or KVER
# changes; needs the extracted tree in the build volume and work/frame/frame-kernel.config):
#   docker run --rm -v steamac-kernel-build:/build:ro -v "$PWD/guest/kernel/tools":/tools:ro \
#     -v "$PWD/work/frame":/frame:ro steamac-kernel-builder:trixie python3 \
#     /tools/gen-frame-fragment.py /build/linux-$KVER /frame/frame-kernel.config \
#     > guest/kernel/config/10-frame-userspace.config
set -eu

KVER=7.2.9
KSHA256=b4c5dfbe51a364a6c7f03869200f88c8e1f77403539005f14b7fc6bc91b8d8ba
KURL=https://cdn.kernel.org/pub/linux/kernel/v7.x/linux-$KVER.tar.xz
IMAGE=steamac-kernel-builder:trixie
VOLUME=steamac-kernel-build
JOBS=${JOBS:-16}

here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
STEAMAC_SOURCE_ROOT="$root"
. "$STEAMAC_SOURCE_ROOT/scripts/work-path.sh"
cache=$STEAMAC_WORK_DIR/cache/kernel
out=$STEAMAC_WORK_DIR/out

if [ "${1:-}" = clean ]; then
	docker volume rm -f "$VOLUME"
	exit 0
fi

# No receipt survives a failed rebuild or a source edit during the build.
inputs=$(python3 "$root/scripts/guest-artifacts.py" begin kernel)

mkdir -p "$cache" "$out"
tarball=$cache/linux-$KVER.tar.xz
if ! echo "$KSHA256  $tarball" | shasum -a 256 -c - > /dev/null 2>&1; then
	echo ">> downloading $KURL"
	curl -fL --retry 3 -o "$tarball.part" "$KURL"
	echo "$KSHA256  $tarball.part" | shasum -a 256 -c -
	mv "$tarball.part" "$tarball"
fi

docker build --platform linux/arm64 -q -t "$IMAGE" "$here" > /dev/null
docker volume create "$VOLUME" > /dev/null

# Keep the container alive while staging.  Never make its startup command depend
# on a script that might not yet exist in the image (or a stale cached image).
# Colima cannot reliably bind-mount external host paths; docker cp/exec use its API.
container=$(docker create --platform linux/arm64 \
    -e KVER="$KVER" -e JOBS="$JOBS" \
    -v "$VOLUME":/build "$IMAGE" /bin/sh -c 'while :; do sleep 3600; done')
cleanup_container() { docker rm -f "$container" >/dev/null 2>&1 || true; }
trap cleanup_container EXIT HUP INT TERM

docker start "$container" >/dev/null
# docker cp requires parent directories to exist in the container.
docker exec "$container" mkdir -p /steamac/config /steamac/patches /cache /out
docker cp "$here/container-build.sh" "$container:/steamac/container-build.sh"
docker cp "$here/check-config.sh" "$container:/steamac/check-config.sh"
docker cp "$here/config/." "$container:/steamac/config/"
docker cp "$here/patches/." "$container:/steamac/patches/"
docker cp "$tarball" "$container:/cache/linux-$KVER.tar.xz"
# Fail with actionable diagnostics if the copied script is not actually visible.
docker exec "$container" /bin/sh -c \
    'test -s /steamac/container-build.sh && test -s /steamac/check-config.sh && test -s "/cache/linux-$KVER.tar.xz"' || {
    echo "CFOS: kernel container staging failed: required files missing inside container" >&2
    docker exec "$container" ls -la /steamac /cache >&2 || true
    exit 1
}
echo "CFOS: kernel container inputs staged; running /steamac/container-build.sh"
# Exec, rather than the container entrypoint, runs the staged script.  Its exit
# code is propagated by set -e; a failed build cannot publish stale artifacts.
docker exec "$container" /bin/sh /steamac/container-build.sh
docker cp "$container:/out/Image" "$out/Image.tmp"
docker cp "$container:/out/kernel.config" "$out/kernel.config.tmp"
docker cp "$container:/out/kernel-check-config.log" "$out/kernel-check-config.log.tmp"
mv -f "$out/Image.tmp" "$out/Image"
mv -f "$out/kernel.config.tmp" "$out/kernel.config"
mv -f "$out/kernel-check-config.log.tmp" "$out/kernel-check-config.log"
cleanup_container
trap - EXIT HUP INT TERM

cp "$out/kernel.config" "$here/config-steamac"
file "$out/Image"
ls -l "$out/Image" "$out/kernel.config"
shasum -a 256 "$out/Image"
python3 "$root/scripts/guest-artifacts.py" finish kernel "$inputs"
