#!/usr/bin/env python3
"""Prepare Creative Suite Linux payloads independently of the shared Linux guest.

This is a package staging tool, not a VM or a successful installation claim.
The host retains verified download archives to avoid repeated large transfers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import os
import subprocess
import sys
import tarfile
import tempfile
import traceback
import time
from datetime import datetime, timezone
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import importlib.util

# Python filesystem locations are owned by CinterFileIO, not this build tool.
_CINTER_PACKAGE_PARENT = next(
    parent for parent in Path(__file__).resolve().parents
    if (parent / "CinterForgeServer" / "cf" / "io" / "CinterFileIO.py").is_file()
)
if str(_CINTER_PACKAGE_PARENT / "CinterForgeServer") not in sys.path:
    sys.path.insert(0, str(_CINTER_PACKAGE_PARENT / "CinterForgeServer"))
from cf.io.CinterIO import cinterforgeBuildsRootPath

HERE = Path(__file__).resolve().parents[1] / "build"
builder_spec = importlib.util.spec_from_file_location("creative_suite_builder", HERE / "build_creative_suite.py")
if builder_spec is None or builder_spec.loader is None:
    raise RuntimeError("Creative Suite catalog builder is unavailable")
BUILDER = importlib.util.module_from_spec(builder_spec)
builder_spec.loader.exec_module(BUILDER)


def safe_name(value: str) -> str:
    # Application display names may contain spaces; filesystem components may not.
    # Preserve existing names for single-word applications and reject traversal.
    import re
    if not value or not re.fullmatch(r"[A-Za-z0-9_-]+(?: [A-Za-z0-9_-]+)*", value):
        raise ValueError(f"Unsafe application name: {value!r}")
    return value.replace(" ", "_")


def download(source: str, destination: Path) -> str:
    digest = hashlib.sha256()
    request = urllib.request.Request(source, headers={"User-Agent": "CinterForge-CreativeSuite/1"})
    with urllib.request.urlopen(request, timeout=90) as response, destination.open("wb") as output:
        started = time.monotonic()
        last_progress = started
        downloaded = 0
        total = response.headers.get("Content-Length")
        expected = int(total) if total and total.isdecimal() else None
        print(f"[creative-suite-linux] downloading {destination.name} "
              f"({expected / 1048576:.1f} MiB)" if expected is not None
              else f"[creative-suite-linux] downloading {destination.name} (size unknown)", flush=True)
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
            digest.update(chunk)
            downloaded += len(chunk)
            now = time.monotonic()
            if now - last_progress >= 5:
                elapsed = max(now - started, 0.001)
                suffix = f" / {expected / 1048576:.1f} MiB" if expected else ""
                print(f"[creative-suite-linux] {destination.name}: "
                      f"{downloaded / 1048576:.1f} MiB{suffix}, "
                      f"{downloaded / elapsed / 1048576:.2f} MiB/s, "
                      f"{elapsed:.0f}s elapsed", flush=True)
                last_progress = now
        if expected is not None and downloaded != expected:
            raise RuntimeError(f"Incomplete download for {source}: {downloaded}/{expected} bytes")
    if not destination.stat().st_size:
        raise RuntimeError(f"Empty Linux package: {source}")
    return digest.hexdigest()


def recover_installed_appimage(entry: dict, destination: Path, disk: Path) -> dict | None:
    """Recover a pre-receipt AppImage install without network or VM activity.

    The existing staged file identifies the exact release in the CSV URL.
    The guest-installed binary must be byte-for-byte identical to that file.
    This is deliberately limited to AppImages; repository and extracted-tar
    installations cannot be verified from these host-visible artifacts.
    """
    if not disk.is_file() or entry["linux"]["sourceType"] != "download":
        return None
    filename = Path(urlparse(entry["linux"]["source"]).path).name
    if not filename.lower().endswith(".appimage"):
        return None
    name = safe_name(entry["name"])
    staged = destination / name / filename
    installed = destination / "Installed" / name / "application.AppImage"
    if not staged.is_file() or not installed.is_file():
        return None
    if staged.stat().st_size == 0 or staged.stat().st_size != installed.stat().st_size:
        return None

    def file_hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    digest = file_hash(staged)
    if digest != file_hash(installed):
        return None
    return {
        "name": name,
        "architecture": entry["linux"]["architecture"],
        "source": entry["linux"]["source"],
        "sha256": digest,
        "packageType": "appimage",
        "payload": str(staged),
        "status": "installed_in_guest",
        "installed": True,
        "verification": "existing guest-installed AppImage matches staged release SHA-256",
    }



def recover_existing_blender(entry: dict, destination: Path, disk: Path) -> dict | None:
    """Preserve a previously staged, working Blender distribution.

    Blender's LinuxKit install operation consumes the already-extracted tree;
    it does not copy the tree into Debian. Missing host receipts therefore
    must not trigger another large download when that tree is present.
    This is presence verification, not a claim that the release was retested.
    """
    if entry["name"] != "Blender" or not disk.is_file():
        return None
    source = entry["linux"]["source"]
    if entry["linux"]["sourceType"] != "download" or not source.endswith((".tar.xz", ".tar.gz", ".tgz")):
        return None
    root = destination / "Blender"
    if not root.is_dir():
        return None
    executables = [path for path in root.rglob("blender") if path.is_file() and os.access(path, os.X_OK)]
    if not executables:
        return None
    return {
        "name": "Blender",
        "architecture": entry["linux"]["architecture"],
        "source": source,
        "packageType": "extracted-tar",
        "payload": str(root),
        "status": "installed_in_guest",
        "installed": True,
        "verification": "preserved existing executable Blender payload; no download or guest installation",
    }


def unpack_tar(archive: Path, target: Path) -> None:
    """Extract files and internal links without allowing archive path escapes.

    Blender distributes versioned libraries through relative symbolic links.
    Validate the complete link graph before writing anything, and create
    symlinks last so no archive entry can write through one during extraction.
    """
    import posixpath
    from pathlib import PurePosixPath

    with tarfile.open(archive, "r:*") as package:
        members = package.getmembers()
        by_name = {}
        for member in members:
            path = PurePosixPath(member.name)
            if (
                not member.name
                or path.is_absolute()
                or ".." in path.parts
                or not (member.isfile() or member.isdir() or member.issym() or member.islnk())
            ):
                raise ValueError(f"Unsafe tar member: {member.name}")
            normalized = posixpath.normpath(member.name)
            if normalized in by_name:
                raise ValueError(f"Duplicate tar member: {member.name}")
            by_name[normalized] = member

        # Resolve every symlink transitively, rejecting cycles and paths that
        # would escape the archive even through another symlink.
        def resolve_link(name: str, visited: frozenset[str] = frozenset()) -> str:
            if name in visited:
                raise ValueError(f"Cyclic tar symlink: {name}")
            member = by_name.get(name)
            if member is None or not member.issym():
                return name
            link = member.linkname
            if not link or posixpath.isabs(link):
                raise ValueError(f"Unsafe tar symlink: {member.name} -> {link}")
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), link))
            if resolved == ".." or resolved.startswith("../") or resolved.startswith("/"):
                raise ValueError(f"Escaping tar symlink: {member.name} -> {link}")
            return resolve_link(resolved, visited | {name})

        for name, member in by_name.items():
            # Reject files nested under a link, including links appearing later
            # in the archive. This avoids writing outside target via parents.
            parent = posixpath.dirname(name)
            while parent and parent != ".":
                ancestor = by_name.get(parent)
                if ancestor is not None and ancestor.issym():
                    raise ValueError(f"Tar member traverses symlink: {name}")
                parent = posixpath.dirname(parent)
            if member.issym():
                resolve_link(name)
            if member.islnk():
                # Tar hard-link targets are archive-root-relative, unlike
                # symbolic links (which are relative to their containing dir).
                link_target = posixpath.normpath(member.linkname)
                if (
                    not member.linkname
                    or posixpath.isabs(member.linkname)
                    or link_target == ".."
                    or link_target.startswith("../")
                ):
                    raise ValueError(f"Unsafe tar hard link: {name} -> {member.linkname}")
                target_member = by_name.get(link_target)
                if target_member is None or not target_member.isfile() or target_member.islnk():
                    raise ValueError(f"Invalid tar hard-link target: {name} -> {member.linkname}")

        for name, member in by_name.items():
            destination = target.joinpath(*PurePosixPath(name).parts)
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                destination.parent.mkdir(parents=True, exist_ok=True)
                source = package.extractfile(member)
                if source is None:
                    raise ValueError(f"Missing tar file contents: {member.name}")
                with source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output)
                destination.chmod(member.mode & 0o777)

        # Hard links refer to previously extracted regular files. Create
        # these before symlinks, so neither endpoint can follow an archive link.
        for name, member in by_name.items():
            if member.islnk():
                destination = target.joinpath(*PurePosixPath(name).parts)
                source = target.joinpath(*PurePosixPath(posixpath.normpath(member.linkname)).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.link(source, destination)

        for name, member in by_name.items():
            if member.issym():
                destination = target.joinpath(*PurePosixPath(name).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                # Some portable releases contain a hard-link and a symlink
                # at the same extracted pathname. The symlink is the final
                # archive entry; replace the already materialized link/file.
                # A few vendor archives encode an executable as a hard link
                # followed by a self-referential symbolic link. Replacing the
                # extracted executable with that symlink destroys the binary
                # and produces ELOOP when the wrapper tries to execute it.
                link_target = posixpath.normpath(
                    posixpath.join(posixpath.dirname(name), member.linkname)
                )
                if link_target == name and destination.is_file() and not destination.is_symlink():
                    continue
                if link_target == name:
                    raise ValueError(f"Self-referential tar symlink: {member.name}")
                if destination.is_symlink() or destination.is_file():
                    destination.unlink()
                elif destination.exists():
                    raise ValueError(f"Tar symlink collides with directory: {member.name}")
                destination.symlink_to(member.linkname)


def natron_staged_tree(destination: Path) -> Path | None:
    """Locate the preserved portable release without assuming renderer is ELF.

    Natron ships a top-level launcher; its bundled executable layout is a
    runtime concern, not grounds for discarding a 140 MB source archive.
    """
    root = destination / "Natron" / "Natron-2.5.0-Linux-x86_64-no-installer"
    launcher = root / "NatronRenderer"
    if root.is_dir() and launcher.is_file() and os.access(launcher, os.X_OK):
        return root
    return None


def stage_one(entry: dict, destination: Path) -> dict:
    name = safe_name(entry["name"])
    source = entry["linux"]["source"]
    architecture = entry["linux"]["architecture"]
    if entry["linux"]["sourceType"] == "flatpak":
        # The Debian guest owns the persistent Flatpak installation. Never
        # download or unpack the obsolete Natron portable archive on macOS.
        return {
            "name": name, "architecture": architecture, "source": source,
            "packageType": "flatpak", "status": "awaiting_guest_flatpak_install",
            "installed": False,
        }
    if entry["linux"]["sourceType"] == "apt-repository":
        # Repository commands must execute inside the Linux guest, never on
        # the macOS host. Staging cannot claim that Inkscape was installed.
        return {
            "name": name, "architecture": architecture, "source": source,
            "status": "awaiting_guest_repository_install", "installed": False,
        }
    suffix = Path(urlparse(source).path).name
    if not suffix:
        raise ValueError(f"Linux source has no filename: {source}")
    if name == "Natron" and natron_staged_tree(destination) is not None:
        print("[creative-suite-linux] Natron: reusing existing extracted release; no download", flush=True)
        return {
            "name": name, "architecture": architecture, "source": source,
            "packageType": "extracted-tar", "payload": str(destination / name),
            "status": "staged_not_installed", "installed": False,
        }
    downloads = destination / "Downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    cached_archive = downloads / suffix
    with tempfile.TemporaryDirectory(prefix="cinter-linux-package-") as temporary:
        if cached_archive.is_file() and cached_archive.stat().st_size > 0:
            print(f"[creative-suite-linux] {name}: reusing cached archive {cached_archive}", flush=True)
            archive = cached_archive
            with archive.open("rb") as cached_stream:
                digest = hashlib.file_digest(cached_stream, "sha256").hexdigest()
        else:
            incoming = downloads / (suffix + ".incoming")
            try:
                digest = download(source, incoming)
                incoming.replace(cached_archive)
            finally:
                incoming.unlink(missing_ok=True)
            archive = cached_archive
        if suffix.lower().endswith((".tar.xz", ".tar.gz", ".tgz")) and not tarfile.is_tarfile(archive):
            raise RuntimeError(f"Cached archive is not a valid tar file: {archive}")
        extracted = Path(temporary) / "payload"
        extracted.mkdir()
        if suffix.lower().endswith((".tar.xz", ".tar.gz", ".tgz")):
            unpack_tar(archive, extracted)
            kind = "extracted-tar"
            if name == "Natron":
                distribution = extracted / "Natron-2.5.0-Linux-x86_64-no-installer"
                launcher = distribution / "NatronRenderer"
                if not distribution.is_dir() or not launcher.is_file():
                    raise RuntimeError(
                        f"Natron portable distribution/launcher missing: {launcher}; "
                        f"archive preserved at {cached_archive}"
                    )
                if not os.access(launcher, os.X_OK):
                    raise RuntimeError(f"Natron renderer launcher not executable: {launcher}")
                print("[creative-suite-linux] Natron: portable launcher present; "
                      "runtime renderer verification deferred to LinuxKit", flush=True)
        elif suffix.lower().endswith(".appimage"):
            # AppImages are executable payloads, not disposable installers.
            binary = extracted / suffix
            shutil.copy2(archive, binary)
            binary.chmod(binary.stat().st_mode | 0o111)
            kind = "appimage"
        elif suffix.lower().endswith(".deb"):
            # A .deb is not an installed application. Retain it until the
            # guest's dpkg/apt operation has completed successfully.
            binary = extracted / suffix
            shutil.copy2(archive, binary)
            kind = "deb-awaiting-guest-install"
        else:
            raise ValueError(f"Unsupported Linux package type for {name}: {suffix}")
        target = destination / name
        # Do not destroy a previously staged working payload until the new
        # download has been checked and extracted successfully.
        replacement = destination / (name + ".incoming")
        if replacement.exists():
            shutil.rmtree(replacement)
        shutil.move(str(extracted), str(replacement))
        if target.exists():
            shutil.rmtree(target)
        replacement.rename(target)
        return {
            "name": name, "architecture": architecture, "source": source,
            "sha256": digest, "packageType": kind, "payload": str(target / suffix) if kind != "extracted-tar" else str(target),
            "status": "staged_not_installed", "installed": False,
        }


def load_installation_plan(root: Path, csv_file: Path) -> dict:
    """Keep the Linux install plan synchronized with the maintained CSV.

    The baseline/style generator is not a prerequisite for installing Linux
    applications. Derive the plan using its shared catalog parser, without
    rebuilding artwork, themes, or any unrelated Update Data producers.
    """
    rows = BUILDER.read_apps(csv_file)
    expected_plan = BUILDER.installation_plan(rows)
    plan_file = cinterforgeBuildsRootPath(root) / "CreativeSuite/InstallationPlan.json"
    plan_file.parent.mkdir(parents=True, exist_ok=True)

    existing_plan = None
    if plan_file.is_file():
        try:
            existing_plan = json.loads(plan_file.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass

    if existing_plan == expected_plan:
        print("[creative-suite-linux] installation plan current; skipping", flush=True)
        return existing_plan

    temporary_plan = plan_file.with_name(plan_file.name + ".incoming")
    temporary_plan.write_text(json.dumps(expected_plan, indent=2) + "\n", encoding="utf-8")
    temporary_plan.replace(plan_file)
    print(f"[creative-suite-linux] generated installation plan: {plan_file}", flush=True)
    return expected_plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--application", help="Stage one application instead of all")
    parser.add_argument("--skip-installed", action="store_true", help="Reuse previously installed applications with the same source and architecture")
    parser.add_argument("--guest-runner", type=Path, required=True,
                        help="Readable Python Linux guest runner supporting --install")
    args = parser.parse_args()
    root = args.repo_root.resolve()
    # Resolve the maintained catalog relative to this script, just as the
    # baseline builder does. The caller may supply any repository root.
    csv_file = HERE.parents[1] / "input" / "Creative Suite.csv"
    plan = load_installation_plan(root, csv_file)
    plan_file = cinterforgeBuildsRootPath(root) / "CreativeSuite/InstallationPlan.json"
    if not isinstance(plan, dict) or not isinstance(plan.get("applications"), list):
        raise RuntimeError(f"Invalid Creative Suite installation plan: {plan_file}")
    if not plan["applications"]:
        raise RuntimeError(f"Creative Suite installation plan has no applications: {plan_file}")
    for entry in plan["applications"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("linux"), dict):
            raise RuntimeError(f"Invalid Linux application entry in {plan_file}: {entry!r}")
        for field in ("name", "architecture", "source", "sourceType"):
            value = entry.get(field) if field == "name" else entry["linux"].get(field)
            if not isinstance(value, str) or not value.strip():
                raise RuntimeError(f"Creative Suite installation plan entry missing {field}: {entry!r}")
    runner = args.guest_runner.resolve()
    if not runner.is_file() or not os.access(runner, os.R_OK):
        parser.error(f"Linux guest runner missing or unreadable: {runner}")
    destination = cinterforgeBuildsRootPath(root) / "CreativeSuite/Linux"
    destination.mkdir(parents=True, exist_ok=True)
    report_file = destination / "PackageReport.json"
    previous = {}
    if report_file.is_file():
        try:
            previous_report = json.loads(report_file.read_text(encoding="utf-8"))
            previous = {
                item["name"]: item
                for item in previous_report.get("applications", [])
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            }
        except (ValueError, KeyError, TypeError, OSError):
            previous = {}

    def save_report() -> None:
        # Preserve successful receipts across interrupted or single-app runs.
        # Otherwise an interruption after app 1 discards the receipts for the
        # remaining apps and the next update reinstalls everything.
        by_name = dict(previous)
        by_name.update({item["name"]: item for item in results})
        ordered = [by_name[entry["name"]] for entry in plan["applications"] if entry["name"] in by_name]
        complete = all(
            item.get("installed") and item.get("status") == "installed_in_guest"
            for item in ordered
        ) and len(ordered) == len(plan["applications"])
        incoming = report_file.with_name(report_file.name + ".incoming")
        incoming.write_text(
            json.dumps({"schemaVersion": 1, "installed": complete, "applications": ordered}, indent=2) + "\n",
            encoding="utf-8",
        )
        incoming.replace(report_file)
    logs = cinterforgeBuildsRootPath(root) / "CreativeSuite/Logs"
    logs.mkdir(parents=True, exist_ok=True)
    results = []
    failed = False
    for entry in plan["applications"]:
        if args.application and entry["name"].casefold() != args.application.casefold():
            continue
        application_log = logs / f"{safe_name(entry['name'])}.log"
        events = [f"time={datetime.now(timezone.utc).isoformat()}",
                  f"application={entry['name']}",
                  f"source={entry['linux']['source']}",
                  f"architecture={entry['linux']['architecture']}"]
        try:
            cached = previous.get(entry["name"])
            if (
                args.skip_installed
                and cached is not None
                and cached.get("installed") is True
                and cached.get("status") == "installed_in_guest"
                and cached.get("source") == entry["linux"]["source"]
                and cached.get("architecture") == entry["linux"]["architecture"]
                and (cinterforgeBuildsRootPath(root) / "Linux/DebianAMD64.ext4").is_file()
                and (
                    not cached.get("payload")
                    or Path(cached["payload"]).exists()
                )
            ):
                print(f"[creative-suite-linux] {entry['name']}: already installed; skipping", flush=True)
                events.append("reused cached installation report (not guest-verified)")
                application_log.write_text("\n".join(events) + "\n", encoding="utf-8")
                results.append(cached)
                save_report()
                continue
            if args.skip_installed:
                preserved_blender = recover_existing_blender(
                    entry, destination, cinterforgeBuildsRootPath(root) / "Linux/DebianAMD64.ext4"
                )
                if preserved_blender is not None:
                    print(
                        "[creative-suite-linux] Blender: existing executable payload preserved; "
                        "skipping download and VM",
                        flush=True,
                    )
                    events.append(preserved_blender["verification"])
                    application_log.write_text("\n".join(events) + "\n", encoding="utf-8")
                    results.append(preserved_blender)
                    save_report()
                    continue
                recovered = recover_installed_appimage(
                    entry, destination, cinterforgeBuildsRootPath(root) / "Linux/DebianAMD64.ext4"
                )
                if recovered is not None:
                    print(
                        f"[creative-suite-linux] {entry['name']}: installed AppImage verified "
                        "against staged release; skipping download and VM",
                        flush=True,
                    )
                    events.append(recovered["verification"])
                    application_log.write_text("\n".join(events) + "\n", encoding="utf-8")
                    results.append(recovered)
                    save_report()
                    continue
                print(
                    f"[creative-suite-linux] {entry['name']}: no matching installation receipt "
                    "or verifiable installed AppImage; installation required",
                    flush=True,
                )
            print(f"[creative-suite-linux] {entry['name']}: staging package source={entry['linux']['source']}", flush=True)
            events.append("stage: downloading/extracting package")
            result = stage_one(entry, destination)
            events.append(f"staged={json.dumps(result, default=str)}")
            # Installation is performed by the actual guest runner. Staging a
            # package on macOS is never evidence that it works inside Linux.
            # The guest runner is responsible for native/translated execution
            # and for installing repository packages inside the guest only.
            command = [
                sys.executable, "-u", str(runner), "--repo-root", str(root), "--install", "--application", entry["name"],
                "--architecture", entry["linux"]["architecture"],
                "--source-type", "extracted-tar" if result.get("packageType") in ("extracted-tar",) else entry["linux"]["sourceType"],
                "--source", entry["linux"]["source"],
            ]
            if result.get("payload") and result.get("packageType") not in ("extracted-tar",):
                command.extend(["--payload", result["payload"]])
            events.append(f"guest command={json.dumps(command)}")
            print(f"[creative-suite-linux] {entry['name']}: starting guest (log: {application_log})", flush=True)
            # Stream guest output to the terminal and durable per-app log.
            # Do not buffer a VM's entire lifetime in capture_output=True.
            application_log.write_text("\n".join(events) + "\n", encoding="utf-8")
            guest_output = application_log.with_name(f"{safe_name(entry['name'])}.Guest.log")
            # The outer staging supervisor must outlive the guest's own
            # 1800-second Natron Flatpak install deadline. Otherwise it kills
            # the VM at 360 seconds even while the SDK/app is still downloading.
            guest_timeout_seconds = (
                1860
                if entry["name"] == "Natron" and entry["linux"]["sourceType"] == "flatpak"
                else 360
            )
            started = time.monotonic()
            with guest_output.open("w", encoding="utf-8") as stream:
                process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT, text=True)
                offset = 0
                last_heartbeat = started
                try:
                    while process.poll() is None:
                        stream.flush()
                        with guest_output.open("r", encoding="utf-8", errors="replace") as reader:
                            reader.seek(offset)
                            chunk = reader.read()
                            offset = reader.tell()
                        if chunk:
                            print(chunk, end="" if chunk.endswith("\n") else "\n", flush=True)
                        now = time.monotonic()
                        if now - started >= guest_timeout_seconds:
                            process.terminate()
                            try:
                                process.wait(timeout=10)
                            except subprocess.TimeoutExpired:
                                process.kill()
                                process.wait()
                            raise TimeoutError(
                                f"Guest runner exceeded {guest_timeout_seconds}s; see {guest_output}"
                            )
                        if now - last_heartbeat >= 15:
                            print(f"[creative-suite-linux] {entry['name']}: guest running {int(now - started)}s; log={guest_output}", flush=True)
                            last_heartbeat = now
                        time.sleep(0.5)
                except BaseException:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                    raise
                stream.flush()
            with guest_output.open("r", encoding="utf-8", errors="replace") as reader:
                reader.seek(offset)
                remaining = reader.read()
            if remaining:
                print(remaining, end="" if remaining.endswith("\n") else "\n", flush=True)
            combined_output = guest_output.read_text(encoding="utf-8", errors="replace")
            events.append(f"guest exit code={process.returncode}")
            events.append(f"guest output log={guest_output}")
            execution = subprocess.CompletedProcess(command, process.returncode, stdout=combined_output, stderr=combined_output)
            result["guestExitCode"] = execution.returncode
            result["guestStdout"] = execution.stdout[-4000:]
            result["guestStderr"] = execution.stderr[-4000:]
            if execution.returncode != 0:
                raise RuntimeError(
                    f"Linux guest installation failed for {entry['name']} "
                    f"(exit {execution.returncode}): {execution.stderr[-2000:]}"
                )
            result["status"] = "installed_in_guest"
            result["installed"] = True
            # Debian archives are only needed until dpkg/apt succeeds. Keep
            # extracted applications and AppImages: those are runtime payloads.
            if result.get("packageType") == "deb-awaiting-guest-install":
                shutil.rmtree(destination / safe_name(entry["name"]))
                result.pop("payload", None)
        except Exception as exc:
            events.append("exception:\n" + traceback.format_exc())
            result = {"name": entry["name"], "status": "failed", "installed": False,
                      "error": str(exc), "log": str(application_log)}
            failed = True
        finally:
            application_log.write_text("\n".join(events) + "\n", encoding="utf-8")
        result["log"] = str(application_log)
        results.append(result)
        save_report()
        print(f"[creative-suite-linux] {result['name']}: {result['status']} "
              f"{result.get('error', '')} (log: {application_log})", flush=True)
        if failed:
            break  # A shared guest failure is not eight separate app failures.
    if not results:
        parser.error("No matching Creative Suite application")
    # Installation success is not document automation success. The separate
    # smoke-test examples must still create and validate both file formats.
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
