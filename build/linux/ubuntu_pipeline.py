#!/usr/bin/env python3
"""Build, stage and smoke-check the separate Ubuntu AMD64 CinterForgeOS distro.

Never overwrites SteamOS, the macOS adapter, or Cloudflare bundles.
The staged rootfs is a container userspace, NOT a bootable VM image.
"""
from __future__ import annotations

import argparse
import hashlib
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
ARCHIVE = 'Ubuntu-24.04-amd64-rootfs.tar'
ADAPTER_SOURCE = SOURCE / 'ubuntu_adapter.py'


from managed_backend import ensure_backend, ensure_buildx, source_directory, storage_root


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


def validate_archive(archive: Path) -> None:
    with tarfile.open(archive, 'r') as src:
        if not any(member.name.lstrip('./') == 'usr/bin/apt-get' for member in src):
            raise RuntimeError(f'Not a complete Ubuntu rootfs: {archive}')


def setup(repo: Path) -> None:
    """Validate the headless AMD64 toolchain without building or installing.

    VM startup and image creation belong to Build; Setup must not implicitly
    mutate any pre-existing Colima environment or download application bits.
    """
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
    if Path(runner).name == 'docker':
        ensure_buildx(runner, environment)
        command = [runner, 'buildx', 'build', '--platform', 'linux/amd64', '--load']
    else:
        command = [runner, 'build', '--platform', 'linux/amd64']
    subprocess.run([*command, '-t', IMAGE, '-f', str(SOURCE / 'Dockerfile.ubuntu'), str(SOURCE)], check=True, env=environment)
    actual = subprocess.check_output([runner, 'image', 'inspect', '--format', '{{.Architecture}}', IMAGE], text=True, env=environment).strip()
    if actual != 'amd64':
        raise RuntimeError(f'Expected Ubuntu AMD64 image, found {actual!r}')
    subprocess.run([runner, 'run', '--rm', '--platform', 'linux/amd64', IMAGE, '/bin/sh', '-ec',
                    'test "$(dpkg --print-architecture)" = amd64; grep -q "Ubuntu 24.04" /etc/os-release; command -v Xvfb; command -v python3; ldconfig -p | grep -q libglibmm-2.4.so.1; ldconfig -p | grep -q libgtkmm-2.4.so.1; ldconfig -p | grep -q liblrdf.so.0; ldconfig -p | grep -q liblilv-0.so.0'], check=True, env=environment)
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


