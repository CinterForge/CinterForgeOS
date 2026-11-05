#!/usr/bin/env python3
"""Installed macOS adapter for the shipping CFOS Ubuntu AMD64 rootfs.

This module is bundled in CinterForge Desktop AND CinterForge OS. It is also
installed beside Ubuntu in the Steam application hierarchy. It never requires
an authored CinterForgeOS/CinterForge checkout to run.
"""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
sys.dont_write_bytecode = True  # Do not mutate signed Contents/Resources on import.
import tarfile
import hashlib
import json
import re
import time
import urllib.request
import uuid

ARCHIVE = 'Ubuntu-24.04-amd64-rootfs.tar'

# `docker export` intentionally ships a portable rootfs, not Docker config.
# Rehydrate the immutable toolchain environment on *every* import: otherwise
# rustup, xwin and the app SDKs disappear from PATH after installation.
# This definition is the SINGLE source for CFOS Test and installed Mac launch.
IMAGE_IMPORT_CHANGES = (
    '--change', 'ENV RUSTUP_HOME=/opt/rustup',
    '--change', 'ENV CARGO_HOME=/opt/cargo',
    '--change', 'ENV CINTER_STEAMAPPS=/run/steamapps',
    '--change', 'ENV CINTER_APPLICATIONS_ROOT=/run/steamapps/common/CinterForge/Applications',
    '--change', 'ENV XDG_DATA_DIRS=/run/steamapps/common/CinterForge/Applications/DesktopEntries/share:/usr/local/share:/usr/share',
    '--change', 'ENV PATH=/opt/cargo/bin:/opt/xwin/bin:/opt/node/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
    '--change', 'WORKDIR /opt/cinterforge',
    '--change', 'CMD ["/bin/bash"]',
)



STEAMAPPS_MOUNT = '/run/steamapps'
VOLUME_SCHEMA = 'cinterforge.cfos.ubuntu-steamapps.v1'


def steamapps_volume(archive: Path, *, create: bool = False) -> Path:
    """Ubuntu-owned persistent application volume, NOT the SteamOS library.

    Its on-disk location is beside the replaceable Distro directory. Both the
    installed macOS adapter and CFOS Stage use this one resolver. The mount
    path inside Ubuntu is the same as the SteamOS virtiofs mount.
    """
    if archive.name != ARCHIVE or archive.parent.name != 'Distro':
        raise RuntimeError(f'Expected an installed Ubuntu Distro archive: {archive}')
    volume = archive.parent.parent / 'steamapps'
    manifest = volume / 'CFOS-Volume.json'
    if volume.is_symlink() or (volume / 'CFOS-Volume.json').is_symlink():
        raise RuntimeError(f'Ubuntu steamapps must not contain a volume/manifest symlink: {volume}')
    for part in ('common', 'common/CinterForge', 'common/CinterForge/Applications'):
        if (volume / part).is_symlink():
            raise RuntimeError(f'Ubuntu steamapps contains a layout symlink: {volume / part}')
    if create:
        volume.mkdir(parents=True, exist_ok=True)
        if not manifest.exists():
            manifest.write_text(json.dumps({
                'schema': VOLUME_SCHEMA,
                'mount': STEAMAPPS_MOUNT,
                'owner': 'CinterForgeOS-Ubuntu',
            }, indent=2, sort_keys=True) + '\n')
        applications = volume / 'common/CinterForge/Applications'
        for name in ('Linux', 'Windows', 'macOS', 'Test', 'Logs', 'DesktopEntries/share/applications'):
            target = applications / name
            if target.is_symlink():
                raise RuntimeError(f'Ubuntu steamapps layout contains a symlink: {target}')
            target.mkdir(parents=True, exist_ok=True)
            if name == 'Test':
                # Like /tmp, only the test scratch folder is sticky and
                # guest-writable across host/guest UID differences. Installed
                # application directories are never made world-writable.
                target.chmod(0o1777)
    if not volume.is_dir() or not manifest.is_file():
        raise RuntimeError(f'Ubuntu steamapps volume missing; stage CFOS Ubuntu first: {volume}')
    try:
        metadata = json.loads(manifest.read_text())
    except (OSError, ValueError) as error:
        raise RuntimeError(f'Invalid Ubuntu steamapps volume manifest: {manifest}') from error
    if metadata != {'schema': VOLUME_SCHEMA, 'mount': STEAMAPPS_MOUNT,
                    'owner': 'CinterForgeOS-Ubuntu'}:
        raise RuntimeError(f'Unexpected Ubuntu steamapps volume identity: {manifest}')
    return volume.resolve()


