"""Generic, read-only build requirement extraction for Creative Suite checkouts.

Evidence is declarative, not executable: do not import/evaluate upstream scripts.
Unknown providers stay unresolved rather than being guessed from a header name.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import tomllib

MAX_SOURCE_BYTES = 2_000_000
SOURCE_FILES = ('wscript', 'CMakeLists.txt', 'meson.build', 'configure.ac', 'configure.in',
                'Cargo.toml', 'package.json')
_VERSION = r'([0-9]+(?:\.[0-9]+){1,4})'


def _literal(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, bool, int)):
        return node.value
    return None


def scan_checkout(checkout: Path, target: str, *, emit=None) -> list[dict]:
    """Read bounded project build entrypoints, never traverse vendored trees.

    Each result has provenance (path, line, exact source excerpt), type, name,
    minimum version if discoverable, and whether it is optional/conditional.
    """
    if target not in ('mac', 'linux', 'win') or not checkout.is_dir():
        return []
    paths = [checkout / filename for filename in SOURCE_FILES]
    # Main project build declarations are commonly in these first-level dirs.
    for folder in ('src', 'cmake', 'build-aux', 'src-tauri', 'build',
                   'cli', 'copilot_shim', 'build/ripple', 'build/win32'):
        paths.extend(checkout.joinpath(folder, name) for name in SOURCE_FILES)
    results: dict[tuple, dict] = {}

    def add(path: Path, line: int, kind: str, name: str, version=None,
            optional=None, excerpt='', method='source', constraint=None, scope=None):
        name = str(name).strip()
        if not name or len(name) > 120:
            return
        item = {'kind': kind, 'name': name, 'minimum_version': str(version) if version else None,
                'optional': optional, 'platform_condition': 'not_statically_resolved',
                'path': str(path.relative_to(checkout)), 'line': line,
                'evidence': excerpt.strip()[:300], 'method': method,
                'provider': None, 'provider_status': 'unresolved',
                'version_constraint': constraint, 'scope': scope}
        key = (item['path'], line, kind, name, item['minimum_version'])
        results[key] = item

    for path in paths:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_SOURCE_BYTES:
            continue
        source = path.read_text(encoding='utf-8', errors='replace')
        lines = source.splitlines()
        if path.name == 'wscript':
            try:
                tree = ast.parse(source, filename=str(path))
            except SyntaxError as exc:
                if emit:
                    emit(f'SOURCE PARSER ERROR: {path}: {exc}')
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                method = node.func.attr if isinstance(node.func, ast.Attribute) else (node.func.id if isinstance(node.func, ast.Name) else '')
                if method not in ('check_cfg', 'check', 'check_cxx', 'check_cc', 'find_program'):
                    continue
                kw = {entry.arg: _literal(entry.value) for entry in node.keywords if entry.arg}
                optional = (not kw['mandatory']) if isinstance(kw.get('mandatory'), bool) else None
                excerpt = lines[node.lineno - 1] if node.lineno <= len(lines) else ''
                package = kw.get('package')
                if method == 'check_cfg' and isinstance(package, str):
                    for name in re.split(r'[\s,]+', package.strip()):
                        if name:
                            add(path, node.lineno, 'pkg_config', name, kw.get('atleast_version'), optional, excerpt, method)
                for key in ('header_name', 'header'):
                    if isinstance(kw.get(key), str):
                        add(path, node.lineno, 'header', kw[key], None, optional, excerpt, method)
                if method == 'find_program':
                    program = kw.get('filename') or (node.args and _literal(node.args[0]))
                    if isinstance(program, str):
                        add(path, node.lineno, 'program', program, None, optional, excerpt, method)
        elif path.name == 'meson.build':
            for line_no, line in enumerate(lines, 1):
                for m in re.finditer(r"\bdependency\s*\(\s*['\"]([^'\"]+)['\"]([^)]*)\)", line):
                    version = re.search(r"version\s*:\s*['\"]>=?\s*" + _VERSION, m.group(2))
                    optional = 'required: false' in m.group(2)
                    add(path, line_no, 'pkg_config', m.group(1), version.group(1) if version else None,
                        optional if optional else None, line, 'meson.dependency')
                for m in re.finditer(r"\bhas_header\s*\(\s*['\"]([^'\"]+)['\"]", line):
                    add(path, line_no, 'header', m.group(1), None, None, line, 'meson.has_header')
        elif path.name == 'CMakeLists.txt':
            for line_no, line in enumerate(lines, 1):
                for m in re.finditer(r'\bfind_package\s*\(\s*([\w+.-]+)(?:\s+' + _VERSION + r')?([^)]*)\)', line, re.I):
                    add(path, line_no, 'cmake_package', m.group(1), m.group(2),
                        False if re.search(r'\bREQUIRED\b', m.group(3), re.I) else None, line, 'cmake.find_package')
                for m in re.finditer(r'\bcheck_include_file(?:_cxx)?\s*\(\s*["\']?([\w./+-]+)', line, re.I):
                    add(path, line_no, 'header', m.group(1), None, None, line, 'cmake.check_include_file')
        elif path.name.startswith('configure.'):
            for line_no, line in enumerate(lines, 1):
                for m in re.finditer(r'\bPKG_CHECK_MODULES\s*\(\s*\[?\w+\]?\s*,\s*\[?([\w+.-]+)(?:\s*>=\s*' + _VERSION + r')?', line):
                    add(path, line_no, 'pkg_config', m.group(1), m.group(2), None, line, 'autoconf.PKG_CHECK_MODULES')
                for m in re.finditer(r'\bAC_CHECK_HEADERS\s*\(\s*\[([^\]]+)\]', line):
                    for name in m.group(1).split():
                        add(path, line_no, 'header', name, None, None, line, 'autoconf.AC_CHECK_HEADERS')
        elif path.name == 'Cargo.toml':
            # Rust dependency versions are Cargo constraints, NOT Ubuntu package
            # versions. Preserve them verbatim and never invent apt providers.
            try:
                manifest = tomllib.loads(source)
            except (tomllib.TOMLDecodeError, ValueError) as exc:
                if emit:
                    emit(f'SOURCE PARSER ERROR: {path}: {exc}')
                continue
            for section in ('dependencies', 'build-dependencies', 'dev-dependencies'):
                packages = manifest.get(section) or {}
                if not isinstance(packages, dict):
                    continue
                for name, declaration in packages.items():
                    if not isinstance(name, str):
                        continue
                    version = declaration if isinstance(declaration, str) else (
                        declaration.get('version') if isinstance(declaration, dict) else None)
                    optional = declaration.get('optional') if isinstance(declaration, dict) else None
                    matching = next((i for i, line in enumerate(lines, 1)
                                     if re.match(r'\s*' + re.escape(name) + r'\s*=', line)), 1)
                    add(path, matching, 'rust_crate', name, None, optional,
                        lines[matching-1], 'cargo.manifest', version, section)
        elif path.name == 'package.json':
            # npm semver ranges are not minimum compiler versions. Keep the
            # original scope and constraint for the app's own build closure.
            try:
                manifest = json.loads(source)
            except (ValueError, TypeError) as exc:
                if emit:
                    emit(f'SOURCE PARSER ERROR: {path}: {exc}')
                continue
            if not isinstance(manifest, dict):
                continue
            # Build-tool pins and runtime constraints are part of the app
            # environment contract, not npm dependencies. Retain the exact
            # package-manager version and Node range with provenance.
            manager = manifest.get('packageManager')
            if isinstance(manager, str):
                match = re.fullmatch(r'([a-zA-Z][a-zA-Z0-9_-]*)@([^\s+]+)(?:\+.+)?', manager)
                if match:
                    matching = next((i for i, line in enumerate(lines, 1)
                                     if '"packageManager"' in line), 1)
                    add(path, matching, 'package_manager', match.group(1), None, False,
                        lines[matching-1], 'npm.packageManager', match.group(2), 'build')
            engines = manifest.get('engines') or {}
            if isinstance(engines, dict):
                for tool, constraint in engines.items():
                    if isinstance(tool, str) and isinstance(constraint, str):
                        matching = next((i for i, line in enumerate(lines, 1)
                                         if re.search(r'"' + re.escape(tool) + r'"\s*:', line)), 1)
                        add(path, matching, 'runtime_version_constraint', tool, None, False,
                            lines[matching-1], 'npm.engines', constraint, 'build')
            for section in ('dependencies', 'devDependencies', 'optionalDependencies'):
                packages = manifest.get(section) or {}
                if not isinstance(packages, dict):
                    continue
                for name, constraint in packages.items():
                    if not isinstance(name, str) or not isinstance(constraint, str):
                        continue
                    matching = next((i for i, line in enumerate(lines, 1)
                                     if re.search(r'[\"\']' + re.escape(name) + r'[\"\']\s*:', line)), 1)
                    add(path, matching, 'npm_package', name, None,
                        True if section == 'optionalDependencies' else None,
                        lines[matching-1], 'npm.manifest', constraint, section)
        if emit:
            emit(f'SOURCE SCANNED: {path} ({len(lines)} lines)')
    return sorted(results.values(), key=lambda x: (x['path'], x['line'], x['name']))


def parse_configure_checks(transcript: str) -> list[dict]:
    """Extract all Waf test outcomes, including successful checks, for audit.

    Does not infer a package provider for failed headers.
    """
    findings = []
    expression = re.compile(r"Checking for (.+?)\s{2,}:\s*(.*?)\s*$")
    for line in transcript.splitlines():
        match = expression.search(line)
        if not match:
            continue
        name, outcome = match.groups()
        if outcome in ('not found', 'no', 'yes') or outcome.startswith('Found'):
            findings.append({'check': name.strip(), 'result': outcome,
                             'passed': outcome in ('yes',) or outcome.startswith('Found'),
                             'provider': None, 'provider_status': 'unresolved' if outcome in ('no', 'not found') else 'not_needed'})
    return findings
