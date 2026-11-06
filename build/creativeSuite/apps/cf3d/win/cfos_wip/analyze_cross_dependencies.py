#!/usr/bin/env python3
"""Audit the real MSVC Windows x64 project for UCRT64 porting dependencies.

Does not translate MSVC libraries into GCC flags or claim a runnable cross-build.
"""
from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from extract_msvc_build_contract import extract, local

MACRO = re.compile(r'\$\(([^)]+)\)')


def split_semicolon(value: str) -> list[str]:
    return [part.strip() for part in value.split(';') if part.strip()]


def project_settings(project: Path, configuration: str) -> dict:
    root = ET.parse(project).getroot()
    target = f"'{configuration}|x64'"
    result: dict[str, dict[str, str]] = {}
    for group in root:
        if local(group) != 'ItemDefinitionGroup':
            continue
        condition = group.get('Condition', '')
        if condition and target not in condition:
            continue
        for section in group:
            name = local(section)
            current = result.setdefault(name, {})
            for field in section:
                key = local(field)
                value = (field.text or '').strip()
                if not value:
                    continue
                # MSBuild inherited-item macros are replaced with earlier values.
                inherited = f'%({key})'
                current[key] = value.replace(inherited, current.get(key, ''))
    return result


def analyze(repo: Path, configuration: str) -> dict:
    contract = extract(repo)
    project_dir = repo / 'CinterForgeSteam'
    settings = project_settings(project_dir / 'CinterForge.vcxproj', configuration)
    compile_settings = settings.get('ClCompile', {})
    link_settings = settings.get('Link', {})
    source_items = [item for item in contract['items'] if item['kind'] == 'ClCompile']
    resources = [item['path'] for item in contract['items'] if item['kind'] == 'ResourceCompile']
    excluded = []
    for item in source_items:
        for override in item['overrides']:
            if override['name'] == 'ExcludedFromBuild' and override['value'].lower() == 'true':
                condition = override['condition']
                if not condition or f"'{configuration}|x64'" in condition:
                    excluded.append(item['path'])
                    break
    libraries = split_semicolon(link_settings.get('AdditionalDependencies', ''))
    include_dirs = split_semicolon(compile_settings.get('AdditionalIncludeDirectories', ''))
    library_dirs = split_semicolon(link_settings.get('AdditionalLibraryDirectories', ''))
    unresolved = sorted(set(MACRO.findall(';'.join(libraries + include_dirs + library_dirs))))
    # A literal library name in an MSVC project is not proof of UCRT64 compatibility.
    return {
        'schema': 'cinterforge.windows-cross-dependencies.v1',
        'configuration': configuration,
        'architecture': 'x64',
        'source_project': 'CinterForgeSteam/CinterForge.vcxproj',
        'compilation_units': len(source_items) - len(excluded),
        'excluded_sources': sorted(excluded),
        'missing_sources': contract['missing_compilation_inputs'],
        'resource_inputs': resources,
        'msvc_libraries_untranslated': libraries,
        'msvc_library_directories': library_dirs,
        'msvc_include_directories': include_dirs,
        'unresolved_msbuild_properties': unresolved,
        'compiler': {k: v for k, v in compile_settings.items() if k not in ('AdditionalIncludeDirectories',)},
        'linker': {k: v for k, v in link_settings.items() if k not in ('AdditionalDependencies', 'AdditionalLibraryDirectories')},
        'cross_build_ready': False,
        'blockers': [
            'MSVC import libraries and Windows SDK dependencies require verified UCRT64 equivalents.',
            'MSBuild properties and per-source options require explicit platform translation.',
            'Windows resources and runtime dependency closure require cross-build staging.',
            'No linked Windows CinterForge executable or Windows runtime test has been demonstrated.',
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', type=Path, default=Path(__file__).resolve().parents[6])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--configuration', choices=['Debug', 'Release'], default='Debug')
    args = parser.parse_args()
    repo = args.repo_root.resolve()
    output = args.output.resolve()
    if 'Builds' not in output.parts or repo in output.parents:
        parser.error('output must be under an external Builds directory')
    report = analyze(repo, args.configuration)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(f"{args.configuration} x64: {report['compilation_units']} source files, "
          f"{len(report['msvc_libraries_untranslated'])} untranslated libraries, "
          f"{len(report['unresolved_msbuild_properties'])} unresolved MSBuild properties")
    print(f"Dependency audit written to {output}; Windows cross-build NOT ready")
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
