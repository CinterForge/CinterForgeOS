#!/usr/bin/env python3
"""Generate a UCRT64 GNU Make compile graph from the native Visual Studio source list.

Linking is deliberately gated by an explicit, reviewed port configuration: MSVC
libraries cannot be passed blindly to MinGW. No output is written into source.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from extract_msvc_build_contract import extract


def make_escape(value: str) -> str:
    return value.replace('$', '$$').replace('#', r'\#').replace(' ', r'\ ')


def shell_quote(value: str) -> str:
    """Quote a path or argument for the POSIX shell used by guest GNU Make."""
    import shlex
    return shlex.quote(value)


def generate(repo: Path, configuration: str, output: Path, port: dict) -> dict:
    contract = extract(repo)
    root = repo / 'CinterForgeSteam'
    sources = []
    excluded = []
    for item in contract['items']:
        if item['kind'] != 'ClCompile':
            continue
        skip = any(o['name'] == 'ExcludedFromBuild' and o['value'].lower() == 'true'
                   and (not o['condition'] or f"'{configuration}|x64'" in o['condition'])
                   for o in item['overrides'])
        if skip:
            excluded.append(item['path'])
            continue
        if not item['exists']:
            raise ValueError(f"Missing project source: {item['path']}")
        sources.append(item['path'])
    if len(set(sources)) != len(sources):
        raise ValueError('Duplicate compilation inputs in native project')
    # Port configuration must explicitly describe external dependencies and linker inputs.
    required = ('include_dirs', 'defines', 'cxxflags', 'ldflags', 'libraries', 'resource_inputs')
    missing = [key for key in required if key not in port or not isinstance(port[key], list)]
    if missing:
        raise ValueError('Port configuration requires reviewed lists: ' + ', '.join(missing))
    if not port.get('reviewed_msvc_dependencies', False):
        raise ValueError('Port configuration must explicitly confirm reviewed_msvc_dependencies=true')
    if any('$(' in str(value) or '%(' in str(value) for values in (port[k] for k in required) for value in values):
        raise ValueError('Unresolved MSBuild variable in port configuration')
    for key in required:
        if not all(isinstance(value, str) and value.strip() for value in port[key]):
            raise ValueError(f'{key} must contain only nonempty strings')
    if any('\n' in value or '\r' in value for key in required for value in port[key]):
        raise ValueError('Newlines are forbidden in port configuration values')
    output.parent.mkdir(parents=True, exist_ok=True)
    build_dir = output.parent
    relative_root = os.path.relpath(root, build_dir)
    objects = [f'obj/{index:04d}.o' for index in range(len(sources))]
    resources = port['resource_inputs']
    for resource in resources:
        resource_path = Path(resource)
        if not resource_path.is_absolute():
            resource_path = root / resource_path
        if not resource_path.is_file():
            raise ValueError(f'Missing Windows resource: {resource}')
    lines = [
        '# Generated from CinterForge.vcxproj; do not edit. Review the port JSON instead.',
        '# GNU Make predefines CXX=g++; ?= would silently use the host compiler.',
        'ifeq ($(origin CXX),default)',
        'CXX := x86_64-w64-mingw32-g++',
        'endif',
        'WINDRES := x86_64-w64-mingw32-windres',
        'CXXFLAGS += -std=c++17 -MMD -MP ' + ' '.join(shell_quote(x) for x in port['cxxflags']),
        'CPPFLAGS += ' + ' '.join(shell_quote('-I' + x) for x in port['include_dirs']) + ' ' + ' '.join(shell_quote('-D' + x) for x in port['defines']),
        'LDFLAGS += ' + ' '.join(shell_quote(x) for x in port['ldflags']),
        'LDLIBS += ' + ' '.join(shell_quote(x) for x in port['libraries']),
        'OBJECTS := ' + ' '.join(objects),
        'RESOURCE_OBJECTS := ' + ' '.join(f'obj/resource_{i:03d}.o' for i in range(len(resources))),
        '.PHONY: all clean',
        'all: CinterForge.exe',
        'CinterForge.exe: $(OBJECTS) $(RESOURCE_OBJECTS)',
        '\t$(CXX) $(LDFLAGS) -o $@ $^ $(LDLIBS)',
        'obj:',
        '\tmkdir -p obj',
    ]
    for index, source in enumerate(sources):
        src = make_escape(os.path.join(relative_root, source))
        lines.extend([f'{objects[index]}: {src} | obj', '\t$(CXX) $(CPPFLAGS) $(CXXFLAGS) -c -o $@ $<'])
    for index, resource in enumerate(resources):
        resource_path = Path(resource)
        if not resource_path.is_absolute():
            resource_path = root / resource_path
        lines.extend([f'obj/resource_{index:03d}.o: {make_escape(os.path.relpath(resource_path, build_dir))} | obj', '\t$(WINDRES) -i $< -o $@'])
    lines.extend(['-include $(OBJECTS:.o=.d)', 'clean:', '\trm -rf obj CinterForge.exe', ''])
    output.write_text('\n'.join(lines), encoding='utf-8')
    return {'schema': 'cinterforge.windows-cross-makefile.v1', 'configuration': configuration,
            'source_project': 'CinterForgeSteam/CinterForge.vcxproj',
            'compilation_units': len(sources), 'excluded_sources': excluded,
            'makefile': str(output), 'status': 'generated-not-built'}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo-root', type=Path, default=Path(__file__).resolve().parents[5])
    p.add_argument('--configuration', choices=('Debug', 'Release'), required=True)
    p.add_argument('--port-config', type=Path, required=True, help='Explicit reviewed UCRT64 dependency mapping JSON')
    p.add_argument('--output', type=Path, required=True, help='Makefile under external Builds')
    a = p.parse_args()
    repo, output = a.repo_root.resolve(), a.output.resolve()
    if 'Builds' not in output.parts or repo in output.parents:
        p.error('output must be in external Builds, never in the source repository')
    try:
        port = json.loads(a.port_config.read_text(encoding='utf-8'))
        result = generate(repo, a.configuration, output, port)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        p.error(str(exc))
    report = output.with_suffix('.json')
    report.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(f"Generated {result['compilation_units']} compilation rules at {output}; executable not built")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
