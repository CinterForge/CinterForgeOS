"""Reusable macOS native-application packaging primitives.

Application-specific packaging *recipes* stay in apps/<name>/mac/build.py;
copying and validating a completed .app is generic.
"""
from __future__ import annotations

import os
from pathlib import Path
import plistlib
import shutil
import tempfile


def validate_application_bundle(bundle: Path) -> Path:
    """Require a real macOS app bundle and its declared executable."""
    bundle = Path(bundle).resolve(strict=True)
    if not bundle.is_dir() or bundle.suffix != ".app":
        raise RuntimeError(f"Not a macOS application bundle: {bundle}")
    contents = bundle / "Contents"
    info = contents / "Info.plist"
    if not info.is_file():
        raise RuntimeError(f"Missing macOS application Info.plist: {info}")
    with info.open("rb") as stream:
        metadata = plistlib.load(stream)
    executable = metadata.get("CFBundleExecutable")
    if not isinstance(executable, str) or not executable or Path(executable).name != executable:
        raise RuntimeError(f"Invalid CFBundleExecutable in {info}: {executable!r}")
    binary = contents / "MacOS" / executable
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise RuntimeError(f"Missing/non-executable bundle entry point: {binary}")
    return bundle


def package_application(*, installation: Path, output: Path) -> Path:
    """Atomically copy a completed native .app to an application-owned output folder.

    The upstream packager builds the bundle; this routine only validates and
    installs the finished bundle without modifying its contents or symlinks.
    Refuse to overwrite a previous good output with an invalid bundle.
    """
    source = validate_application_bundle(Path(installation))
    destination = Path(output).expanduser().absolute()
    if destination.suffix == ".app":
        target = destination
    else:
        target = destination / source.name
    if target.resolve() == source:
        return source
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".mac-package-", dir=target.parent) as temporary:
        incoming = Path(temporary) / source.name
        shutil.copytree(source, incoming, symlinks=True)
        validate_application_bundle(incoming)
        # macOS does not guarantee atomic exchange of two nonempty directories;
        # keep the prior installed bundle untouched until the new one validates.
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"Refusing to overwrite existing macOS app: {target}")
        incoming.rename(target)
    return validate_application_bundle(target)