def steamapps_mount_args(archive: Path) -> list[str]:
    """Docker mount arguments shared by headless, shell and KDE sessions."""
    volume = steamapps_volume(archive)
    return ['--mount', f'type=bind,source={volume},target={STEAMAPPS_MOUNT}']


def find_archive(adapter: Path, override: str | None = None) -> Path:
    """Resolve the installed product, not a stale development Docker image.

    The version directory is part of the installed Applications hierarchy.
    A portable .app can instead be given --archive or CFOS_STEAMAPPS. Never
    guess a build-tree path from HOME or a fixed developer checkout location.
    """
    specified = override or os.environ.get('CINTER_UBUNTU_ARCHIVE')
    if specified:
        path = Path(specified).expanduser().resolve()
        if not path.is_file():
            raise RuntimeError(f'Explicit installed Ubuntu rootfs missing: {path}')
        return path
    versions = ('2026', '2026-Debug')
    # Prefer the version of the installed application where the adapter lives.
    for parent in adapter.parents:
        if parent.name in versions:
            versions = (parent.name,) + tuple(v for v in versions if v != parent.name)
            break
    roots: list[Path] = []
    for parent in adapter.parents:
        if parent.name == 'Applications':
            roots.append(parent)
            break
    for variable in ('CFOS_STEAMAPPS', 'CINTER_STEAMAPPS'):
        if os.environ.get(variable):
            roots.append(Path(os.environ[variable]).expanduser() / 'common/CinterForge/Applications')
    roots.append(Path.home() / 'Library/Application Support/Steam/steamapps/common/CinterForge/Applications')
    for root in roots:
        for version in versions:
            path = root / 'Linux/CinterForgeOS' / version / 'Ubuntu/Distro' / ARCHIVE
            if path.is_file():
                return path.resolve()
    raise RuntimeError(
        'Installed CFOS Ubuntu rootfs not found. Stage CFOS Linux first or '
        'supply --archive /absolute/path/to/' + ARCHIVE + ' (or CFOS_STEAMAPPS).'
    )


