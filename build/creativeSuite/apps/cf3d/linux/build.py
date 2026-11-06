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


def compile_ardour(built: Path) -> Path:
    """Serialize builds sharing a versioned output and preserve incremental Waf state."""
    sync_patch_catalog(url="https://ardour.org/files/patch-info.html",
                       destination=built / "Dependencies" / "Patches")
    with build_lock(built):
        return _compile_ardour_locked(built)


def _compile_ardour_locked(built: Path) -> Path:
    root = Path(os.environ['CINTER_HOPE_ROOT']).resolve()
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
    import shlex
    flags = " ".join(shlex.quote(v) for v in configure_flags)
    script = r'''set -euo pipefail
# Keep the checkout and partial Waf build when configuration/compilation fails.
# Docker runs as root, so restore ownership even on errors.
trap 'chown -R "$HOST_UID:$HOST_GID" /work' EXIT
if [ ! -d /work/cfaudio/.git ]; then
    mkdir -p /work/cfaudio
    cp -a /maintained-cfaudio/. /work/cfaudio/
fi
cd /work/cfaudio
# The build copy must represent the maintained source exactly; do not fetch or
# switch to a different revision. Copying includes local source modifications.
if [ "$(git rev-parse HEAD)" != "$ARDOUR_COMMIT" ]; then
    echo "Maintained source revision changed during build" >&2; exit 1
fi
# Keep build/ and Waf task caches across invocations. Configure is necessary
# only for a first build or a new source revision.
if [ ! -f build/c4che/_cache.py ] || [ ! -f /work/configured.commit ] || [ "$(cat /work/configured.commit)" != "$ARDOUR_SIGNATURE" ]; then
    echo "[Ardour Linux Build] CONFIGURE ${ARDOUR_COMMIT}"
    ./waf configure --prefix=/work/Install __CINTER_FLAGS__
    printf '%s\n' "$ARDOUR_SIGNATURE" > /work/configured.commit
else
    echo "[Ardour Linux Build] REUSE Waf configuration"
fi
echo "[Ardour Linux Build] COMPILE (Waf incremental cache retained)"
./waf -j "$(nproc)"
echo "[Ardour Linux Build] INSTALL"
./waf install
'''
    script = script.replace('__CINTER_FLAGS__', flags)
    print(f'[Ardour Linux Build] Compiling local maintained source commit {commit} on Ubuntu 24.04 AMD64', flush=True)
    run_mounted_image(
        engine=docker, image=deps_image, platform='linux/amd64',
        mounts=((work, '/work', False), (fork, '/maintained-cfaudio', True)),
        variables={'ARDOUR_COMMIT': commit, 'ARDOUR_SIGNATURE': signature, 'HOST_UID': str(os.getuid()),
                   'HOST_GID': str(os.getgid())},
        command=('/bin/bash', '-ec', script), env=env,
        label='Ardour Linux Build: Waf configure/build/install',
    )
    installation = work / 'Install'
    if not installation.is_dir():
        raise RuntimeError('Ardour Waf install did not create an installation')
    verify_portable_ui_config(installation)
    mark_successful_install(installation=installation, marker=marker, revision=signature)
    print(f'[Ardour Linux Build] SAVED successful installation commit={commit}', flush=True)
    return installation



# Use the filenames installed by the maintained Ardour Waf scripts.
# Product branding does not rename the UI menu and keybinding resources.
# These are installed by Ardour's Waf CONFDIR, NOT its DATADIR. A system
# --configdir=/etc makes the application appear built but drops these files
# outside the portable installation, where CFOS/FEX cannot access them.
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
    engines = sorted(p for p in executable.glob('ardour-[0-9]*') if p.is_file())
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
    server_root = root / 'CinterForgeServer'
    if str(server_root) not in sys.path:
        sys.path.insert(0, str(server_root))
    from cf.io.CinterIO import cinterforgeBuildsRootPath
    built = cinterforgeBuildsRootPath(root) / 'CreativeSuite' / 'Built' / 'cfaudio' / 'linux' / product_version
    declared_built = os.environ.get('CINTER_BUILT_ROOT')
    if declared_built and Path(declared_built).resolve() != built.resolve():
        raise RuntimeError(f'Ardour Linux build output disagrees with Cinter: {built} != {declared_built}')
    install = compile_ardour(built)
    publish_linux_payload(install, built)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
