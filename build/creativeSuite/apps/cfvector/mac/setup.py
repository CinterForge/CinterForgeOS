#!/usr/bin/env python3
"""Prepare Inkscape's pinned macOS dependencies and upstream submodules.

The recipe defines required submodule paths and their build-input files.
Setup reuses populated directories, restores only missing gitlinks, and verifies
that the files consumed by upstream CMake actually exist.
"""
from __future__ import annotations
import json
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

def prepare_inkscape_submodules() -> None:
    """Restore upstream-pinned third-party source before CMake FetchContent.

    Inkscape includes 2geom and CapyPDF as Git submodules.  An archive or
    incomplete checkout can leave their directories empty; CMake cannot fill
    SOURCE_DIR-only FetchContent entries by itself.  Never fetch HEAD from an
    unrelated branch or substitute an incompatible system 2geom version.
    """
    import os

    # Resolve the maintained fork exactly as shared.mac.source_setup.validate does.
    # CINTER_HOPE_ROOT points at the workspace, not necessarily the repo.
    suite = Path(__file__).resolve().parents[3]
    recipe = json.loads(Path(__file__).with_name('recipe.json').read_text(encoding='utf-8'))
    checkout_name = recipe['source']['checkout_name']
    if Path(checkout_name).name != checkout_name:
        raise RuntimeError(f'Invalid checkout name: {checkout_name!r}')
    cinterforge = suite.parents[3]
    if cinterforge.name != 'CinterForge':
        raise RuntimeError(f'Cannot locate CinterForge root from {suite}')
    source = cinterforge.parent / checkout_name
    if not (source / 'CMakeLists.txt').is_file():
        raise RuntimeError(f'Inkscape upstream source missing: {source}')
    if not (source / '.git').exists() or not (source / '.gitmodules').is_file():
        raise RuntimeError(f'Inkscape requires its original Git checkout and .gitmodules '
                           f'to restore pinned 2geom/CapyPDF sources: {source}')
    recipe_path = Path(__file__).with_name('recipe.json')
    recipe = json.loads(recipe_path.read_text(encoding='utf-8'))
    required = recipe['source']['required_submodules']
    configured = subprocess.check_output(
        ['git', '-C', str(source), 'config', '-f', '.gitmodules', '--get-regexp',
         r'^submodule\..*\.path$'], text=True,
    )
    registered_paths = {line.split(None, 1)[1].strip() for line in configured.splitlines()}

    for requirement in required:
        relative = requirement['path']
        expected_files = requirement['required_files']
        if relative not in registered_paths:
            raise RuntimeError(f'Inkscape .gitmodules does not declare {relative}: {source}')

        folder = source / relative
        missing = [name for name in expected_files if not (folder / name).is_file()]
        if not missing:
            print(f'[Creative Suite mac][dependencies] Reuse pinned submodule {folder}', flush=True)
            continue

        print(f'[Creative Suite mac][dependencies] Restore pinned submodule {relative}', flush=True)
        run('git', '-C', str(source), 'submodule', 'update', '--init', '--', relative)
        missing = [name for name in expected_files if not (folder / name).is_file()]
        if missing:
            status = subprocess.run(
                ['git', '-C', str(source), 'submodule', 'status', '--', relative],
                capture_output=True, text=True,
            )
            raise RuntimeError(
                f'Pinned submodule {relative} is missing upstream build inputs '
                f'{missing} after git submodule update. Git status: '
                f'{status.stdout.strip() or status.stderr.strip() or "unavailable"}. '
                f'Check the pinned gitlink, checkout and submodule URL in {source}.'
            )
        print(f'[Creative Suite mac][dependencies] READY pinned submodule {folder}', flush=True)

