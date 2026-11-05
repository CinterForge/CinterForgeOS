"""One reusable Colima backend for the CinterForgeOS headless Ubuntu build.

Colima and Lima need short real filesystem paths on macOS. Their VM disks,
Docker storage and caches live on the build volume when the CinterForge checkout
is on an external volume; never silently fill the internal disk.
Application build products remain under Builds/CreativeSuite/Built.
"""
from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path
import platform
import shutil
import subprocess

# Lima's macOS socket address is limited to 104 bytes. Keep the managed
# runtime *inside Builds* but avoid long, product-facing names on its sockets.
SHORT_PROFILE = 'c'
LEGACY_PROFILE = 'cinterforge-linux'
SOCKET_PATH_LIMIT = 104


def host_executable(name: str) -> str | None:
    """Find a host tool when Finder launches Swift without Homebrew on PATH.

    User overrides support nonstandard package-manager prefixes. These are
    host executable paths, never guesses about repository/build locations.
    """
    configured = os.environ.get('CFOS_' + name.upper())
    if configured:
        executable = Path(configured).expanduser()
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise RuntimeError(f'Configured CFOS_{name.upper()} is not executable: {executable}')
        return str(executable.resolve())
    found = shutil.which(name)
    if found:
        return found
    for prefix in ('/opt/homebrew/bin', '/usr/local/bin'):
        executable = Path(prefix) / name
        if executable.is_file() and os.access(executable, os.X_OK):
            return str(executable)
    return None


def storage_root(source: Path) -> Path:
    """Resolve CFOS state from the canonical Linux build Source path.

    The installed Ubuntu adapter ships this helper outside the source repo.
    Its managed VM must therefore resolve from the caller's explicit Builds
    input, not from ``__file__`` or the current working directory. No Cinter
    or CinterForgeOS checkout is required when running the installed product.
    """
    source = Path(source)
    if not source.is_absolute():
        raise RuntimeError(f'CFOS Linux Source must be absolute: {source}')
    source = source.resolve()
    if (source.name != 'Source' or source.parent.name != 'linux'
            or source.parent.parent.name != 'cinterforge-os'
            or source.parents[2].name != 'Built'
            or source.parents[3].name != 'CreativeSuite'
            or source.parents[4].name != 'Builds'):
        raise RuntimeError('Unexpected CFOS Linux build Source path: '
                           f'{source}; expected <workspace>/Builds/CreativeSuite/'
                           'Built/cinterforge-os/linux/Source')
    builds = source.parents[4]
    root = builds / 'CreativeSuite/CFOS'
    # Legacy VM state under Built/ is not part of this selected backend.
    # It is deliberately left untouched; the canonical CFOS profile is
    # isolated under CreativeSuite/CFOS and cannot overwrite it.
    return root


def source_directory(repo: Path) -> Path:
    """Keep application build output in Builds, independent of VM storage."""
    if not (repo / 'CFOS').is_file():
        raise RuntimeError(f'Expected CinterForgeOS repository: {repo}')
    return repo.parent / 'Builds/CreativeSuite/Built/cinterforge-os/linux/Source' 


def managed_colima_settings(source: Path) -> tuple[Path, Path, str]:
    """Select one existing managed VM or the compact layout for a new one.

    Source checkout depth must not determine a product identity or require a
    relocation. Long-lived build state stays in Builds/CreativeSuite/CFOS.
    Only Colima's implementation-specific Unix sockets have a host OS limit.
    Do not replace a previously created Lima disk when the layout changes.
    """
    if not source.is_absolute():
        raise RuntimeError(f'Ubuntu build Source must be absolute: {source}')
    root = storage_root(source)
    home = root / 'Colima'
    legacy_lima = home / '_lima'
    compact_lima = root / 'L'
    legacy_paths = (
        legacy_lima / f'colima-{LEGACY_PROFILE}',
        legacy_lima / '_disks' / f'colima-{LEGACY_PROFILE}',
        home / LEGACY_PROFILE,
    )
    compact_paths = (
        compact_lima / f'colima-{SHORT_PROFILE}',
        compact_lima / '_disks' / f'colima-{SHORT_PROFILE}',
        home / SHORT_PROFILE,
    )
    legacy_present = any(path.exists() or path.is_symlink() for path in legacy_paths)
    compact_present = any(path.exists() or path.is_symlink() for path in compact_paths)
    legacy_socket = legacy_lima / f'colima-{LEGACY_PROFILE}' / 'ssh.sock.1234567890123456'
    # Prefer a previously created compact VM to prevent switching back to an
    # older instance on subsequent invocations. Reuse an existing legacy VM
    # only when its path is still usable. Otherwise start the compact profile
    # without deleting, renaming, or hiding the existing legacy VM or disks.
    if compact_present or not legacy_present or len(os.fsencode(legacy_socket)) >= SOCKET_PATH_LIMIT:
        lima, profile = compact_lima, SHORT_PROFILE
    else:
        lima, profile = legacy_lima, LEGACY_PROFILE
    # Lima / OpenSSH checks this *full* absolute path, not a relative one.
    socket_path = lima / f'colima-{profile}' / 'ssh.sock.1234567890123456'
    if len(os.fsencode(socket_path)) >= SOCKET_PATH_LIMIT:
        raise RuntimeError(
            f'Lima Unix socket would exceed the macOS {SOCKET_PATH_LIMIT}-byte limit: '
            f'{socket_path}. This is a Lima/kernel limit, not a CinterForge '
            'repository-location restriction. Existing VM state has not been '
            'changed. Use an existing Docker daemon or a filesystem mount '
            'with a shorter physical path for the managed VM; CinterForge '
            'source may remain wherever you choose.'
        )
    for directory in (root, home, lima):
        if directory.is_symlink():
            raise RuntimeError(f'CFOS managed state cannot be a symlink: {directory}')
        if directory.exists() and not directory.is_dir():
            raise RuntimeError(f'CFOS managed state path is not a directory: {directory}')
    return home, lima, profile