def stage_adapter(adapter: Path) -> None:
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
    target.chmod(0o755)
    # Staging must install exactly the adapter being authored, not a stale copy.
    for authored, installed in ((ADAPTER_SOURCE, target), (helper, adapter / 'managed_backend.py')):
        if hashlib.sha256(authored.read_bytes()).digest() != hashlib.sha256(installed.read_bytes()).digest():
            raise RuntimeError(f'Installed Ubuntu adapter differs from source: {installed}')
    (adapter / 'README.md').write_text(
        '# Ubuntu host adapter for CinterForgeOS on macOS\n\n'
        'This folder contains host-side execution tooling only.\n'
        'The Ubuntu distribution is installed at '
        '`Applications/Linux/CinterForgeOS/2026/Ubuntu/Distro/`.\n'
        'Test runs the staged rootfs through the selected local Docker/Colima backend.\n'
        'It does not represent an independently booted VM or GUI smoke test.\n'
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
            'Staged headless Linux x86-64 container userspace.\n'
            'The installed persistent copy is in Applications/Linux/CinterForgeOS.\n'
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
            stage_adapter(adapter)
            _verify_stage_install(built, staged, destination)
            return
    # Stage an immutable distribution archive: no extraction over installed
    # applications and no cross-platform duplication into macOS.
    with tempfile.TemporaryDirectory(prefix='ubuntu-stage-', dir=destination.parent) as temp:
        workspace = Path(temp)
        shutil.copy2(archive, workspace / ARCHIVE)
        (workspace / 'README.md').write_text(
            '# CinterForgeOS Ubuntu 24.04 AMD64\n\n'
            'Ubuntu userspace distribution for the Linux CinterForgeOS pipeline.\n'
            'The host macOS CinterForge Adapter owns VM/bootstrap execution.\n'
            'This rootfs tar is not a standalone bootable disk or configured VM.\n'
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
    stage_adapter(adapter)
    print(f'[Ubuntu Stage] INSTALLED {destination / ARCHIVE}', flush=True)
    _verify_stage_install(built, staged, destination)


def test(destination: Path, adapter: Path, source: Path) -> None:
    """Identical installed, headless AMD64 smoke on either macOS build host.

    SteamOS GUI/FEX is a different target. Ubuntu is the headless OCI userspace
    used locally and in the cloud; no X server or guest desktop is started.
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
            [runner, 'image', 'import', '--platform', 'linux/amd64', '-'],
            stdin=stream, env=environment, text=True, capture_output=True, check=True)
    image_id = result.stdout.strip()
    if not image_id.startswith('sha256:'):
        raise RuntimeError(f'Docker import did not return an image ID: {result.stdout!r}; stderr={result.stderr!r}')
    actual = subprocess.check_output(
        [runner, 'image', 'inspect', '--format', '{{.Id}} {{.Os}}/{{.Architecture}}', image_id],
        env=environment, text=True).strip()
    if actual != f'{image_id} linux/amd64':
        raise RuntimeError(f'Installed image architecture mismatch: {actual!r}')
    verify = (
        'set -eu; '
        'test "$(dpkg --print-architecture)" = amd64; '
        'grep -q "Ubuntu 24.04" /etc/os-release; '
        'command -v python3 >/dev/null; '
        'command -v apt-get >/dev/null; '
        "python3 -c \"import platform; assert platform.machine() == 'x86_64'\"; "
        'ldconfig -p | grep -q libglibmm-2.4.so.1; '
        'ldconfig -p | grep -q libgtkmm-2.4.so.1; '
        'ldconfig -p | grep -q liblrdf.so.0; '
        'ldconfig -p | grep -q liblilv-0.so.0; '
        'echo "[Ubuntu Headless Test] PASS: installed Ubuntu 24.04 x86-64 runtime and shared libraries"'
    )
    subprocess.run([runner, 'run', '--rm', '--pull=never', '--platform', 'linux/amd64',
                    image_id, '/bin/sh', '-ec', verify], env=environment, check=True)
    # Test the Ubuntu-to-host Applications bridge after the distro starts.
    # The bind mount is read-only and exposes the *persistent* Applications
    # hierarchy, never developer Builds or the CinterForge source checkout.
    applications = destination.parents[4]  # <Steam>/common/CinterForge/Applications
    if applications.name != 'Applications' or not applications.is_dir():
        raise RuntimeError(f'Persistent Applications root missing: {applications}')
    import json
    guest_scan = r'''import json, pathlib
root = pathlib.Path('/host-applications')
if not root.is_dir():
    raise SystemExit('Applications bridge not mounted')
rows = []
for platform in sorted(root.iterdir()):
    if not platform.is_dir() or platform.is_symlink():
        continue
    for app in sorted(platform.iterdir()):
        if not app.is_dir() or app.is_symlink():
            continue
        versions = sorted(p.name for p in app.iterdir() if p.is_dir() and not p.is_symlink())
        if versions:
            rows.append({'platform': platform.name, 'application': app.name, 'versions': versions})
print(json.dumps(rows, sort_keys=True))'''
    # Verify returned data against the actual host filesystem; simply running
    # 'ls' in Ubuntu could falsely pass against an empty or wrong mount.
    expected = []
    for platform in sorted(applications.iterdir()):
        if not platform.is_dir() or platform.is_symlink():
            continue
        for app in sorted(platform.iterdir()):
            if not app.is_dir() or app.is_symlink():
                continue
            versions = sorted(p.name for p in app.iterdir() if p.is_dir() and not p.is_symlink())
            if versions:
                expected.append({'platform': platform.name, 'application': app.name, 'versions': versions})
    response = subprocess.run(
        [runner, 'run', '--rm', '--pull=never', '--platform', 'linux/amd64',
         '--network', 'none', '--mount',
         f'type=bind,source={applications},target=/host-applications,readonly',
         image_id, 'python3', '-c', guest_scan],
        env=environment, text=True, capture_output=True, check=True)
    try:
        reported = json.loads(response.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f'Ubuntu Applications bridge returned invalid JSON: {response.stdout!r}') from error
    if reported != expected:
        raise RuntimeError(
            'Ubuntu Applications bridge inventory does not match host installation: '
            f'guest={reported!r} host={expected!r}')
    print(f'[Ubuntu Applications Bridge] PASS: received {len(reported)} installed application entries from Ubuntu', flush=True)
    for row in reported:
        print(f"[Ubuntu Applications Bridge] {row['platform']}/{row['application']}: {', '.join(row['versions'])}", flush=True)
    print('[Ubuntu Headless Test] PASS: installed distro executed headlessly; no desktop, Steam, or FEX invoked', flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['setup', 'build', 'stage', 'test', 'all'])
    parser.add_argument('--repo-root', type=Path, default=Path(__file__).resolve().parents[2])
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
    if args.operation in ('setup', 'all'):
        setup(repo)
    if args.operation == 'setup':
        return 0
    for configuration in configurations:
        version = '2026-Debug' if configuration == 'Debug' else '2026'
        built = repo.parent / 'Builds/CreativeSuite/Built/cinterforge-os/linux' / version / 'Ubuntu'
        # Build never depends on a Steam client; Stage/Test do.
        if args.operation in ('build', 'all'):
            build(built)
        if args.operation == 'build':
            continue
        installed_built, installed, adapter = paths(repo, configuration=configuration)
        if args.operation in ('stage', 'all'):
            stage(installed_built, installed, adapter)
        if args.operation in ('test', 'all'):
            test(installed, adapter, source_directory(repo))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
