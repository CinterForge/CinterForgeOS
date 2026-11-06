#!/usr/bin/env python3
"""Extract the native Windows project contract for an eventual UCRT64 build.

This is an inventory, NOT a generated makefile or proof of cross-compiler parity.
Unexpanded MSBuild properties are preserved so unsupported dependencies remain visible.
"""
from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path

FIELDS = ('AdditionalIncludeDirectories', 'PreprocessorDefinitions',
          'AdditionalDependencies', 'AdditionalLibraryDirectories',
          'RuntimeLibrary', 'LanguageStandard', 'PrecompiledHeader',
          'ForcedIncludeFiles', 'AdditionalOptions', 'SubSystem',
          'EntryPointSymbol', 'AdditionalManifestDependencies')


def local(element):
    return element.tag.rsplit('}', 1)[-1]


def properties(node):
    return {local(child): (child.text or '').strip()
            for child in node if local(child) in FIELDS and (child.text or '').strip()}


def extract(repo: Path):
    root = repo / 'CinterForgeSteam'
    project = root / 'CinterForge.vcxproj'
    tree = ET.parse(project).getroot()
    configurations = []
    global_settings = []
    sources = []
    for node in tree.iter():
        kind = local(node)
        if kind == 'ProjectConfiguration':
            configurations.append(node.get('Include'))
        elif kind == 'ItemDefinitionGroup':
            settings = {}
            for section in node:
                data = properties(section)
                if data:
                    settings[local(section)] = data
            global_settings.append({'condition': node.get('Condition', ''), 'settings': settings})
        elif kind in ('ClCompile', 'ResourceCompile', 'CustomBuild', 'None') and node.get('Include'):
            name = node.get('Include').replace('\\', '/')
            overrides = []
            for child in node:
                if local(child) in FIELDS or local(child) in ('ExcludedFromBuild', 'ObjectFileName'):
                    overrides.append({'name': local(child), 'condition': child.get('Condition', ''),
                                      'value': (child.text or '').strip()})
            sources.append({'kind': kind, 'path': name,
                            'exists': (root / name).is_file(), 'overrides': overrides})
    return {'schema': 'cinterforge.windows-msvc-contract.v1',
            'project': 'CinterForgeSteam/CinterForge.vcxproj',
            'configurations': configurations, 'settings': global_settings,
            'items': sources,
            'missing_compilation_inputs': [s['path'] for s in sources if s['kind'] == 'ClCompile' and not s['exists']],
            'note': 'MSVC settings are evidence for porting, not GCC-compatible flags or link arguments.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', type=Path, default=Path(__file__).resolve().parents[6])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo_root.resolve()
    output = args.output.resolve()
    if 'Builds' not in output.parts or repo in output.parents:
        parser.error('output must be in an external Builds directory, not source')
    report = extract(repo)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(f"MSVC contract: {len(report['items'])} items, "
          f"{len(report['missing_compilation_inputs'])} missing compilation inputs -> {output}")
    return 1 if report['missing_compilation_inputs'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
