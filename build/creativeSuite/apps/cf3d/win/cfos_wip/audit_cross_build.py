#!/usr/bin/env python3
"""Audit Windows cross-build inputs without claiming a successful build.

This is a source-only gate. It deliberately does not invoke a compiler or
consider the legacy Steamworks sample Makefile a CinterForge build target.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def audit(repo: Path) -> dict:
    project = repo / 'CinterForgeSteam'
    vcxproj = project / 'CinterForge.vcxproj'
    makefile = project / 'Makefile'
    if not vcxproj.is_file() or not makefile.is_file():
        raise FileNotFoundError('CinterForgeSteam/CinterForge.vcxproj or Makefile is missing')
    xml = vcxproj.read_text(encoding='utf-8-sig')
    mk = makefile.read_text(encoding='utf-8')
    compile_items = re.findall(r'<ClCompile\s+Include="([^"]+)"', xml)
    missing = [name for name in compile_items if not (project / Path(name.replace('\\', '/'))).is_file()]
    declared = re.search(r'SOURCEFILES\s*:=\s*(.*?)(?=\n\S|\Z)', mk, re.S)
    make_sources = re.findall(r'[\w./-]+\.(?:cpp|c)\b', declared.group(1)) if declared else []
    missing_make = [name for name in make_sources if not (project / name).is_file()]
    blockers = []
    if missing_make:
        blockers.append('Existing Makefile references missing sample sources; it is not a CinterForge cross-build.')
    if 'SteamworksExampleLinux' in mk:
        blockers.append('Existing Makefile targets SteamworksExampleLinux, not the Windows CinterForge executable.')
    if '-lGL' in (project / 'flags.mak').read_text(encoding='utf-8'):
        blockers.append('Existing Makefile links Linux GL/SDL libraries, not Windows application dependencies.')
    if missing:
        blockers.append('Native Visual Studio project has unresolved source entries; inspect before porting build graph.')
    blockers.append('A Windows-specific GNU Make source/dependency graph and link contract have not been established.')
    return {
        'schema': 'cinterforge.windows-cross-build-audit.v1',
        'repository': str(repo),
        'visual_studio_project': str(vcxproj.relative_to(repo)),
        'visual_studio_compile_entries': len(compile_items),
        'visual_studio_missing_source_entries': missing,
        'legacy_makefile': str(makefile.relative_to(repo)),
        'legacy_makefile_missing_source_entries': missing_make,
        'cross_build_ready': False,
        'blockers': blockers,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', type=Path, default=Path(__file__).resolve().parents[6])
    parser.add_argument('--output', type=Path, help='Optional JSON report path under the external Builds directory')
    args = parser.parse_args()
    report = audit(args.repo_root.resolve())
    if args.output:
        output = args.output.resolve()
        if 'Builds' not in output.parts:
            parser.error('--output must be inside a Builds directory')
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    return 0 if report['cross_build_ready'] else 2


if __name__ == '__main__':
    sys.exit(main())
