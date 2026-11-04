#!/usr/bin/env python3
"""Prepare the shared Linux runtime from checked-in Linux source.

All outputs under Builds/Linux are generated. Application packages are not
part of this shared runtime and remain owned by their respective consumers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import platform
import subprocess
import sys
from pathlib import Path
def cfos_builds_root(repo_root: Path) -> Path:
    """Resolve the sole sibling Builds location using the CFOS source identity."""
    repo = repo_root.resolve()
    if not (repo / 'CFOS').is_file():
        raise RuntimeError(f'Expected CinterForgeOS checkout: {repo}')
    return repo.parent / 'Builds'



from launcher_signing import has_virtualization_entitlement, sign_launcher

HERE = Path(__file__).resolve().parent
SOURCES = ("linuxkit.yml", "CinterBDSVM.swift")
# The LinuxKit input inventory belongs to this checkout, not CinterForge.
SCRIPT_SOURCES = tuple(sorted((HERE.parents[1] / 'guest/linuxkit').rglob('*.sh')))



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


def stage_sources(repo_root: Path) -> Path:
    destination = cfos_builds_root(repo_root) / "Linux"
    destination.mkdir(parents=True, exist_ok=True)
    from render_linuxkit import render
    render(destination / "linuxkit.yml")
    for filename in ("CinterBDSVM.swift",):
        source = HERE / filename
        if not source.is_file():
            raise FileNotFoundError(f"Missing checked-in Linux source: {source}")
        target = destination / filename
        if not target.is_file() or target.read_bytes() != source.read_bytes():
            shutil.copy2(source, target)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--prepare-debian", action="store_true")
    args = parser.parse_args()
    root = args.repo_root.resolve()
    destination = stage_sources(root)
    required = ("CinterBDSVM", "cinter-bds-kernel", "cinter-bds-initrd.img", "cinter-bds-cmdline")
    digest = hashlib.sha256()
    for filename in SOURCES:
        digest.update(filename.encode())
        digest.update((HERE / filename).read_bytes())
    for script in SCRIPT_SOURCES:
        digest.update(str(script.relative_to(HERE.parents[1])).encode())
        digest.update(script.read_bytes())
    # The rendered configuration is the actual LinuxKit build input. Include it
    # so changes to the renderer or its output cannot reuse a stale guest image.
    digest.update((destination / "linuxkit.yml").read_bytes())
    # Version the image contract so an older, incorrectly marked initrd is rebuilt.
    digest.update(b"creative-suite-rosetta-supervisor-v3")
    fingerprint = digest.hexdigest() + "\n"
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
    if args.prepare_debian:
        subprocess.run([
            sys.executable, "-u", str(HERE / "prepare_debian.py"),
            "--repo-root", str(root),
        ], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
