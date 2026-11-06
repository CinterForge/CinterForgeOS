"""Shared test contract; GUI probes must target installed apps, not build trees."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TestSpec:
    app: str
    platform: str
    installed_directory: Path


def require_installed_application(spec: TestSpec) -> None:
    """Fail early if a smoke test cannot find the installed payload."""
    from lifecycle import require_payload
    require_payload(spec.installed_directory, spec.platform)


def test_installed_application(spec: TestSpec) -> None:
    """Future semantic GUI/headless test routing, with no coordinate clicking."""
    require_installed_application(spec)
    raise NotImplementedError(f"No shared installed test recipe registered for {spec.app}")
