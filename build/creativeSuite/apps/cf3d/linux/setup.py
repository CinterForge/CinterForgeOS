#!/usr/bin/env python3
"""Provision Blender 5.0 Linux AMD64 source-build dependencies, not a binary Blender."""
from __future__ import annotations
import os
import subprocess
from pathlib import Path
import sys

SUITE = next(p for p in Path(__file__).resolve().parents if p.name == 'creativeSuite')
sys.path.insert(0, str(SUITE))
from container import build_cached_image, require_image_architecture, run_mounted_image

IMAGE = 'cinterforge-cf3d-source:ubuntu24.04-amd64'
PACKAGES = (
    'build-essential', 'gcc-14', 'g++-14', 'cmake', 'ninja-build', 'git', 'git-lfs', 'python3',
    'pkg-config', 'libx11-dev', 'libxxf86vm-dev', 'libxcursor-dev',
    'libxi-dev', 'libxrandr-dev', 'libxinerama-dev', 'libxfixes-dev',
    'libwayland-dev', 'wayland-protocols', 'libxkbcommon-dev',
    'libegl-dev', 'libgl-dev', 'libdbus-1-dev', 'libpulse-dev',
    'libasound2-dev', 'libudev-dev', 'libfontconfig1-dev', 'libfreetype-dev',
    'libtbb-dev', 'libzstd-dev', 'libssl-dev', 'patchelf', 'file', 'rsync',
    'libvulkan-dev', 'libdecor-0-dev', 'libx11-xcb-dev', 'libxcb1-dev',
    'libxcb-keysyms1-dev', 'libxcb-randr0-dev', 'libxcb-xfixes0-dev',
    'libxcb-shape0-dev', 'libxcb-shm0-dev', 'libxcb-icccm4-dev',
    'libxcb-image0-dev', 'libxcb-util-dev', 'libxcb-cursor-dev',
    'libxcb-xinerama0-dev', 'libxcb-xinput-dev',
)
LIB_REPO = 'https://projects.cf3d.org/cf3d/lib-linux_x64.git'
SOURCE_REPO = 'https://projects.cf3d.org/cf3d/cf3d.git'
SOURCE_TAG = 'v5.0.1'  # Last official corrective release in the Blender 5.0 series.


def prepare_release_source(built: Path) -> Path:
    """Checkout a fixed released source without changing ~/Source/cf3d.

    Keep every fetched source and revision inside the application's external Built
    hierarchy. Never use whatever moving HEAD happens to be in the developer tree.
    """
    source = built / 'Source'
    if not (source / '.git').exists():
        if source.exists() and any(source.iterdir()):
            raise RuntimeError(f'Foreign/non-Git Blender release source directory: {source}')
        source.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['git', 'clone', '--branch', SOURCE_TAG, '--single-branch',
                        SOURCE_REPO, str(source)], check=True)
    remote = subprocess.run(['git', '-C', str(source), 'remote', 'get-url', 'origin'],
                            capture_output=True, text=True, check=True).stdout.strip()
    if remote != SOURCE_REPO:
        raise RuntimeError(f'Unexpected Blender source origin: {remote}')
    subprocess.run(['git', '-C', str(source), 'fetch', 'origin',
                    'refs/tags/' + SOURCE_TAG + ':refs/tags/' + SOURCE_TAG], check=True)
    tagged = subprocess.run(['git', '-C', str(source), 'rev-parse', SOURCE_TAG + '^{commit}'],
                            capture_output=True, text=True, check=True).stdout.strip()
    head = subprocess.run(['git', '-C', str(source), 'rev-parse', 'HEAD'],
                          capture_output=True, text=True, check=True).stdout.strip()
    if head != tagged:
        raise RuntimeError(f'Blender release source HEAD {head} differs from {SOURCE_TAG} {tagged}; do not overwrite source')
    dirty = subprocess.run(['git', '-C', str(source), 'status', '--porcelain',
                            '--untracked-files=no'], capture_output=True, text=True, check=True).stdout.strip()
    if dirty:
        raise RuntimeError(f'Blender release source has local modifications: {source}')
    subprocess.run(['git', '-C', str(source), 'lfs', 'pull'], check=True)
    print(f'[cf3d][linux] PINNED SOURCE {SOURCE_TAG} {tagged} at {source}', flush=True)
    return source

def pinned_library_commit(source: Path) -> str:
    """Use Blender's OWN pinned lib/linux_x64 gitlink, not a guessed release branch."""
    result = subprocess.run(
        ['git', '-C', str(source), 'ls-tree', 'HEAD', 'lib/linux_x64'],
        capture_output=True, text=True, check=True,
    )
    fields = result.stdout.strip().split()
    if len(fields) != 4 or fields[0] != '160000' or fields[1] != 'commit' or fields[3] != 'lib/linux_x64':
        raise RuntimeError(
            f'Blender source HEAD has no pinned lib/linux_x64 gitlink: {source}; '
            'cannot safely pair source with upstream precompiled dependencies'
        )
    sha = fields[2]
    if len(sha) != 40 or any(c not in '0123456789abcdef' for c in sha):
        raise RuntimeError(f'Invalid upstream Blender dependency SHA: {sha!r}')
    return sha


