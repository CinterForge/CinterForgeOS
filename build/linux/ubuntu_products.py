#!/usr/bin/env python3
"""Install CinterForge Server and WebApp into the *one* CFOS Ubuntu steamapps volume.

The Website owns WebApp compilation (``./Cinter build web``); CFOS owns
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
import tarfile
import zipfile

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
        'CinterForgeOS', 'CinterForgeServer',
        'CinterForgeWebApp', 'cinterforge_website', 'CinterForgePlugins')}
    for name, path in entries.items():
        if not path.is_dir() or path.is_symlink():
            raise RuntimeError(f'CFOS Ubuntu requires sibling source checkout: {name}: {path}')
    if not (entries['cinterforge_website'] / 'Cinter').is_file():
        raise RuntimeError('cinterforge_website does not contain its Website-owned Cinter lifecycle')
    if not (entries['CinterForgeWebApp'] / 'runtime/package.py').is_file():
        raise RuntimeError('Missing CinterForgeWebApp runtime packager')
    if not (entries['CinterForgeServer'] / 'cf/io/CinterIO.py').is_file():
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
        'webapp': b / 'WebApps/CinterForgeWebApp/2026/websites/dev',
        'runtime': b / 'WebApps/CinterForgeWebApp/2026/runtime',
        'website': b / 'WebApps/CinterForgeWebApp/2026/websites/site',
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
    for marker in ('index.html', 'technology/index.html', 'technology/cinterforgeos/index.html',
                   'products/cinterforge-desktop/index.html'):
        if not (expected_artifacts(cfos)['website'] / marker).is_file():
            raise RuntimeError(f'Integrated corporate Hugo website missing: {marker}')
    for marker in ('blockbench/index.html',
                   'codeoss/out/vs/code/browser/workbench/workbench.html'):
        if not (expected_artifacts(cfos)['webapp'] / marker).is_file():
            raise RuntimeError(f'Integrated CinterForge WebApp missing: {marker}')
    if not (expected_artifacts(cfos)['runtime'] / 'CinterRuntime.py').is_file():
        raise RuntimeError('WebApp-owned mini-server was not packaged')
    return metadata


def _exclude_server_files(directory: str, names: list[str]) -> set[str]:
    """Keep credentials, private settings and generated state out of the OS."""
    excluded = {'.git', '.DS_Store', '__pycache__', '.pytest_cache',
                'credentials.json', 'secrets.json', '.env', '.env.local'}
    return {name for name in names if name in excluded or
            name.endswith(('.pyc', '.pyo', '.pem', '.key')) or
            name.startswith('.env.')}


def _run_products_subprocess(command: list[str], *, env: dict[str, str] | None,
                             log_path: Path | None) -> None:
    """Stream a real Hugo/Session build to console and a durable CFOS log."""
    if log_path is None:
        subprocess.run(command, env=env, check=True)
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open('a', encoding='utf-8', errors='replace') as output:
        with subprocess.Popen(command, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True,
                              errors='replace', bufsize=1) as process:
            assert process.stdout is not None
            for line in process.stdout:
                print(line, end='', flush=True)
                output.write(line)
                output.flush()
            code = process.wait()
        if code:
            output.write(f'[CFOS Products] ERROR: command exited {code}: {command[0]}\n')
            raise subprocess.CalledProcessError(code, command)


def build(cfos: Path, docker: str, environment: dict[str, str], image: str, *,
          log_path: Path | None = None) -> None:
    """Run the REAL Cinter WebApp Build inside the prepared Ubuntu AMD64 image."""
    directories = repos(cfos)
    b = builds_root(cfos)
    b.mkdir(parents=True, exist_ok=True)
    # CFOS's Session assets are a platform product; Cinter Build consumes them.
    _run_products_subprocess([sys.executable, '-u', str(cfos / 'scripts/session_console.py'),
                    'build', '--repo-root', str(cfos)], env=None, log_path=log_path)
    # CFOS coordinates the Website-owned lifecycle. The Converter product is
    # built separately after the Ubuntu OS is available; it is not a dependency.
    command = [docker, 'run', '--rm', '--platform', 'linux/amd64', '--pull=never',
               '--user', 'root', '--workdir', '/workspace/cinterforge_website']
    for name, path in directories.items():
        mode = 'rw' if name == 'CinterForgeWebApp' else 'ro'
        command += ['--mount', f'type=bind,source={path},target=/workspace/{name},{mode}']
    command += ['--mount', f'type=bind,source={b},target=/workspace/Builds,rw',
                image, 'bash', '-e', '-c',
                '/workspace/cinterforge_website/Cinter setup web '
                '&& /workspace/cinterforge_website/Cinter build web '
                '&& /workspace/cinterforge_website/Cinter stage web']
    print('[CFOS Ubuntu Products] Building Hugo Website + WebApp; Converter is a separate application', flush=True)
    _run_products_subprocess(command, env=environment, log_path=log_path)
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
    # Install the WebApp-owned standalone bundle with Code and Voxel
    # embedded. Converter is installed later through its own native lifecycle.
    source_webapp = builds_root(cfos) / f'WebApps/CinterForgeWebApp/{VERSION}'
    _install_tree(source_webapp, webapp)
    # One stable guest-visible desktop entry, shared with SteamOS conventions.
    # Ubuntu's graphical session starts the installed API on 20380.
    desktop_entry = apps / 'DesktopEntries/share/applications/cinterforge-webapp.desktop'
    desktop_entry.parent.mkdir(parents=True, exist_ok=True)
    desktop_entry.write_text(
        '[Desktop Entry]\nType=Application\nName=CinterForge WebApp\n'
        'Comment=CinterForge with integrated Code, Voxel and IFPlayer\n'
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
    if inventory(webapp / 'runtime') != receipt['products']['runtime']:
        raise RuntimeError('Installed WebApp minimal runtime differs from Build')
    if inventory(webapp / 'websites/site') != receipt['products']['website']:
        raise RuntimeError('Installed corporate Hugo website differs from Build')
    desktop_entry = apps / 'DesktopEntries/share/applications/cinterforge-webapp.desktop'
    if not desktop_entry.is_file() or 'http://127.0.0.1:20380/' not in desktop_entry.read_text():
        raise RuntimeError('Ubuntu WebApp KDE launcher missing or invalid')



def provision_python(archive: Path, docker: str, environment: dict[str, str], image: str) -> None:
    """Install the full declared Server requirements in a target-native Ubuntu env.

    Never mark an empty venv as a complete Python Server. No pip resolution
    occurs during end-user launch; installation failures abort CFOS Stage.
    """
    from ubuntu_adapter import steamapps_mount_args
    guest_server = f'{GUEST_APPS}/{SERVER}/{VERSION}'
    python_dir = f'{guest_server}/Python'
    req = f'{guest_server}/Source/requirements.txt'
    script = (f'set -eu\n'
              f'test -s {req}\n'
              f'python3 -m venv --system-site-packages {python_dir}\n'
              f'{python_dir}/bin/python -m pip install --disable-pip-version-check --requirement {req}\n'
              f'{python_dir}/bin/python -m pip check\n'
              f'{python_dir}/bin/python -m pip freeze > {guest_server}/cinterenv-requirements-installed.txt\n'
              f"{python_dir}/bin/python -c 'import sys; assert sys.platform == \"linux\"'\n"
              f'test -s {guest_server}/cinterenv-requirements-installed.txt\n')
    command = [docker, 'run', '--rm', '--pull=never', '--platform', 'linux/amd64',
               '--user', 'root', *steamapps_mount_args(archive),
               image, 'sh', '-ec', script]
    subprocess.run(command, env=environment, check=True)
    print(f'[CFOS Ubuntu Products] Server requirements installed into {python_dir}', flush=True)


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
assert (server / 'cinterenv-requirements-installed.txt').is_file()
importlib.import_module('cf.io.CinterIO')
runtime = webapp / 'runtime/CinterRuntime.py'
assert runtime.is_file() and (webapp / 'websites/dev/index.html').is_file()
assert (webapp / 'websites/site/products/cinterforge-desktop/index.html').is_file()
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
    for url in ('/', '/technology/', '/technology/cinterforgeos/',
                '/products/cinterforge-desktop/', '/websites/dev/index.html'):
        with urllib.request.urlopen('http://127.0.0.1:20380' + url, timeout=5) as response:
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


def package_verified_server(cfos: Path, archive: Path) -> Path:
    """Package the FULL installed Linux Server plus requirements-built env.

    A single inner tar.gz preserves Python venv symlinks/permissions. It must be
    extracted at the same guest-visible application path to preserve venv entry
    shebangs; never package or reuse a macOS-created Python environment.
    """
    from ubuntu_adapter import steamapps_volume
    apps = steamapps_volume(archive) / 'common/CinterForge/Applications'
    server = apps / SERVER / VERSION
    marker = server / 'cinterenv-requirements-installed.txt'
    if not marker.is_file() or not marker.read_text().strip():
        raise RuntimeError('Full Server cinterenv has not completed requirements.txt installation')
    if not (server / 'Python/bin/python').is_file():
        raise RuntimeError('Target-native Server interpreter is missing')
    if not (server / 'Source/CinterCore.py').is_file():
        raise RuntimeError('Full CinterForge Server core is missing')
    output = builds_root(cfos) / 'CreativeSuite/Zipped/CinterForgeServer-linux-amd64-2026.zip'
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='cfos-server-package-', dir=output.parent) as temp:
        payload = Path(temp) / 'CinterForgeServer-linux-amd64-2026.tar.gz'
        with tarfile.open(payload, 'w:gz', dereference=False) as tar:
            tar.add(server, arcname='Server/CinterForgeServer/2026', recursive=True)
        manifest = {
            'schema': 'cinterforge.server.release.v1',
            'platform': 'linux-amd64',
            'guest_destination': f'{GUEST_APPS}/{SERVER}/{VERSION}',
            'requirements_sha256': hash_file(server / 'Source/requirements.txt'),
            'environment_freeze_sha256': hash_file(marker),
            'payload': payload.name,
            'payload_sha256': hash_file(payload),
        }
        pending = Path(temp) / 'server.zip'
        with zipfile.ZipFile(pending, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as z:
            z.write(payload, arcname=payload.name)
            z.writestr('manifest.json', json.dumps(manifest, indent=2, sort_keys=True) + '\n')
        pending.replace(output)
    print(f'[CFOS Ubuntu Products] VERIFIED Server release ZIP: {output}', flush=True)
    return output


if __name__ == '__main__':
    print('Use ./CFOS build/stage/test cinterforge-os linux Release; this is an internal product assembler.')
    raise SystemExit(2)
