#!/usr/bin/env python3
"""Build, install and test the single Ubuntu AMD64 CinterForgeOS distribution.

The same immutable rootfs contains the Windows/Linux headless build tools and
Wine plus an opt-in KDE Plasma desktop for the macOS Swift launcher. It is an
OCI container userspace, not a SteamOS/libkrun disk image.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

SOURCE = Path(__file__).resolve().parent
IMAGE = 'cinterforge-ubuntu:24.04-amd64'
# Bump when the actual cross-build/headless test contract changes.
HEADLESS_CONTRACT = 'ubuntu24.04-amd64-comprehensive-fork-toolchains-sdl3-plasma-steamapps-products-upstream-v8'
READY_FILE = 'headless-verified.json'
ARCHIVE = 'Ubuntu-24.04-amd64-rootfs.tar'
ADAPTER_SOURCE = SOURCE / 'ubuntu_adapter.py'


from managed_backend import ensure_backend, ensure_buildx, source_directory, storage_root
from ubuntu_adapter import IMAGE_IMPORT_CHANGES, steamapps_volume, steamapps_mount_args, STEAMAPPS_MOUNT
from build_linux import shared_bootstrap_digest, verify_shared_bootstrap
import ubuntu_products
import ubuntu_unforked


def paths(repo: Path, *, configuration: str = "Release") -> tuple[Path, Path, Path]:
    """CFOS-owned Linux rootfs and installed locations, without CinterForge."""
    if not (repo / 'CFOS').is_file():
        raise RuntimeError(f'--repo-root must point to CinterForgeOS: {repo}')
    if configuration not in ('Debug', 'Release'):
        raise ValueError(f'Invalid CFOS Linux configuration: {configuration}')
    version = '2026-Debug' if configuration == 'Debug' else '2026'
    built = repo.parent / 'Builds/CreativeSuite/Built/cinterforge-os/linux' / version / 'Ubuntu'
    steamapps = os.environ.get('CFOS_STEAMAPPS')
    if not steamapps:
        steamapps = subprocess.check_output(
            [sys.executable, str(repo / 'scripts/resolve-steamapps.py')], text=True).strip()
    base = Path(steamapps) / 'common/CinterForge/Applications'
    installed = base / 'Linux/CinterForgeOS' / version / 'Ubuntu/Distro'
    adapter = base / 'macOS/CinterForgeOS' / version / 'Ubuntu'
    return built, installed, adapter



def built_path(repo: Path, configuration: str = 'Release') -> Path:
    if not (repo / 'CFOS').is_file():
        raise RuntimeError(f'Expected CinterForgeOS checkout: {repo}')
    if configuration not in ('Debug', 'Release'):
        raise RuntimeError(f'Invalid Linux configuration: {configuration}')
    version = '2026-Debug' if configuration == 'Debug' else '2026'
    return repo.parent / 'Builds/CreativeSuite/Built/cinterforge-os/linux' / version / 'Ubuntu'


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as data:
        for block in iter(lambda: data.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def verify_prerequisite(repo: Path, configuration: str = 'Release') -> None:
    """Fail closed unless the installed CFOS Ubuntu image passed real Test.

    The receipt is tied to the built rootfs and authored definition, so neither
    a stale image nor a pre-Wine test pass can silently authorize downstream
    macOS, Windows or Linux app builds. No Docker/Steam/source import required.
    """
    built = built_path(repo, configuration)
    archive = built / ARCHIVE
    receipt = built / READY_FILE
    if not receipt.is_file() or not archive.is_file():
        raise RuntimeError(
            f'CFOS {configuration} Ubuntu has not completed Build → Stage → Test; '
            f'run ./CFOS all cinterforge-os linux {configuration} first (missing {receipt})'
        )
    try:
        recorded = json.loads(receipt.read_text())
    except (ValueError, OSError) as error:
        raise RuntimeError(f'Invalid CFOS Ubuntu verification receipt: {receipt}') from error
    expected = {
        'schema': 'cinterforge.cfos.ubuntu-headless.v1',
        'status': 'passed',
        'contract': HEADLESS_CONTRACT,
        'archive_sha256': sha256(archive),
        'dockerfile_sha256': sha256(SOURCE / 'Dockerfile.ubuntu'),
        'headless_smoke_sha256': sha256(SOURCE / 'headless_contract.sh'),
        'desktop_script_sha256': sha256(SOURCE / 'ubuntu_desktop.sh'),
        'shell_script_sha256': sha256(SOURCE / 'ubuntu_shell.sh'),
        'service_script_sha256': sha256(SOURCE / 'ubuntu_service.sh'),
        'linuxkit_bootstrap_sha256': shared_bootstrap_digest(repo),
        'products_sha256': sha256(ubuntu_products.product_root(repo) / 'manifest.json'),
        'upstream_suite_sha256': sha256(SOURCE / 'ubuntu_unforked.py'),
    }
    for key, value in expected.items():
        if recorded.get(key) != value:
            raise RuntimeError(
                f'CFOS Ubuntu verification is stale or incompatible ({key}); '
                f'run ./CFOS all cinterforge-os linux {configuration} first'
            )
    print(f'[CFOS Ubuntu prerequisite] PASS {configuration}: verified installed Linux/Windows headless toolchain; {archive}', flush=True)


# Real, deterministic cross-compiler and Wine runtime exercises. Run against
# the installed rootfs, never a cached build tag. App-specific binaries are
# additionally tested by their own Cinter Test scripts.
HEADLESS_SMOKE = (SOURCE / 'headless_contract.sh').read_text()


def validate_archive(archive: Path) -> None:
    with tarfile.open(archive, 'r') as src:
        if not any(member.name.lstrip('./') == 'usr/bin/apt-get' for member in src):
            raise RuntimeError(f'Not a complete Ubuntu rootfs: {archive}')


def require_source_checkouts(repo: Path) -> None:
    """Read-only source prerequisite; does not clone, repair or mutate sources."""
    subprocess.run([sys.executable, '-u', str(repo / 'scripts' / 'fork.py'),
                    'clone', 'all', '--dry-run'], check=True)


def setup(repo: Path) -> None:
    """Validate the headless AMD64 toolchain without building or installing.

    VM startup and image creation belong to Build; Setup must not implicitly
    mutate any pre-existing Colima environment or download application bits.
    """
    # CFOS owns the complete source inventory. This preflight never clones,
    # switches a branch, downloads files, or modifies a checkout.
    require_source_checkouts(repo)
    host = platform.system()
    if host == 'Darwin' and platform.machine() == 'arm64':
        tools = ('docker', 'colima')
    elif host == 'Linux':
        tools = ('docker',)
    else:
        raise RuntimeError(f'Unsupported CFOS Linux AMD64 build host: {host}/{platform.machine()}')
    for tool in tools:
        if shutil.which(tool) is None:
            raise RuntimeError(f'Missing {tool}; run ./CFOS setup cinterforge-os linux Release')
    if host == 'Linux':
        subprocess.run(['docker', 'info'], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(['docker', 'buildx', 'version'], check=True, stdout=subprocess.DEVNULL)
    dockerfile = SOURCE / 'Dockerfile.ubuntu'
    if not dockerfile.is_file() or 'FROM ubuntu:24.04' not in dockerfile.read_text():
        raise RuntimeError(f'Missing or invalid Ubuntu 24.04 Dockerfile: {dockerfile}')
    # Detect a stale parallel VM without starting Colima. In particular the
    # renamed Cinter checkout must not require the old CinterForge checkout.
    source = source_directory(repo)
    root = storage_root(source)
    print(f'[Ubuntu Setup] READY: {host} host, {", ".join(tools)}, Dockerfile', flush=True)
    print(f'[Ubuntu Setup] Build state: {root}; source: {source}', flush=True)


def build(built: Path) -> None:
    source = built.parents[1] / 'Source'
    runner, environment = ensure_backend(source)
    built.mkdir(parents=True, exist_ok=True)
    (built / READY_FILE).unlink(missing_ok=True)
    if Path(runner).name == 'docker':
        ensure_buildx(runner, environment)
        command = [runner, 'buildx', 'build', '--platform', 'linux/amd64', '--load']
    else:
        command = [runner, 'build', '--platform', 'linux/amd64']
    subprocess.run([*command, '-t', IMAGE, '-f', str(SOURCE / 'Dockerfile.ubuntu'), str(SOURCE)], check=True, env=environment)
    actual = subprocess.check_output([runner, 'image', 'inspect', '--format', '{{.Architecture}}', IMAGE], text=True, env=environment).strip()
    if actual != 'amd64':
        raise RuntimeError(f'Expected Ubuntu AMD64 image, found {actual!r}')
    subprocess.run([runner, 'run', '--rm', '--platform', 'linux/amd64', '--user', 'cinterforge', IMAGE, '/bin/sh', '-ec', HEADLESS_SMOKE], check=True, env=environment)
    subprocess.run([runner, 'run', '--rm', '--platform', 'linux/amd64', '--network', 'none',
                    '--shm-size', '512m', IMAGE, '/usr/local/bin/cfos-ubuntu-desktop', 'test'],
                   check=True, env=environment)
    cid = subprocess.check_output([runner, 'create', '--platform', 'linux/amd64', IMAGE], text=True, env=environment).strip()
    target = built / ARCHIVE
    partial = built / (ARCHIVE + '.partial')
    try:
        with partial.open('wb') as out:
            subprocess.run([runner, 'export', cid], stdout=out, check=True, env=environment)
        validate_archive(partial)
        partial.replace(target)
    finally:
        subprocess.run([runner, 'rm', '-f', cid], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=environment)
        partial.unlink(missing_ok=True)
    print(f'[Ubuntu Build] VERIFIED image={IMAGE} archive={target}', flush=True)


def stage_adapter(adapter: Path, destination: Path, built: Path) -> None:
    """Install only host-side tooling beside the macOS CinterForgeOS app."""
    if sys.platform != 'darwin':
        print('[Ubuntu Stage] macOS adapter installation skipped (not running on macOS)', flush=True)
        return
    if not ADAPTER_SOURCE.is_file():
        raise RuntimeError(f'Missing authored macOS Ubuntu adapter: {ADAPTER_SOURCE}')
    adapter.mkdir(parents=True, exist_ok=True)
    target = adapter / 'ubuntu_adapter.py'
    shutil.copy2(ADAPTER_SOURCE, target)
    helper = SOURCE / 'managed_backend.py'
    if not helper.is_file():
        raise RuntimeError(f'Missing managed Ubuntu backend helper: {helper}')
    shutil.copy2(helper, adapter / 'managed_backend.py')
    shutil.copy2(SOURCE / 'headless_contract.sh', adapter / 'headless_contract.sh')
    target.chmod(0o755)
    # A host-local hint lets bundled macOS products find the CFOS-managed
    # Colima backend from an INSTALLED rootfs even without any source repo.
    # This file is beside the installed archive, not IN the portable archive,
    # and is advisory: copied installations can use an existing Docker daemon.
    builds_root = built.parents[5].resolve()
    if builds_root.name != 'Builds':
        raise RuntimeError(f'CFOS Ubuntu build root is not under Builds: {built}')
    (destination / 'CFOS-backend.json').write_text(json.dumps({
        'schema': 'cinterforge.cfos.host-backend.v1',
        'builds_root': str(builds_root),
    }, indent=2, sort_keys=True) + '\n')
    # Staging must install exactly the adapter being authored, not a stale copy.
    for authored, installed in ((ADAPTER_SOURCE, target), (helper, adapter / 'managed_backend.py'),
                                (SOURCE / 'headless_contract.sh', adapter / 'headless_contract.sh')):
        if hashlib.sha256(authored.read_bytes()).digest() != hashlib.sha256(installed.read_bytes()).digest():
            raise RuntimeError(f'Installed Ubuntu adapter differs from source: {installed}')
    (adapter / 'README.md').write_text(
        '# Ubuntu host adapter for CinterForgeOS on macOS\n\n'
        'This folder contains host-side execution tooling only.\n'
        'The Ubuntu distribution is installed at '
        '`Applications/Linux/CinterForgeOS/2026/Ubuntu/Distro/`.\n'
        'Test runs the staged rootfs through the selected local Docker/Colima backend.\n'
        'It supports headless smoke and KDE session launch through the same installed image.\n'
    )
    print(f'[Ubuntu Stage] macOS host adapter installed: {target}', flush=True)


def _verify_stage_install(built: Path, staged: Path, installed: Path) -> None:
    source = built / ARCHIVE
    for label, location in (('STAGED', staged), ('INSTALLED', installed)):
        target = location / ARCHIVE
        if not target.is_file():
            raise RuntimeError(f'{label} Ubuntu artifact missing: {target}')
        validate_archive(target)
        if target.stat().st_size != source.stat().st_size:
            raise RuntimeError(f'{label} Ubuntu artifact size mismatch: {target}')
        print(f'[Ubuntu Stage] VERIFIED {label}: {target}', flush=True)


def stage(built: Path, destination: Path, adapter: Path) -> None:
    archive = built / ARCHIVE
    if not archive.is_file():
        raise RuntimeError(f'Ubuntu is not built; run ./CFOS build cinterforge-os linux Release: {archive}')
    validate_archive(archive)

    # Stage is a distinct lifecycle product. Previously this function wrote
    # straight to Applications and reported STAGED even though nothing existed
    # under Builds/CreativeSuite/Staged. Keep the installed runtime in the
    # persistent Applications tree, but also create the expected staged artifact.
    version = built.parents[0].name
    staged = built.parents[4] / 'Staged/linux/cinterforge-os' / version / 'Ubuntu/Distro'
    if staged.resolve() == destination.resolve():
        raise RuntimeError(f'Staged and installed Ubuntu paths must differ: {staged}')
    staged.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ubuntu-stage-', dir=staged.parent) as tmp:
        assembled = Path(tmp) / 'Distro'
        assembled.mkdir()
        shutil.copy2(archive, assembled / ARCHIVE)
        (assembled / 'README.md').write_text(
            '# CinterForgeOS Ubuntu 24.04 AMD64\n\n'
            'Staged Ubuntu Linux x86-64 headless build/test and KDE desktop userspace.\n'
            'The installed persistent copy is in Applications/Linux/CinterForgeOS.\n'
            'Its separate Ubuntu-owned steamapps volume is preserved beside Distro.\n'
        )
        validate_archive(assembled / ARCHIVE)
        previous = staged.with_name('Distro.previous-stage')
        if previous.exists():
            if not staged.is_dir() or not (staged / ARCHIVE).is_file():
                raise RuntimeError(f'Incomplete previous Ubuntu staging replacement: {previous}')
            validate_archive(staged / ARCHIVE)
            if not previous.is_dir() or previous.is_symlink():
                raise RuntimeError(f'Unexpected Ubuntu recovery location: {previous}')
            shutil.rmtree(previous)
        had_staged = staged.exists()
        if had_staged:
            staged.rename(previous)
        try:
            assembled.rename(staged)
            validate_archive(staged / ARCHIVE)
        except Exception:
            if had_staged and not staged.exists() and previous.exists():
                previous.rename(staged)
            raise
        if had_staged:
            shutil.rmtree(previous)
    print(f'[Ubuntu Stage] STAGED {staged / ARCHIVE}', flush=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if (destination / ARCHIVE).is_file():
        def sha256(path: Path) -> str:
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
            return digest.hexdigest()
        if sha256(archive) == sha256(destination / ARCHIVE):
            print(f'[Ubuntu Stage] INSTALLED (unchanged) {destination / ARCHIVE}', flush=True)
            steamapps_volume(destination / ARCHIVE, create=True)
            stage_adapter(adapter, destination, built)
            _verify_stage_install(built, staged, destination)
            return
    # Stage an immutable distribution archive: no extraction over installed
    # applications and no cross-platform duplication into macOS.
    with tempfile.TemporaryDirectory(prefix='ubuntu-stage-', dir=destination.parent) as temp:
        workspace = Path(temp)
        shutil.copy2(archive, workspace / ARCHIVE)
        (workspace / 'README.md').write_text(
            '# CinterForgeOS Ubuntu 24.04 AMD64\n\n'
            'One Ubuntu userspace for Linux/Windows headless builds and tests, and KDE Desktop.\n'
            'The host macOS CinterForge Adapter owns VM/bootstrap execution.\n'
            'This rootfs tar is not a standalone bootable disk or configured VM.\n'
            'A persistent Ubuntu-owned steamapps volume is mounted at /run/steamapps,\n'
            'using the same guest-visible app and KDE shortcut paths as SteamOS.\n'
            'Cloudflare distribution bits remain in the sibling Cloudflare folder.\n'
        )
        # Keep the existing installation intact until the replacement is
        # complete. Distro.previous is a disposable recovery copy, not a
        # permanent version; a stale copy must not block future upgrades.
        old = destination.with_name('Distro.previous')
        if old.exists():
            if not destination.is_dir() or not (destination / ARCHIVE).is_file():
                raise RuntimeError(
                    f'Cannot replace Ubuntu recovery copy while the active installation is missing: {old}'
                )
            validate_archive(destination / ARCHIVE)
            if old.is_dir() and not old.is_symlink():
                shutil.rmtree(old)
            else:
                raise RuntimeError(f'Unexpected Ubuntu recovery path (not a directory): {old}')
            print(f'[Ubuntu Stage] Removed superseded recovery copy: {old}', flush=True)
        moved_previous = False
        if destination.exists():
            destination.rename(old)
            moved_previous = True
        try:
            workspace.rename(destination)
            validate_archive(destination / ARCHIVE)
        except Exception:
            if moved_previous and not destination.exists() and old.exists():
                old.rename(destination)
            raise
        # The newly installed distro is validated. The recovery copy is no
        # longer needed; prevent it from blocking subsequent stage commands.
        if moved_previous:
            shutil.rmtree(old)
    steamapps_volume(destination / ARCHIVE, create=True)
    stage_adapter(adapter, destination, built)
    print(f'[Ubuntu Stage] INSTALLED {destination / ARCHIVE}', flush=True)
    print(f'[Ubuntu Stage] PERSISTENT steamapps volume: {steamapps_volume(destination / ARCHIVE)}', flush=True)
    _verify_stage_install(built, staged, destination)


def test(destination: Path, adapter: Path, source: Path) -> str:
    """Verify the installed image for headless execution and KDE desktop.

    SteamOS GUI/FEX remains a separate guest target. KDE runs in the same
    Ubuntu image via its optional Xvfb/noVNC session, not a second distro.
    """
    archive = destination / ARCHIVE
    if not archive.is_file():
        raise RuntimeError(f'Ubuntu not staged: {archive}; run ./CFOS stage cinterforge-os linux Release')
    validate_archive(archive)
    runner, environment = ensure_backend(source)
    print(f'[Ubuntu Headless Test] Installed rootfs: {archive}', flush=True)
    # Use the installed archive itself on both hosts. A previously tagged
    # development image is not evidence that Stage installed the correct bits.
    with archive.open('rb') as stream:
        result = subprocess.run(
            [runner, 'image', 'import', '--platform', 'linux/amd64', *IMAGE_IMPORT_CHANGES, '-'],
            stdin=stream, env=environment, text=True, capture_output=True, check=True)
    image_id = result.stdout.strip()
    if not image_id.startswith('sha256:'):
        raise RuntimeError(f'Docker import did not return an image ID: {result.stdout!r}; stderr={result.stderr!r}')
    actual = subprocess.check_output(
        [runner, 'image', 'inspect', '--format', '{{.Id}} {{.Os}}/{{.Architecture}}', image_id],
        env=environment, text=True).strip()
    if actual != f'{image_id} linux/amd64':
        raise RuntimeError(f'Installed image architecture mismatch: {actual!r}')
    verify = HEADLESS_SMOKE
    subprocess.run([runner, 'run', '--rm', '--pull=never', '--platform', 'linux/amd64',
                    '--user', 'cinterforge', image_id, '/bin/sh', '-ec', verify], env=environment, check=True)
    # Test KDE from the STAGED/INSTALLED archive too, not merely the Docker
    # build tag. A headless pass alone cannot certify a shipping desktop.
    subprocess.run([runner, 'run', '--rm', '--pull=never', '--platform', 'linux/amd64',
                    '--network', 'none', '--shm-size', '512m',
                    *steamapps_mount_args(archive),
                    image_id, '/usr/local/bin/cfos-ubuntu-desktop', 'test'],
                   env=environment, check=True)
    # Verify the shipping Ubuntu volume, not a read-only view of SteamOS's
    # installed applications. Prove identity, writable access as the desktop
    # user, and that guest writes land in the host's persistent Ubuntu volume.
    volume = steamapps_volume(archive)
    guest_check = r'''import json, os, pathlib
root = pathlib.Path('/run/steamapps')
mounts = pathlib.Path('/proc/self/mountinfo').read_text().splitlines()
if not any(line.split()[4] == '/run/steamapps' for line in mounts):
    raise SystemExit('Ubuntu steamapps is not mounted')
manifest = json.loads((root / 'CFOS-Volume.json').read_text())
if manifest.get('schema') != 'cinterforge.cfos.ubuntu-steamapps.v1':
    raise SystemExit('Wrong Ubuntu steamapps volume identity')
applications = root / 'common/CinterForge/Applications'
if not applications.is_dir():
    raise SystemExit('Ubuntu steamapps Applications layout missing')
probe = applications / 'Test' / 'CFOS-Ubuntu-Volume-Write-Test.txt'
try:
    probe.write_text('ubuntu-steamapps-writable')
    if probe.read_text() != 'ubuntu-steamapps-writable':
        raise SystemExit('Ubuntu steamapps read-after-write mismatch')
finally:
    probe.unlink(missing_ok=True)
print(json.dumps({'mounted': True, 'writable': True, 'schema': manifest['schema']}))'''
    response = subprocess.run(
        [runner, 'run', '--rm', '--pull=never', '--platform', 'linux/amd64',
         '--network', 'none', '--user', 'cinterforge',
         *steamapps_mount_args(archive), image_id, 'python3', '-c', guest_check],
        env=environment, text=True, capture_output=True, check=True)
    try:
        result = json.loads(response.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f'Ubuntu steamapps mount returned invalid result: {response.stdout!r}') from error
    if result != {'mounted': True, 'writable': True, 'schema': 'cinterforge.cfos.ubuntu-steamapps.v1'}:
        raise RuntimeError(f'Ubuntu steamapps mount acceptance failed: {result!r}')
    if (volume / 'common/CinterForge/Applications/Test/CFOS-Ubuntu-Volume-Write-Test.txt').exists():
        raise RuntimeError('Ubuntu steamapps test left an unexpected write artifact')
    print(f'[Ubuntu steamapps] PASS /run/steamapps writable for cinterforge user: {volume}', flush=True)
    print('[Ubuntu Test] PASS: installed distro ran Linux + Windows headlessly and KDE desktop; no Steam or FEX required', flush=True)
    return image_id


def certify_tested_install(repo: Path, destination: Path, source: Path) -> None:
    """Commit readiness only after the installed OS AND products pass Test."""
    # Receipt is committed only AFTER the installed rootfs, native Linux/PE
    # execution and host Applications bridge have all passed.
    # source is .../linux/Source; the distro version is in installed paths.
    version = destination.parent.parent.name
    built = source.parent / version / 'Ubuntu'
    original = built / ARCHIVE
    archive = destination / ARCHIVE
    if not original.is_file() or sha256(original) != sha256(archive):
        raise RuntimeError('Installed Ubuntu archive differs from the built rootfs; cannot certify readiness')
    metadata = {
        'schema': 'cinterforge.cfos.ubuntu-headless.v1',
        'status': 'passed',
        'contract': HEADLESS_CONTRACT,
        'archive_sha256': sha256(original),
        'dockerfile_sha256': sha256(SOURCE / 'Dockerfile.ubuntu'),
        'headless_smoke_sha256': sha256(SOURCE / 'headless_contract.sh'),
        'desktop_script_sha256': sha256(SOURCE / 'ubuntu_desktop.sh'),
        'shell_script_sha256': sha256(SOURCE / 'ubuntu_shell.sh'),
        'service_script_sha256': sha256(SOURCE / 'ubuntu_service.sh'),
        'linuxkit_bootstrap_sha256': shared_bootstrap_digest(SOURCE.parents[1]),
        'products_sha256': sha256(ubuntu_products.product_root(repo) / 'manifest.json'),
        'upstream_suite_sha256': sha256(SOURCE / 'ubuntu_unforked.py'),
    }
    receipt = built / READY_FILE
    temporary = built / (READY_FILE + '.partial')
    temporary.write_text(json.dumps(metadata, indent=2, sort_keys=True) + '\n')
    temporary.replace(receipt)
    print(f'[Ubuntu Headless Test] VERIFIED prerequisite: {receipt}', flush=True)



def bundle_adapter(repo: Path, product: Path, configuration: str = 'Release') -> None:
    """Bundle the *same* tested macOS Ubuntu adapter in both .app products.

    CinterForgeOS owns the implementation; Desktop calls this public command
    instead of vendoring a second copy. No rootfs duplicated into either app.
    """
    verify_prerequisite(repo, configuration)
    product = product.resolve()
    if not product.is_dir() or product.suffix != '.app' or not (product / 'Contents/Info.plist').is_file():
        raise RuntimeError(f'Expected a completed macOS .app product: {product}')
    target = product / 'Contents/Resources/Ubuntu'
    target.mkdir(parents=True, exist_ok=True)
    for filename in ('ubuntu_adapter.py', 'managed_backend.py', 'headless_contract.sh'):
        source = SOURCE / filename
        if not source.is_file():
            raise RuntimeError(f'CFOS Ubuntu adapter source is missing: {source}')
        destination = target / filename
        shutil.copy2(source, destination)
        if sha256(source) != sha256(destination):
            raise RuntimeError(f'CFOS Ubuntu adapter bundle verification failed: {destination}')
    # Modifying resources after Xcode signs a bundle invalidates its signature.
    # Preserve the original app entitlements (including CFOS virtualization)
    # while creating a valid local development signature over the new contents.
    if sys.platform == 'darwin':
        subprocess.run([
            'codesign', '--force', '--deep', '--sign', '-', '--timestamp=none',
            '--preserve-metadata=entitlements,requirements,flags,runtime', str(product),
        ], check=True)
        subprocess.run(['codesign', '--verify', '--deep', '--strict', str(product)], check=True)
    print(f'[CFOS Ubuntu adapter] BUNDLED {target}', flush=True)



def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['setup', 'build', 'stage', 'test', 'verify', 'bundle', 'all'])
    parser.add_argument('--repo-root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--product', type=Path, help='Built macOS .app to receive the adapter')
    parser.add_argument('--configuration', choices=['Debug', 'Release', 'all'], default='Release')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    repo = args.repo_root.resolve()
    configurations = ('Debug', 'Release') if args.configuration == 'all' else (args.configuration,)
    if args.dry_run:
        for configuration in configurations:
            version = '2026-Debug' if configuration == 'Debug' else '2026'
            print(f'[CFOS Linux dry-run] operation={args.operation} configuration={configuration} repo={repo}', flush=True)
            print(f"[CFOS Linux dry-run] built={repo.parent / 'Builds/CreativeSuite/Built/cinterforge-os/linux' / version / 'Ubuntu'}", flush=True)
        return 0
    if args.operation == 'bundle':
        if args.product is None or len(configurations) != 1:
            parser.error('bundle requires --product and one configuration')
        bundle_adapter(repo, args.product, configurations[0])
        return 0
    if args.operation == 'verify':
        for configuration in configurations:
            verify_prerequisite(repo, configuration)
        return 0
    if args.operation in ('setup', 'all'):
        setup(repo)
    if args.operation == 'setup':
        return 0
    for configuration in configurations:
        version = '2026-Debug' if configuration == 'Debug' else '2026'
        built = repo.parent / 'Builds/CreativeSuite/Built/cinterforge-os/linux' / version / 'Ubuntu'
        # Build never depends on a Steam client; Stage/Test do.
        if args.operation in ('build', 'all'):
            # Direct Build must not bypass the source prerequisite established
            # by Setup, even if invoked without a preceding Setup command.
            require_source_checkouts(repo)
            # LinuxKit is the first CFOS build product. Downstream macOS,
            # Linux and Windows consumers must only inspect its verified
            # artifact, never rebuild an application-owned bootstrap.
            subprocess.run([sys.executable, '-u', str(SOURCE / 'build_linux.py'),
                            '--repo-root', str(repo)], check=True)
            build(built)
            runner, environment = ensure_backend(source_directory(repo))
            ubuntu_products.build(repo, runner, environment, IMAGE)
        if args.operation == 'build':
            continue
        installed_built, installed, adapter = paths(repo, configuration=configuration)
        if args.operation in ('stage', 'all'):
            stage(installed_built, installed, adapter)
            ubuntu_products.install(repo, installed / ARCHIVE)
            runner, environment = ensure_backend(source_directory(repo))
            ubuntu_products.provision_python(installed / ARCHIVE, runner, environment, IMAGE)
        if args.operation in ('test', 'all'):
            # A failed Server/WebApp test must never leave a valid Ubuntu READY receipt.
            (built / READY_FILE).unlink(missing_ok=True)
            installed_image = test(installed, adapter, source_directory(repo))
            runner, environment = ensure_backend(source_directory(repo))
            ubuntu_products.test(repo, installed / ARCHIVE, runner, environment, installed_image)
            ubuntu_unforked.test(repo, installed / ARCHIVE, runner, environment, installed_image)
            certify_tested_install(repo, installed, source_directory(repo))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        # Setup/Build prerequisites are expected user-facing failures. A
        # traceback obscures the corrective command and encourages unsafe
        # ad-hoc cleanup of otherwise valid Colima and Ubuntu build state.
        print(f'[CFOS Ubuntu] ERROR: {error}', file=sys.stderr, flush=True)
        raise SystemExit(1)
