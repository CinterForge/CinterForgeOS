#!/usr/bin/env python3
"""Install CinterForge Server and WebApp into the *one* CFOS Ubuntu steamapps volume.

CinterForge owns application compilation (``./Cinter build web``); CFOS owns
Ubuntu image and product assembly. The WebApp's minimal HTTP handler remains
separate from the full Python Server. Every release artifact is checked against
its Build receipt before Stage, and the installed product is tested *inside*
the staged Ubuntu image, without exposing port 20380 on the macOS host.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

SCHEMA = 'cinterforge.cfos.ubuntu-products.v1'
VERSION = '2026'
WEB = 'WebApps/CinterForgeWebApp'
SERVER = 'Server/CinterForgeServer'
GUEST_APPS = '/run/steamapps/common/CinterForge/Applications'


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def inventory(directory: Path) -> dict[str, str]:
    """Deterministic output inventory; reject symlinks and source escape."""
    if not directory.is_dir() or directory.is_symlink():
        raise RuntimeError(f'Missing product directory: {directory}')
    result: dict[str, str] = {}
    for item in sorted(directory.rglob('*')):
        if item.is_symlink():
            raise RuntimeError(f'Product cannot contain external symlink: {item}')
        if item.is_file():
            result[item.relative_to(directory).as_posix()] = hash_file(item)
    if not result:
        raise RuntimeError(f'Empty product: {directory}')
    return result


def repos(cfos: Path) -> dict[str, Path]:
    root = cfos.resolve().parent
    entries = {name: root / name for name in (
        'CinterForgeOS', 'CinterForgeDesktop', 'CinterForgeServer',
        'CinterForgeWebApp', 'CinterForgePlugins')}
    for name, path in entries.items():
        if not path.is_dir() or path.is_symlink():
            raise RuntimeError(f'CFOS Ubuntu requires sibling source checkout: {name}: {path}')
    if not (entries['CinterForgeDesktop'] / 'Cinter').is_file():
        raise RuntimeError('CinterForgeDesktop does not contain the Cinter entry point')
    if not (entries['CinterForgeWebApp'] / 'runtime/package.py').is_file():
        raise RuntimeError('Missing CinterForgeWebApp runtime packager')
    if not (entries['CinterForgeServer'] / 'cinter/io/CinterIO.py').is_file():
        raise RuntimeError('Missing CinterForgeServer backend')
    return entries


def builds_root(cfos: Path) -> Path:
    return cfos.parent / 'Builds'


def product_root(cfos: Path) -> Path:
    return builds_root(cfos) / 'CreativeSuite/Built/cinterforge-os/linux/2026/Products'


def _manifest_path(cfos: Path) -> Path:
    return product_root(cfos) / 'manifest.json'


def expected_artifacts(cfos: Path) -> dict[str, Path]:
    b = builds_root(cfos)
    return {
        'server': product_root(cfos) / 'Server',
        'webapp': b / 'websites/dev',
        'runtime': b / 'Server',
    }


def verify_build(cfos: Path) -> dict:
    file = _manifest_path(cfos)
    if not file.is_file():
        raise RuntimeError(f'CFOS Ubuntu Server/WebApp Build missing: {file}')
    metadata = json.loads(file.read_text(encoding='utf-8'))
    if metadata.get('schema') != SCHEMA:
        raise RuntimeError(f'Invalid CFOS Ubuntu products receipt: {file}')
    for name, folder in expected_artifacts(cfos).items():
        if metadata.get('products', {}).get(name) != inventory(folder):
            raise RuntimeError(f'CFOS Ubuntu {name} changed since Build: {folder}')
    if not (expected_artifacts(cfos)['webapp'] / 'index.html').is_file():
        raise RuntimeError('Hugo WebApp did not produce index.html')
    return metadata


def _exclude_server_files(directory: str, names: list[str]) -> set[str]:
    """Keep credentials, private settings and generated state out of the OS."""
    excluded = {'.git', '.DS_Store', '__pycache__', '.pytest_cache',
                'credentials.json', 'secrets.json', '.env', '.env.local'}
    return {name for name in names if name in excluded or
            name.endswith(('.pyc', '.pyo', '.pem', '.key')) or
            name.startswith('.env.')}


def build(cfos: Path, docker: str, environment: dict[str, str], image: str) -> None:
    """Run the REAL Cinter WebApp Build inside the prepared Ubuntu AMD64 image."""
    directories = repos(cfos)
    b = builds_root(cfos)
    b.mkdir(parents=True, exist_ok=True)
    # CFOS's Session assets are a platform product; Cinter Build consumes them.
    subprocess.run([sys.executable, '-u', str(cfos / 'scripts/session_console.py'),
                    'build', '--repo-root', str(cfos)], check=True)
    command = [docker, 'run', '--rm', '--platform', 'linux/amd64', '--pull=never',
               '--user', 'root', '--workdir', '/workspace/CinterForgeDesktop']
    # Read-only source for everything except WebApp's generated integration
    # copies. Cinter's already-owned Build output is the shared Builds volume.
    for name, path in directories.items():
        mode = 'rw' if name == 'CinterForgeWebApp' else 'ro'
        command += ['--mount', f'type=bind,source={path},target=/workspace/{name},{mode}']
    command += ['--mount', f'type=bind,source={b},target=/workspace/Builds,rw',
                image, 'bash', '-e', '/workspace/CinterForgeDesktop/Cinter', 'build', 'web']
    print('[CFOS Ubuntu Products] Building WebApp through Cinter inside Ubuntu AMD64', flush=True)
    subprocess.run(command, env=environment, check=True)
    source = directories['CinterForgeServer']
    # Reject links before copying: copytree(symlinks=False) would otherwise
    # follow an external secret or a sibling checkout outside the repo.
    for file in source.rglob('*'):
        if file.is_symlink():
            raise RuntimeError(f'Cannot package Server source symlink: {file}')
    destination = expected_artifacts(cfos)['server']
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Only authored server files. No copied venv, git metadata, or temporary
    # bytecode from the host may enter an AMD64 product.
    with tempfile.TemporaryDirectory(prefix='cfos-server-', dir=destination.parent) as temp:
        candidate = Path(temp) / 'Server'
        shutil.copytree(source, candidate, symlinks=False,
                        ignore=_exclude_server_files)
        inventory(candidate)
        backup = destination.with_name('Server.previous-build')
        if backup.exists():
            raise RuntimeError(f'Unreconciled previous Server Build: {backup}')
        if destination.exists():
            destination.rename(backup)
        try:
            candidate.rename(destination)
        except Exception:
            if backup.exists() and not destination.exists():
                backup.rename(destination)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    receipt = {'schema': SCHEMA,
               'products': {name: inventory(path) for name, path in expected_artifacts(cfos).items()}}
    target = _manifest_path(cfos)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix('.json.partial')
    partial.write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    partial.replace(target)
    verify_build(cfos)
    print(f'[CFOS Ubuntu Products] BUILT Server + WebApp: {target}', flush=True)


def _install_tree(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    backup = destination.with_name(destination.name + '.previous-stage')
    if backup.exists():
        raise RuntimeError(f'Incomplete previous Ubuntu product installation: {backup}')
    with tempfile.TemporaryDirectory(prefix='cfos-install-', dir=destination.parent) as temp:
        candidate = Path(temp) / destination.name
        shutil.copytree(source, candidate)
        if inventory(candidate) != inventory(source):
            raise RuntimeError(f'Stage changed product bytes: {destination}')
        if destination.exists():
            destination.rename(backup)
        try:
            candidate.rename(destination)
        except Exception:
            if backup.exists() and not destination.exists():
                backup.rename(destination)
            raise
        if backup.exists():
            shutil.rmtree(backup)


def install(cfos: Path, archive: Path) -> None:
    from ubuntu_adapter import steamapps_volume
    receipt = verify_build(cfos)
    volume = steamapps_volume(archive, create=True)
    apps = volume / 'common/CinterForge/Applications'
    server = apps / SERVER / VERSION
    webapp = apps / WEB / VERSION
    _install_tree(expected_artifacts(cfos)['server'], server / 'Source')
    # Preserve the existing Builds layout inside the installed WebApp:
    # CinterRuntime.py --builds <product> then serves <product>/websites/dev.
    with tempfile.TemporaryDirectory(prefix='cfos-webapp-', dir=webapp.parent if webapp.parent.exists() else apps) as temp:
        assembled = Path(temp) / 'WebApp'
        shutil.copytree(expected_artifacts(cfos)['webapp'], assembled / 'websites/dev')
        shutil.copytree(expected_artifacts(cfos)['runtime'], assembled / 'Server')
        _install_tree(assembled, webapp)
    # One stable guest-visible desktop entry, shared with SteamOS conventions.
    # Ubuntu's graphical session starts the installed API on 20380.
    desktop_entry = apps / 'DesktopEntries/share/applications/cinterforge-webapp.desktop'
    desktop_entry.parent.mkdir(parents=True, exist_ok=True)
    desktop_entry.write_text(
        '[Desktop Entry]\nType=Application\nName=CinterForge WebApp\n'
        'Comment=CinterForge Collection, Forge, Cinter and IFPlayer\n'
        'Exec=xdg-open http://127.0.0.1:20380/\n'
        'Icon=applications-internet\nCategories=Network;Development;\n'
        'Terminal=false\n', encoding='utf-8')
    marker = {
        'schema': SCHEMA,
        'built_products': receipt['products'],
        'server': f'{SERVER}/{VERSION}',
        'webapp': f'{WEB}/{VERSION}',
        'port': 20380,
    }
    location = apps / 'CFOS-Ubuntu-Products.json'
    temporary = location.with_suffix('.json.partial')
    temporary.write_text(json.dumps(marker, indent=2, sort_keys=True) + '\n')
    temporary.replace(location)
    verify_installed(cfos, archive)
    print(f'[CFOS Ubuntu Products] INSTALLED Server + WebApp in {apps}', flush=True)


def verify_installed(cfos: Path, archive: Path) -> None:
    from ubuntu_adapter import steamapps_volume
    receipt = verify_build(cfos)
    apps = steamapps_volume(archive) / 'common/CinterForge/Applications'
    marker = json.loads((apps / 'CFOS-Ubuntu-Products.json').read_text())
    if marker.get('schema') != SCHEMA or marker.get('built_products') != receipt['products']:
        raise RuntimeError('Installed Ubuntu products do not match built product manifest')
    if inventory(apps / SERVER / VERSION / 'Source') != receipt['products']['server']:
        raise RuntimeError('Installed full Server differs from Build')
    webapp = apps / WEB / VERSION
    if inventory(webapp / 'websites/dev') != receipt['products']['webapp']:
        raise RuntimeError('Installed WebApp differs from Build')
    if inventory(webapp / 'Server') != receipt['products']['runtime']:
        raise RuntimeError('Installed WebApp minimal runtime differs from Build')
    desktop_entry = apps / 'DesktopEntries/share/applications/cinterforge-webapp.desktop'
    if not desktop_entry.is_file() or 'http://127.0.0.1:20380/' not in desktop_entry.read_text():
        raise RuntimeError('Ubuntu WebApp KDE launcher missing or invalid')



def provision_python(archive: Path, docker: str, environment: dict[str, str], image: str) -> None:
    """Create the Python environment INSIDE Ubuntu at its final installed path.

    No host-created macOS venv or system pip installation enters the distro.
    The complete optional Server dependency closure is a separate release gate;
    do not silently pretend the full requirements.txt has been provisioned.
    """
    from ubuntu_adapter import steamapps_mount_args
    python_dir = f'{GUEST_APPS}/{SERVER}/{VERSION}/Python'
    command = [docker, 'run', '--rm', '--pull=never', '--platform', 'linux/amd64',
               '--network', 'none', '--user', 'root', *steamapps_mount_args(archive),
               image, 'sh', '-ec',
               f'python3 -m venv --system-site-packages {python_dir} && '
               f"{python_dir}/bin/python -c 'import sys; assert sys.platform == \"linux\"'"]
    subprocess.run(command, env=environment, check=True)
    print(f'[CFOS Ubuntu Products] PYTHON AMD64 {python_dir}', flush=True)


def test(cfos: Path, archive: Path, docker: str, environment: dict[str, str], image: str) -> None:
    """Verify full Server import and port 20380 locally INSIDE the real Ubuntu."""
    from ubuntu_adapter import steamapps_mount_args
    verify_installed(cfos, archive)
    script = r'''import importlib, os, pathlib, subprocess, sys, time, urllib.request
apps = pathlib.Path('/run/steamapps/common/CinterForge/Applications')
server = apps / 'Server/CinterForgeServer/2026'
webapp = apps / 'WebApps/CinterForgeWebApp/2026'
sys.path.insert(0, str(server / 'Source'))
venv = server / 'Python/bin/python'
assert venv.is_file() and venv.resolve().is_file()
importlib.import_module('cinter.io.CinterIO')
runtime = webapp / 'Server/CinterRuntime.py'
assert runtime.is_file() and (webapp / 'websites/dev/index.html').is_file()
p = subprocess.Popen([str(venv), '-u', str(runtime), '--builds', str(webapp),
                      '--host', '127.0.0.1', '--port', '20380'],
                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
try:
    for attempt in range(50):
        if p.poll() is not None:
            raise RuntimeError('CinterRuntime exited: ' + p.stderr.read().decode(errors='replace')[-2000:])
        try:
            with urllib.request.urlopen('http://127.0.0.1:20380/api/health', timeout=1) as response:
                assert response.status == 200
                break
        except OSError:
            time.sleep(.1)
    else:
        raise RuntimeError('CinterRuntime did not bind port 20380 inside Ubuntu')
    with urllib.request.urlopen('http://127.0.0.1:20380/', timeout=5) as response:
        assert response.status == 200
        assert b'<html' in response.read(8192).lower()
    print('[CFOS Ubuntu Products] PASS: full Server import, WebApp and HTTP 20380 in Ubuntu')
finally:
    p.terminate()
    try:
        p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        p.kill(); p.wait(timeout=5)
'''
    command = [docker, 'run', '--rm', '--pull=never', '--platform', 'linux/amd64',
               '--network', 'none', '--user', 'cinterforge', *steamapps_mount_args(archive),
               image, 'python3', '-c', script]
    subprocess.run(command, env=environment, check=True)


if __name__ == '__main__':
    print('Use ./CFOS build/stage/test cinterforge-os linux Release; this is an internal product assembler.')
    raise SystemExit(2)
