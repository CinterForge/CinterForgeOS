#!/usr/bin/env python3
"""Generate a GNU/MinGW import library from the checked-in Steamworks win64 DLL.

The SDK's MSVC .lib is not treated as a GNU import library. This operation
requires gendef and x86_64-w64-mingw32-dlltool and writes only to Builds.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess


def build(repo: Path, builds: Path) -> dict:
    repo, builds = repo.resolve(), builds.resolve()
    if builds.name != 'Builds' or repo == builds or repo in builds.parents or builds in repo.parents:
        raise ValueError('Builds must be the external Builds directory')
    dll = repo / 'CinterForgeSteam/redistributable_bin/win64/steam_api64.dll'
    if not dll.is_file() or dll.stat().st_size == 0:
        raise ValueError(f'Missing Steamworks Windows x64 DLL: {dll}')
    required = ('gendef', 'x86_64-w64-mingw32-dlltool')
    for tool in required:
        if shutil.which(tool) is None:
            raise ValueError(f'Required Windows cross-tool unavailable: {tool}')
    dest = builds / 'DerivedData/WindowsCrossBuild/Steamworks'
    dest.mkdir(parents=True, exist_ok=True)
    definition = dest / 'steam_api64.def'
    library = dest / 'libsteam_api64.a'
    # gendef creates the .def beside its input DLL. Never write into source:
    # copy the vendor DLL to the generated workspace first.
    local_dll = dest / dll.name
    shutil.copyfile(dll, local_dll)
    commands = [
        ['gendef', str(local_dll)],
        ['x86_64-w64-mingw32-dlltool', '-m', 'i386:x86-64', '-d', str(definition),
         '-l', str(library), '-D', dll.name],
    ]
    for command in commands:
        completed = subprocess.run(command, cwd=dest, capture_output=True, text=True, check=False)
        if completed.returncode:
            raise RuntimeError(f"{' '.join(command)} failed ({completed.returncode}):\n{completed.stdout}\n{completed.stderr}")
    for path in (definition, library):
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f'Import library generation produced no valid {path.name}')
    result = {'schema': 'cinterforge.steamworks-gnu-import.v1',
              'source_dll': str(dll), 'definition': str(definition),
              'import_library': str(library), 'runtime_dll': str(dll),
              'status': 'generated-not-linked-or-tested'}
    (dest / 'import_library_report.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo-root', type=Path, default=Path(__file__).resolve().parents[6])
    p.add_argument('--builds-root', type=Path, required=True)
    args = p.parse_args()
    try:
        result = build(args.repo_root, args.builds_root)
    except (OSError, ValueError, RuntimeError) as exc:
        p.error(str(exc))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
