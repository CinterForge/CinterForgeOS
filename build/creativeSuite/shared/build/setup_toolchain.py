#!/usr/bin/env python3
"""Prepare Creative Suite application build environments.

Responsibilities:
    1. Read application inventory and per-platform recipes.
    2. Discover upstream toolchain requirements and evidence.
    3. Provision recipe-declared dependencies and validate source checkouts.
    4. Write diagnostic reports for individual or batch Setup runs.

Setup prepares dependencies; only Build can establish build readiness.
"""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime
import traceback
import zipfile
import csv
import json
import os
from pathlib import Path
import platform as host_platform
import re
import shlex
import shutil
import subprocess
import sys
from urllib.request import urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from html.parser import HTMLParser
import html
# The lifecycle invokes this file directly rather than as an installed package.
# Add the Creative Suite package root so its shared modules can be imported.
CREATIVE_SUITE_ROOT = Path(__file__).resolve().parents[2]
CINTER_SERVER_ROOT = Path(__file__).resolve().parents[5] / "CinterForgeServer"

# Standalone scripts need two import roots:
# - creativeSuite for recipe and shared build helpers
# - CinterForgeServer for the cf.io workspace resolver
for import_root in (CREATIVE_SUITE_ROOT, CINTER_SERVER_ROOT):
    import_root_string = str(import_root)
    if import_root_string not in sys.path:
        sys.path.insert(0, import_root_string)
from recipe import make_recipe, recipe_path, write_recipe, read_recipe, retain_recipe_instructions, review_inspected_recipe
from shared.source.source_requirements import scan_checkout, parse_configure_checks
from cf.io.CinterIO import (cinterforgeSourceRootPath, cinterforgeWorkspaceRootPath,
                               cinterforgeBuildsRootPath, cinterforgeSiblingRepoPath)

CSV = CREATIVE_SUITE_ROOT / 'input' / 'Creative Suite.csv'
PLATFORMS = {'mac': 'mac', 'macos': 'mac', 'linux': 'linux', 'win': 'win', 'windows': 'win'}


def lookup_application_in_inventory(name: str) -> dict[str, str]:
    with CSV.open(newline='', encoding='utf-8-sig') as handle:
        rows = list(csv.DictReader(handle))
    normalized = name.casefold().replace(' ', '').replace('-', '')
    if normalized == 'converter':
        normalized = 'cinterforgeconverter'
    for row in rows:
        if (row.get('Name') or '').casefold().replace(' ', '').replace('-', '') == normalized:
            return {k: (v or '').strip() for k, v in row.items() if k}
    raise RuntimeError(f'Application {name!r} is not defined in {CSV}')


def application_build_log_url(row: dict[str, str], target: str) -> str:
    return row.get(f'{target} Build Log URL', '').strip()


# Evidence URLs are configured exclusively in Creative Suite.csv, per target.
# No application-specific build log fallback is permitted.
TARGETS = ('mac', 'linux', 'win')
EVIDENCE_LIMIT = 12 * 1024 * 1024


def read_application_inventory() -> list[dict[str, str]]:
    with CSV.open(newline='', encoding='utf-8-sig') as handle:
        return [{k: (v or '').strip() for k, v in row.items() if k}
                for row in csv.DictReader(handle)]


def application_evidence_urls(row: dict[str, str], target: str) -> list[str]:
    # CSV is the sole authority. Keep the info/log slots independent so one
    # failed URL doesn't prevent the other document from being inspected.
    return list(dict.fromkeys(url for url in (
        row.get(f'{target} Build Info URL', '').strip(),
        row.get(f'{target} Build Log URL', '').strip(),
        row.get('Build Patches URL', '').strip(),
        row.get(f'{target} Build Patches URL', '').strip(),
    ) if url))


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in ('script', 'style'):
            self.hidden += 1
        if tag in ('br', 'p', 'div', 'pre', 'li', 'tr'):
            self.lines.append('\n')

    def handle_endtag(self, tag: str) -> None:
        if tag in ('script', 'style'):
            self.hidden = max(self.hidden - 1, 0)
        if tag in ('p', 'div', 'pre', 'li', 'tr'):
            self.lines.append('\n')

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.lines.append(data)


def download_upstream_evidence(url: str) -> str:
    if urlparse(url).scheme != 'https':
        raise ValueError('Evidence URL must use HTTPS')
    with urlopen(url, timeout=20) as response:
        content_type = response.headers.get('Content-Type', '')
        body = response.read(EVIDENCE_LIMIT + 1)
    if len(body) > EVIDENCE_LIMIT:
        raise ValueError(f'Upstream evidence exceeds {EVIDENCE_LIMIT} bytes (not truncated)')
    raw = body.decode('utf-8', errors='replace')
    if 'html' in content_type.lower() or re.search(r'<(?:!doctype\s+html|html|head|body)\b', raw[:1000], re.I):
        parser = _HTMLText()
        parser.feed(raw)
        raw = ''.join(parser.lines)
    if not raw.strip():
        raise ValueError('Upstream evidence is empty')
    return html.unescape(raw)


def extract_version_requirements(contents: list[tuple[str, str]]) -> list[dict[str, str]]:
    """Extract actual version evidence, including Ardour's build-stack package list.

    Numeric requirements like '>= 2.28' are minimum constraints, NOT installed
    versions, so they are intentionally excluded from the version inventory.
    """
    output: dict[tuple[str, str, str], dict[str, str]] = {}
    compiler_re = re.compile(r'\b(?:Apple clang|clang|gcc|g\+\+|GCC|MinGW(?:-w64)?)\s+(?:version\s*)?v?(\d+(?:\.\d+){0,3})', re.I)
    tool_names = {'autoconf', 'automake', 'cmake', 'meson', 'ninja', 'libtool', 'pkg-config', 'python', 'xcode', 'waf', 'make', 'git', 'm4', 'intltool', 'itstool'}
    for url, content in contents:
        for line in content.splitlines():
            line = line.strip()
            if not line or len(line) > 900:
                continue
            compiler = compiler_re.search(line)
            if compiler:
                name = compiler.group(0)[:compiler.start(1)].strip(' =:-vV')
                version = compiler.group(1)
                key = ('compiler', name.casefold(), version)
                output.setdefault(key, {'type': 'compiler', 'name': name, 'version': version,
                                        'source_url': url, 'evidence': line[:220]})
            # Ardour info.txt lists exact installed packages as e.g. glib-2.56.4,
            # boost_1_68_0, Python-2.7.18, with no 'version' keyword.
            package = re.fullmatch(r'([A-Za-z][A-Za-z0-9+_.-]*?)[-_]v?(\d+(?:[._]\d+){1,4})(?:[-_][a-zA-Z0-9.-]+)?', line)
            if package:
                name, raw_version = package.groups()
                version = raw_version.replace('_', '.')
                kind = 'tool' if name.lower() in tool_names else 'dependency'
                key = (kind, name.casefold(), version)
                output.setdefault(key, {'type': kind, 'name': name, 'version': version,
                                        'source_url': url, 'evidence': line[:220]})
            # Explicit version declarations (no package-list formatting).
            declaration = re.fullmatch(r'([A-Za-z][A-Za-z0-9+_.-]*)\s+(?:version\s+)?v?(\d+(?:\.\d+){1,4})', line, re.I)
            if declaration:
                name, version = declaration.groups()
                kind = 'tool' if name.lower() in tool_names else 'dependency'
                key = (kind, name.casefold(), version)
                output.setdefault(key, {'type': kind, 'name': name, 'version': version,
                                        'source_url': url, 'evidence': line[:220]})
    return sorted(output.values(), key=lambda r: (r['type'], r['name'].lower(), r['version']))