def prepare(built: Path):
    root = Path(os.environ['CINTER_HOPE_ROOT']).resolve()
    source = prepare_release_source(built)
    if not (source / 'CMakeLists.txt').is_file():
        raise RuntimeError(f'Released Blender source missing: {source}')
    library_sha = pinned_library_commit(source)
    print(f'[cf3d][linux] Source-pinned lib/linux_x64 SHA: {library_sha}', flush=True)
    sys.path.insert(0, str(SUITE / 'apps/cinterforge-os/linux'))
    from managed_backend import ensure_backend
    docker, env = ensure_backend(root.parent / 'Builds/CreativeSuite/Built/cinterforge-os/linux/2026/Ubuntu')
    base = 'cinterforge-ubuntu:24.04-amd64'
    require_image_architecture(engine=docker, image=base, architecture='amd64', env=env, label='Blender Linux setup')
    dependencies = built / 'Dependencies'
    dependencies.mkdir(parents=True, exist_ok=True)
    dockerfile = dependencies / 'Dockerfile.cf3d'
    content = (f'FROM {base}\nENV DEBIAN_FRONTEND=noninteractive\n'
               'RUN apt-get update && apt-get install -y --no-install-recommends '
               + ' '.join(PACKAGES) + ' && rm -rf /var/lib/apt/lists/*\n')
    if not dockerfile.exists() or dockerfile.read_text() != content:
        dockerfile.write_text(content)
    build_cached_image(engine=docker, image=IMAGE, dockerfile=dockerfile,
                       context=dependencies, platform='linux/amd64', architecture='amd64',
                       env=env, label='Blender Linux source prerequisites')
    # Setup must prove the built image contains the compiler required by the
    # supplied Blender source. Image creation alone is not build readiness.
    # This catches a stale Docker layer or a setup script from an older patch
    # before the user reaches CMake in Build.
    verify_toolchain = r'''set -euo pipefail
[ -x /usr/bin/gcc-14 ] && [ -x /usr/bin/g++-14 ] || {
    echo '[cf3d][linux] SETUP FAILED: GCC/G++ 14 absent from built image' >&2
    exit 6
}
/usr/bin/gcc-14 -dumpfullversion
/usr/bin/g++-14 -dumpfullversion
/usr/bin/gcc-14 -dumpfullversion | awk -F. '$1 >= 14 { valid=1 } END { exit !valid }'
/usr/bin/g++-14 -dumpfullversion | awk -F. '$1 >= 14 { valid=1 } END { exit !valid }'
echo '[cf3d][linux] GCC/G++ 14 verified inside build image'
'''
    run_mounted_image(engine=docker, image=IMAGE, platform='linux/amd64',
                      mounts=(), variables={},
                      command=('/bin/bash', '-ec', verify_toolchain), env=env,
                      label='Blender Linux: verify GCC/G++ 14 build image')
    library = dependencies / 'lib-linux_x64'
    library.mkdir(exist_ok=True)
    script = r'''set -euo pipefail
if [ ! -d /deps/lib-linux_x64/.git ]; then
    if [ -n "$(ls -A /deps/lib-linux_x64)" ]; then
        echo '[cf3d][linux] Incomplete or foreign dependency checkout' >&2; exit 7
    fi
    git clone "$BLENDER_LIBRARY_URL" /deps/lib-linux_x64
fi
cd /deps/lib-linux_x64
git fetch origin "$BLENDER_LIBRARY_SHA"
git checkout --detach "$BLENDER_LIBRARY_SHA"
git lfs pull
[ "$(git rev-parse HEAD)" = "$BLENDER_LIBRARY_SHA" ] || {
    echo '[cf3d][linux] Dependency checkout does not match source-pinned gitlink' >&2; exit 8;
}
[ -s /deps/lib-linux_x64/python/bin/python3 ] || [ -d /deps/lib-linux_x64/python ] || {
    echo '[cf3d][linux] Upstream Linux x64 libraries incomplete' >&2; exit 8;
}
printf '[cf3d][linux] LIBRARIES revision=%s\n' "$(git rev-parse HEAD)"
'''
    run_mounted_image(engine=docker, image=IMAGE, platform='linux/amd64',
                      mounts=((dependencies, '/deps', False),),
                      variables={'BLENDER_LIBRARY_SHA': library_sha, 'BLENDER_LIBRARY_URL': LIB_REPO},
                      command=('/bin/bash', '-ec', script), env=env,
                      label='Blender Linux: upstream version-matched build libraries')
    print('[cf3d][linux] SETUP PROVISIONED; full source Build still required before READY', flush=True)
    return docker, env


def main():
    root = Path(os.environ['CINTER_HOPE_ROOT']).resolve()
    version = os.environ.get('CINTER_INSTALL_VERSION', '2026')
    prepare(root.parent / 'Builds/CreativeSuite/Built/cf3d/linux' / version)

if __name__ == '__main__':
    main()
