#!/usr/bin/env python3
"""Build CinterForge Windows x64 from the reviewed VS-derived GNU Make graph.

Run inside the provisioned Linux AMD64/UCRT64 build guest. This command never
uses the host C++ compiler and never writes generated files into source.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from generate_cross_makefile import generate
# The SDK inspection is a reusable Windows utility; do not duplicate it here.
import importlib.util

_WINDOWS_VERIFY_MODULE = Path(__file__).resolve().parents[4] / 'shared' / 'win' / 'verify.py'
_WINDOWS_VERIFY_SPEC = importlib.util.spec_from_file_location('cinterforge_windows_verify', _WINDOWS_VERIFY_MODULE)
if _WINDOWS_VERIFY_SPEC is None or _WINDOWS_VERIFY_SPEC.loader is None:
    raise ImportError(f'Cannot load Windows SDK verification: {_WINDOWS_VERIFY_MODULE}')
_WINDOWS_VERIFY = importlib.util.module_from_spec(_WINDOWS_VERIFY_SPEC)
_WINDOWS_VERIFY_SPEC.loader.exec_module(_WINDOWS_VERIFY)
inspect_steam_sdk = _WINDOWS_VERIFY.inspect


def run(repo: Path, builds: Path, configuration: str, port_file: Path, jobs: int) -> dict:
    repo = repo.resolve()
    builds = builds.resolve()
    if repo == builds or repo in builds.parents or builds in repo.parents:
        raise ValueError('Builds must be an external directory, separate from the source repository')
    if builds.name != 'Builds':
        raise ValueError('Expected the external Builds directory')
    for name in ('make', 'x86_64-w64-mingw32-g++', 'x86_64-w64-mingw32-windres'):
        if shutil.which(name) is None:
            raise ValueError(f'Required UCRT64 guest tool missing from PATH: {name}')
    steam_sdk = inspect_steam_sdk(repo)
    if not steam_sdk['sdk_inputs_present'] or not steam_sdk['checks']['native_project_references_steam_api64']:
        raise ValueError('CinterForge Steamworks SDK inputs are incomplete; inspect shared/win/verify.py report')
    port = json.loads(port_file.read_text(encoding='utf-8'))
    if port.get('_review', {}).get('msvc_libraries_to_resolve') or port.get('_review', {}).get('unresolved_msbuild_properties'):
        raise ValueError('Port worksheet still lists unresolved dependencies; resolve and clear review blockers before building')
    output_dir = builds / 'DerivedData' / 'WindowsCrossBuild' / configuration
    makefile = output_dir / 'Makefile'
    result = generate(repo, configuration, makefile, port)
    report_path = output_dir / 'build_report.json'
    command = ['make', '-f', str(makefile), f'-j{jobs}', 'all']
    report = {**result, 'schema': 'cinterforge.windows-cross-build.v1', 'command': command,
              'steam_sdk': steam_sdk, 'status': 'failed', 'executable': str(output_dir / 'CinterForge.exe'), 'returncode': None}
    start = time.monotonic()
    try:
        with (output_dir / 'build.log').open('w', encoding='utf-8') as log:
            completed = subprocess.run(command, cwd=output_dir, stdout=log, stderr=subprocess.STDOUT,
                                       check=False, env=os.environ.copy())
        report['returncode'] = completed.returncode
        exe = output_dir / 'CinterForge.exe'
        if completed.returncode == 0 and exe.is_file() and exe.stat().st_size:
            report['status'] = 'built-not-staged-or-tested'
        elif completed.returncode == 0:
            report['error'] = 'Make returned success but did not produce a nonempty CinterForge.exe'
    finally:
        report['elapsed_seconds'] = round(time.monotonic() - start, 2)
        report_path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', type=Path, default=Path(__file__).resolve().parents[6])
    parser.add_argument('--builds-root', type=Path, required=True)
    parser.add_argument('--configuration', choices=('Debug', 'Release'), required=True)
    parser.add_argument('--port-config', type=Path, required=True)
    parser.add_argument('--jobs', type=int, default=2)
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    try:
        report = run(args.repo_root, args.builds_root, args.configuration, args.port_config, args.jobs)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(f"Windows cross-build: {report['status']} (exit={report['returncode']})")
    print(f"Build log: {Path(report['executable']).parent / 'build.log'}")
    return 0 if report['status'] == 'built-not-staged-or-tested' else 1


if __name__ == '__main__':
    sys.exit(main())
