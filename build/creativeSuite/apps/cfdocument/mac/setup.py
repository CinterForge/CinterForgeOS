#!/usr/bin/env python3
"""Prepare Scribus macOS source dependencies before the Build phase.

Scribus requires Poppler >= 24.02 and optionally uses PoDoFo for embedded
PDFs in Illustrator files. Keep that upstream feature enabled by provisioning
both rather than accepting the optional-feature downgrade.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys


def execute(args: list[str], *, env: dict[str, str] | None = None) -> None:
    print('[cfdocument][mac][setup] ' + ' '.join(args), flush=True)
    subprocess.run(args, check=True, env=env)


def installed_formula(formula: str) -> bool:
    return subprocess.run(
        ['brew', 'list', '--versions', formula],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def pkg_config_environment() -> dict[str, str]:
    """Expose Homebrew's keg-only ICU metadata without changing global settings."""
    prefix = subprocess.check_output(
        ['brew', '--prefix', 'icu4c@79'], text=True
    ).strip()
    directory = Path(prefix) / 'lib/pkgconfig'
    if not (directory / 'icu-uc.pc').is_file():
        raise RuntimeError(f'ICU pkg-config metadata missing: {directory / "icu-uc.pc"}')
    environment = os.environ.copy()
    environment['PKG_CONFIG_PATH'] = os.pathsep.join(filter(None, (
        str(directory), environment.get('PKG_CONFIG_PATH', ''),
    )))
    return environment


def main() -> None:
    scripts_root = Path(__file__).resolve().parents[3]
    execute([
        sys.executable,
        str(scripts_root / 'shared' / 'mac' / 'source_setup.py'),
        'cfdocument',
    ])

    if not shutil.which('brew'):
        raise RuntimeError('Homebrew is required to provision Scribus macOS dependencies')

    # Poppler is mandatory; PoDoFo provides upstream Illustrator PDF import.
    for formula in ('poppler', 'podofo', 'qt', 'open-scene-graph',
                    'icu4c@79', 'jpeg-xl', 'graphicsmagick', 'librevenge'):
        if installed_formula(formula):
            print(f'[cfdocument][mac][setup] Reusing installed {formula}', flush=True)
        else:
            execute(['brew', 'install', formula])

    environment = pkg_config_environment()
    # HarfBuzz's ICU integration depends on keg-only ICU metadata.
    for module in ('icu-uc', 'harfbuzz-icu', 'libjxl', 'GraphicsMagick',
                   'librevenge-0.0', 'librevenge-stream-0.0',
                   'librevenge-generators-0.0'):
        execute(['pkg-config', '--modversion', module], env=environment)

    # Scribus' Findpoppler module requires the poppler pkg-config package.
    execute(['pkg-config', '--atleast-version=24.02.0', 'poppler'])
    execute(['pkg-config', '--modversion', 'poppler'])
    qt_prefix = subprocess.check_output(['brew', '--prefix', 'qt'], text=True).strip()
    qt_config = Path(qt_prefix) / 'lib/cmake/Qt6/Qt6Config.cmake'
    if not qt_config.is_file():
        raise RuntimeError(f'Qt6 CMake configuration is missing: {qt_config}')
    print(f'[cfdocument][mac][setup] Qt6 available: {qt_config}', flush=True)
    print('[cfdocument][mac][setup] Poppler available; full upstream CMake configure '
          'must still verify PoDoFo and all other features.', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f'[cfdocument][mac][setup] ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
