"""Shared staging façade over the established Creative Suite artifact contract."""
from __future__ import annotations
from pathlib import Path


def require_staged_payload(directory: Path, *, platform: str) -> None:
    """Reuse the existing lifecycle validation rather than duplicating it."""
    from lifecycle import require_payload
    require_payload(directory, platform)


def stage_application(*, built_archive: Path, staged_directory: Path, platform: str) -> None:
    """Stage a previously built ZIP; never build or download during stage."""
    from lifecycle import extract_zip
    extract_zip(built_archive, staged_directory)
    require_staged_payload(staged_directory, platform=platform)