def managed_colima_home(source: Path) -> Path:
    """Return a validated, *physical* Colima home under the canonical Builds."""
    if platform.system() != 'Darwin':
        raise RuntimeError('Managed Colima storage requires macOS')
    home, lima, _ = managed_colima_settings(source)
    home.mkdir(parents=True, exist_ok=True)
    lima.mkdir(parents=True, exist_ok=True)
    return home


def backend_environment(source: Path, *, create: bool = True) -> dict[str, str]:
    """Compute the same Colima configuration used by macOS, Linux and Xcode."""
    home, lima, profile = managed_colima_settings(source)
    root = home.parent
    docker_config = root / 'Docker'
    cache = root / 'Cache' / 'Colima'
    if create:
        managed_colima_home(source)
        docker_config.mkdir(parents=True, exist_ok=True)
        cache.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update({
        'CFOS_COLIMA_PROFILE': profile,
        'COLIMA_HOME': str(home),
        'LIMA_HOME': str(lima),
        'COLIMA_CACHE_HOME': str(cache),
        'DOCKER_CONFIG': str(docker_config),
        'DOCKER_HOST': f'unix://{home / profile / "docker.sock"}',
    })
    environment.pop('DOCKER_CONTEXT', None)
    return environment


def print_shell_environment(repo: Path) -> None:
    """Expose one owner for paths/profile across bash and Python consumers."""
    environment = backend_environment(source_directory(repo), create=False)
    for key in ('CFOS_COLIMA_PROFILE', 'COLIMA_HOME', 'LIMA_HOME',
                'COLIMA_CACHE_HOME', 'DOCKER_CONFIG', 'DOCKER_HOST'):
        print(f'export {key}={shlex.quote(environment[key])}')
    print('unset DOCKER_CONTEXT')

