#!/usr/bin/env python3
"""Native full-feature Blender source build, isolated under external Builds."""
import json
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
        raise RuntimeError('Blender/mac source build requires macOS arm64')
    source = cinterforgeSiblingRepoPath('CinterForge3D')
    if not (source / 'CMakeLists.txt').is_file():
        raise RuntimeError(f'Missing provided Blender source: {source}')
    built = Path(os.environ['CINTER_BUILT_ROOT']).resolve()
    library = built / 'Dependencies/lib-macos_arm64'
    if not (library / '.git').exists():
        raise RuntimeError('Missing upstream Blender Apple Silicon libraries; run ./Cinter setup cf3d mac')
    for tool in ('cmake', 'ninja', 'xcrun'):
        if not shutil.which(tool):
            raise RuntimeError(f'Missing Blender build tool: {tool}')
    build_dir = built / 'Build'
    output = built / 'Output'
    build_dir.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    configure = [
        'cmake', '-S', str(source), '-B', str(build_dir), '-G', 'Ninja',
        '-C', str(source / 'build_files/cmake/config/blender_release.cmake'),
        '-DCMAKE_OSX_ARCHITECTURES=arm64',
        '-DCMAKE_BUILD_TYPE=Release',
        '-DLIBDIR=' + str(library),
        '-DCMAKE_INSTALL_PREFIX=' + str(output),
        '-DWITH_INSTALL_PORTABLE=ON',
    ]
    print('[cf3d][mac] CONFIGURE full upstream release', flush=True)
    subprocess.run(configure, check=True)
    # Build readiness may only become true after successful CMake configuration
    # AND compilation. The shared recipe recorder uses setup_result.configured.
    recipe_path = Path(__file__).with_name('recipe.json')
    recipe = json.loads(recipe_path.read_text(encoding='utf-8'))
    recipe.setdefault('readiness', {}).setdefault('setup_result', {})['configured'] = True
    recipe['readiness']['configured'] = True
    recipe.setdefault('configure', {})['observed_result'] = {'configured': True}
    recipe_path.write_text(json.dumps(recipe, indent=2) + '\n', encoding='utf-8')
    print('[cf3d][mac] COMPILE upstream Blender', flush=True)
    subprocess.run(['cmake', '--build', str(build_dir), '--parallel', str(max(1, (os.cpu_count() or 2) // 2))], check=True)
    print('[cf3d][mac] INSTALL portable macOS bundle', flush=True)
    subprocess.run(['cmake', '--install', str(build_dir)], check=True)
    apps = sorted(output.rglob('Blender.app'))
    if len(apps) != 1 or not (apps[0] / 'Contents/MacOS/Blender').is_file():
        raise RuntimeError(f'Expected exactly one compiled Blender.app under {output}; got {apps}')
    destination = apps[0].with_name('CinterForge 3D.app')
    if destination.exists():
        shutil.rmtree(destination)
    apps[0].rename(destination)
    print(f'[cf3d][mac] BUILD READY {destination}', flush=True)


if __name__ == '__main__':
    main()