def extract_upstream_patch_evidence(contents: list[tuple[str, str]], row: dict[str, str], target: str) -> list[dict[str, str]]:
    """Retain upstream patch commands as evidence; never execute upstream text."""
    patch_urls = {row.get('Build Patches URL', '').strip(),
                  row.get(f'{target} Build Patches URL', '').strip()}
    patches: list[dict[str, str]] = []
    for url, content in contents:
        if url not in patch_urls:
            continue
        component = ''
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            # HTML parser strips headings; upstream patch-info component names
            # are followed by sed/patch command lines. Preserve original text.
            if re.fullmatch(r'[A-Za-z][A-Za-z0-9+_.-]{1,70}', stripped) and not stripped.lower() in ('patch', 'patches'):
                component = stripped
            elif (stripped.startswith(('sed ', 'sed\t', 'patch ', 'find ')) or
                  'sed -i' in stripped or 'patch -p' in stripped):
                patches.append({'component': component or '(unknown)',
                                'action': stripped[:1200], 'source_url': url})
    return patches


def collect_local_requirement_evidence(row: dict[str, str], target: str, emit=None, evidence_dir: Path | None = None) -> list[tuple[str, str]]:
    """Read CSV-declared root-level source documents; never execute their contents.

    Only simple filenames or globs within the checkout root are accepted.
    Missing checkouts are gaps in discovery, not reasons to abort setup-all.
    """
    patterns = [value.strip() for field in ('Build Requirements Files', f'{target} Build Requirements Files')
                for value in row.get(field, '').split(';') if value.strip()]
    if not patterns:
        return []
    upstream = row.get('Git Repo URL', '')
    repository = urlparse(upstream).path.rstrip('/').split('/')[-1].removesuffix('.git')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', repository):
        raise ValueError(f'Invalid repository name for local evidence: {repository!r}')
    checkout = cinterforgeSiblingRepoPath(repository)
    if not checkout.is_dir():
        if emit:
            emit(f'LOCAL EVIDENCE SKIP: source checkout missing: {checkout}; run ./Cinter clone first')
        return []
    documents = []
    for pattern in patterns:
        if not re.fullmatch(r'[A-Za-z0-9_*?.-]+', pattern) or pattern in ('.', '..'):
            raise ValueError(f'Only root-level filename globs are supported: {pattern!r}')
        matches = sorted(p for p in checkout.glob(pattern) if p.is_file() and not p.is_symlink())
        if not matches and emit:
            emit(f'LOCAL EVIDENCE MISSING: {checkout / pattern}')
        for path in matches:
            try:
                if path.stat().st_size > EVIDENCE_LIMIT:
                    raise ValueError(f'Local evidence exceeds {EVIDENCE_LIMIT} bytes')
                content = path.read_text(encoding='utf-8', errors='replace')
                uri = path.as_uri()
                documents.append((uri, content))
                if evidence_dir is not None:
                    destination = evidence_dir / f'local-evidence-{len(documents):02d}-{path.name}.txt'
                    destination.write_text(f'Source: {uri}\n\n{content}', encoding='utf-8')
                if emit:
                    emit(f'LOCAL EVIDENCE OK: {path} ({len(content)} characters)')
            except Exception as exc:
                if emit:
                    emit(f'LOCAL EVIDENCE FAILED: {path}: {type(exc).__name__}: {exc}')
    return documents


def extract_upstream_dependency_constraints(contents: list[tuple[str, str]]) -> list[dict[str, str]]:
    """Track minimum versions separately from installed package versions."""
    results = {}
    # Typical generated INSTALL files list e.g. 'GEGL >= 0.4.52'.
    expression = re.compile(r'\b([A-Za-z][A-Za-z0-9+_.-]{1,70})\s*(>=|=>|>|=|==)\s*v?(\d+(?:\.\d+){1,4})\b')
    for source, document in contents:
        for line in document.splitlines():
            if len(line) > 500:
                continue
            for match in expression.finditer(line):
                name, operator, version = match.groups()
                key = (name.casefold(), operator, version)
                results.setdefault(key, {'name': name, 'operator': operator, 'version': version,
                                         'source_url': source, 'evidence': line.strip()[:220]})
    return sorted(results.values(), key=lambda r: (r['name'].casefold(), r['version']))


def discover_application_requirements(row: dict[str, str], target: str, emit=None, evidence_dir: Path | None = None) -> dict:
    name = row.get('Name', '(unnamed)')
    acquisition = (row.get('Source Acquisition') or 'source').casefold()
    item = {'application': name, 'target': target,
            'build_host': 'macOS' if target == 'mac' else 'Linux x86-64',
            'acquisition': acquisition, 'evidence_urls': application_evidence_urls(row, target),
            'requirements': [], 'source_requirements': [], 'constraints': [], 'patches': [], 'errors': [], 'evidence_inspection': [], 'status': 'unresolved'}
    if emit:
        emit(f'Application={name!r} target={target} acquisition={acquisition} host={item["build_host"]}')
        emit(f'Configured evidence URLs: {item["evidence_urls"]!r}')
    if acquisition == 'prebuilt':
        item['status'] = 'prebuilt_requires_component_provenance'
        item['errors'].append('Prebuilt component versions/URLs are not specified by official build logs.')
        return item
    if acquisition != 'source':
        item['errors'].append(f'Unsupported Source Acquisition: {acquisition}')
        return item
    # Project build declarations are primary dependency evidence, even when
    # CSV points at no external documentation. Never execute upstream code.
    upstream = row.get('Git Repo URL', '').strip()
    repository = urlparse(upstream).path.rstrip('/').split('/')[-1].removesuffix('.git')
    if re.fullmatch(r'[A-Za-z0-9_.-]+', repository or ''):
        checkout = cinterforgeSiblingRepoPath(repository)
        item['source_requirements'] = scan_checkout(checkout, target, emit=emit)
        if emit:
            emit(f'SOURCE REQUIREMENTS: {len(item["source_requirements"])} from {checkout}')
    has_local_files = bool(row.get('Build Requirements Files', '').strip() or row.get(f'{target} Build Requirements Files', '').strip())
    if not item['evidence_urls'] and not has_local_files and not item['source_requirements']:
        item['errors'].append('No upstream evidence URLs, local requirements files, or parseable checkout build declarations.')
        return item
    # Misplaced patch URLs must be reported, not silently treated as build inventories.
    patch_urls = {row.get('Build Patches URL', '').strip(),
                  row.get(f'{target} Build Patches URL', '').strip()} - {''}
    for field in (f'{target} Build Info URL', f'{target} Build Log URL'):
        url = row.get(field, '').strip()
        if url and url in patch_urls:
            message = (f'CSV CONFIGURATION: {field} duplicates Build Patches URL ({url}). '
                       'Keep dependency inventories, build logs, and patch information in separate CSV fields.')
            item['errors'].append(message)
            if emit:
                emit(f'WARNING: {message}')
    contents = []
    for url in item['evidence_urls']:
        try:
            if emit:
                emit(f'FETCH START {url}')
            document = download_upstream_evidence(url)
            if evidence_dir is not None:
                evidence_dir.mkdir(parents=True, exist_ok=True)
                evidence_file = evidence_dir / f'evidence-{len(contents) + 1:02d}.txt'
                evidence_file.write_text(f'Source URL: {url}\n\n{document}', encoding='utf-8')
                if emit:
                    emit(f'Evidence saved: {evidence_file.name}')
            contents.append((url, document))
            if emit:
                emit(f'FETCH OK {url}: {len(document)} characters')
            item['evidence_inspection'].append({'url': url, 'chars': len(document), 'first_lines': [x.strip()[:160] for x in document.splitlines() if x.strip()][:4]})
        except Exception as exc:
            item['errors'].append(f'{url}: {type(exc).__name__}: {exc}')
            if emit:
                emit(f'FETCH FAILED {url}: {type(exc).__name__}: {exc}')
                emit(traceback.format_exc())
    contents.extend(collect_local_requirement_evidence(row, target, emit=emit, evidence_dir=evidence_dir))
    item['requirements'] = extract_version_requirements(contents)
    item['constraints'] = extract_upstream_dependency_constraints(contents)
    # Supplemental patch information is evidence, not an installed version.
    # Preserve component names and patch commands for later build-stack work.
    item['patches'] = extract_upstream_patch_evidence(contents, row, target)
    if emit:
        for patch in item['patches']:
            emit(f'PATCH {patch["component"]}: {patch["action"][:220]} [{patch["source_url"]}]')
    if contents and not item['requirements'] and not item['constraints'] and not item['source_requirements']:
        item['errors'].append('Retrieved evidence but found no version entries or constraints; inspect the captured documents.')
    if contents or item['source_requirements']:
        item['status'] = 'partial_evidence' if item['requirements'] or item['constraints'] or item['source_requirements'] else 'evidence_no_versions'
        # Even when versions appear in logs, completeness is unproven.
        item['errors'].append('Source/static evidence is not a verified complete dependency manifest; validate by native configure.')
    return item