def ensure_backend(source: Path) -> tuple[str, dict[str, str]]:
    """Start or reuse one headless Apple Silicon Colima VM with Rosetta."""
    if platform.system() == 'Linux':
        docker = host_executable('docker')
        if not docker:
            raise RuntimeError('CFOS Linux backend requires Docker')
        # Do not replace the caller's Linux Docker context, daemon, or storage.
        environment = os.environ.copy()
        subprocess.run([docker, 'info'], env=environment, check=True, stdout=subprocess.DEVNULL)
        return docker, environment
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise RuntimeError('CFOS Linux backend requires Linux or Apple Silicon macOS')
    docker = host_executable('docker')
    colima = host_executable('colima')
    if not docker or not colima:
        missing = [name for name, executable in (('docker', docker), ('colima', colima))
                   if executable is None]
        raise RuntimeError(
            'Missing host tools: ' + ', '.join(missing)
            + '; refusing automatic installation onto the internal disk.'
        )

    environment = backend_environment(source)
    # Finder does not inherit the interactive shell's Homebrew PATH. Colima
    # may itself invoke limactl and other helpers by name; resolve their
    # executable parents for the CHILD environment, never change the user's
    # global PATH or tool installation.
    host_bins = []
    for binary in (docker, colima, host_executable('limactl')):
        if binary:
            directory = str(Path(binary).parent)
            if directory not in host_bins:
                host_bins.append(directory)
    inherited = environment.get('PATH', '').split(os.pathsep)
    environment['PATH'] = os.pathsep.join(host_bins + [p for p in inherited if p and p not in host_bins])
    profile = environment['CFOS_COLIMA_PROFILE']
    status = subprocess.run(
        [colima, 'status', '--profile', profile],
        env=environment, capture_output=True, text=True, check=False,
    )
    if status.returncode:
        print(f'[Ubuntu backend] Starting {profile}; VM storage at {environment["COLIMA_HOME"]}', flush=True)
        managed_instance = Path(environment['LIMA_HOME']) / f'colima-{profile}'
        # An existing profile may have been resized for macOS SteamOS Build.
        # Starting Linux again must not silently shrink it back to 8 GiB.
        if managed_instance.is_dir():
            start_command = [colima, 'start', '--profile', profile, '--activate=false']
        else:
            start_command = [
                colima, 'start', '--profile', profile, '--vm-type', 'vz',
                '--arch', 'aarch64', '--vz-rosetta', '--cpu', '4',
                '--memory', '8', '--disk', '96', '--activate=false',
            ]
        # A partially deleted disposable VM can leave either a stale Lima
        # instance, a detached disk, or both. Handle either failure at *any*
        # startup attempt; do not assume they arrive in a particular order.
        # Recovery is bounded and scoped to this profile's external Lima home.
        external_home = Path(environment['COLIMA_HOME']).resolve()
        managed_disk = Path(environment['LIMA_HOME']) / '_disks' / f'colima-{profile}'
        for attempt in range(4):
            start = subprocess.run(
                start_command, env=environment, capture_output=True, text=True,
                check=False,
            )
            if start.stdout:
                print(start.stdout, end='' if start.stdout.endswith('\n') else '\n', flush=True)
            if start.stderr:
                print(start.stderr, end='' if start.stderr.endswith('\n') else '\n', flush=True)
            if start.returncode == 0:
                break

            diagnostics = start.stdout + start.stderr
            missing_agent = (
                'ha.sock' in diagnostics
                and 'no such file or directory' in diagnostics
                and 'errors inspecting instance' in diagnostics
                and str(managed_instance) in diagnostics
            )
            # Lima also reports this exact broken VZ/host-agent state without
            # mentioning ha.sock or the instance pathname. The VM is unusable;
            # only Setup may recreate this explicitly managed disposable profile.
            broken_vz_agent = (
                'vz driver is running but host agent is not' in diagnostics
                and 'errors inspecting instance' in diagnostics
                and f'colima-{profile}' in diagnostics
            )
            disk_conflict = (
                f'disk `colima-{profile}` already exists' in diagnostics
                and str(managed_disk) in diagnostics
            )
            if attempt == 3 or not (missing_agent or broken_vz_agent or disk_conflict):
                raise RuntimeError(
                    f'Managed Colima startup failed (exit={start.returncode}); '
                    'no further safe managed-profile recovery available. '
                    f'{diagnostics.strip()[-1400:]}'
                )
            if missing_agent or broken_vz_agent:
                print(f'[Ubuntu backend] Discarding incomplete disposable {profile} '
                      f'instance under {external_home}; no migration', flush=True)
                delete = subprocess.run(
                    [colima, 'delete', '--force', '--profile', profile],
                    env=environment, capture_output=True, text=True, check=False,
                )
                if delete.returncode:
                    raise RuntimeError(
                        f'Could not delete incomplete managed Colima profile '
                        f'(exit={delete.returncode}): '
                        f'{(delete.stderr or delete.stdout).strip()}'
                    )
            else:
                lima = host_executable('limactl')
                if not lima:
                    raise RuntimeError(
                        f'Orphaned managed Lima disk {managed_disk}; limactl is '
                        'unavailable. Refusing filesystem-level deletion.'
                    )
                print(f'[Ubuntu backend] Discarding orphaned disposable '
                      f'colima-{profile} disk under {external_home}; no migration', flush=True)
                remove_disk = subprocess.run(
                    [lima, 'disk', 'delete', f'colima-{profile}'],
                    env=environment, capture_output=True, text=True, check=False,
                )
                if remove_disk.returncode:
                    raise RuntimeError(
                        f'Could not delete orphaned managed Lima disk '
                        f'(exit={remove_disk.returncode}): '
                        f'{(remove_disk.stderr or remove_disk.stdout).strip()}'
                    )
                # Lima can report success while leaving the on-disk directory
                # behind (for example after manual cleanup of VM metadata).
                # That directory prevents every subsequent clean VM creation.
                # Remove ONLY this disposable, disconnected disk, never the
                # containing Lima storage or another profile's data.
                if managed_disk.exists() or managed_disk.is_symlink():
                    if managed_instance.exists() or managed_instance.is_symlink():
                        raise RuntimeError(
                            f'Orphaned disk still exists, but Lima instance '
                            f'{managed_instance} is also present; refusing deletion'
                        )
                    if managed_disk.is_symlink() or not managed_disk.is_dir():
                        raise RuntimeError(
                            f'Unexpected managed disk path type: {managed_disk}; '
                            'refusing deletion'
                        )
                    if managed_disk.parent.resolve() != (Path(environment['LIMA_HOME']) / '_disks').resolve():
                        raise RuntimeError('Managed disk escaped its external storage root')
                    print(f'[Ubuntu backend] Removing residual disposable disk directory '
                          f'{managed_disk}', flush=True)
                    shutil.rmtree(managed_disk)
                    if managed_disk.exists():
                        raise RuntimeError(f'Managed disk still exists: {managed_disk}')
    else:
        print(f'[Ubuntu backend] Reusing {profile}; VM storage at {environment["COLIMA_HOME"]}', flush=True)

    socket = Path(environment['COLIMA_HOME']) / profile / 'docker.sock'
    if not socket.is_socket():
        raise RuntimeError(f'Managed Colima Docker socket not found: {socket}')
    subprocess.run([docker, 'info'], env=environment, check=True, stdout=subprocess.DEVNULL)
    return docker, environment


