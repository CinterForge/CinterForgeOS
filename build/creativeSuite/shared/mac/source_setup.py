"""Recipe-driven macOS source/toolchain validation; no application policies."""
from __future__ import annotations
import json
import os
import platform
from pathlib import Path
import shutil
import subprocess

def validate(app: str) -> None:
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise RuntimeError('Native macOS source builds require Apple Silicon macOS')
    suite = Path(__file__).resolve().parents[2]
    recipe_file = suite / 'apps' / app / 'mac' / 'recipe.json'
    with recipe_file.open() as stream:
        recipe = json.load(stream)
    if recipe['identity']['application'] != app or recipe['identity']['platform'] != 'mac':
        raise RuntimeError(f'Invalid recipe identity: {recipe_file}')
    # Maintained forks are siblings of CinterForge, not of Cinter or $HOME.
    cinterforge = suite.parents[2]
    if cinterforge.name != 'CinterForge':
        raise RuntimeError(f'Cannot locate CinterForge root from {suite}')
    checkout_name = recipe.get('source', {}).get('checkout_name')
    if not isinstance(checkout_name, str) or Path(checkout_name).name != checkout_name:
        raise RuntimeError(f'Invalid maintained checkout name: {checkout_name!r}')
    source = cinterforge.parent / checkout_name
    build_definition = recipe.get('source', {}).get('build_definition', 'CMakeLists.txt')
    if not (source / build_definition).is_file():
        raise RuntimeError(f'Missing upstream source: {source / build_definition}')
    for command in recipe.get('preparation', {}).get('toolchain', []):
        if not shutil.which(command):
            raise RuntimeError(f'Missing build prerequisite: {command}')
    for cmd in (['xcodebuild', '-version'], ['xcrun', '--sdk', 'macosx', '--show-sdk-path']):
        subprocess.run(cmd, check=True)
    print(f'[Creative Suite mac] SOURCE {source}', flush=True)
    print('[Creative Suite mac] Toolchain validated; full upstream Build remains required', flush=True)

if __name__ == '__main__':
    import sys
    if len(sys.argv) != 2:
        raise SystemExit('usage: source_setup.py <app>')
    validate(sys.argv[1])
