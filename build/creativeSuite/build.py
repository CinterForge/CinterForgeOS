"""Shared native compilation entry points; recipes supply the actual commands."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Sequence
from shared.build.build_runtime import build_lock, product_build_directory, run_visible


@dataclass(frozen=True)
class BuildSpec:
    app: str
    platform: str
    version: str
    builds_root: Path

    @property
    def directory(self) -> Path:
        return product_build_directory(self.builds_root, self.app, self.platform, self.version)


def execute_commands(commands: Sequence[Sequence[str]], *, spec: BuildSpec, cwd: Path,
                     env: dict[str, str] | None = None) -> None:
    """Lock a versioned build and run app-provided commands sequentially."""
    with build_lock(spec.directory):
        for command in commands:
            run_visible(command, cwd=cwd, env=env, label=spec.app)


def compile_application(spec: BuildSpec) -> None:
    """Future recipe dispatch for Waf/CMake/Meson/etc.; no magic fallback."""
    raise NotImplementedError(f"No shared compiler recipe registered for {spec.app}")


def configure_if_changed(*, source: Path, stamp: Path, fingerprint: str,
                         configured_cache: Path, command: Sequence[str],
                         env: dict[str, str] | None = None, label: str = "build") -> bool:
    """Reuse incremental configure state only for the same inputs.

    Returns True when configure ran. Writes stamp only after success.
    """
    if configured_cache.is_file() and stamp.is_file() and stamp.read_text() == fingerprint:
        print(f"[{label}] REUSE configuration and incremental objects", flush=True)
        return False
    print(f"[{label}] CONFIGURE: missing cache or changed inputs", flush=True)
    run_visible(command, cwd=source, env=env, label=label)
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(fingerprint, encoding="utf-8")
    return True


def reusable_installation(*, installation: Path, marker: Path, revision: str,
                          label: str = "build") -> bool:
    """Reuse a completed install only when its source revision exactly matches.

    A partial failed install without the success marker is never considered valid.
    """
    if installation.is_dir() and marker.is_file() and marker.read_text(encoding="utf-8").strip() == revision:
        print(f"[{label}] REUSE installed revision={revision} path={installation}", flush=True)
        return True
    return False


def mark_successful_install(*, installation: Path, marker: Path, revision: str) -> None:
    """Mark an install reusable only after it exists and build/install succeeded."""
    if not installation.is_dir() or not any(installation.iterdir()):
        raise RuntimeError(f"Cannot mark empty or missing install successful: {installation}")
    marker.parent.mkdir(parents=True, exist_ok=True)
    temporary = marker.with_name(marker.name + ".tmp")
    temporary.write_text(revision + "\n", encoding="utf-8")
    temporary.replace(marker)