def ensure_buildx(docker: str, environment: dict[str, str]) -> None:
    """Expose the existing Docker buildx plugin inside the managed CFOS config.

    Docker searches DOCKER_CONFIG/cli-plugins, not ~/.docker/cli-plugins when
    DOCKER_CONFIG is overridden. Link the installed plugin, never copy it or
    mutate the user's global Docker configuration.
    """
    check = subprocess.run(
        [docker, 'buildx', 'version'],
        env=environment, capture_output=True, text=True, check=False,
    )
    if check.returncode == 0:
        print(f'[Ubuntu backend] {check.stdout.strip()}', flush=True)
        return
    if platform.system() == 'Linux':
        raise RuntimeError('Docker Buildx missing on Linux; run CFOS Linux Setup and install the Docker Buildx plugin')

    candidates = [
        Path.home() / '.docker/cli-plugins/docker-buildx',
        Path('/opt/homebrew/lib/docker/cli-plugins/docker-buildx'),
        Path('/usr/local/lib/docker/cli-plugins/docker-buildx'),
    ]
    brew = host_executable('brew')
    if brew:
        prefix = subprocess.run(
            [brew, '--prefix', 'docker-buildx'],
            capture_output=True, text=True, check=False,
        )
        if prefix.returncode == 0 and prefix.stdout.strip():
            candidates.insert(0, Path(prefix.stdout.strip()) / 'lib/docker/cli-plugins/docker-buildx')

    plugin = next(
        (candidate.resolve() for candidate in candidates
         if candidate.is_file() and os.access(candidate, os.X_OK)),
        None,
    )
    if plugin is None:
        # Build can be invoked without a preceding Setup (including on a
        # separate build host). Provision the missing host plugin using the
        # same Homebrew contract as Setup, then register it in the managed
        # Docker configuration. Never create a plugin under the Cinter tree.
        if not brew:
            raise RuntimeError(
                'Docker buildx is unavailable and Homebrew was not found. '
                'Run ./CFOS setup cinterforge-os linux Release on this build host.'
            )
        print('[Ubuntu backend] Provisioning missing docker-buildx via Homebrew', flush=True)
        subprocess.run([brew, 'install', 'docker-buildx'], check=True)
        prefix = subprocess.run(
            [brew, '--prefix', 'docker-buildx'], capture_output=True, text=True, check=True,
        ).stdout.strip()
        if not prefix:
            raise RuntimeError('Homebrew installed docker-buildx but returned no formula prefix')
        plugin_path = Path(prefix) / 'lib/docker/cli-plugins/docker-buildx'
        if not plugin_path.is_file() or not os.access(plugin_path, os.X_OK):
            raise RuntimeError(f'Homebrew docker-buildx executable missing: {plugin_path}')
        plugin = plugin_path.resolve()

    plugin_directory = Path(environment['DOCKER_CONFIG']) / 'cli-plugins'
    plugin_directory.mkdir(parents=True, exist_ok=True)
    link = plugin_directory / 'docker-buildx'
    if link.is_symlink():
        if link.resolve() != plugin:
            raise RuntimeError(f'Conflicting managed Docker buildx plugin: {link}')
    elif link.exists():
        raise RuntimeError(f'Refusing to replace managed Docker buildx file: {link}')
    else:
        link.symlink_to(plugin)
        print(f'[Ubuntu backend] Registered buildx: {link} -> {plugin}', flush=True)

    verify = subprocess.run(
        [docker, 'buildx', 'version'],
        env=environment, capture_output=True, text=True, check=False,
    )
    if verify.returncode:
        raise RuntimeError(
            'Docker buildx remains unavailable after Homebrew provisioning in managed CFOS: '
            + (verify.stderr or verify.stdout).strip()
        )
    print(f'[Ubuntu backend] {verify.stdout.strip()}', flush=True)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--shell-env':
        print_shell_environment(Path(sys.argv[2]).resolve())
    else:
        raise SystemExit('Usage: managed_backend.py --shell-env <CinterForgeOS repo>')
