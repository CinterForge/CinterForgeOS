#!/usr/bin/env python3
"""Compile Linux AMD64 Ardour with the existing headless Colima/Ubuntu backend.

Build only. Dependencies and upstream checkout are stored in the generated
Creative Suite Builds tree; no legacy LinuxKit or secondary VM is started.
"""
from __future__ import annotations
import os
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
import subprocess
import sys
import time
import threading

_SUITE = next(p for p in Path(__file__).resolve().parents if p.name == 'creativeSuite')
if str(_SUITE) not in sys.path:
    sys.path.insert(0, str(_SUITE))
from shared.source.source import resolve_maintained_source
from shared.build.upstream_patches import sync_patch_catalog
from shared.build.build_runtime import run_visible, validate_fork, build_lock
from shared.source.source import resolve_revision
from build import reusable_installation, mark_successful_install
from container import require_image_architecture, build_cached_image, run_mounted_image

from apps.cfaudio.linux.setup import prepare as prepare_dependencies


def _run_visible(command: list[str], *, env: dict[str, str], label: str) -> None:
    run_visible(command, env=env, label=f"Ardour Linux Build: {label}")



def _linux_container_build() -> None:
    """Incremental Ardour Waf build inside the existing Ubuntu AMD64 image.

    Use Python for checkout, revision validation, configure, build and install.
    Preserve the prepared Waf build directory on both success and failure.
    """
    import json
    import os
    from pathlib import Path
    import shutil
    import subprocess

    work = Path('/work')
    checkout = work / 'cfaudio'
    maintained = Path('/maintained-cfaudio')
    commit = os.environ['ARDOUR_COMMIT']
    signature = os.environ['ARDOUR_SIGNATURE']
    marker = work / 'configured.commit'

    def run(*argv: str, cwd: Path = checkout) -> None:
        command = [str(arg) for arg in argv]
        subprocess.run(command, cwd=cwd, check=True)

    try:
        # Copy once, including upstream Git metadata and local source edits.
        # Never re-clone, reset, or erase partial Waf artifacts on retries.
        if not (checkout / '.git').is_dir():
            checkout.mkdir(parents=True, exist_ok=True)
            shutil.copytree(maintained, checkout, dirs_exist_ok=True, symlinks=True)
        current = subprocess.check_output(['git', 'rev-parse', 'HEAD'],
                                          cwd=checkout, text=True).strip()
        if current != commit:
            raise RuntimeError(f'Maintained Ardour revision changed during build: '
                               f'{current} != {commit}')
        flags = json.loads(os.environ['ARDOUR_CONFIGURE_FLAGS'])
        if not isinstance(flags, list) or not all(isinstance(arg, str) for arg in flags):
            raise RuntimeError('Invalid Ardour configure argv received from host')
        if not (checkout / 'build/c4che/_cache.py').is_file() or not marker.is_file() \
                or marker.read_text().strip() != signature:
            print(f'[Ardour Linux Build] CONFIGURE {commit}', flush=True)
            run('./waf', 'configure', '--prefix=/work/Install', *flags)
            marker.write_text(signature + '\n')
        else:
            print('[Ardour Linux Build] REUSE Waf configuration', flush=True)
        print('[Ardour Linux Build] COMPILE (incremental Waf)', flush=True)
        run('./waf', '-j', str(len(os.sched_getaffinity(0))))
        print('[Ardour Linux Build] INSTALL', flush=True)
        run('./waf', 'install')
    finally:
        # The container builds as root; leave generated contents writable by
        # the host even after a failed configure or compilation.
        subprocess.run(['chown', '-R', f"{os.environ['HOST_UID']}:{os.environ['HOST_GID']}",
                        str(work)], check=True)


def _linux_container_source() -> str:
    """Transport the current Python build function, without creating scripts."""
    import inspect
    return inspect.getsource(_linux_container_build) + '\n_linux_container_build()\n'


def compile_ardour(built: Path) -> Path:
    """Serialize builds sharing a versioned output and preserve incremental Waf state."""
    sync_patch_catalog(url="https://ardour.org/files/patch-info.html",
                       destination=built / "Dependencies" / "Patches")
    with build_lock(built):
        return _compile_ardour_locked(built)


