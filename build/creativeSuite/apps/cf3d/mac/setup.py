#!/usr/bin/env python3
"""Prepare Blender's upstream Apple Silicon dependency stack for source builds."""
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

# Standalone recipe entrypoints use the lightweight central path resolver.
sys.path.insert(0, str(Path(__file__).resolve().parents[6]))
from cf.io.CinterIO import cinterforgeSiblingRepoPath, cinterforgeBuildsRootPath


def main():
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise RuntimeError('Blender mac source build requires macOS on Apple Silicon')
    source = cinterforgeSiblingRepoPath('CinterForge3D')
    if not (source / 'CMakeLists.txt').is_file():
        raise RuntimeError(f'Blender source not found at {source}; use the supplied cf3d source checkout')
    built = cinterforgeBuildsRootPath() / 'CreativeSuite/Built/cf3d/mac' / os.environ.get('CINTER_INSTALL_VERSION', '2026')
    dependency = built / 'Dependencies/lib-macos_arm64'
    for tool in ('git', 'cmake', 'ninja', 'git-lfs', 'xcrun', 'xcodebuild'):
        if shutil.which(tool) is None:
            raise RuntimeError(f'Missing macOS Blender build tool {tool}; provision through Homebrew/Xcode')
    subprocess.run(['xcodebuild', '-version'], check=True)
    subprocess.run(['xcrun', '--sdk', 'macosx', '--show-sdk-path'], check=True)
    subprocess.run(['git', 'lfs', 'version'], check=True)
    # Blender publishes its own complete, version-matched binary dependency stack.
    # These are build inputs, not a prebuilt Blender application.
    if not (dependency / '.git').exists():
        dependency.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['git', 'clone', 'https://projects.blender.org/blender/lib-macos_arm64.git', str(dependency)], check=True)
    subprocess.run(['git', '-C', str(dependency), 'lfs', 'pull'], check=True)
    if not (dependency / '.git').exists() or len(list(dependency.iterdir())) < 4:
        raise RuntimeError(f'Blender macOS dependency checkout incomplete: {dependency}')
    print(f'[cf3d][mac] SETUP READY source={source} dependencies={dependency}', flush=True)
    print('[cf3d][mac] Build completion is required before recipe READY', flush=True)


if __name__ == '__main__':
    main()
