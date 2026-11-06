#!/usr/bin/env python3
"""Application-owned native macOS dependency preparation."""
from __future__ import annotations
import shutil
import re
import os
import hashlib
import urllib.request
import tarfile
import subprocess
import sys
from pathlib import Path

SUITE_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SUITE_ROOT))
from shared.mac.dependencies import run, provision_brew as _shared_provision_brew, provision_ecm, provision_pkg_config

def validate_toolchain(app: str) -> None:
    from shared.mac.source_setup import validate
    validate(app)

provision_brew = _shared_provision_brew

def provision_rapidraw(source: Path) -> None:
    import tomllib

    toolchain_file = source / 'src-tauri' / 'rust-toolchain.toml'
    if not toolchain_file.is_file():
        raise RuntimeError(f'cfphoto upstream Rust toolchain pin missing: {toolchain_file}')
    with toolchain_file.open('rb') as handle:
        channel = tomllib.load(handle)['toolchain']['channel']
    if not shutil.which('rustup'):
        raise RuntimeError('rustup is required to install the upstream Rust toolchain')
    run('rustup', 'toolchain', 'install', channel)
    run('rustup', 'run', channel, 'rustc', '--version')

    # Node 22.12 from the log is below the explicit npm lockfile engine floor.
    # Use the Homebrew Node 24 LTS toolchain without changing global PATH.
    provision_brew(('node@24',))
    prefix = subprocess.check_output(['brew', '--prefix', 'node@24'], text=True).strip()
    node = Path(prefix) / 'bin' / 'node'
    npm = Path(prefix) / 'bin' / 'npm'
    if not node.is_file() or not npm.exists():
        raise RuntimeError(f'Node 24 binaries missing from {prefix}/bin')
    run(str(node), '--version')
    print(f'[Creative Suite mac][dependencies] cfphoto pinned Rust {channel}; Node: {node}', flush=True)

def main() -> None:
    validate_toolchain("cfphoto")
    # Match the validated source checkout; never derive it from HOME/Cinter.
    import json
    suite = Path(__file__).resolve().parents[3]
    recipe = json.loads(Path(__file__).with_name('recipe.json').read_text(encoding='utf-8'))
    checkout_name = recipe['source']['checkout_name']
    if Path(checkout_name).name != checkout_name:
        raise RuntimeError(f'Invalid checkout name: {checkout_name!r}')
    cinterforge = suite.parents[3]
    if cinterforge.name != 'CinterForge':
        raise RuntimeError(f'Cannot locate CinterForge root from {suite}')
    source = cinterforge.parent / checkout_name
    if not (source / 'src-tauri/rust-toolchain.toml').is_file():
        raise RuntimeError(f'cfphoto upstream source missing: {source}')
    provision_rapidraw(source)

if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, KeyError, ValueError, subprocess.CalledProcessError) as exc:
        print(f'[Creative Suite mac][setup] ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
