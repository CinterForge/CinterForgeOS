#!/usr/bin/env python3
"""Construct the Apple-Silicon CraftMaster Bedrock bootstrap payload.

The payload contains no Microsoft BDS bytes.  A macOS host exposes a locally
acquired BDS directory and Apple's Rosetta-for-Linux share to an ARM64 Linux
guest.  The guest contract keeps BDS console I/O on the VM serial console.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow this script to be executed directly while importing shared build_cinterforge modules.
_BUILD_CINTERFORGE_ROOT = Path(__file__).resolve().parent.parent
if str(_BUILD_CINTERFORGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_BUILD_CINTERFORGE_ROOT))
import hashlib, json, os, platform, queue, re, shutil, signal, subprocess, threading, time
from pathlib import Path

GUEST = r'''#!/bin/sh
set -eu
BDS_ROOT="${CINTER_BDS_ROOT:-/mnt/cinter-bds}"
ROSETTA_ROOT="${CINTER_ROSETTA_ROOT:-/mnt/rosetta}"
GLIBC_ROOT="$BDS_ROOT/.cinter/glibc-x86_64"
[ -x "$BDS_ROOT/bedrock_server" ] || { echo "CINTER_BDS_ERROR missing $BDS_ROOT/bedrock_server" >&2; exit 20; }
[ -x "$ROSETTA_ROOT/rosetta" ] || { echo "CINTER_BDS_ERROR missing $ROSETTA_ROOT/rosetta" >&2; exit 21; }
[ -x "$GLIBC_ROOT/ld-linux-x86-64.so.2" ] || { echo "CINTER_BDS_ERROR missing $GLIBC_ROOT/ld-linux-x86-64.so.2" >&2; exit 26; }
if [ -w /proc/sys/fs/binfmt_misc/register ] && [ ! -e /proc/sys/fs/binfmt_misc/rosetta ]; then
  # Apple's documented Rosetta-for-Linux binfmt rule.  The mask is required:
  # without it, ordinary x86-64 ELF files do not match and /bin/sh tries to
  # interpret the BDS executable as a shell script.
  printf ':rosetta:M::\\x7fELF\\x02\\x01\\x01\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x02\\x00\\x3e\\x00:\\xff\\xff\\xff\\xff\\xff\\xfe\\xfe\\x00\\xff\\xff\\xff\\xff\\xff\\xff\\xff\\xff\\xfe\\xff\\xff\\xff:%s:OCF' "$ROSETTA_ROOT/rosetta" > /proc/sys/fs/binfmt_misc/register
fi
[ -r /proc/sys/fs/binfmt_misc/rosetta ] || { echo "CINTER_BDS_ERROR rosetta-binfmt-registration-missing" >&2; exit 23; }
grep -q '^enabled$' /proc/sys/fs/binfmt_misc/rosetta || { echo "CINTER_BDS_ERROR rosetta-binfmt-disabled" >&2; cat /proc/sys/fs/binfmt_misc/rosetta >&2; exit 23; }
echo "CINTER_BDS_ROSETTA_REGISTERED"
echo "CINTER_BDS_GUEST_CONTRACT direct-bds-v2"
CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
[ -s "$CA_BUNDLE" ] || { echo "CINTER_BDS_ERROR missing CA bundle $CA_BUNDLE" >&2; exit 27; }
echo "CINTER_BDS_CA_BUNDLE_READY $CA_BUNDLE"
echo "CINTER_BDS_NETWORK_DIAGNOSTIC_BEGIN"
echo "CINTER_BDS_NETWORK_RESOLV_CONF_BEGIN"
cat /etc/resolv.conf 2>&1 || true
echo "CINTER_BDS_NETWORK_RESOLV_CONF_END"
echo "CINTER_BDS_NETWORK_ROUTE_BEGIN"
(ip addr 2>&1 || true)
(ip route 2>&1 || true)
echo "CINTER_BDS_NETWORK_ROUTE_END"
echo "CINTER_BDS_FIREWALL_BEGIN"
if command -v iptables >/dev/null 2>&1; then iptables -L -n -v 2>&1 || true; else echo "CINTER_BDS_FIREWALL iptables=unavailable"; fi
if command -v nft >/dev/null 2>&1; then nft list ruleset 2>&1 || true; else echo "CINTER_BDS_FIREWALL nft=unavailable"; fi
echo "CINTER_BDS_FIREWALL_END"
echo "CINTER_BDS_UDP_PRELAUNCH_BEGIN port=19132"
if command -v nc >/dev/null 2>&1; then
  set +e
  printf 'CINTER_BDS_UDP_BROADCAST_PROBE\n' | nc -u -w 1 255.255.255.255 19132 >/dev/null 2>&1
  UDP_RC=$?
  set -e
  echo "CINTER_BDS_UDP_BROADCAST_SEND rc=$UDP_RC destination=255.255.255.255:19132"
else
  echo "CINTER_BDS_UDP_BROADCAST_SEND nc=unavailable"
fi
echo "CINTER_BDS_UDP_TABLE_BEGIN"
cat /proc/net/udp 2>&1 || true
cat /proc/net/udp6 2>&1 || true
echo "CINTER_BDS_UDP_TABLE_END"
echo "CINTER_BDS_UDP_PRELAUNCH_END"
echo "CINTER_BDS_NETWORK_DIAGNOSTIC_END"
cd "$BDS_ROOT"
echo "CINTER_BDS_ROOT_RESOLVED $BDS_ROOT"
echo "CINTER_BDS_WORKDIR $(pwd)"
if [ -f "$BDS_ROOT/server.properties" ]; then
  echo "CINTER_BDS_PROPERTIES_VISIBLE path=$BDS_ROOT/server.properties bytes=$(wc -c < "$BDS_ROOT/server.properties" | tr -d ' ')"
  LEVEL_NAME=$(sed -n 's/^level-name=//p' "$BDS_ROOT/server.properties" | head -n 1)
  echo "CINTER_BDS_LEVEL_NAME ${LEVEL_NAME:-<unset>}"
else
  echo "CINTER_BDS_ERROR server.properties-not-visible root=$BDS_ROOT cwd=$(pwd)" >&2
  exit 28
fi
echo "CINTER_BDS_WORLDS_BEGIN"
find worlds -mindepth 1 -maxdepth 1 -type d -print 2>/dev/null || true
echo "CINTER_BDS_WORLDS_END"
# Execute BDS as BDS, not as ld-linux.  Invoking the loader as argv[0] makes
# /proc/self/exe identify the loader, which causes BDS to resolve data files
# (for example profiler bootstrap.json) under .cinter/glibc-x86_64/data.
# Materialize the interpreter at the ELF-requested path, then let Rosetta's
# binfmt handler execute bedrock_server directly.
mkdir -p /lib64
cp "$GLIBC_ROOT/ld-linux-x86-64.so.2" /lib64/ld-linux-x86-64.so.2
chmod 755 /lib64/ld-linux-x86-64.so.2
export LD_LIBRARY_PATH="$GLIBC_ROOT:$BDS_ROOT${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
[ -x "$BDS_ROOT/.cinter/cinter-nethernet-probe" ] || { echo "CINTER_BDS_ERROR missing $BDS_ROOT/.cinter/cinter-nethernet-probe" >&2; exit 22; }
echo "CINTER_BDS_EXEC $BDS_ROOT/bedrock_server"
# BDS is launched as an asynchronous command so the NetherNet readiness probe can
# run beside it.  In a non-interactive POSIX shell an asynchronous command with no
# explicit stdin redirection may receive /dev/null instead of the shell's stdin.
# Attach all three BDS console descriptors directly to the VM serial console.
# This deliberately avoids the former FIFO/cat relay, which put Rosetta BDS behind
# a Linux pipe and native runs eventually wedged in pipe_read.
echo "CINTER_BDS_CONSOLE_MODE direct-serial-console"
./bedrock_server </dev/console >/dev/console 2>&1 &
BDS_PID=$!
(
  # Keep this deadline comfortably inside the host-side smoke deadline.
  # One probe already tries IPv6 loopback and IPv4 loopback.
  # address, so probing a second port doubled the wall time without adding
  # evidence for the configured BDS port (19132).
  i=0
  while [ "$i" -lt 12 ]; do
    # This subshell is a child of the LinuxKit service.  Write acceptance
    # markers to the service stdout/stderr inherited by containerd.  Writing
    # them to /dev/console races BDS, which also owns /dev/console for its
    # interactive stdin/stdout and caused the host smoke to wait forever even
    # after BDS printed "Server started.".
    echo "CINTER_BDS_NETHERNET_ATTEMPT attempt=$((i+1)) port=19132"
    if "$BDS_ROOT/.cinter/cinter-nethernet-probe" 19132 >/dev/null 2>&1; then
      echo "CINTER_BDS_NETHERNET_PASS port=19132"
      exit 0
    fi
    i=$((i+1)); sleep 1
  done
  echo "CINTER_BDS_NETHERNET_DIAGNOSTIC_BEGIN"
  echo "CINTER_BDS_NETHERNET_DIAGNOSTIC port=19132"
  "$BDS_ROOT/.cinter/cinter-nethernet-probe" 19132 2>&1 || true
  echo "CINTER_BDS_NETHERNET_FAIL timeout attempts=$i port=19132"
) &
PROBE_PID=$!
set +e
wait "$BDS_PID"
RC=$?
kill "$PROBE_PID" 2>/dev/null || true
wait "$PROBE_PID" 2>/dev/null || true
echo "CINTER_BDS_EXIT rc=$RC"
# This script runs inside a LinuxKit service container. `poweroff` here is not a
# reliable way to stop the enclosing Virtualization.framework VM. Publish the
# completed BDS result through the writable VirtioFS share; CinterBDSVM owns the
# VM lifecycle and will stop the VM only after seeing this marker.
printf '%s\n' "$RC" > "$BDS_ROOT/.cinter/vm-stop-request"
echo "CINTER_BDS_VM_STOP_REQUEST rc=$RC"
exit "$RC"
'''

SWIFT = r'''import Foundation
import Virtualization

@available(macOS 13.0, *)
final class CinterVMDelegate: NSObject, VZVirtualMachineDelegate {
    var requestedExitCode: Int32?
    func guestDidStop(_ virtualMachine: VZVirtualMachine) { exit(requestedExitCode ?? 0) }
    func virtualMachine(_ virtualMachine: VZVirtualMachine, didStopWithError error: Error) {
        fputs("CINTER_VM_ERROR \(error)\n", stderr); exit(30)
    }
}

@available(macOS 13.0, *)
func runCinterBDSVM() throws {
    let env = ProcessInfo.processInfo.environment
    guard let kernel = env["CINTER_VM_KERNEL"], let initrd = env["CINTER_VM_INITRD"],
          let cmdlinePath = env["CINTER_VM_CMDLINE"], let bds = env["CINTER_BDS_HOST_DIR"] else {
        throw NSError(domain:"CinterBDSVM", code:2, userInfo:[NSLocalizedDescriptionKey:
          "CINTER_VM_KERNEL, CINTER_VM_INITRD, CINTER_VM_CMDLINE and CINTER_BDS_HOST_DIR are required"])
    }
    if VZLinuxRosettaDirectoryShare.availability == .notInstalled {
        let install = DispatchSemaphore(value: 0)
        var installError: Error?
        VZLinuxRosettaDirectoryShare.installRosetta { error in installError = error; install.signal() }
        install.wait()
        if let error = installError { throw error }
    }
    guard VZLinuxRosettaDirectoryShare.availability == .installed else {
        throw NSError(domain:"CinterBDSVM", code:3, userInfo:[NSLocalizedDescriptionKey:"Rosetta for Linux is unavailable on this Mac"])
    }
    let config = VZVirtualMachineConfiguration()
    config.cpuCount = max(2, min(4, VZVirtualMachineConfiguration.maximumAllowedCPUCount))
    config.memorySize = 4 * 1024 * 1024 * 1024
    let boot = VZLinuxBootLoader(kernelURL: URL(fileURLWithPath: kernel))
    boot.initialRamdiskURL = URL(fileURLWithPath: initrd)
    let generatedCommandLine = try String(contentsOfFile: cmdlinePath, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines)
    guard !generatedCommandLine.isEmpty else {
        throw NSError(domain:"CinterBDSVM", code:4, userInfo:[NSLocalizedDescriptionKey:"generated LinuxKit kernel command line is empty"])
    }
    boot.commandLine = generatedCommandLine
    config.bootLoader = boot
    let console = VZVirtioConsoleDeviceSerialPortConfiguration()
    console.attachment = VZFileHandleSerialPortAttachment(fileHandleForReading: .standardInput,
                                                           fileHandleForWriting: .standardOutput)
    config.serialPorts = [console]
    let bdsShare = VZSharedDirectory(url: URL(fileURLWithPath: bds), readOnly: false)
    let bdsFS = VZVirtioFileSystemDeviceConfiguration(tag: "cinter-bds")
    bdsFS.share = VZSingleDirectoryShare(directory: bdsShare)
    let rosettaFS = VZVirtioFileSystemDeviceConfiguration(tag: "rosetta")
    rosettaFS.share = try VZLinuxRosettaDirectoryShare()
    config.directorySharingDevices = [bdsFS, rosettaFS]
    let network = VZVirtioNetworkDeviceConfiguration()
    network.attachment = VZNATNetworkDeviceAttachment()
    config.networkDevices = [network]
    config.entropyDevices = [VZVirtioEntropyDeviceConfiguration()]
    try config.validate()
    let vm = VZVirtualMachine(configuration: config)
    let delegate = CinterVMDelegate(); vm.delegate = delegate
    let stopRequestURL = URL(fileURLWithPath: bds).appendingPathComponent(".cinter/vm-stop-request")
    try? FileManager.default.removeItem(at: stopRequestURL)
    var stopRequested = false
    let stopWatcher = DispatchSource.makeTimerSource(queue: .main)
    stopWatcher.schedule(deadline: .now(), repeating: .milliseconds(100))
    stopWatcher.setEventHandler {
        guard !stopRequested, let raw = try? String(contentsOf: stopRequestURL, encoding: .utf8),
              let code = Int32(raw.trimmingCharacters(in: .whitespacesAndNewlines)) else { return }
        stopRequested = true
        delegate.requestedExitCode = code
        fputs("CINTER_VM_STOP_REQUESTED bds_rc=\(code)\n", stderr); fflush(stderr)
        vm.stop { error in
            if let error = error {
                fputs("CINTER_VM_ERROR stop failed: \(error)\n", stderr); fflush(stderr); exit(32)
            }
            fputs("CINTER_VM_STOPPED bds_rc=\(code)\n", stderr); fflush(stderr)
            exit(code)
        }
    }
    stopWatcher.resume()
    vm.start { result in
        switch result {
        case .success:
            fputs("CINTER_VM_STARTED\n", stderr); fflush(stderr)
        case .failure(let error):
            fputs("CINTER_VM_ERROR \(error)\n", stderr); fflush(stderr); exit(31)
        }
    }
    dispatchMain()
}

if #available(macOS 13.0, *) {
    do { try runCinterBDSVM() } catch { fputs("CINTER_VM_ERROR \(error)\n", stderr); exit(1) }
} else { fputs("CINTER_VM_ERROR macOS 13 or newer required\n", stderr); exit(1) }
'''

LINUXKIT_YAML = r'''kernel:
  image: __CINTER_KERNEL_IMAGE__
  cmdline: "console=hvc0"
init:
  - "@pkg:./linuxkit-source/pkg/init"
  - "@pkg:./linuxkit-source/pkg/runc"
  - "@pkg:./linuxkit-source/pkg/containerd"
  - "@pkg:./linuxkit-source/pkg/ca-certificates"
onboot:
  # LinuxKit's root /etc is immutable. Follow LinuxKit's documented dhcpcd
  # pattern: create host /tmp/etc at boot, mount that DIRECTORY as dhcpcd's
  # /etc, then bind its generated resolv.conf into later services.
  - name: resolver-init
    image: alpine:3.20
    binds:
      - /tmp:/tmp
    command:
      - /bin/sh
      - -c
      - 'mkdir -p /tmp/etc'
  - name: dhcpcd
    image: "@pkg:./linuxkit-source/pkg/dhcpcd"
    net: host
    binds:
      - /var:/var
      - /tmp/etc:/etc
    capabilities:
      - CAP_NET_ADMIN
      - CAP_NET_BIND_SERVICE
      - CAP_NET_RAW
    command:
      - /sbin/dhcpcd
      - --nobackground
      - -f
      - /dhcpcd.conf
      - "-1"
services:
  - name: xbox-auth-curl
    image: curlimages/curl@sha256:d9b4541e214bcd85196d6e92e2753ac6d0ea699f0af5741f8c6cccbfcf00ef4b
    net: host
    devices:
      - path: /dev/console
        type: c
        major: 5
        minor: 1
        mode: "0666"
    command:
      - /bin/sh
      - -c
      - |
        exec >/dev/console 2>&1
        rm -f /var/cinter-xbox-auth-curl.done
        echo CINTER_XBOX_AUTH_CURL_BEGIN
        set +e
        curl -iv --connect-timeout 10 --max-time 20 https://auth.xboxlive.com/
        rc=$?
        echo CINTER_XBOX_AUTH_CURL_END rc=$rc
        printf '%s\n' "$rc" > /var/cinter-xbox-auth-curl.done
        exit 0
    binds:
      - /var:/var
      - /tmp/etc/resolv.conf:/etc/resolv.conf

  - name: cinter-bds
    image: alpine:3.20
    net: host
    binds:
      - /proc:/proc
      - /sys:/sys
      - /var:/var
      - /usr/local/bin/cinter-bds-guest.sh:/usr/local/bin/cinter-bds-guest.sh
      - /etc/ssl/certs:/etc/ssl/certs
      - /tmp/etc/resolv.conf:/etc/resolv.conf

    devices:
      - path: /dev/console
        type: c
        major: 5
        minor: 1
        mode: "0666"
    capabilities:
      - CAP_SYS_ADMIN
      - CAP_SYS_BOOT
      - CAP_NET_ADMIN
      - CAP_NET_RAW
    command:
      - /bin/sh
      - -c
      - |
        exec >/dev/console 2>&1
        set -eux
        echo CINTER_BDS_GUEST_BOOT
        echo CINTER_BDS_NETWORK_READY_CHECK_BEGIN
        ip -4 addr show dev eth0 2>&1 || true
        ip route 2>&1 || true
        cat /etc/resolv.conf 2>&1 || true
        ip -4 addr show dev eth0 | grep -q 'inet ' || { echo CINTER_BDS_ERROR network-no-ipv4; exit 29; }
        ip route | grep -q '^default ' || { echo CINTER_BDS_ERROR network-no-default-route; exit 30; }
        grep -Eq '^[[:space:]]*nameserver[[:space:]]+[^[:space:]]+' /etc/resolv.conf || { echo CINTER_BDS_ERROR network-no-dns; exit 31; }
        echo CINTER_BDS_NETWORK_READY_CHECK_PASS
        echo CINTER_XBOX_AUTH_CURL_WAIT
        i=0
        while [ ! -f /var/cinter-xbox-auth-curl.done ] && [ "$i" -lt 30 ]; do i=$((i+1)); sleep 1; done
        [ -f /var/cinter-xbox-auth-curl.done ] || { echo CINTER_BDS_ERROR xbox-auth-curl-timeout; exit 28; }
        echo "CINTER_XBOX_AUTH_CURL_RESULT rc=$(cat /var/cinter-xbox-auth-curl.done)"
        mkdir -p /mnt/cinter-bds /mnt/rosetta
        echo CINTER_BDS_MOUNT_BEGIN cinter-bds
        timeout 10 mount -t virtiofs cinter-bds /mnt/cinter-bds || { rc=$?; echo CINTER_BDS_ERROR cinter-bds-mount rc=$rc; dmesg | tail -40; exit 24; }
        echo CINTER_BDS_MOUNT_PASS cinter-bds
        mkdir -p /mnt/cinter-bds/.cinter
        GUEST_IP=$(ip -4 -o addr show dev eth0 | awk '{split($4,a,"/"); print a[1]; exit}')
        printf '%s\n' "$GUEST_IP" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' || { echo CINTER_BDS_ERROR guest-ip-unavailable; exit 32; }
        printf '%s\n' "$GUEST_IP" > /mnt/cinter-bds/.cinter/guest-ip
        echo "CINTER_BDS_GUEST_IP_PUBLISHED $GUEST_IP"
        echo CINTER_BDS_MOUNT_BEGIN rosetta
        timeout 10 mount -t virtiofs rosetta /mnt/rosetta || { rc=$?; echo CINTER_BDS_ERROR rosetta-mount rc=$rc; dmesg | tail -40; exit 25; }
        echo CINTER_BDS_MOUNT_PASS rosetta
        mount -t binfmt_misc binfmt_misc /proc/sys/fs/binfmt_misc || true
        exec /usr/local/bin/cinter-bds-guest.sh </dev/console >/dev/console 2>&1
files:
  - path: usr/local/bin/cinter-bds-guest.sh
    mode: "0755"
    contents: |
__CINTER_GUEST_YAML__
trust:
  org:
    - linuxkit
'''


BUILD_SH = r'''#!/bin/sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if ! command -v linuxkit >/dev/null 2>&1; then
  command -v brew >/dev/null 2>&1 || { echo "CINTER_VM_ERROR linuxkit is missing and Homebrew is unavailable" >&2; exit 40; }
  echo "[craftmaster-mac] installing LinuxKit build dependency from linuxkit/linuxkit Homebrew tap..."
  brew tap linuxkit/linuxkit
  brew trust linuxkit/linuxkit
  brew install --HEAD linuxkit
fi
cd "$HERE"
LINUXKIT_SOURCE="$HERE/linuxkit-source"
LINUXKIT_REF="v1.8.2"
LINUXKIT_COMMIT="43200ea"
if [ ! -d "$LINUXKIT_SOURCE/.git" ]; then
  command -v git >/dev/null 2>&1 || { echo "CINTER_VM_ERROR git is required to acquire pinned LinuxKit source" >&2; exit 44; }
  rm -rf "$LINUXKIT_SOURCE"
  echo "[craftmaster-mac] acquiring pinned LinuxKit source $LINUXKIT_REF ($LINUXKIT_COMMIT)..."
  git clone --depth 1 --branch "$LINUXKIT_REF" --single-branch https://github.com/linuxkit/linuxkit.git "$LINUXKIT_SOURCE"
else
  git -C "$LINUXKIT_SOURCE" fetch --depth 1 origin "refs/tags/$LINUXKIT_REF:refs/tags/$LINUXKIT_REF"
  git -c advice.detachedHead=false -C "$LINUXKIT_SOURCE" checkout --detach "$LINUXKIT_REF"
fi
RESOLVED_LINUXKIT_COMMIT=$(git -C "$LINUXKIT_SOURCE" rev-parse --short=7 HEAD)
[ "$RESOLVED_LINUXKIT_COMMIT" = "$LINUXKIT_COMMIT" ] || { echo "CINTER_VM_ERROR LinuxKit source commit mismatch: expected $LINUXKIT_COMMIT got $RESOLVED_LINUXKIT_COMMIT" >&2; exit 45; }
ARCH=$(uname -m)
[ "$ARCH" = "arm64" ] || { echo "CINTER_VM_ERROR CraftMaster macOS guest build requires Apple Silicon arm64; got $ARCH" >&2; exit 43; }
# LinuxKit custom kernel packages are OCI builds.  Keep that maintainer-only
# dependency local and automatic: use an existing Docker daemon when present,
# otherwise bootstrap the open-source Colima VM and Docker CLI with Homebrew.
CINTER_STARTED_COLIMA=0
if ! command -v docker >/dev/null 2>&1; then
  command -v brew >/dev/null 2>&1 || { echo "CINTER_VM_ERROR custom LinuxKit kernel requires Docker CLI; Homebrew is unavailable" >&2; exit 48; }
  echo "[craftmaster-mac] installing maintainer-only Docker CLI + Colima kernel-build dependencies..."
  brew install docker colima
fi
if ! docker info >/dev/null 2>&1; then
  if ! command -v colima >/dev/null 2>&1; then
    command -v brew >/dev/null 2>&1 || { echo "CINTER_VM_ERROR Docker daemon unavailable and Colima is not installed" >&2; exit 48; }
    echo "[craftmaster-mac] installing maintainer-only Colima kernel-build dependency..."
    brew install colima
  fi
  echo "[craftmaster-mac] starting maintainer-only Colima Docker daemon for LinuxKit kernel build..."
  colima start --cpu 4 --memory 6 --disk 30
  CINTER_STARTED_COLIMA=1
fi
docker info >/dev/null 2>&1 || { echo "CINTER_VM_ERROR Docker daemon unavailable after Colima bootstrap" >&2; exit 48; }
# The guest is ARM64. Build the acceptance probe natively for the guest instead
# of running a second x86-64 process through Rosetta beside BDS. This keeps
# NetherNet verification independent of Rosetta's socket translation.
echo "[craftmaster-mac] building native ARM64 guest NetherNet probe..."
docker run --rm --platform linux/arm64 -v "$HERE:/work" -w /work alpine:3.20 sh -ec 'apk add --no-cache build-base >/dev/null && cc -O2 -static -o cinter-nethernet-probe-linux-arm64 cinter_nethernet_probe.c'
[ -x "$HERE/cinter-nethernet-probe-linux-arm64" ] || { echo "CINTER_VM_ERROR ARM64 NetherNet probe build failed" >&2; exit 58; }
file "$HERE/cinter-nethernet-probe-linux-arm64" | grep -Eq 'ARM aarch64|ARM64|aarch64' || { echo "CINTER_VM_ERROR NetherNet probe is not ARM64" >&2; exit 59; }
echo "CINTER_VM_NETHERNET_PROBE_ARM64_READY"
# LinuxKit v1.8.2 does not follow Docker's current context when choosing its
# BuildKit node: absent an explicit mapping it falls back to the context named
# "default".  Colima deliberately uses its own context, so bind the native
# ARM64 build to the daemon we just verified instead of silently targeting a
# different/non-running Docker endpoint.
CINTER_DOCKER_CONTEXT=$(docker context show)
[ -n "$CINTER_DOCKER_CONTEXT" ] || { echo "CINTER_VM_ERROR Docker current context is empty" >&2; exit 49; }
export LINUXKIT_BUILDERS="linux/arm64=$CINTER_DOCKER_CONTEXT"
echo "[craftmaster-mac] LinuxKit ARM64 builder context: $CINTER_DOCKER_CONTEXT"
# BDS is a dynamically linked x86-64 GNU/Linux executable. Rosetta translates
# its instructions, but Linux still needs the x86-64 ELF interpreter and glibc.
# Materialize that compatibility runtime without executing foreign code: create
# a stopped amd64 Debian container, copy its loader/runtime libraries, and ship
# them inside the finished Mac bootstrap.
GLIBC_ROOT="$HERE/glibc-x86_64"
GLIBC_IMAGE="debian:bookworm-slim"
rm -rf "$GLIBC_ROOT"
mkdir -p "$GLIBC_ROOT"
echo "[craftmaster-mac] acquiring x86-64 glibc runtime for Rosetta BDS..."
docker pull --platform linux/amd64 "$GLIBC_IMAGE" >/dev/null
GLIBC_CONTAINER=$(docker create --platform linux/amd64 "$GLIBC_IMAGE" /bin/true)
cleanup_glibc_container() { docker rm -f "$GLIBC_CONTAINER" >/dev/null 2>&1 || true; }
trap cleanup_glibc_container INT TERM
docker cp "$GLIBC_CONTAINER:/lib/x86_64-linux-gnu/." "$GLIBC_ROOT/"
# docker cp preserves /lib64/ld-linux-x86-64.so.2 as Debian's absolute symlink
# into /lib/x86_64-linux-gnu.  That target does not exist on the macOS host, so
# materialize the loader bytes from the real multiarch path instead of copying
# the symlink.
docker cp "$GLIBC_CONTAINER:/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2" "$GLIBC_ROOT/ld-linux-x86-64.so.2"
cleanup_glibc_container
GLIBC_CONTAINER=""
[ -f "$GLIBC_ROOT/ld-linux-x86-64.so.2" ] && [ ! -L "$GLIBC_ROOT/ld-linux-x86-64.so.2" ] || { echo "CINTER_VM_ERROR x86-64 glibc loader was not materialized as a regular file" >&2; exit 55; }
chmod 755 "$GLIBC_ROOT/ld-linux-x86-64.so.2"
for lib in libc.so.6 libm.so.6 libpthread.so.0 libdl.so.2 librt.so.1 libgcc_s.so.1; do
  [ -e "$GLIBC_ROOT/$lib" ] || { echo "CINTER_VM_ERROR x86-64 glibc runtime missing $lib" >&2; exit 56; }
done
docker image inspect "$GLIBC_IMAGE" --format '{{index .RepoDigests 0}}' > "$GLIBC_ROOT/SOURCE_IMAGE.txt"
echo "CINTER_VM_GLIBC_X86_64_READY $(cat "$GLIBC_ROOT/SOURCE_IMAGE.txt")"
cleanup_kernel_builder() {
  if [ "${GLIBC_CONTAINER:-}" != "" ]; then docker rm -f "$GLIBC_CONTAINER" >/dev/null 2>&1 || true; fi
  if [ "$CINTER_STARTED_COLIMA" = "1" ]; then
    echo "[craftmaster-mac] stopping maintainer-only Colima Docker daemon..."
    colima stop || true
  fi
}
trap cleanup_kernel_builder EXIT INT TERM
rm -f cinter-bds-kernel cinter-bds-initrd.img cinter-bds-cmdline
# LinuxKit v1.8.2's stock ARM64 6.6 kernel explicitly disables CONFIG_VIRTIO_FS.
# Build a local pinned variant with VirtioFS built into the kernel.  This is a
# maintainer/update-data dependency only; end users receive the finished guest.
KERNEL_VERSION="6.6.71"
KERNEL_DIR="$LINUXKIT_SOURCE/kernel"
KERNEL_EXTRA="-cinter-virtiofs"
KERNEL_EXTRA_DIR="$KERNEL_DIR/6.6.x-${KERNEL_EXTRA}"
KERNEL_FRAGMENT="$KERNEL_EXTRA_DIR/config-aarch64"
mkdir -p "$KERNEL_EXTRA_DIR"
printf '%s\n' 'CONFIG_VIRTIO_FS=y' > "$KERNEL_FRAGMENT"
KERNEL_BUILD_ARGS="$HERE/cinter-kernel-build-args"
cat "$KERNEL_DIR/6.6.x/build-args" > "$KERNEL_BUILD_ARGS"
printf 'EXTRA=%s\n' "$KERNEL_EXTRA" >> "$KERNEL_BUILD_ARGS"
echo "[craftmaster-mac] building LinuxKit ARM64 kernel $KERNEL_VERSION with CONFIG_VIRTIO_FS=y..."
cd "$KERNEL_DIR"
linuxkit pkg build . --force --platforms linux/arm64 --build-yml ./build-kernel.yml --tag "$KERNEL_VERSION-cinter-virtiofs-{{.Hash}}" --build-arg-file "$KERNEL_BUILD_ARGS"
cd "$HERE"
CINTER_KERNEL_IMAGE=$(cd "$KERNEL_DIR" && linuxkit pkg show-tag . --build-yml ./build-kernel.yml --tag "$KERNEL_VERSION-cinter-virtiofs-{{.Hash}}")
[ -n "$CINTER_KERNEL_IMAGE" ] || { echo "CINTER_VM_ERROR custom LinuxKit kernel tag resolution failed" >&2; exit 46; }
echo "[craftmaster-mac] custom kernel package: $CINTER_KERNEL_IMAGE"
python3 - "$CINTER_KERNEL_IMAGE" <<'PYKERNELYAML'
from pathlib import Path
import sys
p = Path('linuxkit.yml')
s = p.read_text()
lines = s.splitlines()
if not lines or lines[0] != 'kernel:' or not lines[1].lstrip().startswith('image:'):
    raise SystemExit('CINTER_VM_ERROR unexpected linuxkit.yml kernel stanza')
lines[1] = '  image: ' + sys.argv[1]
p.write_text('\n'.join(lines) + '\n')
PYKERNELYAML
linuxkit build --arch arm64 --format kernel+initrd --name cinter-bds linuxkit.yml
# This marker means the LinuxKit build was explicitly wired to the EXTRA fragment.
# Runtime acceptance remains the real VirtioFS mount in the guest; do not claim
# the kernel feature is present merely because the source fragment exists.
grep -qx 'EXTRA=-cinter-virtiofs' "$KERNEL_BUILD_ARGS" || { echo "CINTER_VM_ERROR custom kernel EXTRA build arg was lost" >&2; exit 47; }
grep -qx 'CONFIG_VIRTIO_FS=y' "$KERNEL_FRAGMENT" || { echo "CINTER_VM_ERROR custom kernel VirtioFS fragment was lost" >&2; exit 48; }
echo CINTER_VM_KERNEL_VIRTIOFS_REQUESTED
# --name controls the kernel+initrd output basename; do not rename nonexistent linuxkit-* outputs.
[ -s cinter-bds-kernel ] || { echo "CINTER_VM_ERROR LinuxKit kernel was not produced" >&2; exit 41; }
[ -s cinter-bds-initrd.img ] || { echo "CINTER_VM_ERROR LinuxKit initrd was not produced" >&2; exit 42; }
# VZLinuxBootLoader on Apple Silicon requires an uncompressed ARM64 Linux Image.
# Normalize LinuxKit's runner-oriented kernel artifact at maintainer build time.
python3 - <<'PYKERNEL'
from pathlib import Path
import gzip
p = Path("cinter-bds-kernel")
data = p.read_bytes()
if data[:2] == b"\x1f\x8b":
    data = gzip.decompress(data)
    p.write_bytes(data)
    print("CINTER_VM_KERNEL_DECOMPRESSED")
if len(data) < 0x3c or data[0x38:0x3c] != b"ARMd":
    raise SystemExit("CINTER_VM_ERROR LinuxKit kernel is not an uncompressed ARM64 Linux Image")
print(f"CINTER_VM_KERNEL_VERIFIED bytes={len(data)}")
PYKERNEL
[ -s "$HERE/source-fingerprint.txt" ] || { echo "CINTER_VM_ERROR missing source-fingerprint.txt" >&2; exit 57; }
cp "$HERE/source-fingerprint.txt" "$HERE/built-source-fingerprint.txt"
echo "CINTER_VM_GUEST_BUILT fingerprint=$(cat "$HERE/built-source-fingerprint.txt") kernel=$HERE/cinter-bds-kernel initrd=$HERE/cinter-bds-initrd.img"
'''

LAN_RELAY_C = r'''#include <arpa/inet.h>
#include <netinet/in.h>
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <unistd.h>

static volatile sig_atomic_t running = 1;
static char guest_ip[INET_ADDRSTRLEN];
static char host_ip[INET_ADDRSTRLEN];
static void on_signal(int sig) { (void)sig; running = 0; }
static int connect_guest(int port) {
    int fd=socket(AF_INET,SOCK_STREAM,0); if(fd<0)return -1;
    struct sockaddr_in a={0}; a.sin_family=AF_INET; a.sin_port=htons((uint16_t)port);
    if(inet_pton(AF_INET,guest_ip,&a.sin_addr)!=1 || connect(fd,(struct sockaddr*)&a,sizeof a)!=0){close(fd);return -1;} return fd;
}
struct tcp_pair{int client;int guest;};
static void *tcp_pair_main(void *opaque){
    struct tcp_pair*p=opaque; char buf[65536];
    while(running){fd_set r;FD_ZERO(&r);FD_SET(p->client,&r);FD_SET(p->guest,&r);int hi=p->client>p->guest?p->client:p->guest;if(select(hi+1,&r,NULL,NULL,NULL)<=0)break;int src=FD_ISSET(p->client,&r)?p->client:p->guest;int dst=src==p->client?p->guest:p->client;ssize_t n=recv(src,buf,sizeof buf,0);if(n<=0)break;char*q=buf;ssize_t left=n;while(left>0){ssize_t w=send(dst,q,(size_t)left,0);if(w<=0){left=-1;break;}q+=w;left-=w;}if(left<0)break;}
    close(p->client);close(p->guest);free(p);return NULL;
}
static void *tcp_main(void *opaque){
    int port=*(int*)opaque;int fd=socket(AF_INET,SOCK_STREAM,0),one=1;if(fd<0){running=0;return NULL;}setsockopt(fd,SOL_SOCKET,SO_REUSEADDR,&one,sizeof one);struct sockaddr_in a={0};a.sin_family=AF_INET;a.sin_port=htons((uint16_t)port);a.sin_addr.s_addr=htonl(INADDR_ANY);if(bind(fd,(struct sockaddr*)&a,sizeof a)!=0||listen(fd,32)!=0){perror("CINTER_BDS_LAN_RELAY tcp bind/listen");close(fd);running=0;return NULL;}fprintf(stderr,"CINTER_BDS_LAN_RELAY_TCP_READY port=%d guest=%s\n",port,guest_ip);fflush(stderr);
    while(running){struct timeval tv={1,0};fd_set r;FD_ZERO(&r);FD_SET(fd,&r);if(select(fd+1,&r,NULL,NULL,&tv)<=0)continue;int client=accept(fd,NULL,NULL);if(client<0)continue;int guest=connect_guest(port);if(guest<0){close(client);continue;}struct tcp_pair*pair=malloc(sizeof*pair);if(!pair){close(client);close(guest);continue;}pair->client=client;pair->guest=guest;pthread_t t;if(pthread_create(&t,NULL,tcp_pair_main,pair)==0)pthread_detach(t);else{close(client);close(guest);free(pair);}}close(fd);return NULL;
}
struct udp_arg{int port;};
static void *udp_main(void *opaque){
    int port=((struct udp_arg*)opaque)->port;int fd=socket(AF_INET,SOCK_DGRAM,0),one=1;if(fd<0){running=0;return NULL;}setsockopt(fd,SOL_SOCKET,SO_REUSEADDR,&one,sizeof one);setsockopt(fd,SOL_SOCKET,SO_BROADCAST,&one,sizeof one);struct sockaddr_in bind_addr={0};bind_addr.sin_family=AF_INET;bind_addr.sin_port=htons((uint16_t)port);bind_addr.sin_addr.s_addr=htonl(INADDR_ANY);if(bind(fd,(struct sockaddr*)&bind_addr,sizeof bind_addr)!=0){perror("CINTER_BDS_LAN_RELAY udp bind");close(fd);running=0;return NULL;}struct sockaddr_in guest={0};guest.sin_family=AF_INET;guest.sin_port=htons((uint16_t)port);inet_pton(AF_INET,guest_ip,&guest.sin_addr);struct sockaddr_in client={0};socklen_t client_len=sizeof client;int have_client=0;fprintf(stderr,"CINTER_BDS_LAN_RELAY_UDP_READY port=%d guest=%s\n",port,guest_ip);fflush(stderr);char buf[65536];
    while(running){struct timeval tv={1,0};fd_set r;FD_ZERO(&r);FD_SET(fd,&r);if(select(fd+1,&r,NULL,NULL,&tv)<=0)continue;struct sockaddr_in src={0};socklen_t slen=sizeof src;ssize_t n=recvfrom(fd,buf,sizeof buf,0,(struct sockaddr*)&src,&slen);if(n<=0)continue;if(src.sin_addr.s_addr==guest.sin_addr.s_addr){if(have_client)sendto(fd,buf,(size_t)n,0,(struct sockaddr*)&client,client_len);}else{client=src;client_len=slen;have_client=1;sendto(fd,buf,(size_t)n,0,(struct sockaddr*)&guest,sizeof guest);}}close(fd);return NULL;
}
static void *discovery_main(void *opaque){
    (void)opaque;int port=7551;int fd=socket(AF_INET,SOCK_DGRAM,0),one=1;if(fd<0){running=0;return NULL;}setsockopt(fd,SOL_SOCKET,SO_REUSEADDR,&one,sizeof one);setsockopt(fd,SOL_SOCKET,SO_BROADCAST,&one,sizeof one);struct sockaddr_in bind_addr={0};bind_addr.sin_family=AF_INET;bind_addr.sin_port=htons((uint16_t)port);bind_addr.sin_addr.s_addr=htonl(INADDR_ANY);if(bind(fd,(struct sockaddr*)&bind_addr,sizeof bind_addr)!=0){perror("CINTER_BDS_LAN_DISCOVERY bind");close(fd);running=0;return NULL;}struct sockaddr_in guest={0};guest.sin_family=AF_INET;guest.sin_port=htons((uint16_t)port);inet_pton(AF_INET,guest_ip,&guest.sin_addr);struct sockaddr_in broadcast={0};broadcast.sin_family=AF_INET;broadcast.sin_port=htons((uint16_t)port);broadcast.sin_addr.s_addr=htonl(INADDR_BROADCAST);fprintf(stderr,"CINTER_BDS_LAN_DISCOVERY_READY port=7551 guest=%s\n",guest_ip);fflush(stderr);char buf[65536];
    while(running){struct timeval tv={1,0};fd_set r;FD_ZERO(&r);FD_SET(fd,&r);if(select(fd+1,&r,NULL,NULL,&tv)<=0)continue;struct sockaddr_in src={0};socklen_t slen=sizeof src;ssize_t n=recvfrom(fd,buf,sizeof buf,0,(struct sockaddr*)&src,&slen);if(n<=0)continue;char src_ip[INET_ADDRSTRLEN]={0};inet_ntop(AF_INET,&src.sin_addr,src_ip,sizeof src_ip);if(src.sin_addr.s_addr==guest.sin_addr.s_addr){fprintf(stderr,"CINTER_BDS_LAN_DISCOVERY_RESPONSE bytes=%zd guest=%s\n",n,guest_ip);fflush(stderr);if(sendto(fd,buf,(size_t)n,0,(struct sockaddr*)&broadcast,sizeof broadcast)<0)perror("CINTER_BDS_LAN_DISCOVERY broadcast sendto");}else if(strcmp(src_ip,host_ip)==0){fprintf(stderr,"CINTER_BDS_LAN_DISCOVERY_SELF_SUPPRESSED bytes=%zd host=%s\n",n,host_ip);fflush(stderr);}else{fprintf(stderr,"CINTER_BDS_LAN_DISCOVERY_REQUEST bytes=%zd client=%s:%u\n",n,src_ip,(unsigned)ntohs(src.sin_port));fflush(stderr);if(sendto(fd,buf,(size_t)n,0,(struct sockaddr*)&guest,sizeof guest)<0)perror("CINTER_BDS_LAN_DISCOVERY guest sendto");}}
    close(fd);return NULL;
}
static int read_guest_ip(const char*path){for(int i=0;i<300&&running;++i){FILE*f=fopen(path,"r");if(f){char line[128]={0};if(fgets(line,sizeof line,f)){line[strcspn(line,"\r\n")]=0;struct in_addr a;if(inet_pton(AF_INET,line,&a)==1){snprintf(guest_ip,sizeof guest_ip,"%s",line);fclose(f);return 0;}}fclose(f);}usleep(100000);}return -1;}
int main(int argc,char**argv){if(argc!=3){fprintf(stderr,"usage: %s /path/to/guest-ip host-lan-ipv4\n",argv[0]);return 2;}struct in_addr host_addr;if(inet_pton(AF_INET,argv[2],&host_addr)!=1){fprintf(stderr,"CINTER_BDS_LAN_RELAY_ERROR invalid-host-ip %s\n",argv[2]);return 6;}snprintf(host_ip,sizeof host_ip,"%s",argv[2]);signal(SIGINT,on_signal);signal(SIGTERM,on_signal);if(read_guest_ip(argv[1])!=0){fprintf(stderr,"CINTER_BDS_LAN_RELAY_ERROR guest-ip-timeout path=%s\n",argv[1]);return 3;}fprintf(stderr,"CINTER_BDS_LAN_RELAY_GUEST guest=%s host=%s\n",guest_ip,host_ip);fflush(stderr);int tcp_port=19132;pthread_t tcp;if(pthread_create(&tcp,NULL,tcp_main,&tcp_port)!=0)return 4;pthread_t discovery;if(pthread_create(&discovery,NULL,discovery_main,NULL)!=0){running=0;pthread_join(tcp,NULL);return 5;}const int ports[]={19140,19141,19142,19143,19144,19145,19146,19147,19148,19149,19150,19151,19152,19153,19154,19155};struct udp_arg args[sizeof ports/sizeof ports[0]];pthread_t threads[sizeof ports/sizeof ports[0]];size_t started=0;for(size_t i=0;i<sizeof ports/sizeof ports[0];++i){args[i].port=ports[i];if(pthread_create(&threads[i],NULL,udp_main,&args[i])!=0){running=0;break;}started++;}while(running)sleep(1);pthread_join(tcp,NULL);pthread_join(discovery,NULL);for(size_t i=0;i<started;++i)pthread_join(threads[i],NULL);return 0;}
'''

RUN_SH = r'''#!/bin/sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BDS=${1:?usage: run-mac-bds.sh /path/to/extracted/Linux/Server}
BDS=$(cd "$BDS" 2>/dev/null && pwd -P) || { echo "CINTER_VM_ERROR BDS server directory not found: $1" >&2; exit 59; }
echo "CINTER_BDS_HOST_ROOT $BDS"
[ -x "$HERE/CinterBDSVM" ] || { echo "CINTER_VM_ERROR missing $HERE/CinterBDSVM; run preflight to build it" >&2; exit 50; }
[ -x "$HERE/CinterBDSNetRelay" ] || { echo "CINTER_VM_ERROR missing $HERE/CinterBDSNetRelay; run preflight to build it" >&2; exit 56; }
[ -s "$HERE/cinter-bds-kernel" ] || { echo "CINTER_VM_ERROR missing $HERE/cinter-bds-kernel" >&2; exit 51; }
[ -s "$HERE/cinter-bds-initrd.img" ] || { echo "CINTER_VM_ERROR missing $HERE/cinter-bds-initrd.img" >&2; exit 52; }
[ -s "$HERE/cinter-bds-cmdline" ] || { echo "CINTER_VM_ERROR missing $HERE/cinter-bds-cmdline" >&2; exit 54; }
[ -x "$BDS/bedrock_server" ] || { echo "CINTER_VM_ERROR missing $BDS/bedrock_server" >&2; exit 53; }
mkdir -p "$BDS/.cinter"
[ -d "$HERE/glibc-x86_64" ] || { echo "CINTER_VM_ERROR missing $HERE/glibc-x86_64; run preflight to build it" >&2; exit 55; }
rm -rf "$BDS/.cinter/glibc-x86_64"
cp -R "$HERE/glibc-x86_64" "$BDS/.cinter/glibc-x86_64"
cp "$HERE/cinter-nethernet-probe-linux-arm64" "$BDS/.cinter/cinter-nethernet-probe"
chmod 755 "$BDS/.cinter/cinter-nethernet-probe"
rm -f "$BDS/.cinter/guest-ip" "$BDS/.cinter/vm-stop-request"
DEFAULT_IF=$(/sbin/route -n get default 2>/dev/null | awk '/interface:/{print $2; exit}')
[ -n "$DEFAULT_IF" ] || { echo "CINTER_VM_ERROR cannot determine macOS default LAN interface" >&2; exit 57; }
HOST_IP=$(/usr/sbin/ipconfig getifaddr "$DEFAULT_IF" 2>/dev/null || true)
printf '%s\n' "$HOST_IP" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' || { echo "CINTER_VM_ERROR cannot determine macOS LAN IPv4 on $DEFAULT_IF" >&2; exit 58; }
PROPS="$BDS/server.properties"
PROPS_BACKUP="$BDS/.cinter/server.properties.cinter-host-backup"
[ -f "$PROPS" ] || : > "$PROPS"
cp "$PROPS" "$PROPS_BACKUP"
set_prop() { key=$1; value=$2; tmp="$PROPS.cinter-tmp"; awk -v key="$key" -v value="$value" 'BEGIN{done=0} index($0,key "=")==1 {if(!done){print key "=" value; done=1}; next} {print} END{if(!done) print key "=" value}' "$PROPS" > "$tmp"; mv "$tmp" "$PROPS"; }
set_prop transport nethernet
set_prop enable-lan-visibility true
set_prop server-udp-ports "$HOST_IP:19140-19155:19140-19155"
echo "CINTER_BDS_LAN_MAPPING host=$HOST_IP tcp=19132 udp=19140-19155 discovery_udp=7551"
"$HERE/CinterBDSNetRelay" "$BDS/.cinter/guest-ip" "$HOST_IP" &
RELAY_PID=$!
cleanup() { rc=$?; kill "$RELAY_PID" 2>/dev/null || true; wait "$RELAY_PID" 2>/dev/null || true; if [ -f "$PROPS_BACKUP" ]; then mv "$PROPS_BACKUP" "$PROPS"; fi; rm -f "$BDS/.cinter/guest-ip"; trap - EXIT INT TERM; exit "$rc"; }
trap cleanup EXIT INT TERM
export CINTER_VM_KERNEL="$HERE/cinter-bds-kernel"
export CINTER_VM_INITRD="$HERE/cinter-bds-initrd.img"
export CINTER_VM_CMDLINE="$HERE/cinter-bds-cmdline"
export CINTER_BDS_HOST_DIR="$BDS"
"$HERE/CinterBDSVM"
'''

def _nethernet_probe_source_path() -> Path:
    """Resolve the authored Linux probe shared with the CinterForgeOS build."""
    return (
        _BUILD_CINTERFORGE_ROOT / 'creativeSuite' / 'apps' / 'cinterforge-os'
        / 'linux' / 'probes' / 'cinter_nethernet_probe.c'
    )


def _compiled_source_payloads() -> dict[str, bytes]:
    """Return only source inputs that determine the compiled Mac guest/runtime."""
    probe = _nethernet_probe_source_path()
    if not probe.is_file():
        raise RuntimeError(f'Mac bootstrap NetherNet probe source missing: {probe}')
    guest_yaml='\n'.join('      '+line for line in GUEST.splitlines())
    return {
        'cinter-bds-guest.sh': GUEST.encode(),
        'CinterBDSVM.swift': SWIFT.encode(),
        'cinter_bds_lan_relay.c': LAN_RELAY_C.encode(),
        'linuxkit.yml': LINUXKIT_YAML.replace('__CINTER_GUEST_YAML__', guest_yaml).replace('__CINTER_KERNEL_IMAGE__', 'linuxkit/kernel:6.6.71').encode(),
        'build-mac-guest.sh': BUILD_SH.encode(),
        'run-mac-bds.sh': RUN_SH.encode(),
        'cinter_nethernet_probe.c': probe.read_bytes(),
    }

def source_fingerprint() -> str:
    """Fingerprint only inputs that are compiled into or launch the Mac guest."""
    h = hashlib.sha256()
    for name, payload in sorted(_compiled_source_payloads().items()):
        h.update(name.encode()+b'\0'+payload+b'\0')
    return h.hexdigest()

def bootstrap_sources_match(bootstrap: Path) -> bool:
    """True when an existing generated bootstrap has the current compiled inputs."""
    for name, payload in _compiled_source_payloads().items():
        path=bootstrap/name
        if not path.is_file() or path.read_bytes() != payload:
            return False
    return True


def build(root: Path) -> Path:
    out = root / 'MacBootstrap'
    # MacBootstrap is a generated workspace, but keep the directory inode when it
    # already exists.  Real macOS preflight runs can have filesystem observers
    # touching Builds; deleting the directory and then recreating the same path
    # leaves a race in which mkdir sees EEXIST.  Clear the generated contents
    # strictly instead: any cleanup failure still propagates, and we verify the
    # workspace is empty before writing the new generation.
    if out.is_symlink() or out.is_file():
        out.unlink()
        out.mkdir(parents=True, exist_ok=False)
    elif out.is_dir():
        for child in list(out.iterdir()):
            if child.is_symlink() or child.is_file():
                child.unlink()
            else:
                shutil.rmtree(child)
        remaining = list(out.iterdir())
        if remaining:
            raise RuntimeError(
                f'Mac bootstrap cleanup incomplete: {out}: ' +
                ', '.join(str(path.name) for path in remaining[:10])
            )
    else:
        out.mkdir(parents=True, exist_ok=False)
    guest = out / 'cinter-bds-guest.sh'; guest.write_text(GUEST); guest.chmod(0o755)
    (out / 'CinterBDSVM.swift').write_text(SWIFT)
    (out / 'cinter_bds_lan_relay.c').write_text(LAN_RELAY_C)
    guest_yaml='\n'.join('      '+line for line in GUEST.splitlines())
    (out / 'linuxkit.yml').write_text(LINUXKIT_YAML.replace('__CINTER_GUEST_YAML__', guest_yaml).replace('__CINTER_KERNEL_IMAGE__', 'linuxkit/kernel:6.6.71'))
    builder=out/'build-mac-guest.sh'; builder.write_text(BUILD_SH); builder.chmod(0o755)
    (out/'source-fingerprint.txt').write_text(source_fingerprint()+'\n')
    runner=out/'run-mac-bds.sh'; runner.write_text(RUN_SH); runner.chmod(0o755)
    probe_source=_nethernet_probe_source_path()
    if not probe_source.is_file(): raise RuntimeError(f'Mac bootstrap NetherNet probe source missing: {probe_source}')
    shutil.copy2(probe_source, out/'cinter_nethernet_probe.c')
    manifest = {
        'schema':'cinterforge.craftmaster.mac-bootstrap.v1',
        'architecture':'arm64-linux-guest', 'host_virtualization':'Virtualization.framework',
        'x86_execution_provider':'apple-rosetta-for-linux', 'bds_embedded':False,
        'bds_share_tag':'cinter-bds', 'rosetta_share_tag':'rosetta',
        'console':'virtio-serial-stdio', 'network':'virtio-nat-host-nethernet-relay', 'bedrock_udp_port':19132,
        'nethernet_host_tcp_port':19132, 'nethernet_host_udp_ports':'19140-19155', 'nethernet_lan_discovery_udp_port':7551,
        'required_guest_mounts':{'cinter-bds':'/mnt/cinter-bds','rosetta':'/mnt/rosetta'},
        'acceptance':['launch','nethernet-listener','list-response','stop','exit-0'],
        'source_fingerprint':source_fingerprint(),
        'files':['CinterBDSVM.swift','cinter_bds_lan_relay.c','cinter-bds-guest.sh','linuxkit.yml','build-mac-guest.sh','run-mac-bds.sh','cinter_nethernet_probe.c','source-fingerprint.txt']
    }
    (out/'bootstrap.json').write_text(json.dumps(manifest, indent=2, sort_keys=True)+'\n')
    for name in manifest['files']:
        artifact=out/name
        if not artifact.is_file() or artifact.stat().st_size == 0:
            raise RuntimeError(f'Mac bootstrap construction failed: {artifact}')
    return out


def smoke(mac_bootstrap: Path | None, server: Path, startup_timeout: float = 90.0,
          *, launcher_command: list[str] | None = None) -> dict:
    """Exercise real BDS via serial console, NetherNet, list and graceful stop.

    CFOS owns the VM/image. The optional command invokes its public launcher;
    the older bootstrap argument is retained only for consumers not migrated.
    """
    server=server.resolve()
    require_cfos_rosetta = launcher_command is not None
    if launcher_command is None:
        if mac_bootstrap is None:
            raise RuntimeError('CFOS BDS smoke requires an explicit launcher command')
        mac_bootstrap=mac_bootstrap.resolve()
        runner=mac_bootstrap/'run-mac-bds.sh'
        if not runner.is_file(): raise RuntimeError(f'Mac BDS runner missing: {runner}')
        launcher_command=[str(runner),str(server)]
    elif not launcher_command:
        raise RuntimeError('CFOS BDS smoke launcher command cannot be empty')
    # Acceptance is about the local BDS process/console/NetherNet contract, not
    # Microsoft's external account services.  Run this transient smoke with
    # online mode disabled, then restore the exact original properties file.
    # This keeps production/default BDS behavior untouched while preventing a
    # Microsoft-services outage from making a local runtime acceptance flaky.
    props=server/'server.properties'
    props_existed=props.exists()
    props_original=props.read_bytes() if props_existed else None
    props_text=props_original.decode('utf-8',errors='replace') if props_original is not None else ''
    prop_lines=props_text.splitlines()
    replaced=False
    for i,line in enumerate(prop_lines):
        if line.strip().startswith('online-mode='):
            prop_lines[i]='online-mode=false'; replaced=True
    if not replaced: prop_lines.append('online-mode=false')
    allow_replaced=False
    for i,line in enumerate(prop_lines):
        if line.strip().startswith('allow-list='):
            prop_lines[i]='allow-list=false'; allow_replaced=True
    if not allow_replaced: prop_lines.append('allow-list=false')
    # BDS 1.26.51.1 reports that NetherNet is the only client transport accepted
    # by this release.  Exercise the supported transport; forcing legacy RakNet
    # can produce an offline pong while BDS explicitly says players cannot connect.
    transport_replaced=False
    for i,line in enumerate(prop_lines):
        if line.strip().startswith('transport='):
            prop_lines[i]='transport=nethernet'; transport_replaced=True
    if not transport_replaced: prop_lines.append('transport=nethernet')
    # BDS LAN discovery is the documented switch controlling whether the
    # server listens/responds to LAN discovery pings. Force it on only for
    # acceptance, then restore the exact original properties in finally.
    lan_replaced=False
    for i,line in enumerate(prop_lines):
        if line.strip().startswith('enable-lan-visibility='):
            prop_lines[i]='enable-lan-visibility=true'; lan_replaced=True
    if not lan_replaced: prop_lines.append('enable-lan-visibility=true')
    content_log_replaced=False
    for i,line in enumerate(prop_lines):
        if line.strip().startswith('content-log-console-output-enabled='):
            prop_lines[i]='content-log-console-output-enabled=true'; content_log_replaced=True
    if not content_log_replaced: prop_lines.append('content-log-console-output-enabled=true')
    props.write_text('\n'.join(prop_lines)+'\n',encoding='utf-8')
    print('[craftmaster-mac] acceptance smoke: online-mode=false allow-list=false transport=nethernet enable-lan-visibility=true content-log-console-output-enabled=true (temporary; original server.properties will be restored)',flush=True)
    p=subprocess.Popen(launcher_command,cwd=server,stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',bufsize=1,start_new_session=True)
    output=queue.Queue(); lines=[]
    def reader():
        if not p.stdout: return
        try:
            for line in p.stdout: output.put(line)
        except ValueError:
            # Expected only during teardown if the pipe is closed after the VM exits/is killed.
            if p.poll() is None: raise
    reader_thread=threading.Thread(target=reader,daemon=True)
    reader_thread.start()
    deadline=time.monotonic()+startup_timeout; network=False; server_started=False; rosetta=False
    try:
        while time.monotonic()<deadline:
            try:
                line=output.get(timeout=.1); lines.append(line); print('[craftmaster-mac] '+line.rstrip(),flush=True)
                if 'CINTER_BDS_ROSETTA_REGISTERED' in line: rosetta=True
                if 'CINTER_BDS_NETHERNET_PASS' in line: network=True
                if 'Server started.' in line: server_started=True
                if 'CINTER_BDS_NETHERNET_FAIL' in line or 'CINTER_VM_ERROR' in line or 'CINTER_BDS_ERROR' in line:
                    detail=line.strip()
                    if 'CINTER_VM_ERROR' in line and platform.system() == 'Darwin':
                        try:
                            diag=subprocess.run(['/usr/bin/log','show','--last','2m','--style','compact','--predicate',
                                'subsystem == \"com.apple.Virtualization\" OR eventMessage CONTAINS \"CinterBDSVM\" OR eventMessage CONTAINS \"Virtualization\" OR eventMessage CONTAINS \"Sandbox: \" OR process == \"kernel\"'],
                                text=True,capture_output=True,timeout=10,check=False)
                            vzlog='\n'.join(x for x in diag.stdout.splitlines() if x.strip())[-12000:]
                            if vzlog: detail += '\nCINTER_VM_DIAGNOSTICS:\n'+vzlog
                        except Exception as exc:
                            detail += f'\nCINTER_VM_DIAGNOSTICS unavailable: {exc}'
                    raise RuntimeError('Mac BDS bootstrap reported failure: '+detail)
                if network and server_started and (rosetta or not require_cfos_rosetta): break
            except queue.Empty: pass
            if p.poll() is not None: break
        if not (network and server_started and (rosetta or not require_cfos_rosetta)):
            raise RuntimeError(f'Mac BDS smoke readiness incomplete; rosetta={rosetta}; nethernet={network}; server_started={server_started}; vm_exit={p.poll()}; tail='+''.join(lines[-30:]))
        if not p.stdin: raise RuntimeError('Mac BDS console stdin unavailable')
        # Do not issue acceptance commands merely because the signaling listener is
        # open. BDS can accept/echo console input during initialization while not yet
        # reliably executing shutdown commands. Wait for both the NetherNet listener
        # and the real `Server started.` marker, then prove list and graceful stop.
        p.stdin.write('list\n'); p.stdin.flush()
        list_ok=False; telemetry_banner=False; list_retried_after_telemetry=False; list_deadline=time.monotonic()+10
        while time.monotonic()<list_deadline:
            try:
                line=output.get(timeout=.1); lines.append(line); print('[craftmaster-mac] '+line.rstrip(),flush=True)
                if re.search(r'\bplayers? online\b',line,re.I) or re.search(r'\bThere are \d+/',line,re.I): list_ok=True; break
                if '================ TELEMETRY MESSAGE ===================' in line:
                    telemetry_banner=True
                if telemetry_banner and '======================================================' in line and not list_retried_after_telemetry:
                    if p.poll() is not None: break
                    p.stdin.write('list\n'); p.stdin.flush()
                    list_retried_after_telemetry=True
                    print('[craftmaster-mac] CINTER_BDS_CONSOLE_LIST_RETRY_AFTER_TELEMETRY',flush=True)
            except queue.Empty: pass
            if p.poll() is not None: break
        if not list_ok: raise RuntimeError('Mac BDS console `list` response not verified; tail='+''.join(lines[-30:]))
        if p.poll() is not None: raise RuntimeError(f'Mac BDS exited before console `stop`; vm_exit={p.poll()}; tail='+''.join(lines[-30:]))
        try:
            p.stdin.write('stop\n'); p.stdin.flush()
        except (BrokenPipeError, ValueError) as exc:
            raise RuntimeError('Mac BDS console `stop` could not be delivered; tail='+''.join(lines[-30:])) from exc
        # Continue consuming the VM stream after `stop`.  A bare p.wait() here
        # hid BDS's shutdown output in the reader queue and could make a healthy
        # shutdown look hung.  More importantly, exit 0 alone is not sufficient
        # evidence that BDS processed the console command gracefully.
        stop_requested=False; stopping_server=False; quit_correctly=False; bds_exit_zero=False
        stop_deadline=time.monotonic()+30
        while time.monotonic()<stop_deadline:
            try:
                line=output.get(timeout=.1); lines.append(line); print('[craftmaster-mac] '+line.rstrip(),flush=True)
                if 'Server stop requested.' in line: stop_requested=True
                if 'Stopping server...' in line: stopping_server=True
                if 'Quit correctly' in line: quit_correctly=True
                if 'CINTER_BDS_EXIT rc=0' in line: bds_exit_zero=True
            except queue.Empty:
                pass
            if p.poll() is not None:
                # Drain anything the reader thread queued immediately before EOF.
                while True:
                    try:
                        line=output.get_nowait(); lines.append(line); print('[craftmaster-mac] '+line.rstrip(),flush=True)
                        if 'Server stop requested.' in line: stop_requested=True
                        if 'Stopping server...' in line: stopping_server=True
                        if 'Quit correctly' in line: quit_correctly=True
                        if 'CINTER_BDS_EXIT rc=0' in line: bds_exit_zero=True
                    except queue.Empty:
                        break
                break
        if p.poll() is None:
            raise RuntimeError('Mac BDS graceful stop timed out; tail='+''.join(lines[-40:]))
        rc=p.returncode
        if rc!=0: raise RuntimeError(f'Mac BDS graceful stop returned {rc}; tail='+''.join(lines[-40:]))
        if not (stop_requested and stopping_server and quit_correctly and bds_exit_zero):
            raise RuntimeError('Mac BDS graceful stop evidence incomplete; '
                f'stop_requested={stop_requested}; stopping_server={stopping_server}; '
                f'quit_correctly={quit_correctly}; bds_exit_zero={bds_exit_zero}; tail='+''.join(lines[-40:]))
        print('[craftmaster-mac] CINTER_BDS_GRACEFUL_STOP_PASS',flush=True)
        return {'status':'pass','provider':'apple-virtualization-rosetta','network_ready':True,
                'network_probe':'guest-local-nethernet-tcp','rosetta_registered':rosetta if require_cfos_rosetta else None,'console_response_verified':True,'graceful_stop':True}
    finally:
        if p.poll() is None:
            try: os.killpg(p.pid,signal.SIGKILL)
            except (ProcessLookupError, PermissionError): pass
            try: p.wait(timeout=5)
            except subprocess.TimeoutExpired: raise RuntimeError(f'Mac BDS VM could not be killed pid={p.pid}')
        if p.stdin:
            try: p.stdin.close()
            except (BrokenPipeError, ValueError): pass
        if props_existed:
            props.write_bytes(props_original)
        else:
            props.unlink(missing_ok=True)
        reader_thread.join(timeout=2)
        if p.stdout and not reader_thread.is_alive(): p.stdout.close()