def print_application_discovery_report(item: dict) -> None:
    """Print the same evidence-backed report for one app or an all-target pass."""
    print(f'\n{item["application"]} / {item["target"]} '
          f'(build host: {item["build_host"]}) — {item["status"]}')
    for url in item['evidence_urls']:
        print(f'  Evidence: {url}')
    for inspection in item.get('evidence_inspection', []):
        print(f'  Retrieved: {inspection["chars"]} chars from {inspection["url"]}')
        if not item['requirements']:
            print(f'  Response sample: {inspection["first_lines"]!r}')
    for req in item['requirements']:
        print(f'  {req["type"]}: {req["name"]} {req["version"]}  [{req["source_url"]}]')
    for constraint in item.get('constraints', []):
        print(f'  required: {constraint["name"]} {constraint["operator"]} {constraint["version"]}  [{constraint["source_url"]}]')
    for patch in item.get('patches', []):
        print(f'  PATCH {patch["component"]}: {patch["action"][:220]}  [{patch["source_url"]}]')
    if not item['requirements'] and not item.get('constraints'):
        print('  Tools/dependencies/versions: NOT ESTABLISHED')
    for error in item['errors']:
        print(f'  GAP: {error}')


def run_full_discovery_dry_run() -> int:
    """Always attempt every app/platform and write a self-contained diagnostic ZIP.

    No toolchain install or source checkout mutation occurs. Logs/reports are
    generated even when individual evidence URLs, parsers or targets fail.
    """
    cinter_root = cinterforgeSourceRootPath()
    reports_root = cinterforgeBuildsRootPath() / 'Intermediates' / 'Reports' / 'UpdateData'
    stamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S-%f')
    run_dir = reports_root / 'setup-all'
    results: list[dict] = []
    overall_errors: list[str] = []
    transcript = None
    zip_path = reports_root / 'setup-all.zip'

    def say(message: str, target_log=None) -> None:
        print(message, flush=True)
        if transcript is not None:
            transcript.write(message + '\n')
            transcript.flush()
        if target_log is not None:
            target_log.write(message + '\n')
            target_log.flush()

    try:
        reports_root.mkdir(parents=True, exist_ok=True)
        # This is the current diagnostic snapshot, not an archive of past runs.
        # Remove only the legacy timestamped setup-all outputs we own.
        for old in reports_root.glob('setup-all-*'):
            if old.is_dir():
                shutil.rmtree(old)
            elif old.is_file() and old.suffix == '.zip':
                old.unlink()
        if run_dir.exists():
            shutil.rmtree(run_dir)
        run_dir.mkdir(parents=True, exist_ok=False)
        transcript = (run_dir / 'console.log').open('w', encoding='utf-8')
        say(f'[Cinter setup all] BEGIN {stamp}; diagnostic directory: {run_dir}')
        say('[Cinter setup all] DRY RUN: read-only source/toolchain discovery; diagnostic logs written')
        try:
            rows = read_application_inventory()
            say(f'[Cinter setup all] Inventory loaded: {len(rows)} applications from {CSV}')
        except Exception as exc:
            rows = []
            overall_errors.append(f'Inventory failure: {type(exc).__name__}: {exc}')
            say(f'[Cinter setup all] INVENTORY FAILED: {overall_errors[-1]}')
            (run_dir / 'inventory-error.txt').write_text(traceback.format_exc(), encoding='utf-8')

        for row_index, row in enumerate(rows, 1):
            name = row.get('Name') or f'unnamed-{row_index}'
            for target in TARGETS:
                index = len(results) + 1
                safe_name = re.sub(r'[^a-zA-Z0-9_.-]', '_', name)[:70]
                directory = run_dir / f'{index:02d}-{safe_name}-{target}'
                item = None
                try:
                    directory.mkdir(parents=True, exist_ok=False)
                    with (directory / 'setup.log').open('w', encoding='utf-8') as target_log:
                        emit = lambda message: say(f'[Cinter setup all] [{name}/{target}] {message}', target_log)
                        emit('BEGIN target discovery')
                        try:
                            item = discover_application_requirements(row, target, emit=emit, evidence_dir=directory)
                            emit(f'RESULT status={item["status"]} version_entries={len(item["requirements"])}')
                            for constraint in item.get('constraints', []):
                                emit(f'REQUIRES {constraint["name"]} {constraint["operator"]} {constraint["version"]} [{constraint["source_url"]}]')
                            for requirement in item['requirements']:
                                emit(f'FOUND {requirement["type"]}: {requirement["name"]} {requirement["version"]} [{requirement["source_url"]}]')
                            for error in item['errors']:
                                emit(f'GAP: {error}')
                        except Exception as exc:
                            emit(f'FAILED: {type(exc).__name__}: {exc}')
                            emit(traceback.format_exc())
                            item = {'application': name, 'target': target, 'status': 'failed',
                                    'build_host': 'macOS' if target == 'mac' else 'Linux x86-64',
                                    'evidence_urls': [], 'requirements': [],
                                    'errors': [f'{type(exc).__name__}: {exc}']}
                        emit('END target discovery')
                except Exception as exc:
                    say(f'[Cinter setup all] [{name}/{target}] LOG/REPORT FAILURE: {type(exc).__name__}: {exc}')
                    overall_errors.append(f'{name}/{target}: {type(exc).__name__}: {exc}')
                    item = item or {'application': name, 'target': target, 'status': 'failed',
                                    'build_host': 'macOS' if target == 'mac' else 'Linux x86-64',
                                    'evidence_urls': [], 'requirements': [],
                                    'errors': [f'{type(exc).__name__}: {exc}']}
                results.append(item)
                try:
                    recipe = make_recipe(row, target, item)
                    write_recipe(recipe, directory / 'recipe.json')
                    emit_recipe = recipe_path(name, target)
                    say(f'[Cinter setup all] [{name}/{target}] RECIPE SNAPSHOT: {directory / "recipe.json"}; persistent target: {emit_recipe}')
                except Exception as exc:
                    overall_errors.append(f'{name}/{target} recipe generation: {type(exc).__name__}: {exc}')
                    say(f'[Cinter setup all] [{name}/{target}] RECIPE FAILURE: {exc}')
                try:
                    (directory / 'report.json').write_text(json.dumps(item, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
                except Exception as exc:
                    overall_errors.append(f'{name}/{target} report write: {exc}')
                    say(f'[Cinter setup all] [{name}/{target}] REPORT WRITE FAILED: {exc}')

        counts = {status: sum(item['status'] == status for item in results)
                  for status in sorted({item['status'] for item in results})}
        summary = {'started': stamp, 'inventory': str(CSV), 'target_count': len(results),
                   'statuses': counts, 'overall_errors': overall_errors, 'targets': results}
        try:
            (run_dir / 'summary.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        except Exception as exc:
            overall_errors.append(f'Summary JSON write: {exc}')
            say(f'[Cinter setup all] SUMMARY JSON FAILED: {exc}')
        say('\n========== CREATIVE SUITE TOOLCHAIN REPORT (DRY RUN) ==========')
        for item in results:
            try:
                # Capture the complete per-target textual report in the single
                # console log as well as stdout, without losing any version lines.
                from io import StringIO
                stream = StringIO()
                with contextlib.redirect_stdout(stream):
                    print_application_discovery_report(item)
                for line in stream.getvalue().splitlines():
                    say(line)
            except Exception as exc:
                say(f'[Cinter setup all] REPORT RENDER FAILED {item.get("application")}/{item.get("target")}: {exc}')
        say(f'[Cinter setup all] FINAL SUMMARY: {len(results)} targets; statuses={counts}; target_gaps={sum(bool(i.get("errors")) for i in results)}; run_errors={len(overall_errors)}')
    except Exception as exc:
        overall_errors.append(f'Global failure: {type(exc).__name__}: {exc}')
        say(f'[Cinter setup all] GLOBAL ERROR: {overall_errors[-1]}')
        if run_dir.is_dir():
            try:
                (run_dir / 'global-error.txt').write_text(traceback.format_exc(), encoding='utf-8')
            except OSError:
                pass
    finally:
        if transcript is not None:
            transcript.close()
        # Always attempt to package everything recorded, including failed
        # target logs and HTTP evidence, into one uploadable archive.
        try:
            if run_dir.is_dir():
                temp_zip = zip_path.with_name(zip_path.name + '.tmp')
                try:
                    with zipfile.ZipFile(temp_zip, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                        for file in sorted(run_dir.rglob('*')):
                            if file.is_file():
                                archive.write(file, file.relative_to(run_dir))
                    temp_zip.replace(zip_path)
                finally:
                    if temp_zip.exists():
                        temp_zip.unlink()
                print(f'[Cinter setup all] DIAGNOSTIC ZIP: {zip_path}', flush=True)
            else:
                print(f'[Cinter setup all] DIAGNOSTIC DIRECTORY COULD NOT BE CREATED: {run_dir}', flush=True)
        except Exception as exc:
            print(f'[Cinter setup all] ZIP CREATION FAILED: {type(exc).__name__}: {exc}; logs at {run_dir}', flush=True)
        print(f'[Cinter setup all] COMPLETE: targets={len(results)} errors={len(overall_errors)}; see report above', flush=True)
    # Discovery gaps are intentionally reported but must not stop the sweep.
    return 0



def prepare_application_environment(row: dict[str, str], target: str, checkout: Path,
                        logs_dir: Path, evidence: dict, instructions: dict | None = None) -> bool:
    """Perform only CSV-authorized setup work; never install nightly inventory en masse.

    Setup packages are host package-manager names, NOT dependency versions
    guessed from upstream build logs. Configure commands are argv, not shell.
    The complete audit, commands, output and failures stay beside the build.
    """
    logs_dir.mkdir(parents=True, exist_ok=True)
    audit: dict = {'application': row['Name'], 'target': target,
                   'host': host_platform.system(), 'host_arch': host_platform.machine(),
                   'source': str(checkout), 'evidence_status': evidence['status'],
                   'packages': [], 'configure': None, 'configure_checks': [], 'environment': {}, 'errors': [], 'ready': False, 'configured': False, 'build_succeeded': False}
    audit_file = logs_dir / 'toolchain-setup.json'
    transcript_path = logs_dir / 'toolchain-setup.log'

    def log(message: str) -> None:
        print(f'[Cinter setup] {message}', flush=True)
        with transcript_path.open('a', encoding='utf-8') as handle:
            handle.write(message + '\n')

    def record() -> None:
        audit_file.write_text(json.dumps(audit, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')

    configure_output: list[str] = []

    def run_command(argv: list[str], *, cwd: Path, label: str, env: dict[str, str] | None = None) -> int:
        log(f'{label} START: {shlex.join(argv)} (cwd={cwd})')
        try:
            with subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True,
                                  errors='replace', bufsize=1) as process:
                assert process.stdout is not None
                for line in process.stdout:
                    if label == 'CONFIGURE':
                        configure_output.append(line.rstrip())
                    log(f'{label}: {line.rstrip()}')
                rc = process.wait()
            log(f'{label} EXIT: {rc}')
            return rc
        except OSError as exc:
            log(f'{label} ERROR: {type(exc).__name__}: {exc}')
            return 127

    log(f'BEGIN {row["Name"]}/{target}; CSV={CSV}')
    try:
        if not checkout.is_dir():
            audit['errors'].append(f'Source checkout is missing: {checkout}; run ./Cinter clone {row["Name"]}')
            return False
        # Toolchain commands must execute on their designated host, never
        # accidentally run a macOS package installer or configure on Linux.
        expected_host = 'Darwin' if target == 'mac' else 'Linux'
        if host_platform.system() != expected_host:
            audit['errors'].append(f'{target} setup requires {expected_host} host; current host is {host_platform.system()}')
            return False
        if target == 'win':
            # Cross-compilation only. Check for the real compiler before setup.
            cross = shutil.which('x86_64-w64-mingw32-gcc')
            if not cross:
                audit['errors'].append('Windows x64 cross-compiler x86_64-w64-mingw32-gcc not found on Linux host')
                return False
            audit['cross_compiler'] = cross

        packages = (list(instructions['preparation']['packages']) if instructions is not None else
                    [name.strip() for name in row.get(f'{target} Setup Packages', '').split(';') if name.strip()])
        if len(set(packages)) != len(packages):
            audit['errors'].append('Duplicate entries in Setup Packages CSV field')
            return False
        if packages:
            manager = 'brew' if target == 'mac' else 'apt-get'
            binary = shutil.which(manager)
            if not binary:
                audit['errors'].append(f'Configured setup packages require {manager}, but it is unavailable')
                return False
            for name in packages:
                if not re.fullmatch(r'[A-Za-z0-9+_.@-]+', name) or name.startswith('-'):
                    audit['errors'].append(f'Invalid package name in CSV: {name!r}')
                    continue
                package = {'name': name, 'manager': manager, 'status': 'pending'}
                audit['packages'].append(package)
                if target == 'mac':
                    installed = subprocess.run([binary, 'list', '--versions', name],
                                               capture_output=True, text=True, check=False)
                    package['before'] = (installed.stdout or installed.stderr).strip()[:1000]
                    if installed.returncode == 0 and installed.stdout.strip():
                        package['status'] = 'already_installed'
                        log(f'PACKAGE PRESENT: {name}: {package["before"]}')
                        continue
                    command = [binary, 'install', name]
                else:
                    installed = subprocess.run(['dpkg-query', '-W', '-f=${Status} ${Version}', name],
                                               capture_output=True, text=True, check=False)
                    package['before'] = installed.stdout.strip()[:1000]
                    if installed.returncode == 0 and 'install ok installed' in installed.stdout:
                        package['status'] = 'already_installed'
                        log(f'PACKAGE PRESENT: {name}: {package["before"]}')
                        continue
                    # Never invoke sudo/password prompts from unattended Setup.
                    if hasattr(os, 'geteuid') and os.geteuid() != 0:
                        package['status'] = 'needs_root'
                        audit['errors'].append(f'{name}: apt-get installation needs root; setup will not use sudo')
                        continue
                    command = [binary, 'install', '-y', name]
                rc = run_command(command, cwd=checkout, label=f'PACKAGE {name}')
                package['status'] = 'installed' if rc == 0 else 'failed'
                package['exit_code'] = rc
                if rc:
                    audit['errors'].append(f'{manager} install {name} failed (exit {rc})')
        else:
            log(f'No {target} Setup Packages configured in CSV; no package installation attempted')

        # Expose each CSV-authorized Homebrew package to pkg-config and
        # compiler/linker discovery. Keg-only formulae (e.g. libarchive) are
        # not present on pkg-config's default search path. These paths are
        # per-process only and never alter the user's shell or global links.
        configure_env = os.environ.copy()
        environment_added: dict[str, str] = {}
        if target == 'mac' and packages and not audit['errors']:
            brew = shutil.which('brew')
            include_dirs: list[str] = []
            lib_dirs: list[str] = []
            pkgconfig_dirs: list[str] = []
            for package in audit['packages']:
                if package.get('status') not in ('already_installed', 'installed'):
                    continue
                name = package['name']
                result = subprocess.run([brew, '--prefix', name], capture_output=True,
                                        text=True, check=False) if brew else None
                prefix = Path(result.stdout.strip()) if result and result.returncode == 0 and result.stdout.strip() else None
                if prefix is None or not prefix.is_dir():
                    audit['errors'].append(f'Homebrew {name} prefix missing after preparation')
                    continue
                package['prefix'] = str(prefix)
                if (prefix / 'include').is_dir():
                    include_dirs.append(str(prefix / 'include'))
                if (prefix / 'lib').is_dir():
                    lib_dirs.append(str(prefix / 'lib'))
                for subdir in ('lib/pkgconfig', 'share/pkgconfig'):
                    location = prefix / subdir
                    if location.is_dir():
                        pkgconfig_dirs.append(str(location))
                if name == 'boost':
                    if not (prefix / 'include/boost/version.hpp').is_file():
                        audit['errors'].append('Homebrew Boost headers not found after installation')
                    else:
                        configure_env['BOOST_ROOT'] = str(prefix)
                        environment_added['BOOST_ROOT'] = str(prefix)
                if name == 'jpeg-turbo':
                    header = prefix / 'include/jpeglib.h'
                    if not header.is_file():
                        audit['errors'].append(f'Homebrew jpeg-turbo header missing: {header}')
                    else:
                        package['header'] = str(header)
                        log(f'JPEG HEADER READY: {header}')
                if name == 'libarchive':
                    if not (prefix / 'include/archive.h').is_file():
                        audit['errors'].append('Homebrew libarchive header archive.h not found')
                    if not (prefix / 'lib/pkgconfig/libarchive.pc').is_file():
                        audit['errors'].append('Homebrew libarchive pkg-config file libarchive.pc not found')
                log(f'PACKAGE PATHS: {name}: prefix={prefix} '
                    f'pkgconfig={(prefix / "lib/pkgconfig").is_dir()}')

            def add_paths(key: str, values: list[str], *, separator: str) -> None:
                existing = configure_env.get(key, '')
                combined = separator.join(dict.fromkeys(values))
                if combined:
                    configure_env[key] = separator.join(x for x in (combined, existing) if x)
                    environment_added[key] = configure_env[key]

            add_paths('PKG_CONFIG_PATH', pkgconfig_dirs, separator=os.pathsep)
            add_paths('CPPFLAGS', [f'-I{x}' for x in include_dirs], separator=' ')
            add_paths('CXXFLAGS', [f'-I{x}' for x in include_dirs], separator=' ')
            add_paths('LDFLAGS', [f'-L{x}' for x in lib_dirs], separator=' ')
            audit['environment'] = environment_added
            log(f'PACKAGE SEARCH ENVIRONMENT: {json.dumps(environment_added, sort_keys=True)}')
            if 'libarchive' in packages and not audit['errors']:
                pkg_config = shutil.which('pkg-config')
                if not pkg_config:
                    audit['errors'].append('pkg-config unavailable for libarchive verification')
                else:
                    verification = subprocess.run([pkg_config, '--modversion', 'libarchive'],
                                                  env=configure_env, capture_output=True,
                                                  text=True, check=False)
                    if verification.returncode:
                        audit['errors'].append('libarchive is installed but pkg-config cannot find it: '
                                               + verification.stderr.strip()[:400])
                    else:
                        version = verification.stdout.strip()
                        audit['libarchive_pkgconfig_version'] = version
                        log(f'LIBARCHIVE READY: pkg-config version={version}')
        command_string = row.get(f'{target} Setup Command', '').strip()
        if instructions is not None:
            argv = list(instructions['configure']['argv'])
        elif command_string:
            try:
                argv = shlex.split(command_string)
            except ValueError as exc:
                audit['errors'].append(f'Invalid Setup Command: {exc}')
                return False
        else:
            argv = []
        if argv:
            if not argv or argv[0].startswith('-') or any(x in (';', '|', '&&', '||', '>', '<') for x in argv):
                audit['errors'].append('Invalid Setup Command arguments')
                return False
            command_path = checkout / argv[0]
            if argv[0].startswith('./') and not command_path.is_file():
                audit['errors'].append(f'Setup executable is missing: {command_path}')
                return False
            if audit['errors']:
                log('Configure SKIPPED because package preparation is incomplete')
                return False
            rc = run_command(argv, cwd=checkout, label='CONFIGURE', env=configure_env)
            audit['configure'] = {'argv': argv, 'exit_code': rc}
            audit['configure_checks'] = parse_configure_checks('\n'.join(configure_output))
            if rc:
                audit['errors'].append(f'Configure failed (exit {rc}); see {transcript_path}')
        else:
            audit['errors'].append(f'No {target} Setup Command configured in Creative Suite CSV; readiness not established')
        audit['configured'] = not audit['errors'] and audit['configure'] is not None and audit['configure']['exit_code'] == 0
        # Setup cannot establish build readiness without completed compilation.
        audit['ready'] = False
        return audit['configured']
    finally:
        for error in audit['errors']:
            log(f'GAP: {error}')
        log(f'END configured={audit["configured"]} build_ready={audit["ready"]}; setup log={transcript_path}')
        record()


def synchronize_application_branding(args: argparse.Namespace) -> None:
    """Synchronize source artwork before a real single-application Setup."""
    if not args.dry_run and args.app in {'cf3d', 'cfaudio', 'cfdocument', 'cfpaint', 'cfphoto', 'cfvector', 'cfvideo'}:
        from shared.source.branding_assets import sync as sync_branding, report as report_branding
        repository_parent = cinterforgeWorkspaceRootPath()
        branding_result = sync_branding(args.app, PLATFORMS.get(args.platform),
                                       [repository_parent], inspect=False)
        report_branding(branding_result, inspect=False)
        if branding_result['errors']:
            raise RuntimeError('Branding source checkout missing; cannot complete Setup')


def setup_application_batch(args: argparse.Namespace) -> int:
    """Run each inventory application independently and write a complete batch report."""
    if args.app == 'all':
        if args.platform == 'all':
            if args.dry_run:
                # The OS is not a Creative Suite recipe and must be checked
                # through its own public entry point, even during discovery.
                for platform in ('mac', 'linux'):
                    setup_managed_operating_system(args, platform)
                return run_full_discovery_dry_run()
            # All platforms have distinct app recipes, but CFOS is a single
            # sibling product. Provision it only once before the sweeps.
            setup_managed_operating_system(args, 'mac')
            per_platform = argparse.Namespace(**vars(args))
            per_platform._cfos_prepared = True
            failures = 0
            for platform in ('mac', 'linux', 'win'):
                per_platform.platform = platform
                if setup_application_batch(per_platform):
                    failures += 1
            return 1 if failures else 0
        if args.dry_run:
            raise RuntimeError('Use ./Cinter setup all all --dry-run for discovery')
        if not getattr(args, '_cfos_prepared', False) and args.platform == 'mac':
            setup_managed_operating_system(args, 'mac')
        # The inventory defines coverage; each recipe declares eligibility and
        # the actual Setup executor. No application names or exceptions are
        # embedded in the batch runner.
        # Every inventory row has one terminal outcome. A failed recipe or Setup
        # cannot prevent later applications from running or the report being written.
        records = []
        report_root = cinterforgeBuildsRootPath() / 'CreativeSuite' / 'Reports' / 'Setup' / args.platform
        run_dir = report_root / datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        run_dir.mkdir(parents=True, exist_ok=True)
        try:
            rows = read_application_inventory()
            if not rows:
                raise RuntimeError(f'No applications defined in {CSV}')
        except Exception as exc:
            rows = [{'Name': '(inventory)'}]
            inventory_error = f'{type(exc).__name__}: {exc}'
        else:
            inventory_error = None
        for index, row in enumerate(rows, 1):
            name = (row.get('Name') or '').strip()
            entry = {'application': name, 'platform': args.platform,
                     'status': 'failed', 'exit_code': None, 'detail': '',
                     'build_ready': False}
            records.append(entry)
            log_path = run_dir / f'{index:02d}-{re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-") or "unnamed"}.log'
            entry['log'] = str(log_path)
            try:
                if inventory_error is not None:
                    raise RuntimeError(inventory_error)
                if not name:
                    raise ValueError(f'Unnamed Creative Suite inventory entry in {CSV}')
                if name.casefold().replace('-', '') == 'cinterforgeos':
                    entry.update(status='passed', exit_code=0,
                                 detail='CFOS Ubuntu prerequisite verified; standalone OS lifecycle is separate')
                    log_path.write_text(entry['detail'] + '\n', encoding='utf-8')
                    continue
                path = recipe_path(name, PLATFORMS[args.platform])
                recipe = read_recipe(path, application=name, target=PLATFORMS[args.platform])
                if recipe is None:
                    raise ValueError(f'Missing application recipe: {path}. Declare frozen CFOS Ubuntu requirements; no provisioning in Setup')
                sweep = recipe.get('setup_sweep', {})
                if not isinstance(sweep, dict) or not isinstance(sweep.get('included', True), bool):
                    raise ValueError(f'Invalid setup_sweep in {path}')
                if not sweep.get('included', True):
                    reason = sweep.get('reason', '')
                    if not isinstance(reason, str) or not reason.strip():
                        raise ValueError(f'Excluded route needs a reason in {path}')
                    entry.update(status='excluded', detail=reason)
                    log_path.write_text(f'EXCLUDED: {reason}\n', encoding='utf-8')
                    continue
                setup_spec = recipe.get('dependency_setup')
                if args.platform == 'mac' and (not isinstance(setup_spec, dict) or setup_spec.get('executor') not in ('python', 'prebuilt')):
                    raise ValueError(f'Missing or invalid dependency_setup executor in {path}')
                print(f'[Cinter setup all/{args.platform}] [{index}/{len(rows)}] BEGIN {name}', flush=True)
                with log_path.open('w', encoding='utf-8') as log:
                    process = subprocess.Popen(
                        [sys.executable, str(Path(__file__).resolve()), name, args.platform],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
                    assert process.stdout is not None
                    for line in process.stdout:
                        print(line, end='', flush=True)
                        log.write(line)
                    result = process.wait()
                entry['exit_code'] = result
                entry['status'] = 'passed' if result == 0 else 'failed'
                entry['detail'] = 'Setup completed; Build READY not inferred' if result == 0 else f'Setup exited {result}'
            except Exception as exc:
                entry['detail'] = f'{type(exc).__name__}: {exc}'
                log_path.write_text(entry['detail'] + '\n', encoding='utf-8')
            print(f'[Cinter setup all/{args.platform}] {entry["status"].upper()} {name}: {entry["detail"]}', flush=True)
        counts = {status: sum(r['status'] == status for r in records)
                  for status in ('passed', 'failed', 'excluded')}
        report = {'platform': args.platform, 'inventory': str(CSV), 'counts': counts,
                  'build_ready_inferred': False, 'applications': records}
        (run_dir / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        lines = [f'# Creative Suite Setup All — {args.platform}', '',
                 f'Passed: {counts["passed"]} · Failed: {counts["failed"]} · Excluded: {counts["excluded"]}',
                 '', '| Application | Status | Exit | Detail |', '| --- | --- | --- | --- |']
        for item in records:
            details = item['detail'].replace('|', r'\|').replace('\n', ' ')
            lines.append(f'| {item["application"]} | {item["status"]} | {item["exit_code"] if item["exit_code"] is not None else "—"} | {details} |')
        lines.extend(['', 'Setup completion does not establish Build READY.', ''])
        (run_dir / 'report.md').write_text('\n'.join(lines), encoding='utf-8')
        print(f'[Cinter setup all/{args.platform}] FINAL REPORT: {run_dir / "report.json"}', flush=True)
        print(f'[Cinter setup all/{args.platform}] RESULT: {counts}', flush=True)
        return 1 if counts['failed'] else 0


def setup_managed_operating_system(args: argparse.Namespace, target: str) -> int:
    """Delegate platform setup to the sibling CinterForgeOS public lifecycle."""
    os_entry = cinterforgeSourceRootPath().parent / 'CinterForgeOS/CFOS'
    if not os_entry.is_file():
        raise RuntimeError(f'CinterForgeOS repository missing: {os_entry}')
    if target not in ('mac', 'linux'):
        raise RuntimeError(f'CinterForgeOS does not support {target}')
    command = ['bash', str(os_entry), 'setup', 'cinterforge-os', target, 'Release']
    if args.dry_run:
        command.append('--dry-run')
    subprocess.run(command, check=True)
    return 0



def verify_frozen_ubuntu_application(args: argparse.Namespace, target: str) -> int:
    """Linux/Windows Setup is a read-only requirements check, NEVER provisioning."""
    row = lookup_application_in_inventory(args.app)
    path = recipe_path(row['Name'], target)
    recipe = read_recipe(path, application=row['Name'], target=target)
    if recipe is None:
        raise RuntimeError(
            f'No {target} application requirements recipe for {args.app}: {path}. '
            'Audit the app dependencies and declare cfos_ubuntu_requirements; '
            'rebuild CFOS Linux if it lacks packages or SDKs.')
    requirements = recipe.get('cfos_ubuntu_requirements')
    if not isinstance(requirements, dict):
        # Audit existing recipe declarations without executing the legacy
        # package/image provisioning scripts. An inferred checklist is useful
        # for reporting gaps but is never sufficient to certify readiness.
        requirements = {'schema': 'cinterforge.cfos.app-requirements.v1',
                        'complete': False, 'tools': [], 'packages': [],
                        'pkg_config': [], 'files': [], 'versions': []}
        if target == 'linux':
            spec = recipe.get('linux_amd64_build_dependencies') or {}
            if isinstance(spec.get('apt_packages'), list):
                requirements['packages'] = spec['apt_packages']
        if target == 'win':
            requirements['tools'] = ['x86_64-w64-mingw32-gcc', 'x86_64-w64-mingw32-g++',
                                     'clang-cl', 'lld-link', 'wine']
            spec = recipe.get('windows_cross_dependencies') or {}
            lock = spec.get('locked_toolchain') or {}
            if lock.get('compiler_major') == 10:
                requirements['versions'] = [{'tool': 'x86_64-w64-mingw32-gcc',
                                             'args': ['-dumpfullversion'], 'prefix': '10.'}]
        print(f'[Cinter setup] PARTIAL requirements derived from {path}; '
              'declare cfos_ubuntu_requirements with complete=true after app-specific audit', flush=True)
    logs = (cinterforgeBuildsRootPath() / 'CreativeSuite' / 'Reports' / 'Setup'
            / target / 'FrozenUbuntu' / recipe['identity']['application'])
    logs.mkdir(parents=True, exist_ok=True)
    manifest = logs / 'requirements.json'
    manifest.write_text(json.dumps(requirements, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    cfos = cinterforgeSourceRootPath().parent / 'CinterForgeOS/CFOS'
    if not cfos.is_file():
        raise RuntimeError(f'CFOS Linux is required for {args.app}/{target}: {cfos}')
    # CFOS is the ONLY owner of the host adapter and installed-image probing.
    # This does not call the platform's legacy setup.py or build a Docker layer.
    subprocess.run(['bash', str(cfos), 'verify-app', 'cinterforge-os', 'linux',
                    'Release', recipe['identity']['application'], target, str(manifest)], check=True)
    return 0


def setup_source_application(args: argparse.Namespace, target: str) -> int:
    """Prepare the application recipe, dependencies, and upstream toolchain evidence."""
    row = lookup_application_in_inventory(args.app)
    if args.dry_run:
        # Single-target setup must use the same upstream evidence discovery as
        # `setup all all --dry-run`, including Ardour's official nightly URLs.
        # Merely looking for a CSV log column silently lost those references.
        item = discover_application_requirements(row, target)
        print_application_discovery_report(item)
        print('[Cinter setup] Read-only discovery; no downloads to source, installs, or checkout changes.')
        return 0 if item['status'] in ('partial_evidence', 'evidence_no_versions', 'prebuilt_requires_component_provenance') else 1
    # Consume existing Inspect evidence without executing or promoting its
    # candidate. This preserves the known-good authored build instructions.
    review = review_inspected_recipe(row['Name'], target, cinterforgeBuildsRootPath())
    if review['status'] == 'not_inspected':
        print(f'[Cinter setup] INSPECT NOT RUN: {review["instruction"]}', flush=True)
    elif review['status'] == 'incomplete':
        print(f'[Cinter setup] INSPECTION INCOMPLETE: {review["instruction"]}', flush=True)
    else:
        print(f'[Cinter setup] INSPECT MATCH: {review["path"]} '
              f'(plan={review.get("candidate_plan_status", "not_audited")}; '
              'authored build plan preserved; requirements not yet certified)', flush=True)
        for conflict in review.get('historical_evidence_conflicts', []):
            print(f'[Cinter setup] HISTORICAL INSPECTION WARNING: {conflict}', flush=True)
    acquisition = (row.get('Source Acquisition') or 'source').casefold()
    if target in ('linux', 'win'):
        return verify_frozen_ubuntu_application(args, target)
    if acquisition == 'prebuilt':
        # Prebuilt products declare their acquisition adapters in the recipe.
        # Setup validates those declarations without downloading an application.
        app_recipe = read_recipe(recipe_path(row['Name'], target),
                                 application=row['Name'], target=target)
        if app_recipe is None:
            raise RuntimeError(f'Missing prebuilt product recipe: {row["Name"]}/{target}')
        setup_spec = app_recipe.get('dependency_setup', {})
        if setup_spec.get('executor') != 'prebuilt':
            raise RuntimeError(f'Prebuilt product requires a prebuilt Setup declaration: {row["Name"]}/{target}')
        suite = Path(__file__).resolve().parents[2]
        contracts = app_recipe.get('acquisition_contract', {})
        for field in ('existing_build_adapter', 'existing_stage_adapter'):
            relative = Path(contracts.get(field, ''))
            if not str(relative) or relative == Path('.') or relative.is_absolute() or '..' in relative.parts:
                raise RuntimeError(f'Invalid {field} in {row["Name"]}/{target}: {relative}')
            adapter = suite / relative
            if not adapter.is_file():
                raise RuntimeError(f'Missing prebuilt {field}: {adapter}')
        print(f'[Cinter setup] PREPARED {row["Name"]}/{target}: '
              'declared prebuilt acquisition adapters exist; Build remains unverified', flush=True)
        return 0
    if acquisition != 'source':
        raise RuntimeError(f'Unknown Source Acquisition for {row["Name"]}: {acquisition}')
    upstream = row.get('Git Repo URL')
    if not upstream:
        raise RuntimeError(f'{args.app} has no Git Repo URL in {CSV}')
    if urlparse(upstream).scheme != 'https':
        raise RuntimeError(f'Invalid upstream Git URL: {upstream}')
    repo_name = urlparse(upstream).path.rstrip('/').split('/')[-1].removesuffix('.git')
    if not re.fullmatch('[A-Za-z0-9_.-]+', repo_name):
        raise RuntimeError(f'Invalid upstream repository name: {repo_name!r}')
    cinter_root = cinterforgeSourceRootPath()
    source_root = cinterforgeWorkspaceRootPath()
    source_checkout = cinterforgeSiblingRepoPath(repo_name)
    urls = application_evidence_urls(row, target)
    output_root = cinterforgeBuildsRootPath() / 'CreativeSuite' / 'Built' / repo_name / target / '2026'
    logs_dir = output_root / 'Logs'
    # A single app/platform recipe is authoritative. Setup executors are
    # declared in recipe.json; Build never invokes dependency provisioning.
    app_recipe_file = recipe_path(row['Name'], target)
    if app_recipe_file.is_file():
        recipe = read_recipe(app_recipe_file, application=row['Name'], target=target)
        # Upstream Git repository names identify sources, never product Build roots.
        # Setup must provision the same application-ID directory used by Build.
        output_root = (cinterforgeBuildsRootPath() / 'CreativeSuite' / 'Built'
                       / recipe['identity']['application'] / target / '2026')
        logs_dir = output_root / 'Logs'
        setup_spec = recipe.get('dependency_setup')
        if setup_spec is None:
            raise RuntimeError(f'Missing dependency_setup declaration in {app_recipe_file}')
        if setup_spec is not None:
            if setup_spec.get('executor') != 'python':
                raise RuntimeError(f'Unsupported dependency_setup executor: {setup_spec}')
            suite_root = Path(__file__).resolve().parents[2]
            relative = Path(setup_spec['script'])
            if relative.is_absolute() or '..' in relative.parts:
                raise RuntimeError(f'Unsafe dependency setup script: {relative}')
            script = suite_root / relative
            if not script.is_file():
                raise RuntimeError(f'Dependency setup script does not exist: {script}')
            args_list = setup_spec.get('args', [])
            if not isinstance(args_list, list) or not all(isinstance(v, str) for v in args_list):
                raise RuntimeError('dependency_setup.args must be a string array')
            # A platform-specific app recipe must never execute a different
            # platform's dependency builder. Shared scripts outside apps/ are
            # still allowed for other Creative Suite applications.
            app_scripts = suite_root / 'apps' / repo_name
            if script.is_relative_to(app_scripts):
                expected_scripts = app_scripts / target
                if not script.is_relative_to(expected_scripts):
                    raise RuntimeError(
                        f'{repo_name}/{target} recipe invokes the wrong platform setup: {relative}; '
                        f'expected a script beneath apps/{repo_name}/{target}/'
                    )
            # Setup runs before Build. Resolve the same persistent product version
            # without creating a fresh build identifier or taking a different
            # platform's version. Build will reuse this versioned dependency tree.
            setup_env = os.environ.copy()
            setup_env['CINTER_HOPE_ROOT'] = str(cinter_root)
            setup_env['CINTER_SCRIPTS_ROOT'] = str(suite_root)
            if not setup_env.get('CINTER_INSTALL_VERSION'):
                marker = output_root.parent / 'CURRENT_VERSION'
                version = marker.read_text(encoding='utf-8').strip() if marker.is_file() else '2026'
                if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', version):
                    raise RuntimeError(f'Invalid installed-version selector for {repo_name}/{target}: {version!r}')
                setup_env['CINTER_INSTALL_VERSION'] = version
            # Dependency Setup must share the exact versioned build root with Build.
            # Do not inherit a stale caller-supplied root or put outputs in Cinter.
            setup_env['CINTER_BUILT_ROOT'] = str(
                output_root.parent / setup_env['CINTER_INSTALL_VERSION']
            )
            print(f'[Cinter setup] PREPARE {repo_name}/{target} version={setup_env["CINTER_INSTALL_VERSION"]} from {app_recipe_file}', flush=True)
            print(f'[Cinter setup] EXEC {script} {" ".join(args_list)}', flush=True)
            subprocess.run([sys.executable, str(script), *args_list], check=True, env=setup_env)
            return 0
    state = {'application': row['Name'], 'platform': target, 'upstream': upstream,
             'source': str(source_checkout), 'evidence_urls': urls,
             'host_os': host_platform.system(), 'host_arch': host_platform.machine(),
             'detected_compilers': {}, 'toolchain_resolved': False}
    print(f'[Cinter setup] {row["Name"]} {target}: upstream {upstream}')
    print(f'[Cinter setup] source checkout: {source_checkout}')
    # Source acquisition leaves upstream submodules as gitlinks. Initialize them
    # only when preparing a developer build, retaining their native paths and
    # pinned commits; never flatten or relocate their .git/modules metadata.
    if (source_checkout / '.gitmodules').is_file():
        if not (source_checkout / '.git').exists():
            raise RuntimeError(f'Source checkout has .gitmodules but no Git metadata: {source_checkout}')
        git_args = ['git', '-C', str(source_checkout), 'submodule', 'update', '--init', '--recursive']
        if args.dry_run:
            print(f'[Cinter setup] DRY RUN: initialize pinned submodules recursively in {source_checkout}')
        else:
            print(f'[Cinter setup] Initializing pinned upstream submodules in place: {source_checkout}', flush=True)
            completed = subprocess.run(git_args, check=False)
            if completed.returncode:
                raise RuntimeError(f'Submodule initialization failed ({completed.returncode}); source preserved at {source_checkout}')
            print('[Cinter setup] Submodules initialized in their upstream-defined paths', flush=True)
    for binary in ('cc', 'c++', 'clang', 'clang++', 'gcc', 'g++', 'cmake', 'meson', 'ninja', 'git'):
        path = shutil.which(binary)
        if path:
            version = subprocess.run([path, '--version'], capture_output=True, text=True, check=False)
            state['detected_compilers'][binary] = {'path': path, 'version': (version.stdout or version.stderr).splitlines()[:1]}
    # Follow the same CSV-driven evidence retrieval and error accounting as
    # the all-target discovery sweep. Failures are recorded, not guessed.
    evidence = discover_application_requirements(row, target)
    state['requirements'] = evidence['requirements']
    state['evidence_errors'] = evidence['errors']
    state['evidence_status'] = evidence['status']
    print_application_discovery_report(evidence)
    if not args.dry_run:
        logs_dir.mkdir(parents=True, exist_ok=True)
        report = logs_dir / 'toolchain-discovery.json'
        report.write_text(json.dumps(state, indent=2) + '\n', encoding='utf-8')
        print(f'[Cinter setup] discovery report: {report}')
    instructions = read_recipe(recipe_path(row['Name'], target), application=row['Name'], target=target)
    if instructions is not None:
        print(f'[Cinter setup] Using persistent recipe instructions: {recipe_path(row["Name"], target)}', flush=True)
    configured = False
    try:
        configured = prepare_application_environment(row, target, source_checkout, logs_dir, evidence, instructions)
    finally:
        setup_file = logs_dir / 'toolchain-setup.json'
        setup_audit = json.loads(setup_file.read_text(encoding='utf-8')) if setup_file.is_file() else None
        # Retain a successful completed Build only while configure still passes.
        if setup_audit is not None:
            previous_build = (instructions or {}).get('build', {})
            build_succeeded = bool(configured and previous_build.get('verified') and
                                   previous_build.get('last_result', {}).get('success'))
            setup_audit['build_succeeded'] = build_succeeded
            setup_audit['ready'] = build_succeeded
            setup_file.write_text(json.dumps(setup_audit, indent=2) + '\n', encoding='utf-8')
        recipe = retain_recipe_instructions(make_recipe(row, target, evidence, setup_audit), instructions)
        recipe['toolchain']['detected'] = state['detected_compilers']
        destination = recipe_path(row['Name'], target)
        write_recipe(recipe, destination)
        # The same JSON emitted in repository is included alongside build diagnostics.
        write_recipe(recipe, logs_dir / 'recipe.json')
        print(f'[Cinter setup] RECIPE: {destination} (ready={recipe["readiness"]["ready"]})', flush=True)
    build_ready = recipe["readiness"]["ready"]
    print(f'[Cinter setup] Configure: {"PASS" if configured else "FAIL"}; Build readiness: {"READY" if build_ready else "BLOCKED"}; see {logs_dir / "toolchain-setup.json"}')
    return 0 if configured else 1


def main() -> int:
    parser = argparse.ArgumentParser(description='Prepare/discover application developer toolchains')
    parser.add_argument('app', help='Creative Suite application name')
    parser.add_argument('platform', choices=sorted(set(PLATFORMS) | {'all'}))
    # Setup provisions platform dependencies, independent of build configuration.
    # Accept the lifecycle selector even when an older Cinter dispatcher forwards it.
    parser.add_argument('configuration', nargs='?', choices=('Debug', 'Release', 'New'))
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    # Every actual application Setup depends on CFOS's already-tested single
    # Ubuntu AMD64 Release image. CFOS Linux Setup itself remains independent.
    # Dry-run discovery must not require Docker, Steam or an existing image.
    if not args.dry_run and args.app.casefold().replace('-', '') != 'cinterforgeos':
        # A single tested Ubuntu Release is the shared Linux+Windows Build/Test
        # foundation. Cinter never accesses CFOS's internal Python modules or
        # silently builds the image while preparing application dependencies.
        os_entry = cinterforgeSourceRootPath().parent / 'CinterForgeOS/CFOS'
        if not os_entry.is_file():
            raise RuntimeError(f'Missing sibling CFOS public lifecycle: {os_entry}')
        subprocess.run(['bash', str(os_entry), 'verify', 'cinterforge-os',
                        'linux', 'Release'], check=True)
    # Branding/source synchronization is authored asset production, not a
    # Linux/Windows read-only toolchain Setup side effect.
    if args.platform in ('mac', 'macos'):
        synchronize_application_branding(args)

    if args.app == 'all':
        return setup_application_batch(args)

    target = PLATFORMS[args.platform]
    if args.app.casefold().replace('-', '') == 'cinterforgeos':
        return setup_managed_operating_system(args, target)

    return setup_source_application(args, target)

if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print(f'[Cinter setup] ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