def recover_craft_gettext() -> None:
    """Resume an interrupted private gettext image after Finder metadata collision.

    KDE Craft cannot qmerge its staged gettext image while Finder .DS_Store
    files inhabit either the package image or the prefix. Retain all compiled
    artifacts, remove only Finder metadata, and perform Craft's native merge.
    """
    built_root = os.environ.get('CINTER_BUILT_ROOT')
    if not built_root:
        return
    craft_root = Path(built_root).expanduser().resolve() / 'Dependencies/KDECraft/Root'
    if not craft_root.is_dir():
        return
    image = craft_root / 'build/libs/gettext/image-RelWithDebInfo-1.0'
    staged = image.joinpath(*craft_root.parts[1:])
    if not (staged / 'share/doc/gettext').is_dir():
        return
    for directory, dirs, files in os.walk(craft_root):
        if '.DS_Store' in dirs:
            raise RuntimeError(f'Unexpected directory named .DS_Store: {directory}')
        if '.DS_Store' in files:
            metadata = Path(directory) / '.DS_Store'
            if metadata.is_symlink() or not metadata.is_file():
                raise RuntimeError(f'Unexpected Finder metadata type: {metadata}')
            metadata.unlink()
    craft_script = craft_root / 'craft-tmp/bin/craft.py'
    if not craft_script.is_file():
        raise RuntimeError(f'Interrupted Craft gettext image lacks Craft runner: {craft_script}')
    print('[cfvector][mac] Resume Craft gettext from compiled image (qmerge)', flush=True)
    run(sys.executable, str(craft_script), '--qmerge', 'libs/gettext')


def main() -> None:
    validate_toolchain("cfvector")
    recover_craft_gettext()
    prepare_inkscape_submodules()
    provision_brew(('bdw-gc', 'gsl', 'icu4c@79', 'potrace', 'cairomm', 'pangomm', 'graphene', 'gtk4', 'gtkmm4', 'dbus'))
    # The native macOS bundle launches Inkscape inside a private D-Bus session.
    # Prepare its daemon, session runner, and configuration during Setup rather
    # than discovering a missing Homebrew dependency during Build packaging.
    dbus_prefix = Path(subprocess.check_output(
        ['brew', '--prefix', 'dbus'], text=True).strip())
    for relative in ('bin/dbus-daemon', 'bin/dbus-run-session',
                     'share/dbus-1/session.conf'):
        required = dbus_prefix / relative
        if not required.is_file():
            raise RuntimeError(f'cfvector private D-Bus prerequisite missing: {required}')
    print(f'[cfvector][mac] READY private D-Bus session tools: {dbus_prefix}', flush=True)
    provision_pkg_config('bdw-gc', 'bdw-gc')
    provision_pkg_config('gsl', 'gsl')
    for formula, module in (('cairomm', 'cairomm-1.16'),
                            ('pangomm', 'pangomm-2.48'),
                            ('graphene', 'graphene-1.0'),
                            ('gtk4', 'gtk4'),
                            ('gtkmm4', 'gtkmm-4.0')):
        provision_pkg_config(formula, module)
    run('pkg-config', '--atleast-version=4.14.0', 'gtk4')
    run('pkg-config', '--atleast-version=4.13.3', 'gtkmm-4.0')
    run('pkg-config', '--atleast-version=2.78.1', 'glibmm-2.68')
    run('pkg-config', '--modversion', 'giomm-2.68')
    potrace_prefix = Path(subprocess.check_output(
        ['brew', '--prefix', 'potrace'], text=True
    ).strip())
    if not (potrace_prefix / 'include/potracelib.h').is_file():
        raise RuntimeError(f'Potrace development header missing under {potrace_prefix}')
    if not any((potrace_prefix / 'lib' / name).is_file() for name in
               ('libpotrace.dylib', 'libpotrace.a')):
        raise RuntimeError(f'Potrace library missing under {potrace_prefix}')
    print(f'[Creative Suite mac][dependencies] READY Potrace library: {potrace_prefix}', flush=True)
    icu_prefix = Path(subprocess.check_output(
        ['brew', '--prefix', 'icu4c@79'], text=True
    ).strip())
    icu_pc = icu_prefix / 'lib/pkgconfig/icu-uc.pc'
    if not icu_pc.is_file():
        raise RuntimeError(f'ICU metadata missing: {icu_pc}')
    env = __import__('os').environ.copy()
    env['PKG_CONFIG_PATH'] = __import__('os').pathsep.join(filter(None, (
        str(icu_pc.parent), env.get('PKG_CONFIG_PATH', '')
    )))
    run('pkg-config', '--modversion', 'bdw-gc')
    subprocess.run(['pkg-config', '--atleast-version=79', 'icu-uc'], env=env, check=True)
    print('[Creative Suite mac][dependencies] READY icu-uc ' +
          subprocess.check_output(['pkg-config', '--modversion', 'icu-uc'],
                                  env=env, text=True).strip(), flush=True)

if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, KeyError, ValueError, subprocess.CalledProcessError) as exc:
        print(f'[Creative Suite mac][setup] ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
