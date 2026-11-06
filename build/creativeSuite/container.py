"""Reusable OCI build operations for Creative Suite Linux/Windows cross-builds.

The caller chooses the existing container backend, platform, recipe and mounts.
Nothing here starts a VM, modifies source, or installs host packages.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
from collections.abc import Mapping, Sequence

from shared.build.build_runtime import run_visible


def require_image_architecture(*, engine: str, image: str, architecture: str,
                               env: dict[str, str], label: str = "container") -> None:
    """Fail loudly when the selected image cannot run the requested ABI."""
    actual = subprocess.check_output(
        [engine, "image", "inspect", "--format", "{{.Architecture}}", image],
        env=env, text=True,
    ).strip()
    if actual != architecture:
        raise RuntimeError(
            f"[{label}] Wrong architecture for {image}: {actual!r}; expected {architecture!r}"
        )


def build_cached_image(*, engine: str, image: str, dockerfile: Path,
                       context: Path, platform: str, architecture: str,
                       env: dict[str, str], label: str = "container") -> None:
    """Build using Docker's normal layer cache and check the resulting ABI."""
    if not dockerfile.is_file() or not context.is_dir():
        raise FileNotFoundError(f"[{label}] Missing build recipe/context: {dockerfile}, {context}")
    # --progress belongs to BuildKit/buildx, not Docker's legacy builder.
    # Use the common docker build flags so a newly created Colima daemon can
    # reproduce the dependency image without requiring buildx to be installed.
    run_visible(
        [engine, "build", "--platform", platform,
         "--pull=false", "--tag", image, "--file", str(dockerfile), str(context)],
        env=env, label=f"{label}: dependency image",
    )
    require_image_architecture(engine=engine, image=image, architecture=architecture,
                               env=env, label=label)



class BindMountUnavailable(RuntimeError):
    """The daemon ran the probe, but could not access its host inputs."""


def probe_bind_mounts(*, engine: str, image: str, platform: str,
                      mounts: Sequence[tuple[Path, str, bool]],
                      env: dict[str, str], label: str = "container",
                      required: Mapping[str, Sequence[str]] | None = None) -> None:
    """Check the *daemon-visible* paths, not just files on the client host.

    Docker's daemon may be inside Colima with a different filesystem view.
    Probe real files inside every important mount; a mounted empty directory
    or an automatically created missing bind path cannot establish success.
    This is shared by Windows cross-builds and the SteamOS guest builder.
    """
    import posixpath
    import shlex

    # Separate missing probe images from missing mount shares. Docker's
    # "No such image" is NOT a bind failure and must never restart Colima.
    inspected = subprocess.run(
        [engine, "image", "inspect", image], env=env, text=True,
        capture_output=True, check=False,
    )
    if inspected.returncode:
        raise RuntimeError(
            f"[{label}] Docker probe image {image!r} is unavailable on the "
            "selected daemon; mount accessibility has NOT been tested. "
            f"{(inspected.stderr or inspected.stdout).strip()}"
        )
    args = [engine, "run", "--rm", "--pull=never", "--platform", platform]
    checks = []
    for source, destination, readonly in mounts:
        source = source.expanduser().resolve(strict=True)
        if not destination.startswith("/") or ".." in Path(destination).parts:
            raise ValueError(f"[{label}] Invalid mount destination: {destination}")
        args += ["--mount", f"type=bind,source={source},target={destination}" +
                 (",readonly" if readonly else "")]
        expected = tuple((required or {}).get(destination, ()))
        if not expected and source.is_dir():
            # Prefer an existing non-empty file to distinguish real mounts from
            # an empty host path accidentally fabricated by the Docker daemon.
            sample = next((entry for entry in source.iterdir() if entry.is_file()), None)
            if sample is None:
                sample = next((entry for entry in source.iterdir() if entry.is_dir()), None)
            if sample is not None:
                expected = (sample.name,)
        for relative in expected:
            if relative.startswith("/") or ".." in Path(relative).parts:
                raise ValueError(f"[{label}] Unsafe mount probe path: {relative}")
            target = posixpath.join(destination, relative)
            if not (source / relative).exists():
                raise FileNotFoundError(f"[{label}] Required host input is missing: {source / relative}")
            checks.append(f"test -e {shlex.quote(target)}")
        checks.append(f"test -d {shlex.quote(destination)}" if source.is_dir() else
                      f"test -f {shlex.quote(destination)}")
    args += ["--entrypoint", "/bin/sh", image, "-ec", " && ".join(checks) or "true"]
    result = subprocess.run(args, env=env, text=True, capture_output=True, check=False)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise BindMountUnavailable(
            f"[{label}] Docker bind probe failed with installed image {image}: {detail}. "
            "Verify the daemon can see the host paths and the required files."
        )
    print(f"[{label}] VERIFIED daemon-visible bind mounts ({len(mounts)})", flush=True)

def run_mounted_image(*, engine: str, image: str, platform: str,
                      mounts: Sequence[tuple[Path, str, bool]],
                      variables: Mapping[str, str], command: Sequence[str],
                      env: dict[str, str], label: str = "container") -> None:
    """Run an existing image with explicitly declared host paths and environment.

    Each mount is (source, guest_path, readonly). Writable outputs are owned by
    the caller and remain available after the disposable container exits.
    """
    probe_bind_mounts(engine=engine, image=image, platform=platform,
                      mounts=mounts, env=env, label=label)
    arguments = [engine, "run", "--rm", "--pull=never", "--platform", platform]
    for host, guest, readonly in mounts:
        host = host.expanduser().resolve(strict=True)
        if not guest.startswith("/") or ".." in Path(guest).parts:
            raise ValueError(f"Invalid container mount destination: {guest!r}")
        arguments.extend(["--mount", f"type=bind,source={host},target={guest}" +
                          (",readonly" if readonly else "")])
    for name, value in variables.items():
        if not name or "=" in name or "\x00" in name or "\x00" in value:
            raise ValueError(f"Invalid container environment name: {name!r}")
        arguments.extend(["-e", f"{name}={value}"])
    arguments.extend([image, *command])
    run_visible(arguments, env=env, label=label)
