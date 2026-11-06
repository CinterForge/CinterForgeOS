#!/usr/bin/env python3
"""Prepare the shared Linux runtime from checked-in Linux source.

All LinuxKit outputs are under Builds/CreativeSuite/CFOS/LinuxKit. Application packages are not
part of this shared runtime and remain owned by their respective consumers.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import platform
import subprocess
from pathlib import Path
def cfos_builds_root(repo_root: Path) -> Path:
    """Resolve the sole sibling Builds location using the CFOS source identity."""
    repo = repo_root.resolve()
    if not (repo / 'CFOS').is_file():
        raise RuntimeError(f'Expected CinterForgeOS checkout: {repo}')
    return repo.parent / 'Builds'


_LINUXKIT_ARTIFACT_NAMES = frozenset({
    "linuxkit.yml", "CinterBDSVM.swift", "CinterBDSVM",
    "CinterBDSNetRelay", "bootstrap.json",
    "cinter-bds-kernel", "cinter-bds-initrd.img", "cinter-bds-cmdline",
    "built-source-fingerprint.txt", "vm-launcher-source-fingerprint.txt",
})


def linuxkit_bootstrap_root(repo_root: Path, *, migrate: bool = True) -> Path:
    """Resolve CFOS-owned LinuxKit artifacts, safely relocating old Builds/Linux.

    Move only CFOS-owned LinuxKit outputs. A legacy Builds/Linux directory can
    contain unrelated applications and disk state: leave those files intact.
    Reuse existing signed launchers, images and fingerprints without rebuilding.
    """
    builds = cfos_builds_root(repo_root)
    target = builds / "CreativeSuite" / "CFOS" / "LinuxKit"
    legacy = builds / "Linux"
    if migrate and legacy.exists():
        if legacy.is_symlink() or not legacy.is_dir():
            raise RuntimeError(f"Legacy LinuxKit location is not a regular directory: {legacy}")
        selected = [p for p in legacy.iterdir() if p.name in _LINUXKIT_ARTIFACT_NAMES]
        if selected:
            if target.exists() and (not target.is_dir() or target.is_symlink()):
                raise RuntimeError(f"Invalid LinuxKit destination: {target}")
            # Check for conflicts and symlinks before moving any file.
            for artifact in selected:
                destination = target / artifact.name
                if artifact.is_symlink() or not artifact.is_file():
                    raise RuntimeError(f"Invalid legacy LinuxKit artifact: {artifact}")
                if destination.exists() or destination.is_symlink():
                    if destination.is_symlink() or not destination.is_file() or \
                       artifact.stat().st_size != destination.stat().st_size or \
                       artifact.read_bytes() != destination.read_bytes():
                        raise RuntimeError(
                            f"Conflicting LinuxKit artifacts; preserve both and reconcile: "
                            f"{artifact} and {destination}")
            target.mkdir(parents=True, exist_ok=True)
            for artifact in selected:
                destination = target / artifact.name
                if destination.is_file():
                    artifact.unlink()  # Byte-for-byte identical verified duplicate
                else:
                    artifact.rename(destination)
            if not any(legacy.iterdir()):
                legacy.rmdir()
            else:
                print(f"[CFOS LinuxKit] leaving unrelated legacy files in {legacy}", flush=True)
            print(f"[CFOS LinuxKit] migrated {len(selected)} artifacts: {legacy} -> {target}", flush=True)
    return target


from launcher_signing import has_virtualization_entitlement, sign_launcher

HERE = Path(__file__).resolve().parent
SOURCES = ("linuxkit.yml", "CinterBDSVM.swift")



def ensure_arm64_boot_kernel(kernel: Path) -> None:
    """Ensure the VZLinuxBootLoader receives a raw ARM64 Image, not Image.gz."""
    import gzip
    import os

    if not kernel.is_file():
        raise FileNotFoundError(f"LinuxKit ARM64 kernel is missing: {kernel}")
    with kernel.open("rb") as stream:
        signature = stream.read(2)
    if signature == b"\x1f\x8b":
        temporary = kernel.with_name(kernel.name + ".uncompressed.tmp")
        try:
            with gzip.open(kernel, "rb") as source, temporary.open("wb") as output:
                shutil.copyfileobj(source, output)
            with temporary.open("rb") as stream:
                stream.seek(0x38)
                magic = stream.read(4)
            if magic != b"ARM\x64":
                raise RuntimeError(
                    f"Decompressed LinuxKit kernel is not an ARM64 Linux Image: {kernel} "
                    f"(header magic={magic.hex()})"
                )
            os.replace(temporary, kernel)
            print(f"[linux] decompressed ARM64 Linux Image for Virtualization.framework: {kernel}", flush=True)
        finally:
            temporary.unlink(missing_ok=True)
    else:
        with kernel.open("rb") as stream:
            stream.seek(0x38)
            magic = stream.read(4)
        if magic != b"ARM\x64":
            raise RuntimeError(
                f"LinuxKit kernel is not a raw ARM64 Linux Image: {kernel} "
                f"(header magic={magic.hex()}, first bytes={signature.hex()}). "
                "Virtualization.framework cannot boot this kernel format."
            )
    print(f"[linux] verified ARM64 Linux Image: {kernel} ({kernel.stat().st_size} bytes)", flush=True)


def validate_linuxkit_images(source: Path) -> None:
    """Do not send unexpanded local package placeholders to linuxkit build.

    The CFOS appliance uses explicitly selected LinuxKit ARM64 OCI images;
    linuxkit-source/pkg is neither checked in nor staged beside this YAML.
    """
    configuration = source.read_text()
    if any(token in configuration for token in ('@pkg:', 'dummy://', 'linuxkit-source/pkg/')):
        raise RuntimeError(
            f"Unresolved LinuxKit package reference in {source}. "
            "The LinuxKit base images must have explicit ARM64 image references "
            "in the checked-in linuxkit.yml; no local linuxkit-source tree is staged."
        )


def stage_sources(repo_root: Path) -> Path:
    destination = linuxkit_bootstrap_root(repo_root)
    destination.mkdir(parents=True, exist_ok=True)
    for filename in SOURCES:
        source = HERE / filename
        if not source.is_file():
            raise FileNotFoundError(f"Missing checked-in Linux source: {source}")
        if filename == "linuxkit.yml":
            validate_linuxkit_images(source)
        target = destination / filename
        if not target.is_file() or target.read_bytes() != source.read_bytes():
            shutil.copy2(source, target)
    return destination


def shared_source_fingerprint(destination: Path) -> str:
    """Identify the authored LinuxKit build inputs, not runtime build timestamps."""
    digest = hashlib.sha256()
    for filename in SOURCES:
        digest.update(filename.encode())
        digest.update((HERE / filename).read_bytes())
    rendered = destination / "linuxkit.yml"
    if not rendered.is_file():
        raise RuntimeError(f"CFOS shared LinuxKit configuration missing: {rendered}")
    digest.update(rendered.read_bytes())
    digest.update(b"linuxkit-bedrock-rosetta-appliance-v1")
    return digest.hexdigest() + "\n"


def verify_shared_bootstrap(repo_root: Path, *, require_launcher: bool = False) -> Path:
    """Read-only consumer prerequisite; only CFOS Linux Build may construct it.

    The VM host launcher is macOS-only (Virtualization.framework), whereas the
    ARM64 LinuxKit kernel/initrd can be built on a Linux build host. When run
    on macOS the launcher must already be compiled and signed by CFOS.
    """
    destination = linuxkit_bootstrap_root(repo_root)
    kernel = destination / "cinter-bds-kernel"
    required = (
        destination / "cinter-bds-kernel",
        destination / "cinter-bds-initrd.img",
        destination / "cinter-bds-cmdline",
        destination / "built-source-fingerprint.txt",
    )
    if require_launcher:
        required += (
            destination / "CinterBDSVM",
            destination / "vm-launcher-source-fingerprint.txt",
        )
    missing = [str(path) for path in required if not path.is_file() or path.stat().st_size == 0]
    if missing:
        raise RuntimeError(
            "CFOS LinuxKit must be built and provisioned before any consumer build; "
            "run ./CFOS build cinterforge-os linuxkit Release first. Missing: " + ", ".join(missing)
        )
    actual = (destination / "built-source-fingerprint.txt").read_text()
    if actual != shared_source_fingerprint(destination):
        raise RuntimeError(
            "CFOS LinuxKit image is stale: its authored source changed. "
            "Rebuild with ./CFOS build cinterforge-os linux Release"
        )
    with kernel.open("rb") as stream:
        stream.seek(0x38)
        magic = stream.read(4)
    if magic != b"ARM\x64":
        raise RuntimeError(f"CFOS LinuxKit kernel is not an uncompressed ARM64 Image: {kernel}")
    if require_launcher:
        expected = hashlib.sha256((HERE / "CinterBDSVM.swift").read_bytes()).hexdigest() + "\n"
        if (destination / "vm-launcher-source-fingerprint.txt").read_text() != expected:
            raise RuntimeError("CFOS macOS LinuxKit host launcher is stale; rebuild it through CFOS")
        if not has_virtualization_entitlement(destination / "CinterBDSVM"):
            raise RuntimeError("CFOS LinuxKit launcher has no valid virtualization entitlement")
    print(f"[CFOS LinuxKit] VERIFIED prebuilt shared bootstrap: {destination}", flush=True)
    return destination


def shared_bootstrap_digest(repo_root: Path) -> str:
    """Fingerprint the tested LinuxKit image consumed by the CFOS Ubuntu lock."""
    destination = verify_shared_bootstrap(repo_root)
    digest = hashlib.sha256()
    for name in ("built-source-fingerprint.txt", "cinter-bds-kernel", "cinter-bds-initrd.img", "cinter-bds-cmdline"):
        digest.update(name.encode())
        with (destination / name).open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true",
                        help="Validate the already built shared LinuxKit artifact without modifying it")
    args = parser.parse_args()
    root = args.repo_root.resolve()
    if args.verify_only:
        verify_shared_bootstrap(root, require_launcher=platform.system() == "Darwin")
        return 0
    destination = stage_sources(root)
    required = ("cinter-bds-kernel", "cinter-bds-initrd.img", "cinter-bds-cmdline")
    # LinuxKit produces the kernel/initrd/cmdline only. The macOS Swift launcher
    # is compiled below, by CFOS, after its guest image is ready. It is never
    # an expected output of `linuxkit build`.
    fingerprint = shared_source_fingerprint(destination)
    built_marker = destination / "built-source-fingerprint.txt"
    current = all((destination / name).is_file() for name in required) and built_marker.is_file() and built_marker.read_text() == fingerprint
    if current:
        print("[linux] shared LinuxKit bootstrap current; skipping", flush=True)
    else:
        # Build the shared image directly from its staged, authored LinuxKit
        # configuration. CraftMaster's bootstrap script is application-owned
        # and must not be a prerequisite for the shared Linux runtime.
        if shutil.which("linuxkit") is None:
            raise RuntimeError("Shared Linux runtime build requires the linuxkit executable on PATH")
        print("[linux] building shared LinuxKit bootstrap from staged YAML", flush=True)
        subprocess.run([
            "linuxkit", "build", "--arch", "arm64", "--format", "kernel+initrd",
            "--name", "cinter-bds", "linuxkit.yml",
        ], cwd=destination, check=True)
        missing = [name for name in required if not (destination / name).is_file()]
        if missing:
            raise RuntimeError(f"Linux bootstrap did not produce: {missing}")
        built_marker.write_text(fingerprint)
    ensure_arm64_boot_kernel(destination / "cinter-bds-kernel")
    # The LinuxKit builder may reuse an older compiled VM launcher even when
    # CinterBDSVM.swift changes. Compile the shared host launcher independently:
    # its VirtioFS tags must match those mounted by linuxkit.yml.
    swift_source = HERE / "CinterBDSVM.swift"
    swift_fingerprint = hashlib.sha256(swift_source.read_bytes()).hexdigest() + "\n"
    swift_marker = destination / "vm-launcher-source-fingerprint.txt"
    swift_binary = destination / "CinterBDSVM"
    if platform.system() == "Darwin":
        launcher_current = (
            swift_binary.is_file()
            and swift_marker.is_file()
            and swift_marker.read_text() == swift_fingerprint
        )
        if launcher_current:
            print("[linux] shared VM launcher current; skipping", flush=True)
            if not has_virtualization_entitlement(swift_binary):
                print("[linux] signing VM launcher with virtualization entitlement", flush=True)
                sign_launcher(swift_binary)
        else:
            print("[linux] compiling shared VM launcher (VirtioFS shares)", flush=True)
            subprocess.run([
                "xcrun", "swiftc", "-framework", "Virtualization",
                "-o", str(swift_binary), str(swift_source),
            ], check=True)
            sign_launcher(swift_binary)
            swift_marker.write_text(swift_fingerprint)
    verify_shared_bootstrap(root, require_launcher=platform.system() == "Darwin")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        import sys
        print(f'[CFOS LinuxKit] ERROR: {error}', file=sys.stderr, flush=True)
        raise SystemExit(1)
