#!/usr/bin/env python3
"""Run an application install job in the shared LinuxKit VM.

The LinuxKit image is built from linux/linuxkit.yml into Builds/CreativeSuite/CFOS/LinuxKit/.
The VM mounts the CreativeSuite/Linux directory through the existing
CINTER_BDS_HOST_DIR VirtioFS interface. No shell commands are passed to Linux.
This runner deliberately fails for unsupported package formats instead of
pretending an Alpine copy is a Debian package installation.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import os
import signal
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

# The VM launcher and signing helpers are owned by the shared Linux builder.
# This Creative Suite runner is a sibling of that directory, not its owner.
LINUX_SOURCE_DIR = Path(__file__).resolve().parents[2] / "apps" / "cinterforge-os" / "linux"
if str(LINUX_SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(LINUX_SOURCE_DIR))
from launcher_signing import has_virtualization_entitlement, sign_launcher

# Python filesystem locations are owned by CinterFileIO, not this build tool.
_CINTER_PACKAGE_PARENT = next(
    parent for parent in Path(__file__).resolve().parents
    if (parent / "CinterForgeServer" / "cf" / "io" / "CinterFileIO.py").is_file()
)
if str(_CINTER_PACKAGE_PARENT / "CinterForgeServer") not in sys.path:
    sys.path.insert(0, str(_CINTER_PACKAGE_PARENT / "CinterForgeServer"))
from cf.io.CinterIO import cinterforgeBuildsRootPath



def release_stale_vm_disk_writer(disk: Path, announce) -> None:
    """Reap only a writable Virtualization XPC holder after our VM has exited.

    Virtualization.framework can leave its per-VM XPC service alive after the
    launcher exits. Such a service retains the exclusive Debian disk handle
    and prevents the next VM from attaching it. Never touch read-only holders.
    """
    if not disk.is_file():
        return
    try:
        result = subprocess.run(
            ["lsof", "-F", "pfc", "--", str(disk)],
            capture_output=True, text=True, check=False,
        )
    except OSError as error:
        announce(f"WARNING: unable to inspect Debian disk holders: {error}")
        return
    if result.returncode not in (0, 1):
        announce(f"WARNING: lsof failed while inspecting Debian disk: {result.stderr.strip()}")
        return
    holders = {}
    pid = None
    for line in result.stdout.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            pid = int(line[1:])
            holders.setdefault(pid, [])
        elif line.startswith("f") and pid is not None:
            holders[pid].append(line[1:])

    for holder_pid, descriptors in holders.items():
        if not any(descriptor.endswith("u") or descriptor.endswith("w") for descriptor in descriptors):
            continue
        details = subprocess.run(
            ["ps", "-p", str(holder_pid), "-o", "command="],
            capture_output=True, text=True, check=False,
        )
        command = details.stdout.strip()
        if not command.startswith(
            "/System/Library/Frameworks/Virtualization.framework/Versions/A/XPCServices/com.apple.Virtualization."
        ):
            announce(f"WARNING: writable Debian disk holder PID {holder_pid} is not a Virtualization XPC service: {command}")
            continue
        announce(f"releasing orphaned Virtualization disk writer PID {holder_pid}")
        try:
            os.kill(holder_pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except PermissionError as error:
            announce(f"WARNING: cannot terminate disk writer PID {holder_pid}: {error}")
            continue
        for _ in range(50):
            check = subprocess.run(
                ["lsof", "-F", "p", "--", str(disk)],
                capture_output=True, text=True, check=False,
            )
            if f"p{holder_pid}" not in check.stdout.splitlines():
                break
            time.sleep(0.2)
        else:
            announce(f"WARNING: Virtualization disk writer PID {holder_pid} still holds {disk}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--build-ardour", action="store_true")
    parser.add_argument("--prepare-wine", action="store_true")
    parser.add_argument("--test-wine", action="store_true")
    parser.add_argument("--test-ardour", choices=("win-gui", "win-headless", "linux-gui", "linux-headless"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--application", required=True)
    parser.add_argument("--shared-distro", action="store_true",
                        help="Use the separately prepared shared CinterForge Linux disk")
    parser.add_argument("--architecture")
    parser.add_argument("--source-type")
    parser.add_argument("--source")
    parser.add_argument("--payload", type=Path)
    args = parser.parse_args()
    if platform.system() != "Darwin":
        parser.error("Linux guest installation is supported only on macOS")
    if sum((bool(args.install), bool(args.uninstall), bool(args.smoke_test), bool(args.build_ardour), bool(args.test_ardour), bool(args.prepare_wine), bool(args.test_wine))) != 1:
        parser.error("Specify exactly one of --install, --uninstall, --smoke-test, or --build-ardour")

    # Display names are protocol values (the guest dispatches on the exact name).
    # Validate them without prohibiting spaces, and derive a separate safe log stem.
    if not re.fullmatch(r"[A-Za-z0-9_-]+(?: [A-Za-z0-9_-]+)*", args.application):
        parser.error("Unsafe application identifier")
    application_log_name = args.application.replace(" ", "_")

    root = args.repo_root.resolve()
    # A guest job must execute the checked-in LinuxKit script, not an old
    # initrd. Direct linux_packages.py / smoke invocations bypass the suite
    # preparation phase, so validate and rebuild the shared image here.
    # build_linux.py uses source fingerprints and skips an unchanged image.
    bootstrap_builder = LINUX_SOURCE_DIR / "build_linux.py"
    subprocess.run(
        [sys.executable, str(bootstrap_builder), "--repo-root", str(root)],
        check=True,
    )
    bootstrap = cinterforgeBuildsRootPath(root) / "CreativeSuite" / "CFOS" / "LinuxKit"
    binary = bootstrap / "CinterBDSVM"
    for required in (binary, bootstrap / "cinter-bds-kernel", bootstrap / "cinter-bds-initrd.img", bootstrap / "cinter-bds-cmdline"):
        if not required.is_file():
            parser.error(f"Missing built Linux VM component: {required}")

    # The LinuxKit image builder can leave a stale CinterBDSVM executable.
    # Validate the launcher at the point of use, not just during bootstrap.
    # Without this, the guest receives no cinter.mode kernel argument and
    # silently starts CraftMaster even when the Creative Suite job exists.
    launcher_source = LINUX_SOURCE_DIR / "CinterBDSVM.swift"
    launcher_marker = bootstrap / "vm-launcher-source-fingerprint.txt"
    expected_fingerprint = hashlib.sha256(launcher_source.read_bytes()).hexdigest() + "\n"
    launcher_is_current = (
        launcher_marker.is_file()
        and launcher_marker.read_text() == expected_fingerprint
        and binary.stat().st_mtime_ns >= launcher_source.stat().st_mtime_ns
    )
    if not launcher_is_current:
        print("[linux-guest] compiling current VM launcher before guest start", flush=True)
        subprocess.run(
            ["xcrun", "swiftc", "-framework", "Virtualization", "-o", str(binary), str(launcher_source)],
            check=True,
        )
        sign_launcher(binary)
        launcher_marker.write_text(expected_fingerprint)
    else:
        print("[linux-guest] VM launcher source verified", flush=True)
        if not has_virtualization_entitlement(binary):
            print("[linux-guest] signing VM launcher with virtualization entitlement", flush=True)
            sign_launcher(binary)

    system = bootstrap / "SharedDistro" if args.shared_distro else bootstrap
    debian = system / "DebianAMD64"
    disk = system / "DebianAMD64.ext4"
    if not ((debian / "bin/sh").is_file() or (debian / "usr/bin/sh").is_file()) or not disk.is_file():
        if args.shared_distro:
            parser.error(f"Shared distribution not prepared: {disk}. Run ./Cinter build linux-guest-shared first; existing Ardour disk is untouched.")
        print("[linux-guest] preparing missing AMD64 Debian userspace or disk", flush=True)
        subprocess.run([
            sys.executable, str(bootstrap_builder), "--repo-root", str(root),
            "--prepare-debian",
        ], check=True)
    if not ((debian / "bin/sh").is_file() or (debian / "usr/bin/sh").is_file()):
        parser.error(f"Debian userspace not prepared: {debian}")
    if not disk.is_file():
        parser.error(f"Missing persistent Debian ext4 disk after preparation: {disk}")
    if args.build_ardour:
        # The Windows cross-compiler needs more than the original 8 GiB disk.
        # Grow the persistent filesystem before attaching it to the VM.
        from prepare_debian import grow_ext4_disk
        engine = shutil.which("docker") or shutil.which("podman")
        if disk.stat().st_size < 32 * 1024 ** 3:
            if not engine:
                parser.error("Growing DebianAMD64.ext4 requires Docker or Podman")
            grow_ext4_disk(system, engine)

    shared = cinterforgeBuildsRootPath(root) / "CreativeSuite/Linux"
    shared.mkdir(parents=True, exist_ok=True)
    control = shared / ".cinter"
    control.mkdir(exist_ok=True)
    job = control / "creative-suite-job"
    result = control / "creative-suite-result"
    log = control / "creative-suite-log"
    stop = control / "vm-stop-request"
    # The job marker is a guest instruction, not a reliable host-side lock.
    # Interrupted runs can leave it behind. Hold an OS lock for the entire
    # invocation so stale markers can be safely replaced without allowing
    # two installers to launch competing VMs.
    lock_path = control / "creative-suite-host.lock"
    lock_file = lock_path.open("a+")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error(f"Another Linux guest runner is active (lock: {lock_path})")
    if job.exists():
        print(f"[linux-guest] removing stale job marker from interrupted run: {job}", flush=True)
        job.unlink()
    package = ""
    if args.smoke_test:
        if args.output is None or args.output.resolve() != (cinterforgeBuildsRootPath(root) / "CreativeSuite/Tests").resolve():
            parser.error("Smoke output must be Builds/CreativeSuite/Tests")
        args.output.mkdir(parents=True, exist_ok=True)
        # The VM only mounts Builds/CreativeSuite/Linux via VirtioFS.
        # Clear the guest-side output directory before every smoke run so
        # stale results cannot be mistaken for outputs from this run.
        guest_tests = shared / "Tests"
        guest_tests.mkdir(parents=True, exist_ok=True)
        for previous in guest_tests.iterdir():
            if previous.is_file() or previous.is_symlink():
                previous.unlink()

        # Every application runs in a fresh VM and the guest test directory
        # is cleared for isolation. Ardour consumes the Converter's WAV from
        # the *host* test output; restore only this validated dependency after
        # clearing the guest directory. Do not reuse any Ardour output.
        if args.application == "Ardour":
            converter_wav = args.output / "CinterForge Converter.wav"
            if not converter_wav.is_file() or converter_wav.is_symlink():
                parser.error(
                    "Ardour requires CinterForge Converter.wav from a "
                    "successful Converter smoke test"
                )
            import wave
            try:
                with wave.open(str(converter_wav), "rb") as source_audio:
                    if source_audio.getnframes() < 1000:
                        parser.error("Converter WAV has fewer than 1000 frames")
            except (OSError, EOFError, wave.Error) as error:
                parser.error(f"Invalid Converter WAV dependency: {error}")
            shutil.copy2(converter_wav, guest_tests / converter_wav.name)
            print(
                f"[linux-guest] staged Converter WAV for Ardour: "
                f"{converter_wav.stat().st_size} bytes",
                flush=True,
            )
    if args.install:
        if args.payload is not None:
            payload = args.payload.resolve()
            if not payload.is_file():
                parser.error(f"Installation requires a package file: {payload}")
            try:
                package = payload.relative_to(shared.resolve()).as_posix()
            except ValueError:
                parser.error("Payload must be inside Builds/CreativeSuite/Linux")
            if any(part in {"..", ""} for part in Path(package).parts):
                parser.error("Unsafe package path")
        elif args.source_type not in ("apt-repository", "extracted-tar", "flatpak"):
            parser.error("Installation requires a staged payload")

    for marker in (result, log, stop):
        marker.unlink(missing_ok=True)
    if args.build_ardour:
        if args.application != "Ardour":
            parser.error("--build-ardour requires --application Ardour")
        if not (shared / "Ardour/ardour-source.tar.gz").is_file():
            parser.error("Ardour source archive missing from Builds/CreativeSuite/Linux/Ardour")
    operation = "test-wine" if args.test_wine else ("prepare-wine" if args.prepare_wine else ("test-ardour" if args.test_ardour else ("build-ardour" if args.build_ardour else ("smoke" if args.smoke_test else ("install" if args.install else "uninstall")))))
    if (args.test_wine or args.prepare_wine) and args.application != "Wine":
        parser.error("Wine preparation and testing require --application Wine")
    if args.test_ardour:
        if args.application != "Ardour":
            parser.error("--test-ardour requires --application Ardour")
        package = args.test_ardour
        if not (shared / f"Ardour/TestInstall/{args.test_ardour.split('-', 1)[0]}/test_guest.sh").is_file():
            parser.error("Ardour guest test payload not staged")
    job.write_text(f"{operation}\n{args.application}\n{package}\n")
    if not job.is_file() or job.read_text().splitlines()[:2] != [operation, args.application]:
        raise RuntimeError(f"Guest job handoff was not persisted: {job}")
    environment = dict(os.environ)
    environment.update({
        "CINTER_VM_KERNEL": str(bootstrap / "cinter-bds-kernel"),
        "CINTER_VM_INITRD": str(bootstrap / "cinter-bds-initrd.img"),
        "CINTER_VM_CMDLINE": str(bootstrap / "cinter-bds-cmdline"),
        "CINTER_BDS_HOST_DIR": str(shared),
        "CINTER_LINUX_SYSTEM_DIR": str(system),
    })
    # VM console output is written to disk as it arrives. The guest also
    # publishes a separate log on VirtioFS, which is tailed while the VM runs.
    logs = cinterforgeBuildsRootPath(root) / "CreativeSuite/Logs"
    logs.mkdir(parents=True, exist_ok=True)
    vm_log = logs / f"{application_log_name}.VM.log"
    # Tenacity may hang during headless audio-device initialization. Bound its
    # smoke VM independently so a stalled app cannot block the entire suite.
    if args.smoke_test and args.application == "Tenacity":
        timeout_seconds = 90
    elif args.build_ardour:
        timeout_seconds = 14400
    elif args.test_ardour or args.test_wine:
        timeout_seconds = 1800
    elif args.install and args.application == "Natron" and args.source_type == "flatpak":
        # The initial Flathub install pulls the x86_64 SDK, locale, GL and app
        # into persistent Debian ext4. Five minutes truncates a healthy first
        # install midway through SDK download. Other guest jobs retain 300s.
        timeout_seconds = 1800
    else:
        timeout_seconds = 300
    missing_share_error = "virtio-fs: tag <cinter-linux> not found"
    guest_failure_markers = (
        "CINTER_CREATIVE_SUITE_ERROR",
        "CINTER_CREATIVE_SUITE_IDLE",
        "CINTER_VM_MODE craftmaster",
        "CINTER_CREATIVE_SUITE_ERROR job-not-visible",
        "CINTER_BDS_ERROR",
    )
    started = time.monotonic()
    guest_offset = 0
    console_offset = 0

    def announce(message: str) -> None:
        # VM console logs are authoritative; a nonblocking terminal must not
        # terminate the guest or discard its result when its output buffer fills.
        try:
            print(f"[linux-guest] {message}", flush=True)
        except BlockingIOError:
            pass

    def publish_new_lines(path: Path, offset: int, label: str) -> int:
        if not path.is_file():
            return offset
        with path.open("rb") as stream:
            stream.seek(offset)
            content = stream.read()
            next_offset = stream.tell()
        if content:
            for line in content.decode("utf-8", errors="replace").splitlines():
                announce(f"{label}: {line}")
        return next_offset

    announce(f"starting VM application={args.application} action={operation}")
    announce(f"console log={vm_log}")
    announce(f"guest log={log}")
    try:
        with vm_log.open("wb") as console:
            process = subprocess.Popen(
                [str(binary)], env=environment, stdout=console,
                stderr=subprocess.STDOUT,
            )
            last_heartbeat = started
            try:
                while process.poll() is None:
                    console.flush()
                    console_offset = publish_new_lines(vm_log, console_offset, "VM")
                    # Fail immediately if the host launcher did not advertise the
                    # required Linux-system share. Waiting cannot repair a missing
                    # VirtioFS device, and obscures the underlying failure.
                    recent_console = vm_log.read_text(encoding="utf-8", errors="replace")
                    failure = next((marker for marker in guest_failure_markers if marker in recent_console), None)
                    if missing_share_error in recent_console or failure:
                        announce(f"ERROR: guest dispatch failed: {failure or missing_share_error}; see {vm_log}")
                        process.terminate()
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                        return 78
                    guest_offset = publish_new_lines(log, guest_offset, "Debian")
                    now = time.monotonic()
                    if now - started >= timeout_seconds:
                        announce(f"VM timed out after {timeout_seconds}s; terminating")
                        process.terminate()
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                        return 124
                    if now - last_heartbeat >= 15:
                        announce(f"waiting for VM/guest result ({int(now - started)}s elapsed)")
                        last_heartbeat = now
                    time.sleep(0.5)
            except KeyboardInterrupt:
                announce("interrupted; terminating VM")
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise
            console.flush()
            console_offset = publish_new_lines(vm_log, console_offset, "VM")
            guest_offset = publish_new_lines(log, guest_offset, "Debian")

        # Isolate Virtualization.framework startup failures without rebuilding
        # applications or repeating the real guest operation. The probes are
        # bounded, sequential and logged alongside the failing VM console.
        if process.returncode == 31 and "CINTER_VM_ERROR" in vm_log.read_text(encoding="utf-8", errors="replace"):
            probe_log = logs / f"{application_log_name}.VM.probes.log"
            announce(f"VM startup rejected; isolating attachments in {probe_log}")
            with probe_log.open("w", encoding="utf-8") as diagnostic:
                for stage in ("kernel", "initrd", "serial", "network", "entropy", "boot", "disk", "builds", "rosetta", "full"):
                    probe_env = dict(environment)
                    probe_env["CINTER_VM_PROBE_STAGE"] = stage
                    diagnostic.write(f"\n=== VM STARTUP PROBE {stage} ===\n")
                    diagnostic.flush()
                    try:
                        attempt = subprocess.run(
                            [str(binary)], env=probe_env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, errors="replace", timeout=35,
                        )
                        diagnostic.write(attempt.stdout)
                        diagnostic.write(f"PROBE_RESULT stage={stage} exit={attempt.returncode}\n")
                        diagnostic.flush()
                        announce(f"VM probe {stage}: exit={attempt.returncode}")
                        if attempt.returncode != 0:
                            announce(f"first failing VM configuration: {stage}; details: {probe_log}")
                            break
                    except subprocess.TimeoutExpired as error:
                        diagnostic.write(f"PROBE_TIMEOUT stage={stage}: {error}\n")
                        diagnostic.flush()
                        announce(f"VM probe timed out at {stage}; details: {probe_log}")
                        break
        announce(f"VM exit={process.returncode} result_present={result.is_file()}")
        if not result.is_file():
            announce(f"missing guest result; inspect {vm_log} and {log}")
            return 76
        try:
            code = int(result.read_text().strip())
        except ValueError:
            announce(f"invalid guest result: {result.read_text()!r}")
            return 77
        announce(f"guest exit={code}")
        if args.smoke_test:
            # Copy real guest-produced artifacts into the public test output.
            # The VM cannot directly access the sibling CreativeSuite/Tests
            # directory because its VirtioFS share is scoped to Linux/.
            guest_tests = shared / "Tests"
            for artifact in guest_tests.iterdir():
                if artifact.is_file() and not artifact.is_symlink():
                    shutil.copy2(artifact, args.output / artifact.name)
            copied = [artifact.name for artifact in guest_tests.iterdir()
                      if artifact.is_file() and not artifact.is_symlink()]
            announce(f"copied smoke artifacts from {guest_tests} to {args.output}: {copied}")
        if code != 0 or process.returncode != 0:
            announce(f"failure details: {vm_log} and {log}")
            return code or process.returncode
        return 0
    finally:
        # Only inspect/clean up after the launcher process has exited.
        # Never terminate a live guest or unrelated read-only XPC holder.
        if "process" in locals() and process.poll() is not None:
            release_stale_vm_disk_writer(system / "DebianAMD64.ext4", announce)
        job.unlink(missing_ok=True)



if __name__ == "__main__":
    raise SystemExit(main())
