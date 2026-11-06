"""Common build execution, cache locking, and versioned paths for Creative Suite apps.

No app-specific dependencies or source modifications belong here. Build operations
preserve their working directories between retries; stage/remove are owned by lifecycle.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from collections.abc import Iterator, Sequence


PLATFORMS = frozenset({"mac", "linux", "win"})


def product_build_directory(builds_root: Path, app: str, platform: str, version: str) -> Path:
    """Resolve the sole, per-platform/version writable compilation root."""
    if platform not in PLATFORMS:
        raise ValueError(f"Unsupported platform: {platform!r}")
    for kind, value in (("app", app), ("version", version)):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value) or value in (".", ".."):
            raise ValueError(f"Invalid {kind}: {value!r}")
    return builds_root / "CreativeSuite" / "Built" / app / platform / version


@contextmanager
def build_lock(directory: Path) -> Iterator[None]:
    """Hold an exclusive advisory lock for the entire configure/build/install phase."""
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".build.lock").open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"Build directory is already in use: {directory}") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def run_visible(command: Sequence[str], *, cwd: Path | None = None,
                env: dict[str, str] | None = None, label: str = "build",
                heartbeat_seconds: int = 30) -> None:
    """Stream child diagnostics unmodified; report progress and propagate failures."""
    cmd = [str(item) for item in command]
    description = " ".join(cmd)
    print(f"[{label}] START {description}", flush=True)
    started = time.monotonic()
    finished = threading.Event()

    def heartbeat() -> None:
        while not finished.wait(heartbeat_seconds):
            print(f"[{label}] WORKING {description}: {int(time.monotonic() - started)}s", flush=True)

    worker = threading.Thread(target=heartbeat, daemon=True)
    worker.start()
    try:
        subprocess.run(cmd, cwd=cwd, env=env, check=True)
    except subprocess.CalledProcessError as exc:
        print(f"[{label}] FAILED {description}: exit={exc.returncode} after "
              f"{int(time.monotonic() - started)}s", flush=True)
        raise
    finally:
        finished.set()
        worker.join(timeout=1)
    print(f"[{label}] DONE {description}: {int(time.monotonic() - started)}s", flush=True)


def validate_fork(fork: Path, *, expected_origin: str, required_file: str = "waf") -> None:
    """Fail rather than quietly substituting an unrelated upstream checkout."""
    if not (fork / ".git").exists() or not (fork / required_file).is_file():
        raise RuntimeError(f"Maintained source checkout missing: {fork}")
    origin = subprocess.check_output(["git", "remote", "get-url", "origin"],
                                     cwd=fork, text=True).strip()
    if expected_origin not in origin:
        raise RuntimeError(f"Wrong source checkout origin: {origin}; expected {expected_origin}")


def verify_pe_amd64(output: Path, *, label: str = "build") -> tuple[int, int]:
    """Verify Windows PE headers rather than trusting .exe/.dll extensions."""
    binaries = sorted(path for path in output.rglob("*")
                      if path.is_file() and path.suffix.lower() in (".exe", ".dll"))
    executables = [path for path in binaries if path.suffix.lower() == ".exe"]
    if not executables:
        raise RuntimeError(f"Windows output contains no executable: {output}")
    for path in binaries:
        with path.open("rb") as binary:
            header = binary.read(64)
            if len(header) < 64 or header[:2] != b"MZ":
                raise RuntimeError(f"Not a PE binary: {path}")
            offset = int.from_bytes(header[0x3c:0x40], "little")
            if not (64 <= offset <= 1024 * 1024):
                raise RuntimeError(f"Invalid PE header offset: {path}")
            binary.seek(offset)
            signature = binary.read(6)
            if len(signature) != 6 or signature[:4] != b"PE\x00\x00":
                raise RuntimeError(f"Missing PE signature: {path}")
            machine = int.from_bytes(signature[4:6], "little")
            if machine != 0x8664:
                raise RuntimeError(f"Expected PE AMD64, got 0x{machine:04x}: {path}")
        print(f"[{label}] verified PE AMD64: {path.relative_to(output)}", flush=True)
    return len(executables), len(binaries) - len(executables)