def backend(archive: Path) -> tuple[str, dict[str, str]]:
    """Use an explicitly configured CFOS backend or an existing Docker daemon.

    In production the installed app must work without CINTER_UBUNTU_SOURCE.
    When Builds is known, reuse the same managed Colima profile as CFOS Setup.
    Otherwise use an already running Docker daemon without modifying its
    context, home directory, or any user-global state.
    """
    source = os.environ.get('CINTER_UBUNTU_SOURCE')
    builds = os.environ.get('CINTER_BUILDS_ROOT') or os.environ.get('CFOS_BUILDS_ROOT')
    if not source and not builds:
        hint = archive.parent / 'CFOS-backend.json'
        if hint.is_file():
            try:
                metadata = json.loads(hint.read_text(encoding='utf-8'))
                if metadata.get('schema') == 'cinterforge.cfos.host-backend.v1':
                    candidate = Path(metadata['builds_root']).expanduser().resolve()
                    if candidate.name == 'Builds' and (candidate / 'CreativeSuite/CFOS').is_dir():
                        builds = str(candidate)
            except (OSError, TypeError, KeyError, ValueError):
                pass  # Installed on a different Mac: try an existing Docker daemon.
    if not source and builds:
        source = str(Path(builds).expanduser().resolve() /
                     'CreativeSuite/Built/cinterforge-os/linux/Source')
    if source:
        from managed_backend import ensure_backend
        return ensure_backend(Path(source).expanduser().resolve())
    from managed_backend import host_executable
    docker = host_executable('docker')
    if not docker:
        raise RuntimeError(
            'Docker not found; install a supported Docker backend or configure '
            'CINTER_BUILDS_ROOT to use the CFOS-managed Colima instance'
        )
    environment = os.environ.copy()
    try:
        subprocess.run([docker, 'info'], env=environment, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as error:
        raise RuntimeError(
            'No running Docker daemon. Configure CINTER_BUILDS_ROOT for the '
            'managed CFOS Colima backend or start an existing Docker runtime.'
        ) from error
    return docker, environment


def load_installed_image(docker: str, environment: dict[str, str], archive: Path) -> str:
    """Reuse only the image imported from this exact installed rootfs digest."""
    checksum = hashlib.sha256()
    with archive.open('rb') as contents:
        for block in iter(lambda: contents.read(1024 * 1024), b''):
            checksum.update(block)
    # Include reconstructed OCI configuration in the tag as well as rootfs
    # content; a prior import without PATH/RUSTUP_HOME must never be reused.
    configuration = hashlib.sha256(repr(IMAGE_IMPORT_CHANGES).encode()).hexdigest()[:8]
    tag = f'cinterforge-ubuntu-installed:{checksum.hexdigest()[:20]}-{configuration}'
    probe = subprocess.run(
        [docker, 'image', 'inspect', '--format', '{{.Id}} {{.Os}}/{{.Architecture}}', tag],
        env=environment, text=True, capture_output=True, check=False)
    if probe.returncode != 0:
        print(f'[Ubuntu Adapter] Importing verified installed distribution: {archive}', flush=True)
        with archive.open('rb') as stream:
            result = subprocess.run(
                [docker, 'image', 'import', '--platform', 'linux/amd64', *IMAGE_IMPORT_CHANGES, '-', tag],
                env=environment, stdin=stream, text=True, capture_output=True, check=True)
        if not result.stdout.strip().startswith('sha256:'):
            raise RuntimeError(f'Unexpected Docker import result: {result.stdout!r}')
        probe = subprocess.run(
            [docker, 'image', 'inspect', '--format', '{{.Id}} {{.Os}}/{{.Architecture}}', tag],
            env=environment, text=True, capture_output=True, check=True)
    parts = probe.stdout.strip().split()
    if len(parts) != 2 or not parts[0].startswith('sha256:') or parts[1] != 'linux/amd64':
        raise RuntimeError(f'Installed image is not Linux AMD64: {probe.stdout.strip()!r}')
    return tag


def desktop(docker: str, environment: dict[str, str], image: str, archive: Path) -> None:
    """One KDE session, bound only to the Mac loopback, owned by this process.

    Closing the Swift window closes our stdin; the adapter stops *its own*
    container, never the shared Docker daemon, other Ubuntu sessions or SteamOS.
    """
    container = 'cfos-ubuntu-desktop-' + uuid.uuid4().hex[:12]
    args = [docker, 'run', '--rm', '-d', '--pull=never', '--platform', 'linux/amd64',
            '--name', container, '--publish', '127.0.0.1::6080',
            '--shm-size', '512m', '--tmpfs', '/tmp:rw,nosuid,size=768m']
    # Installed Linux applications live in Ubuntu's own persistent volume,
    # at the exact same guest path as the SteamOS host library.
    args += steamapps_mount_args(archive)
    args += [image, '/usr/local/bin/cfos-ubuntu-desktop', 'launch']
    started = False
    try:
        subprocess.run(args, env=environment, check=True, capture_output=True, text=True)
        started = True
        port_result = subprocess.run([docker, 'port', container, '6080/tcp'],
                                     env=environment, check=True, capture_output=True, text=True)
        matched = re.search(r'127\.0\.0\.1:(\d+)\b', port_result.stdout)
        if not matched:
            raise RuntimeError('Ubuntu desktop port was not bound to loopback: ' + port_result.stdout.strip())
        port = int(matched.group(1))
        if port < 1 or port > 65535:
            raise RuntimeError(f'Invalid Ubuntu desktop port: {port}')
        probe = f'http://127.0.0.1:{port}/vnc.html'
        local_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(90):
            state = subprocess.run([docker, 'inspect', '--format', '{{.State.Running}}', container],
                                   env=environment, text=True, capture_output=True)
            if state.returncode or state.stdout.strip() != 'true':
                logs = subprocess.run([docker, 'logs', '--tail', '100', container],
                                      env=environment, text=True, capture_output=True)
                raise RuntimeError('Ubuntu desktop exited before readiness: ' + (logs.stderr + logs.stdout)[-4000:])
            try:
                with local_opener.open(probe, timeout=2) as response:
                    if response.status == 200:
                        # The desktop script emits READY only after plasmashell
                        # and its VNC transport are both running.
                        logs = subprocess.run([docker, 'logs', '--tail', '25', container],
                                              env=environment, text=True, capture_output=True)
                        if '[CFOS Ubuntu desktop] READY' in (logs.stdout + logs.stderr):
                            break
            except (OSError, TimeoutError):
                pass
            time.sleep(1)
        else:
            logs = subprocess.run([docker, 'logs', '--tail', '100', container],
                                  env=environment, text=True, capture_output=True)
            raise RuntimeError('Ubuntu desktop not ready: ' + (logs.stdout + logs.stderr)[-4000:])
        # The Swift application consumes this *exact* signal and loads noVNC in
        # an embedded WKWebView. No XQuartz or separate RDP application required.
        print(f'CFOS_DESKTOP_URL=http://127.0.0.1:{port}/vnc.html?autoconnect=true&resize=remote', flush=True)
        # An EOF means the owner Swift process exited, including abnormal exits
        # where its pipe descriptors are closed by the OS.
        while sys.stdin.buffer.read(1024):
            pass
    finally:
        if started:
            subprocess.run([docker, 'rm', '-f', container], env=environment,
                           check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('test', 'shell', 'desktop'))
    parser.add_argument('--archive', help='Absolute path to an installed Ubuntu rootfs tar')
    args = parser.parse_args()
    if platform.system() != 'Darwin':
        raise RuntimeError('The bundled macOS Ubuntu adapter requires macOS')
    adapter = Path(__file__).resolve().parent
    archive = find_archive(adapter, args.archive)
    with tarfile.open(archive) as contents:
        names = {m.name.lstrip('./') for m in contents}
        if 'usr/bin/apt-get' not in names:
            raise RuntimeError(f'Installed Ubuntu distro archive invalid: {archive}')
        if args.operation == 'desktop' and 'usr/local/bin/cfos-ubuntu-desktop' not in names:
            raise RuntimeError(f'Installed Ubuntu lacks KDE launch support; rebuild and stage CFOS Linux: {archive}')
    docker, environment = backend(archive)
    image = load_installed_image(docker, environment, archive)
    base = [docker, 'run', '--rm', '--pull=never', '--platform', 'linux/amd64',
            *steamapps_mount_args(archive)]
    if args.operation == 'test':
        smoke = adapter / 'headless_contract.sh'
        if not smoke.is_file():
            raise RuntimeError(f'Installed Ubuntu headless contract missing: {smoke}')
        subprocess.run([*base, '--network', 'none', '--user', 'cinterforge', image, '/bin/sh', '-ec', smoke.read_text()],
                       env=environment, check=True)
        subprocess.run([*base, '--network', 'none', '--shm-size', '512m',
                        image, '/usr/local/bin/cfos-ubuntu-desktop', 'test'],
                       env=environment, check=True)
        print('[Ubuntu Adapter] PASS: installed Ubuntu AMD64 headless build/test and KDE desktop session', flush=True)
    elif args.operation == 'desktop':
        desktop(docker, environment, image, archive)
    else:
        subprocess.run([*base, '-it', '--user', 'cinterforge', image,
                        '/usr/local/bin/cfos-ubuntu-shell'], env=environment, check=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f'[Ubuntu Adapter] ERROR: {error}', file=sys.stderr)
        raise SystemExit(1)