def _compile_ardour_locked(built: Path) -> Path:
    root = _SUITE.parents[2]
    docker, env, deps_image = prepare_dependencies(built)
    built.mkdir(parents=True, exist_ok=True)
    work = built / 'SourceBuild'
    fork = resolve_maintained_source(root, Path(__file__).with_name('recipe.json'))
    work.mkdir(exist_ok=True)
    revision = os.environ.get('CINTER_ARDOUR_REVISION', 'HEAD')
    if any(c in revision for c in '\n\r\0') or not revision:
        raise RuntimeError('Invalid Ardour source revision')
    # Resolve the exact maintained-source commit before deciding to rebuild. This
    # records *what* was installed, not merely the presence of a stale directory.
    commit = resolve_revision(fork, revision)
    recipe_path = Path(__file__).parent / 'recipe.json'
    recipe_bytes = recipe_path.read_bytes()
    recipe = json.loads(recipe_bytes)
    configure_flags = [v for v in recipe['configure']['argv'][2:] if not v.startswith('--prefix=')]
    # A source-only cache marker wrongly reuses old builds after configuration,
    # dependencies or build-toolchain changes. Keep this in the existing marker.
    signature = hashlib.sha256(json.dumps({
        'source_commit': commit,
        'configure_flags': configure_flags,
        'recipe_sha256': hashlib.sha256(recipe_bytes).hexdigest(),
        'dependency_image': deps_image,
        'baseline': 'ubuntu24.04-amd64',
    }, sort_keys=True).encode()).hexdigest()
    marker = work / 'install.commit'
    installation = work / 'Install'
    if reusable_installation(installation=installation, marker=marker, revision=signature,
                             label='Ardour Linux Build'):
        return installation
    print(f'[Ardour Linux Build] Source commit: {commit}; build signature: {signature}', flush=True)
    print(f'[Ardour Linux Build] Compiling local maintained source commit {commit} on Ubuntu 24.04 AMD64', flush=True)
    run_mounted_image(
        engine=docker, image=deps_image, platform='linux/amd64',
        mounts=((work, '/work', False), (fork, '/maintained-cfaudio', True)),
        variables={
            'ARDOUR_COMMIT': commit,
            'ARDOUR_SIGNATURE': signature,
            'ARDOUR_CONFIGURE_FLAGS': json.dumps(configure_flags),
            'HOST_UID': str(os.getuid()), 'HOST_GID': str(os.getgid()),
        },
        command=('python3', '-u', '-c', _linux_container_source()), env=env,
        label='Ardour Linux Build: Waf configure/build/install',
    )
    installation = work / 'Install'
    if not installation.is_dir():
        raise RuntimeError('Ardour Waf install did not create an installation')
    verify_portable_ui_config(installation)
    mark_successful_install(installation=installation, marker=marker, revision=signature)
    print(f'[Ardour Linux Build] SAVED successful installation commit={commit}', flush=True)
    return installation



# These are installed by Ardour's Waf CONFDIR, NOT its DATADIR. A system
# --configdir=/etc makes the application appear built but drops these files
# outside the portable installation, where CFOS/FEX cannot access them.
# Waf installs upstream ardour.menus and ardour.keys; any CinterForge
# rebranding belongs to packaging, not to this installation check.
_REQUIRED_UI_CONFIG = (
    'clearlooks.rc', 'clearlooks.ardoursans.rc', 'default_ui_config',
    'ardour.menus', 'ardour.keys', 'system_config',
)


def verify_portable_ui_config(installation: Path) -> None:
    config_dir = installation / 'etc' / 'ardour9'
    missing = [name for name in _REQUIRED_UI_CONFIG if not (config_dir / name).is_file()]
    if missing:
        raise RuntimeError(
            f'Ardour portable UI configuration missing in {config_dir}: {missing}. '
            'Waf must configure with --configdir=/work/Install/etc and reinstall.'
        )


