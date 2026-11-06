#!/usr/bin/env python3
"""Creative Suite Linux document-automation examples and acceptance tests.

This module is intentionally separate from Linux package installation: each
application's native-project/export pair is a *sample automation contract* for
future CinterForge document workflows. Add working examples to the Linux guest
runner, not host-side substitutes that merely fabricate the expected files.

The actual application must create and save both documents inside the guest.
Do not automate mouse coordinates, keyboard events, or accessibility controls.
Use the application's scripting, CLI, or headless document engine instead.

Run this file independently when iterating on the guest runner, or let
update_data_pipeline.py invoke it during macOS --update-data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import zipfile
import platform
import subprocess
import shutil
import sys
from pathlib import Path
# Import the shared Python filesystem authority from this checkout.
_CINTER_PACKAGE_PARENT = next(
    parent for parent in Path(__file__).resolve().parents
    if (parent / "CinterForgeServer" / "cf" / "io" / "CinterFileIO.py").is_file()
)
if str(_CINTER_PACKAGE_PARENT / "CinterForgeServer") not in sys.path:
    sys.path.insert(0, str(_CINTER_PACKAGE_PARENT / "CinterForgeServer"))
from cf.io.CinterIO import cinterforgeBuildsRootPath


# SAMPLE OUTPUT CONTRACT
# Each entry describes two independently validated outputs from ONE newly
# created document. The first is the application's editable native project;
# the second is its standard export. The guest runner implements the actual
# application-specific creation and export steps.
OUTPUTS = {
    "GIMP": ("xcf", "png"),
    "Krita": ("kra", "png"),
    "Inkscape": ("svg", "pdf"),
    "Scribus": ("sla", "pdf"),
    "Blender": ("blend", "glb"),
    "Kdenlive": ("kdenlive", "mp4"),
    "CinterForge Converter": ("wav", "flac"),
    "Ardour": ("ardour", "wav"),
    "RapidRAW": ("input.png", "png"),
}


def validate_format(path: Path, extension: str) -> None:
    """Validate recognizable file structure, not just a nonempty filename.

    This host-side check supplements, but cannot replace, the application's
    own ability to reopen its native project. Guest examples should reopen
    the project before exporting whenever the application supports it.
    """
    if not path.is_file() or path.stat().st_size < 16:
        raise ValueError(f"Missing or undersized output: {path.name}")
    with path.open("rb") as stream:
        header = stream.read(64)
    if extension == "png" and not header.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("PNG signature missing")
    if extension == "pdf" and not header.startswith(b"%PDF-"):
        raise ValueError("PDF signature missing")
    if extension == "flac" and not header.startswith(b"fLaC"):
        raise ValueError("FLAC signature missing")
    if extension == "wav" and not (header[:4] in (b"RIFF", b"RF64") and header[8:12] == b"WAVE"):
        raise ValueError("WAV signature missing")
    if extension == "xcf" and not header.startswith(b"gimp xcf "):
        raise ValueError("XCF signature missing")
    if extension == "blend" and not header.startswith(b"BLENDER"):
        raise ValueError("Blender signature missing")
    if extension == "glb" and not (header[:4] == b"glTF" and len(header) >= 12 and struct.unpack("<I", header[8:12])[0] == path.stat().st_size):
        raise ValueError("GLB header or declared length invalid")
    if extension in {"kra", "aup3"}:
        signature = b"PK\x03\x04" if extension == "kra" else b"SQLite format 3\x00"
        if not header.startswith(signature):
            raise ValueError(f"{extension} container signature missing")
        if extension == "kra" and not zipfile.is_zipfile(path):
            raise ValueError("KRA ZIP container invalid")
    if extension in {"svg", "sla", "kdenlive", "ntp", "ardour"}:
        # XML-based projects: reject arbitrary binary data or unrelated text.
        import xml.etree.ElementTree as ET
        try:
            tree = ET.parse(path)
        except ET.ParseError as error:
            raise ValueError(f"Invalid XML in {extension} output: {error}") from error
        tag = tree.getroot().tag.lower()
        expected = {"svg": "svg", "sla": "scribusutf8new", "kdenlive": "mlt", "ntp": "natron", "ardour": "session"}[extension]
        if expected not in tag:
            raise ValueError(f"Unexpected {extension} XML root: {tag}")
    if extension == "mp4":
        if b"ftyp" not in header[4:16]:
            raise ValueError("MP4 file type box missing")



def prepare_linux_environment(root: Path, guest_runner: Path) -> None:
    """Use the shared Linux builder before installing Creative Suite packages."""
    linux_builder = Path(__file__).resolve().parents[2] / "apps" / "cinterforge-os" / "linux" / "build_linux.py"
    subprocess.run([
        sys.executable, "-u", str(linux_builder),
        "--repo-root", str(root), "--prepare-debian",
    ], check=True)
    package_script = Path(__file__).with_name("linux_packages.py")
    print("[creative-suite-linux] installing missing Linux Creative Suite applications", flush=True)
    subprocess.run([
        sys.executable, "-u", str(package_script),
        "--repo-root", str(root), "--guest-runner", str(guest_runner),
        "--skip-installed",
    ], cwd=root, check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--guest-runner", type=Path, help="Executable guest automation runner (not a shell string)")
    parser.add_argument("--skip-prepare", action="store_true", help="Run smoke tests without building or installing Linux applications")
    parser.add_argument("--application", choices=tuple(OUTPUTS), help="Test one application")
    args = parser.parse_args()
    root = args.repo_root.resolve()
    output = cinterforgeBuildsRootPath(root) / "CreativeSuite" / "Tests"
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / "SmokeTestReport.json"
    report = {"schemaVersion": 1, "platform": platform.system(), "ok": False, "applications": {}}

    if platform.system() != "Darwin":
        report["status"] = "not_applicable_windows_uses_native_automation"
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        return 0

    # The shared Python guest runner is invoked through sys.executable.
    # It need not have the executable filesystem bit set.
    if args.guest_runner is None or not args.guest_runner.is_file() or not os.access(args.guest_runner, os.R_OK):
        report["status"] = "missing_creative_suite_guest_runner"
        report["error"] = (
            "Creative Suite Linux guest runner is missing or unreadable: " + str(args.guest_runner)
        )
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        print("[creative-suite-linux] " + report["error"], file=sys.stderr)
        return 1

    # --skip-prepare skips Debian/package installation, NOT the LinuxKit image
    # build. Smoke automation lives inside linuxkit.yml and must be rebuilt
    # when its source changes, otherwise the VM runs an obsolete command.
    try:
        if args.skip_prepare:
            linux_builder = Path(__file__).resolve().parents[2] / "apps" / "cinterforge-os" / "linux" / "build_linux.py"
            print("[creative-suite-linux] verifying current LinuxKit guest image", flush=True)
            subprocess.run([
                sys.executable, "-u", str(linux_builder), "--repo-root", str(root),
            ], check=True)
        else:
            prepare_linux_environment(root, args.guest_runner.resolve())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        report["status"] = "linux_preparation_failed"
        report["error"] = str(error)
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        print(f"[creative-suite-linux] {error}", file=sys.stderr)
        return 1

    # EXAMPLE EXECUTION CONTRACT
    # The runner accepts --application NAME --output DIRECTORY, creates a new
    # document using that application's real automation interface, then saves
    # both files in the shared output directory. A nonzero exit or missing
    # output fails this application; no placeholder outputs are acceptable.
    selected = {args.application: OUTPUTS[args.application]} if args.application else OUTPUTS
    for name, extensions in selected.items():
        artifact_name = "Converter" if name == "CinterForge Converter" else name
        paths = [output / f"{artifact_name}.{ext}" for ext in extensions]
        for path in paths:
            path.unlink(missing_ok=True)
        print(f"[creative-suite-linux] runtime smoke: {name}", flush=True)
        try:
            result = subprocess.run(
                [sys.executable, str(args.guest_runner), "--repo-root", str(root), "--smoke-test", "--application", name, "--output", str(output)],
                capture_output=True, text=True, check=False,
                timeout=110 if name == "Tenacity" else 360,
            )
            exit_code = result.returncode
            stdout = result.stdout
            stderr = result.stderr
        except subprocess.TimeoutExpired as error:
            # The guest runner has its own 90-second Tenacity VM deadline.
            # This second deadline also bounds launcher/cleanup failures.
            exit_code = 124
            stdout = (error.stdout or b"").decode(errors="replace") if isinstance(error.stdout, bytes) else (error.stdout or "")
            stderr = (error.stderr or b"").decode(errors="replace") if isinstance(error.stderr, bytes) else (error.stderr or "")
            stderr += "\nSmoke guest runner exceeded its deadline; see Logs/Tenacity.VM.log."

        # The VM writes through VirtioFS into Linux/Tests.  Copy those files
        # here as well as in linux_guest.py: an older guest runner must not
        # cause every otherwise successful application to fail validation.
        # Never copy from a previous test: the guest runner clears this folder
        # before dispatch, and the host removes expected outputs above.
        guest_output = cinterforgeBuildsRootPath(root) / "CreativeSuite" / "Linux" / "Tests"
        if guest_output.is_dir():
            for guest_file in guest_output.iterdir():
                if guest_file.is_file() and not guest_file.is_symlink():
                    target = output / guest_file.name
                    if guest_file.resolve() != target.resolve():
                        shutil.copy2(guest_file, target)
            print(f"[creative-suite-linux] synchronized guest artifacts: {name}", flush=True)

        # This checks that files were produced, not yet their internal format
        # or content. The guest runner must perform format/content validation;
        # expand this host-side check when format validators are available.
        errors = []
        for path, extension in zip(paths, extensions):
            try:
                validate_format(path, extension)
            except (ValueError, OSError) as error:
                errors.append(f"{path.name}: {error}")
        valid = exit_code == 0 and not errors
        print(f"[creative-suite-linux] {name}: {'PASS' if valid else 'FAIL'} (exit={exit_code})", flush=True)
        report["applications"][name] = {
            "ok": valid, "exitCode": exit_code,
            "validationErrors": errors,
            "nativeProject": paths[0].name, "export": paths[1].name,
            "stdout": stdout[-4000:], "stderr": stderr[-4000:],
        }
    report["ok"] = all(item["ok"] for item in report["applications"].values())
    report["status"] = "passed" if report["ok"] else "failed"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
