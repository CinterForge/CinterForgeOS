#!/usr/bin/env python3
"""Provision the complete recipe-declared Ardour Linux AMD64 build image."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys

SUITE = next(p for p in Path(__file__).resolve().parents if p.name == 'creativeSuite')
sys.path.insert(0, str(SUITE))
from container import build_cached_image, require_image_architecture


def prepare(built: Path) -> tuple[str, dict[str, str], str]:
    root = next((p for p in SUITE.parents if (p / 'Cinter').is_file()), None)
    if root is None:
        raise RuntimeError(f'Cannot locate CinterForge repository above {SUITE}')
    backend_path = SUITE / 'apps/cinterforge-os/linux'
    sys.path.insert(0, str(backend_path))
    from managed_backend import ensure_backend
    docker, env = ensure_backend(root.parent / 'Builds/CreativeSuite/Built/cinterforge-os/linux/2026/Ubuntu')
    recipe = json.loads((Path(__file__).parent / 'recipe.json').read_text(encoding='utf-8'))
    spec = recipe['linux_amd64_build_dependencies']
    base, image = spec['base_image'], spec['image']
    require_image_architecture(engine=docker, image=base, architecture='amd64', env=env, label='Ardour Linux setup')
    packages = spec['apt_packages']
    if not packages or not all(isinstance(p, str) and p.replace('-', '').replace('+', '').replace('.', '').isalnum() for p in packages):
        raise RuntimeError('Invalid Ardour Linux recipe apt package list')
    dockerfile_content = (f'FROM {base}\nENV DEBIAN_FRONTEND=noninteractive\n'
        'RUN set -eux; '
        "sed -i 's/^Types: deb$/Types: deb deb-src/' /etc/apt/sources.list.d/ubuntu.sources; "
        'apt-get update; '
        'missing=; for package in ' + ' '.join(packages) + '; do '
        'apt-cache show "$package" >/dev/null 2>&1 || missing="$missing $package"; '
        'done; if [ -n "$missing" ]; then '
        'printf "ERROR: Ubuntu 24.04 AMD64 missing recipe packages:%s\\n" "$missing" >&2; exit 41; fi; '
        'apt-get install -y --no-install-recommends '
        + ' '.join(packages) + '; apt-get build-dep -y ' + spec['apt_build_dep']
        + '; rm -rf /var/lib/apt/lists/*\n')
    folder = built / 'Dependencies'
    folder.mkdir(parents=True, exist_ok=True)
    dockerfile = folder / 'Dockerfile.builddeps'
    if not dockerfile.exists() or dockerfile.read_text() != dockerfile_content:
        dockerfile.write_text(dockerfile_content)
    build_cached_image(engine=docker, image=image, dockerfile=dockerfile,
                       context=folder, platform='linux/amd64', architecture='amd64',
                       env=env, label='Ardour Linux dependencies')
    print(f'[Ardour Linux setup] PROVISIONED {image} (amd64); Waf build still required for READY', flush=True)
    return docker, env, image


def main() -> int:
    root = next((p for p in SUITE.parents if (p / 'Cinter').is_file()), None)
    if root is None:
        raise RuntimeError(f'Cannot locate CinterForge repository above {SUITE}')
    version = os.environ.get('CINTER_INSTALL_VERSION') or os.environ.get('CINTER_APP_VERSION') or '2026'
    built = root.parent / 'Builds/CreativeSuite/Built/cfaudio/linux' / version
    prepare(built)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
