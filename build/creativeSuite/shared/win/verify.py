#!/usr/bin/env python3
"""Verify checked-in Steamworks SDK inputs for CinterForge Windows cross-build.

This is a source/SDK closure check, not a Windows compilation or runtime test.
Steamworks redistributables are vendor binaries, not locally compiled sources.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def inspect(repo: Path) -> dict:
    root = repo.resolve() / 'CinterForgeSteam'
    expected = {
        'public_header': root / 'steam' / 'steam_api.h',
        'linux_sdk_library': root / 'redistributable_bin' / 'linux64' / 'libsteam_api.so',
        'windows_sdk_dll': root / 'redistributable_bin' / 'win64' / 'steam_api64.dll',
        'windows_msvc_import_library': root / 'redistributable_bin' / 'win64' / 'steam_api64.lib',
        'native_project': root / 'CinterForge.vcxproj',
        'linux_example_makefile': root / 'Makefile',
        'linux_example_flags': root / 'flags.mak',
    }
    checks = {name: {'path': str(path), 'exists': path.is_file(),
                     'bytes': path.stat().st_size if path.is_file() else 0}
              for name, path in expected.items()}
    project = expected['native_project']
    xml = project.read_text(encoding='utf-8-sig') if project.is_file() else ''
    checks['native_project_references_steam_api64'] = 'steam_api64.lib' in xml
    # MSVC .lib is not automatically usable by the MinGW linker. Its presence
    # does not prove a suitable GNU import library exists.
    return {'schema': 'cinterforge.steam-sdk-closure.v1', 'checks': checks,
            'sdk_inputs_present': all(v['exists'] and v['bytes'] > 0 for v in checks.values() if isinstance(v, dict)),
            'mingw_import_library_verified': False,
            'windows_executable_built': False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', type=Path, default=Path(__file__).resolve().parents[6])
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = inspect(args.repo_root)
    if args.output:
        output = args.output.resolve()
        if 'Builds' not in output.parts:
            parser.error('Report must be written under external Builds')
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))
    return 0 if result['sdk_inputs_present'] and result['checks']['native_project_references_steam_api64'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
