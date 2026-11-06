"""Shared removal planning; installed version ownership stays with lifecycle."""
from __future__ import annotations
from pathlib import Path


def validate_removal_target(installed_version: Path, *, applications_root: Path) -> None:
    """Require a nested version directory; refuse root/parent deletion."""
    root = applications_root.resolve(strict=True)
    target = installed_version.resolve(strict=True)
    if not target.is_relative_to(root) or len(target.relative_to(root).parts) < 3:
        raise ValueError(f"Unsafe application removal target: {installed_version}")


def remove_installed_version(installed_version: Path, *, applications_root: Path) -> None:
    """Remove one requested version, never the application or Applications root.

    Callers must resolve the exact version and handle user confirmation before
    calling this primitive. Special products with mutable persistent state need
    their own lifecycle handler rather than this generic operation.
    """
    import shutil
    validate_removal_target(installed_version, applications_root=applications_root)
    shutil.rmtree(installed_version)