def publish_linux_payload(installation: Path, built: Path) -> Path:
    """Produce the exact Output tree consumed by shared Build -> ZIP -> Stage.

    Waf installs into SourceBuild/Install for incremental reuse.  That private
    build directory is never the public distribution payload.  Copy the *whole*
    installed prefix (including plugins, resources and relative symlinks), then
    atomically make it available as Built/<app>/<platform>/<version>/Output.
    """
    if not installation.is_dir() or not any(installation.iterdir()):
        raise RuntimeError(f'Ardour Linux install missing or empty: {installation}')
    verify_portable_ui_config(installation)
    executable = installation / 'lib/ardour9'
    # Upstream Waf still installs the versioned engine as ardour-9.x.
    # The product rename is applied during packaging, not by Waf install.
    engines = sorted(p for pattern in ('ardour-[0-9]*', 'cfaudio-[0-9]*')
                     for p in executable.glob(pattern) if p.is_file())
    if len(engines) != 1 or not (installation / 'bin/ardour9').exists():
        raise RuntimeError(f'Incomplete Ardour Linux installation at {installation}: '
                           f'engine candidates={engines}, bin/ardour9 missing={not (installation / "bin/ardour9").exists()}')
    output = built / 'Output'
    built.mkdir(parents=True, exist_ok=True)
    # Package and validate in a temporary directory first. An unsuccessful
    # dependency scan must not corrupt the previous completed Output.
    with tempfile.TemporaryDirectory(prefix='cfaudio-output-', dir=built) as staging_dir:
        staged = Path(staging_dir) / 'Output'
        shutil.copytree(installation, staged, symlinks=True)
        docker, docker_env, dependency_image = prepare_dependencies(built)
        packager = _SUITE / 'shared/linux/package_runtime.py'
        if not packager.is_file():
            raise RuntimeError(f'Missing shared Linux runtime packager: {packager}')
        run_mounted_image(
            engine=docker, image=dependency_image, platform='linux/amd64',
            mounts=((staged, '/payload', False), (packager, '/recipe/package_runtime.py', True)),
            variables={}, command=('python3', '/recipe/package_runtime.py'), env=docker_env,
            label='Ardour Linux Build: installed runtime dependency closure',
        )
        verify_portable_ui_config(staged)
        # Only replace the published tree after all required packaging passes.
        # Preserve the previous one until the new tree is known complete.
        previous = built / 'Output.previous'
        if previous.exists():
            if not previous.is_dir() or previous.is_symlink():
                raise RuntimeError(f'Unexpected old Ardour Output backup: {previous}')
            shutil.rmtree(previous)
        if output.exists():
            if not output.is_dir() or output.is_symlink():
                raise RuntimeError(f'Unexpected Ardour Output entry: {output}')
            output.rename(previous)
        try:
            staged.rename(output)
        except BaseException:
            if previous.exists() and not output.exists():
                previous.rename(output)
            raise
        if previous.exists():
            shutil.rmtree(previous)
    print(f'[Ardour Linux Build] READY shared lifecycle payload: {output}', flush=True)
    return output


def main() -> int:
    root = Path(os.environ['CINTER_HOPE_ROOT']).resolve()
    version = os.environ.get('CINTER_APP_VERSION') or os.environ.get('CINTER_INSTALL_VERSION')
    if not version:
        raise RuntimeError('Cinter must resolve CINTER_APP_VERSION before Ardour Linux build')
    suite = next(p for p in Path(__file__).resolve().parents if p.name == 'creativeSuite')
    import sys
    sys.path.insert(0, str(suite))
    from lifecycle import version_value
    # CINTER_INSTALL_VERSION is the product version selected by Cinter (e.g.
    # 2026). CINTER_APP_VERSION is the *invocation timestamp*, not the output
    # directory name. Never compile into a timestamp folder and then ask the
    # shared lifecycle to package the different product-version directory.
    product_version = version_value(os.environ.get('CINTER_INSTALL_VERSION') or version)
    built = root.parent / 'Builds' / 'CreativeSuite' / 'Built' / 'cfaudio' / 'linux' / product_version
    declared_built = os.environ.get('CINTER_BUILT_ROOT')
    if declared_built and Path(declared_built).resolve() != built.resolve():
        raise RuntimeError(f'Ardour Linux build output disagrees with Cinter: {built} != {declared_built}')
    install = compile_ardour(built)
    publish_linux_payload(install, built)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
