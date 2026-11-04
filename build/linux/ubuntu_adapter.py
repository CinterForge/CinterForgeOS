#!/usr/bin/env python3
"""macOS host adapter for executing installed Ubuntu AMD64 userspace (OCI)."""
import argparse
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile

ARCHIVE = 'Ubuntu-24.04-amd64-rootfs.tar'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('test', 'shell'))
    operation = parser.parse_args().operation
    if platform.system() != 'Darwin':
        raise RuntimeError('macOS Ubuntu adapter requires macOS')
    adapter = Path(__file__).resolve().parent
    # <common>/Applications/macOS/CinterForgeOS/2026/Ubuntu
    root = adapter.parents[4]
    version = adapter.parent.name
    archive = root / 'Applications/Linux/CinterForgeOS' / version / 'Ubuntu/Distro' / ARCHIVE
    if not archive.is_file():
        raise RuntimeError(f'Installed Ubuntu distro missing: {archive}')
    with tarfile.open(archive) as tf:
        if not any(m.name.lstrip('./') == 'usr/bin/apt-get' for m in tf):
            raise RuntimeError(f'Installed distro archive invalid: {archive}')
    source = os.environ.get('CINTER_UBUNTU_SOURCE')
    if not source:
        raise RuntimeError(
            'Set CINTER_UBUNTU_SOURCE to the visible '
            'Builds/CreativeSuite/Built/cinterforge-os/linux/Source directory'
        )
    from managed_backend import ensure_backend
    docker, env = ensure_backend(Path(source).resolve())
    print(f'[Ubuntu Adapter] Loading installed Ubuntu distribution: {archive}', flush=True)
    with archive.open('rb') as stream:
        imported = subprocess.run(
            [docker, 'image', 'import', '--platform', 'linux/amd64', '-'],
            env=env,
            stdin=stream,
            capture_output=True,
            text=True,
            check=True,
        )

    # A rootfs tar has no OCI architecture metadata. Explicitly specify AMD64
    # on import or Docker defaults to the ARM64 architecture of Colima's daemon.
    # Import the staged rootfs WITHOUT assigning a tag. Docker import returns
    # the immutable image ID, which is the only identifier Test may execute.
    # No registry lookup, stale tag or build image can satisfy this test.
    image_id = imported.stdout.strip()
    if not image_id.startswith('sha256:'):
        raise RuntimeError(
            f'Docker did not return an imported image ID: {image_id!r}; '
            f'stderr={imported.stderr.strip()!r}'
        )
    inspected = subprocess.check_output(
        [docker, 'image', 'inspect', '--format', '{{.Id}} {{.Os}}/{{.Architecture}}', image_id],
        env=env,
        text=True,
    ).strip()
    if inspected != f'{image_id} linux/amd64':
        raise RuntimeError(
            f'Imported Ubuntu image mismatch: expected {image_id} linux/amd64, got {inspected!r}'
        )
    print(f'[Ubuntu Adapter] Imported installed image: {image_id}', flush=True)
    base = [docker, 'run', '--rm', '--pull=never', '--platform', 'linux/amd64']
    if operation == 'test':
        verify = ('set -eu; test "$(dpkg --print-architecture)" = amd64; '
                  'grep -q "Ubuntu 24.04" /etc/os-release; '
                  'command -v python3; command -v Xvfb; command -v openbox')
        subprocess.run([*base, image_id, '/bin/sh', '-ec', verify], env=env, check=True)
        print('[Ubuntu Adapter] PASS: installed Ubuntu AMD64 rootfs executes on macOS OCI backend', flush=True)
        print('[Ubuntu Adapter] VM graphical desktop boot is not implemented', flush=True)
    else:
        subprocess.run([*base, '-it', image_id, '/bin/bash'], env=env, check=True)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f'[Ubuntu Adapter] ERROR: {exc}', file=sys.stderr)
        raise SystemExit(1)
