#!/usr/bin/env python3
"""CFOS LinuxKit acceptance: install and run real x86-64 Bedrock with Rosetta.

CFOS owns the LinuxKit VM and test. CinterForge owns official Bedrock package
acquisition and the established guest NetherNet/console acceptance assertions.
All mutable test files live under an isolated Builds/Intermediates directory;
no player's worlds, Steam installation or production server may be modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

from build_linux import verify_shared_bootstrap, linuxkit_bootstrap_root
from managed_backend import ensure_backend, source_directory

HERE = Path(__file__).resolve().parent
PROBE_SOURCE = HERE / 'probes/cinter_nethernet_probe.c'
GLIBC_IMAGE = 'ubuntu:24.04'  # Same base distribution as Dockerfile.ubuntu
PROBE_IMAGE = 'alpine:3.20'   # ARM64 native static acceptance probe


def _run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    print('[CFOS LinuxKit Test] ' + ' '.join(map(str, command)), flush=True)
    return subprocess.run(command, check=True, **kwargs)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _require_elf(path: Path, machine: int) -> None:
    with path.open('rb') as stream:
        header = stream.read(20)
    if len(header) < 20 or header[:4] != b'\x7fELF' or header[4] != 2 or header[5] != 1:
        raise RuntimeError(f'Not a 64-bit little-endian ELF executable: {path}')
    actual = struct.unpack_from('<H', header, 18)[0]
    if actual != machine:
        raise RuntimeError(f'Unexpected ELF architecture {actual} (expected {machine}): {path}')


def _prepare_runtime(server: Path, docker: str, docker_env: dict[str, str]) -> dict:
    """Stage the guest's own runtime closure, never a separate Debian builder."""
    if not PROBE_SOURCE.is_file():
        raise RuntimeError(f'CFOS NetherNet probe source missing: {PROBE_SOURCE}')
    _require_elf(server / 'bedrock_server', 62)  # x86-64
    cinter = server / '.cinter'
    cinter.mkdir(parents=True, exist_ok=False)
    probe = cinter / 'cinter-nethernet-probe'
    _run([docker, 'run', '--rm', '--platform', 'linux/arm64',
          '--mount', f'type=bind,src={PROBE_SOURCE.parent.resolve()},dst=/source,readonly',
          '--mount', f'type=bind,src={cinter.resolve()},dst=/out',
          PROBE_IMAGE, 'sh', '-ec',
          'apk add --no-cache build-base >/dev/null && '
          'cc -O2 -static -o /out/cinter-nethernet-probe /source/cinter_nethernet_probe.c && '
          'chmod 755 /out/cinter-nethernet-probe'], env=docker_env)
    _require_elf(probe, 183)  # AArch64 native guest probe

    runtime = cinter / 'glibc-x86_64'
    runtime.mkdir()
    # Docker's filesystem export retains compatible x86-64 glibc and its
    # loader. This is the Ubuntu base image, not retired Debian tooling.
    container = _run([docker, 'create', '--platform', 'linux/amd64',
                      GLIBC_IMAGE, '/bin/true'], capture_output=True, text=True, env=docker_env).stdout.strip()
    if not container:
        raise RuntimeError('Docker did not provide an Ubuntu glibc container ID')
    try:
        _run([docker, 'cp', '-L', f'{container}:/lib/x86_64-linux-gnu/.', str(runtime)], env=docker_env)
        loader = runtime / 'ld-linux-x86-64.so.2'
        if not loader.is_file():
            _run([docker, 'cp', '-L',
                  f'{container}:/lib64/ld-linux-x86-64.so.2', str(loader)], env=docker_env)
        _require_elf(loader, 62)
        loader.chmod(loader.stat().st_mode | 0o111)
    finally:
        _run([docker, 'rm', '-f', container], stdout=subprocess.DEVNULL, env=docker_env)

    inspect = _run([docker, 'image', 'inspect', GLIBC_IMAGE,
                    '--format', '{{json .RepoDigests}}'], capture_output=True, text=True, env=docker_env)
    manifest = {
        'glibc_image': GLIBC_IMAGE,
        'glibc_image_digests': json.loads(inspect.stdout.strip()),
        'arm64_probe_image': PROBE_IMAGE,
        'bedrock_sha256': _sha256(server / 'bedrock_server'),
        'glibc_loader_sha256': _sha256(loader),
        'probe_sha256': _sha256(probe),
    }
    (cinter / 'acceptance-inputs.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    print('[CFOS LinuxKit Test] Bedrock x86-64, native ARM64 probe and Ubuntu glibc ready', flush=True)
    return manifest


def test_linuxkit(repo: Path, *, offline: bool = False) -> None:
    repo = repo.resolve()
    if not (repo / 'CFOS').is_file():
        raise RuntimeError(f'Expected CinterForgeOS checkout: {repo}')
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise RuntimeError('CFOS LinuxKit Bedrock/Rosetta Test requires Apple Silicon macOS')
    server_repo = repo.parent / 'CinterForgeServer'
    if not (server_repo / 'cf/build_cinterforge/craftmaster').is_dir():
        raise RuntimeError(f'Missing sibling CinterForgeServer source for Bedrock Test: {server_repo}; run ./CFOS clone all')
    verify_shared_bootstrap(repo, require_launcher=True)
    root = repo.parent / 'Builds'
    if not root.is_dir():
        raise RuntimeError(f'Shared Builds directory missing: {root}')
    tmp_parent = root / 'Intermediates/CFOS/LinuxKit'
    tmp_parent.mkdir(parents=True, exist_ok=True)
    build = server_repo / 'cf/build_cinterforge'
    installer = build / 'craftmaster/build_craftmaster_server.py'
    validator = build / 'craftmaster/validate_minecraft_server.py'
    if not installer.is_file() or not validator.is_file():
        raise RuntimeError(f'CinterForge Bedrock installer/validator missing under {build}')
    # LinuxKit Test shares the SAME CFOS-owned Docker/Colima instance as Ubuntu
    # Build. A user's shell may still point DOCKER_HOST at a retired Colima
    # profile, so never invoke bare `docker` with inherited socket settings.
    # Check/start the managed backend BEFORE downloading Bedrock or creating
    # disposable test installations. This does not change the host's context.
    # Keep a durable Test receipt outside the disposable test installation.
    receipt = root / 'Logs/CFOS/linuxkit-bedrock-acceptance.json'
    receipt.parent.mkdir(parents=True, exist_ok=True)
    bootstrap = linuxkit_bootstrap_root(repo)
    evidence = {'schema': 'cinterforge.cfos.linuxkit-bedrock-test.v1',
                'bootstrap_sha256': {name: _sha256(bootstrap / name) for name in
                   ('cinter-bds-kernel', 'cinter-bds-initrd.img', 'cinter-bds-cmdline', 'CinterBDSVM')},
                'status': 'failed', 'checked_at': datetime.now(timezone.utc).isoformat(),
                'test': 'real-official-bedrock-rosetta-nethernet-console-stop'}
    try:
        docker, docker_env = ensure_backend(source_directory(repo))
        _execute_acceptance(repo, server_repo, installer, validator, tmp_parent, evidence, offline,
                            docker, docker_env)
    except Exception as exc:
        evidence['error'] = str(exc)
        receipt.write_text(json.dumps(evidence, indent=2, sort_keys=True) + '\n')
        raise
    evidence['status'] = 'passed'
    receipt.write_text(json.dumps(evidence, indent=2, sort_keys=True) + '\n')
    print(f'[CFOS LinuxKit Test] Evidence: {receipt}', flush=True)
    print('[CFOS LinuxKit Test] PASS: official Bedrock installed and executed via ARM64 LinuxKit/Rosetta; NetherNet, list, stop, exit verified', flush=True)


def _execute_acceptance(repo: Path, server_repo: Path, installer: Path, validator: Path,
                        tmp_parent: Path, evidence: dict, offline: bool,
                        docker: str, docker_env: dict[str, str]) -> None:
    with tempfile.TemporaryDirectory(prefix='Acceptance-', dir=tmp_parent) as temp:
        server = Path(temp) / 'Server'
        command = [sys.executable, '-u', str(installer), '--repo-root', str(server_repo),
                   '--linuxkit-test-install', str(server)]
        if offline:
            command.append('--offline')
        _run(command)
        evidence['runtime'] = _prepare_runtime(server, docker, docker_env)
        _run([sys.executable, '-u', str(validator), '--repo-root', str(server_repo),
              '--cfos-linuxkit-test-server', str(server), '--cfos-root', str(repo)])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', type=Path, required=True)
    parser.add_argument('--offline', action='store_true',
                        help='Use previously downloaded official Bedrock ZIP')
    args = parser.parse_args()
    try:
        test_linuxkit(args.repo_root, offline=args.offline)
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f'[CFOS LinuxKit Test] FAIL: {exc}', file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
