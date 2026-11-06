#!/usr/bin/env python3
"""Read-only multi-platform source/recipe evidence for the entire Creative Suite.

Never downloads sources, modifies checkouts, or infers build readiness from
file presence. Reports replace prior versions in visible external Builds.
"""
from __future__ import annotations
import argparse
import copy
import importlib.util
import hashlib
import json
import os
import re
import subprocess
import shutil
import sys
from functools import lru_cache
from pathlib import Path

SUITE = Path(__file__).resolve().parents[2]
PLATFORMS = ('mac', 'linux', 'win')
CONFIG_NAMES = {'CMakeLists.txt','CMakePresets.json','meson.build','meson_options.txt',
                'meson.options','configure','configure.ac','Makefile','Makefile.am',
                'waf','wscript','Cargo.toml','package.json','pyproject.toml',
                'requirements.txt','vcpkg.json','conanfile.py','conanfile.txt',
                'Dockerfile','build.gradle','gradlew','go.mod','autogen.sh',
                'README.md','README','INSTALL','INSTALL.md','BUILDING.md',
                'Makefile.in','CMakeUserPresets.json'}
INSTALL_EVIDENCE = re.compile(
    r'\b(?:uninstall|remove\s+application|delete\s+application|install_name_tool|\binstall\s*\(|'
    r'\binstall_dir|\binstall_destination|\binstall_prefix|CMAKE_INSTALL_PREFIX|'
    r'\bpostinstall|\bpreinstall|\bpostflight|\bpkgbuild|\bproductbuild|'
    r'\bcreate-dmg|\bmacdeployqt|\bfixup_bundle|\bBundleUtilities|'
    r'/Applications|~/Library|/Library/|/usr/local/|/opt/|\bdefaults\s+write)', re.I)
INSTALL_FILE_HINT = re.compile(
    r'(?:^|/)(?:install|uninstall|remove|deploy|package|packaging|bundle|distribution|macos|osx|darwin|'
    r'installer|postinstall|preinstall|postflight|pkgbuild|productbuild)(?:[./_-]|/|$)', re.I)
# Evidence is bounded, read-only, and includes file/line provenance; never infer
# that deletion of user preferences is safe from the presence of a path alone.

HINT = re.compile(r'find_package\s*\(|dependency\s*\(|pkg_check_modules\s*\(|\b(?:dependency|deps|feature|option|enable|disable|install|subproject)\b', re.I)


def git(root: Path, *args: str) -> str:
    proc = subprocess.run(['git', '-C', str(root), *args], text=True, capture_output=True, timeout=40)
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or f'git {args} failed in {root}')
    return proc.stdout.strip()


def installation_evidence(path: Path, tracked: list[str]) -> dict:
    files = []
    matches = []
    for rel in tracked:
        f = path / rel
        if not (INSTALL_FILE_HINT.search(rel) or f.name in CONFIG_NAMES or f.suffix.lower() in {'.pkgproj', '.pmdoc', '.wxs'}):
            continue
        if not f.is_file() or f.is_symlink():
            continue
        try:
            if f.stat().st_size > 500_000:
                continue
            lines = f.read_text(encoding='utf-8', errors='replace').splitlines()
        except OSError:
            continue
        files.append(rel)
        for n, line in enumerate(lines, 1):
            if INSTALL_EVIDENCE.search(line):
                matches.append({'file': rel, 'line': n, 'text': line.strip()[:500]})
    return {'candidateFiles': files, 'matchedLines': matches,
            'truncated': False,
            'interpretation': 'Source references are evidence only. They do not establish what a published installer changed or authorize removal of user data.'}


# Explicit source-file references are resolved relative to the referencing script,
# repository root, and conventional packaging subdirectories. This follows real
# upstream deployment scripts (e.g. CI -> packaging/macos/macos-deploy.py), instead
# of collecting only filenames that happen to match install-related keywords.
SOURCE_REFERENCE = re.compile(r"[A-Za-z0-9_./+-]+\.(?:py|sh|bash|cmake|yml|yaml|toml|json|txt|in|wxs|nsi|iss|ps1|bat|command|cfg|plist|xml)", re.I)
SOURCE_EXTENSIONS = {'.py','.sh','.bash','.cmake','.yml','.yaml','.toml','.json','.txt','.in','.wxs','.nsi','.iss','.ps1','.bat','.command','.cfg','.plist','.xml'}


def _source_priority(relative: str) -> tuple[int, int, str]:
    """Prioritize the real packaging/build entry points over broad source matches."""
    lower = relative.lower()
    basename = Path(lower).name
    if any(word in lower for word in ('uninstall', 'deploy', 'packaging/macos', 'build/macos/',
                                      'release/darwin/', 'osx-package/', 'package_spec/',
                                      'dmg/', 'installer', 'distribution')):
        rank = 0
    elif basename in ('cmakelists.txt', 'meson.build', 'wscript', 'cargo.toml',
                      'package.json', 'makefile', 'configure.ac'):
        rank = 1
    elif lower.startswith(('.github/workflows/', 'build_files/', 'packaging/', 'release/')):
        rank = 2
    else:
        rank = 3
    return rank, relative.count('/'), relative


def collect_installation_sources(repositories: list[dict], report_dir: Path) -> dict:
    """Collect real tracked build/install sources, prioritizing and deduplicating.

    Only regular tracked files from the user's existing Git checkouts are copied.
    Unique source trees are processed once even when discovered by several
    recipe aliases. The report explicitly records skipped files and coverage.
    """
    from collections import deque

    target = report_dir / 'Logs' / 'SourceFiles'
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    copied, skipped, references, duplicates = [], [], [], []
    unique_checkouts: set[Path] = set()
    for repo_index, repo in enumerate(repositories):
        if repo.get('status') != 'inspected':
            continue
        checkout = Path(repo['path']).resolve()
        if checkout in unique_checkouts:
            duplicates.append({'repository': str(checkout), 'reason': 'same Git checkout already collected'})
            continue
        unique_checkouts.add(checkout)
        tracked = set(repo.get('trackedFiles', []))
        seeds = set(repo.get('installationEvidence', {}).get('candidateFiles', []))
        for rel in tracked:
            if (Path(rel).name in CONFIG_NAMES and rel.count('/') <= 3) or (
                rel.startswith(('packaging/', 'build/macos/', 'release/darwin/',
                                'build_files/package_spec/', 'OSX-package/'))
                and Path(rel).suffix.lower() in SOURCE_EXTENSIONS):
                seeds.add(rel)
        queue = deque((rel, None) for rel in sorted(seeds, key=_source_priority))
        queued = set(seeds)
        seen: set[str] = set()
        while queue:
            rel, parent = queue.popleft()
            if rel in seen:
                continue
            seen.add(rel)
            if rel not in tracked:
                continue
            source = checkout / rel
            if not source.is_file() or source.is_symlink() or not source.resolve().is_relative_to(checkout):
                skipped.append({'repository': str(checkout), 'file': rel, 'reason': 'not a safe tracked regular file'})
                continue
            try:
                size = source.stat().st_size
            except OSError as exc:
                skipped.append({'repository': str(checkout), 'file': rel, 'reason': str(exc)})
                continue
            destination = target / f'Repository-{len(unique_checkouts)}' / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            copied.append({'repository': str(checkout), 'source': rel,
                           'copy': str(destination.relative_to(report_dir)), 'referencedBy': parent,
                           'size': size})
            if Path(rel).suffix.lower() not in SOURCE_EXTENSIONS and Path(rel).name not in CONFIG_NAMES:
                continue
            try:
                content = source.read_text(encoding='utf-8', errors='replace')
            except OSError:
                continue
            discovered = []
            for match in SOURCE_REFERENCE.finditer(content):
                value = match.group(0).strip('./')
                if not value or '$' in value or len(value) > 240:
                    continue
                for candidate in (Path(rel).parent / value, Path(value)):
                    name = candidate.as_posix()
                    if name in tracked and name not in queued:
                        queued.add(name)
                        discovered.append((name, rel))
                        references.append({'repository': str(checkout), 'from': rel, 'to': name})
                        break
            # Following referenced scripts takes precedence over unrelated seeds;
            # this avoids losing a deploy script because 300 unrelated files
            # were encountered first.
            for pair in reversed(sorted(discovered, key=lambda x: _source_priority(x[0]))):
                queue.appendleft(pair)
    report = {'copied': copied, 'skipped': skipped, 'resolvedReferences': references,
              'duplicateRepositories': duplicates,
              'coverage': {'repositoriesCollected': len(unique_checkouts),
                           'filesCopied': len(copied), 'filesSkipped': len(skipped),
                           'budgetExceeded': False},
              'limits': {'filesPerRepository': None, 'bytesPerRepository': None},
              'description': 'All discovered tracked build/install/deploy sources and referenced scripts; refreshed per run without arbitrary collection caps.'}
    write_atomic(report_dir / 'Logs' / 'SourceFilesIndex.json', report)
    return {'filesCopied': len(copied), 'directory': str(target),
            'index': str(report_dir / 'Logs' / 'SourceFilesIndex.json'),
            'referencesResolved': len(references), 'skipped': len(skipped)}


# A source archive may be complete enough to inspect build requirements even
# when the exporter omitted Git metadata. Never equate it with a verified Git
# checkout: revisions, tracked-file history and contributor totals are absent.
SNAPSHOT_BUILD_MARKERS = ('package.json', 'CMakeLists.txt', 'meson.build',
                          'wscript', 'Cargo.toml', 'Makefile', 'configure.ac')


def _source_snapshot_info(path: Path) -> dict | None:
    if not path.is_dir() or (path / '.git').exists():
        return None
    markers = [name for name in SNAPSHOT_BUILD_MARKERS if (path / name).is_file()]
    if not markers:
        return None
    # Bound the evidence inventory to the actual project entry points. A Code
    # OSS source archive has tens of thousands of files; walking its vendored
    # trees to claim they were Git-tracked would be both slow and misleading.
    evidence = list(markers)
    for directory in ('src', 'build', 'src-tauri', 'cmake', 'build-aux'):
        for name in SNAPSHOT_BUILD_MARKERS:
            item = path / directory / name
            if item.is_file() and not item.is_symlink():
                evidence.append(f'{directory}/{name}')
    return {
        'path': str(path), 'git': False, 'status': 'source_snapshot',
        'commit': None, 'branch': None, 'origin': None,
        'trackedFiles': [], 'trackedFileCount': None,
        'buildAndDocumentationFiles': sorted(set(evidence)),
        'installationEvidence': installation_evidence(path, evidence),
        'snapshotBuildMarkers': markers,
        'warning': 'Source archive without .git: build requirements are inspectable; '
                   'tracked-file coverage, history, contributor counts and revision are unavailable.',
    }

def checkout_info(path: Path) -> dict:
    result = {'path': str(path), 'git': False}
    if not path.is_dir():
        result['status'] = 'missing'
        return result
    try:
        git_root = Path(git(path, 'rev-parse', '--show-toplevel')).resolve()
        if git_root != path.resolve():
            result['status'] = 'nested_in_another_repo'
            result['gitRoot'] = str(git_root)
            return result
        result['git'] = True
        result['commit'] = git(path,'rev-parse','HEAD')
        result['branch'] = git(path,'branch','--show-current')
        result['origin'] = git(path,'remote','get-url','origin') if 'origin' in git(path,'remote').splitlines() else None
        result['dirty'] = bool(git(path,'status','--porcelain','--untracked-files=no'))
        result['submodules'] = git(path,'submodule','status','--recursive').splitlines()
        tracked = git(path,'ls-files','-z').split('\0')
        tracked = sorted(set(t for t in tracked if t))
        result['trackedFileCount'] = len(tracked)
        result['trackedFiles'] = tracked
        result['installationEvidence'] = installation_evidence(path, tracked)
        candidates = [t for t in tracked if Path(t).name in CONFIG_NAMES or
                      Path(t).suffix in {'.sln','.xcodeproj','.vcxproj','.pro','.pri'}]
        result['buildAndDocumentationFiles'] = candidates
        snippets = {}
        for rel in candidates:
            f = path / rel
            if not f.is_file() or f.stat().st_size > 512_000:
                continue
            try:
                lines = f.read_text(encoding='utf-8',errors='replace').splitlines()
            except OSError:
                continue
            matching = [{'line': i, 'text': line[:500]} for i,line in enumerate(lines,1) if HINT.search(line)]
            if matching:
                snippets[rel] = matching[:350]
        result['configurationEvidence'] = snippets
        result['status'] = 'inspected'
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as exc:
        result['status'] = 'inspection_error'
        result['error'] = str(exc)
    return result


@lru_cache(maxsize=1)
def _maintained_forks() -> dict[str, str]:
    """Read the maintained checkout names from the CFOS-owned clone registry."""
    source = SUITE.parents[2] / 'CinterForgeOS' / 'scripts' / 'fork.py'
    spec = importlib.util.spec_from_file_location('cfos_inspect_maintained_forks', source)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'Missing maintained fork registry: {source}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return dict(module.MAINTAINED_FORKS)


def _internal_web_component(app: str) -> dict | None:
    """Converter now has its own public repository, not an internal WebApp.

    Source inspection resolves it via CFOS MAINTAINED_FORKS. All actual CLI
    prerequisites belong to the CraftMaster recipe, not a fake internal app.
    """
    return None


def _internal_requirement_evidence(component: dict | None) -> list[dict]:
    """Extract installed CLI requirements from the actual internal format backend."""
    if not component or component.get('status') != 'located':
        return []
    relative = 'CinterForgeServer/cf/csRuntime/suite_formats.py'
    source = SUITE.parents[2] / relative
    expected = {
        'ffmpeg': ('ffmpeg',),
        'assimp': ('assimp',),
        'ImageMagick': ('magick', 'identify'),
    }
    lines = source.read_text(encoding='utf-8').splitlines()
    result = []
    for name, alternatives in expected.items():
        found = [(number, line.strip()) for number, line in enumerate(lines, 1)
                 if 'shutil.which(' in line and any(f'"{tool}"' in line or f"'{tool}'" in line
                                                      for tool in alternatives)]
        if found:
            result.append({
                'kind': 'runtime_executable', 'name': name,
                'alternatives': list(alternatives),
                'minimum_version': None, 'optional': False,
                'platform_condition': 'all', 'path': relative, 'line': found[0][0],
                'evidence': found[0][1][:300], 'method': 'in_repo_backend_source',
                'provider': None, 'provider_status': 'unresolved',
                'version_constraint': None, 'scope': 'server_runtime',
            })
    return result


def repository_candidates(app: str, recipes: dict, source_roots: list[Path], builds: Path) -> list[Path]:
    names = {app, app.replace('-',''), app.replace('-','_'), app.capitalize(), app.upper(), app.title()}
    fork_name = _maintained_forks().get(app.casefold())
    if fork_name:
        names.add(fork_name)
    for recipe in recipes.values():
        branding = recipe.get('branding') or {}
        if branding.get('repository'):
            names.add(branding['repository'])
        source = recipe.get('source') or {}
        if source.get('checkout_name'):
            names.add(source['checkout_name'])
    choices = []
    # Existing local checkouts are not always named using the adapter slug:
    # e.g. maintained forks are siblings of the CinterForge repository.
    # Resolve direct children case-insensitively without crawling unrelated repos.
    folded_names = {name.casefold() for name in names}
    for root in source_roots:
        if root.is_dir():
            for child in root.iterdir():
                if child.is_dir() and child.name.casefold() in folded_names:
                    choices.append(child)
    for root in source_roots:
        for name in sorted(names):
            choices.append(root / name)
    # Builds is generated output, never an authored source discovery root.
    return list(dict.fromkeys(p.resolve() for p in choices))



def inspect(app: str, source_roots: list[Path], builds: Path) -> dict:
    appdir = SUITE / 'apps' / app
    recipes = {}
    for platform in PLATFORMS:
        f = appdir / platform / 'recipe.json'
        if f.is_file():
            try:
                recipes[platform] = json.loads(f.read_text())
            except (OSError, ValueError) as exc:
                recipes[platform] = {'error': str(exc)}
    found = []
    seen_checkouts = set()
    internal_component = _internal_web_component(app)
    for candidate in ([] if internal_component else repository_candidates(app, recipes, source_roots, builds)):
        resolved = candidate.resolve()
        if resolved in seen_checkouts:
            continue
        seen_checkouts.add(resolved)
        if candidate.is_dir() and (candidate / '.git').exists():
            found.append(checkout_info(candidate))
        else:
            snapshot = _source_snapshot_info(candidate)
            if snapshot is not None:
                found.append(snapshot)
    dependency_inspection = None
    if app == 'cfaudio':
        # The old setup.inspect_ardour_windows_dependencies function was
        # removed during the frozen-Ubuntu refactor. Never import an app Setup
        # implementation from Inspect: it may install/compile toolchains.
        # Report the existing authored Windows recipe instead. Local binary/ABI
        # closure is *not* certified merely because the recipe mentions it.
        win_recipe = recipes.get('win') or {}
        dependencies = [item for item in win_recipe.get('dependencies', [])
                        if isinstance(item, dict) and item.get('name')]
        dependency_inspection = {
            'schemaVersion': 1, 'status': 'recipe_only_unverified',
            'source': f'apps/{app}/win/recipe.json',
            'dependencies': [{'name': item['name'],
                              'requiredVersion': item.get('required_version'),
                              'resolution': 'unverified_local_source_and_abi'}
                             for item in dependencies],
            'summary': {'required': len(dependencies), 'withLocalSource': 0},
        }
    return {
        'windowsDependencyInspection': dependency_inspection,
        'schemaVersion': 1, 'application': app,
        'internalSource': internal_component,
        'platforms': {p: {
            'adapterExists': (appdir/p).is_dir(),
            'recipe': recipes.get(p),
            'adapterFiles': sorted(str(f.relative_to(appdir/p)) for f in (appdir/p).rglob('*')
                                   if f.is_file() and '__pycache__' not in f.parts and len(f.parts) < len((appdir/p).parts)+5)
        } for p in PLATFORMS},
        'repositories': found,
        'repositoryStatus': ('internal_web_component' if internal_component and internal_component['status'] == 'located'
                             else 'inspected' if any(r.get('status') == 'inspected' for r in found)
                             else 'source_snapshot' if any(r.get('status') == 'source_snapshot' for r in found)
                             else 'not_found'),
        'checkedSourceRoots': [str(p) for p in source_roots],
        'expectedForkCheckout': None if internal_component else _maintained_forks().get(app.casefold()),
        'warning': 'Source analysis is evidence, not build readiness. Git-less archives are marked source_snapshot and have no verified revision or contributor history.',
    }



# The existing `Cinter inspect` owns *evidence collection*. These are candidate
# requirements for human review; never mutate the authored recipe or provision a
# toolchain while inspecting. The proven Linux IFPlayer route still lives under
# update-data and must be represented separately from the new app adapters.
REQUIREMENT_SCRIPT_HINT = re.compile(
    r"\b(?:apt(?:-get)?|brew|port|pacman|choco|winget|pip3?|npm|bun|cargo)\s+"
    r"(?:install|add|ci|build)|\b(?:cmake|meson|ninja|xcodebuild|waf|configure|"
    r"pkg-config|vcpkg|msbuild|clang|gcc|g\+\+|rustc|wine|xwin)\b|"
    r"[\"\'](?:apt-get|brew|npm|pip3?|cargo|xwin)[\"\']", re.I)
LEGACY_PRODUCER = re.compile(r'script_dir\s*/\s*[\"\']([^\"\']+)[\"\']')
LEGACY_STAGE = re.compile(r'[\"\']stage[\"\']\s*:\s*[\"\']([^\"\']+)[\"\']')


def _candidate(name: str, kind: str, provenance: str, *, version=None,
               policy=None, required=None) -> dict:
    return {'name': str(name), 'kind': kind, 'version': version, 'versionPolicy': policy,
            'required': required, 'provenance': provenance}


def _recipe_candidate_requirements(recipe: dict, provenance: str) -> list[dict]:
    """Preserve declared/observed distinctions; do not infer package providers."""
    collected: list[dict] = []
    if not isinstance(recipe, dict) or 'error' in recipe:
        return collected
    preparation = recipe.get('preparation') or {}
    for name in preparation.get('packages') or []:
        if isinstance(name, str) and name:
            collected.append(_candidate(name, 'package', provenance+'.preparation.packages',
                                        required=None))
    toolchain = recipe.get('toolchain') or {}
    for item in toolchain.get('components') or []:
        if isinstance(item, dict) and item.get('name'):
            collected.append(_candidate(item['name'], item.get('category') or 'tool',
                                        provenance+'.toolchain.components',
                                        version=item.get('required_version') or item.get('observed_version'),
                                        policy=item.get('version_policy'),
                                        required=True if item.get('required_version') else None))
    for item in recipe.get('dependencies') or []:
        if isinstance(item, dict) and item.get('name'):
            collected.append(_candidate(item['name'], 'dependency', provenance+'.dependencies',
                                        version=item.get('required_version') or item.get('observed_version'),
                                        policy=item.get('version_policy'),
                                        required=True if item.get('required_version') else None))
    declared = recipe.get('cfos_ubuntu_requirements') or {}
    if isinstance(declared, dict):
        for field, kind in (('tools','tool'),('packages','package'),
                            ('pkg_config','pkg_config'),('files','file')):
            for value in declared.get(field) or []:
                if isinstance(value, str) and value:
                    collected.append(_candidate(value, kind, provenance+'.cfos_ubuntu_requirements.'+field,
                                                required=declared.get('complete') is True))
        for value in declared.get('versions') or []:
            if isinstance(value, dict) and value.get('tool'):
                collected.append(_candidate(value['tool'], 'tool_version',
                                            provenance+'.cfos_ubuntu_requirements.versions',
                                            version=value.get('prefix'), policy='prefix',
                                            required=declared.get('complete') is True))
    spec = recipe.get('linux_amd64_build_dependencies') or {}
    for name in spec.get('apt_packages') or []:
        if isinstance(name, str) and name:
            collected.append(_candidate(name, 'apt_package',
                                        provenance+'.linux_amd64_build_dependencies.apt_packages'))
    deps = recipe.get('source_dependencies') or {}
    if isinstance(deps, dict):
        for name in deps.get('required_pkgconfig') or []:
            if isinstance(name, str) and name:
                collected.append(_candidate(name, 'pkg_config',
                                            provenance+'.source_dependencies.required_pkgconfig', required=True))
        for row in deps.get('recipes') or []:
            if isinstance(row, dict) and row.get('name'):
                collected.append(_candidate(row['name'], 'source_dependency',
                                            provenance+'.source_dependencies.recipes',
                                            version=row.get('tag'), policy='source_pin', required=True))
    for row in recipe.get('source_requirements') or []:
        if isinstance(row, dict) and row.get('name'):
            collected.append(_candidate(row['name'], row.get('kind') or 'source_requirement',
                                        provenance+'.source_requirements',
                                        version=row.get('minimum_version'), policy='source_scan',
                                        required=None if row.get('optional') is None else not row['optional']))
    # Several mac recipes define package identities solely in their preparation
    # and configure fields. Keep the exact source values in the analysis output.
    return sorted({json.dumps(row, sort_keys=True): row for row in collected}.values(),
                  key=lambda r: (r['kind'], r['name'].casefold(), r['provenance']))


def _source_requirement_evidence(repository: dict, platform: str) -> list[dict]:
    if repository.get('status') not in ('inspected', 'source_snapshot'):
        return []
    from source_requirements import scan_checkout
    root = Path(repository['path'])
    items = scan_checkout(root, platform)
    for item in items:
        item['checkout'] = str(root)
    return items


def _adapter_script_evidence(app: str, platform: str) -> list[dict]:
    """Capture command clues without running the adapter or guessing packages."""
    folder = SUITE / 'apps' / app / platform
    matches = []
    for name in ('setup.py', 'build.py', 'configure.py', 'stage.py', 'recipe.json'):
        path = folder / name
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 1_000_000:
            continue
        # Skip JSON: it is extracted structurally above, not regex-parsed.
        if path.suffix == '.json':
            continue
        for index, line in enumerate(path.read_text(encoding='utf-8', errors='replace').splitlines(), 1):
            if REQUIREMENT_SCRIPT_HINT.search(line):
                matches.append({'path': str(path.relative_to(SUITE)), 'line': index,
                                'excerpt': line.strip()[:320], 'status': 'command_evidence_only'})
    return matches[:300]


def candidate_requirements_for_app(analysis: dict) -> dict:
    """Compare recipe declarations and tracked checkout build-system evidence.

    No generated candidate is an executable Setup recipe or evidence of a
    successful build. In particular, no version inferred from a Mac recipe is
    promoted to an Ubuntu package requirement.
    """
    application = analysis['application']
    result = {'schemaVersion': 1, 'application': application,
              'kind': 'read_only_requirement_candidates', 'platforms': {},
              'sourceCheckoutStatus': analysis.get('repositoryStatus', 'unknown'),
              'acceptance': 'not_verified_by_inspect'}
    for platform in PLATFORMS:
        record = analysis['platforms'][platform]
        recipe = record.get('recipe')
        provenance = f'apps/{application}/{platform}/recipe.json'
        recipe_file = SUITE / provenance
        recipe_digest = hashlib.sha256(recipe_file.read_bytes()).hexdigest() if recipe_file.is_file() else None
        declared = _recipe_candidate_requirements(recipe, provenance)
        scanned = []
        for repository in analysis['repositories']:
            scanned.extend(_source_requirement_evidence(repository, platform))
        scanned.extend(_internal_requirement_evidence(analysis.get('internalSource')))
        declared_names = {r['name'].casefold() for r in declared}
        unmapped = [r for r in scanned if r['name'].casefold() not in declared_names]
        frozen = recipe.get('cfos_ubuntu_requirements') if isinstance(recipe, dict) else None
        result['platforms'][platform] = {
            'recipePresent': isinstance(recipe, dict) and 'error' not in recipe,
            'recipeSha256': recipe_digest,
            'frozenUbuntuContractComplete': isinstance(frozen, dict) and frozen.get('complete') is True,
            'declared': declared, 'checkoutEvidence': scanned,
            'unmappedCheckoutEvidence': unmapped,
            'adapterCommandEvidence': _adapter_script_evidence(application, platform),
            'candidateStatus': 'review_required',
        }
    return result



@lru_cache(maxsize=1)
def _load_recipe_module():
    """Share the production recipe reader and comparison contract."""
    path = SUITE / 'recipe.py'
    spec = importlib.util.spec_from_file_location('cinter_inspect_recipe_reader', path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'Cannot load Creative Suite recipe reader: {path}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_recipe_reader():
    """Use the production recipe reader; don't invent a second recipe schema."""
    return _load_recipe_module().read_recipe


def generate_inspected_recipe(analysis: dict, candidate: dict, platform: str) -> dict:
    """Emit a real recipe.v1 document while preserving every authored instruction.

    Source scans are *not* dependency locks. Store them in the inspection section
    of the candidate recipe, not in configure/build/package fields. No compiler,
    source revision, version or build command is inferred from a regex match.
    """
    app = analysis['application']
    current = analysis['platforms'][platform].get('recipe')
    authored = isinstance(current, dict) and 'error' not in current
    if authored:
        generated = copy.deepcopy(current)
        # The authored recipe may contain an OLD successful Build result. That
        # is historical evidence, never a fresh certification of this candidate.
        historical = {
            'readiness': copy.deepcopy(current.get('readiness')),
            'build_verified': current.get('build', {}).get('verified'),
            'build_last_result': copy.deepcopy(current.get('build', {}).get('last_result')),
        }
        generated.setdefault('build', {})['verified'] = False
        generated['build'].pop('last_result', None)
        generated['readiness'] = {
            'ready': False, 'configured': False, 'setup_result': None,
            'issues': ['Inspection candidate has not passed native Setup, Build, Stage and Test'],
        }
    else:
        historical = None
        # An adapter without an authored recipe still gets a readable recipe.json.
        # It is intentionally non-executable until its real build plan is known.
        repo = next((item for item in analysis['repositories']
                     if item.get('status') in ('inspected', 'source_snapshot')), None)
        generated = {
            'schema': 'cinterforge.creative-suite.recipe.v1',
            'identity': {'application': app, 'platform': platform,
                         'target_arch': 'arm64' if platform == 'mac' else 'x86_64',
                         'build_host': 'macOS' if platform == 'mac' else 'Linux x86-64',
                         'acquisition': 'unverified'},
            'source': {'checkout_name': Path(repo['path']).name if repo else None,
                       'revision': repo.get('commit') if repo else None},
            'preparation': {'packages': [], 'package_manager': None,
                            'status': 'not_declared'},
            'configure': {'system': None, 'argv': [], 'observed_result': None},
            'build': {'system': None, 'argv_by_config': {}, 'verified': False},
            'readiness': {'ready': False, 'configured': False, 'setup_result': None,
                          'issues': [f'No authored {app}/{platform} recipe; '
                                     'Setup and Build instructions must be established']},
        }
        if platform in ('linux', 'win'):
            generated['cfos_ubuntu_requirements'] = {
                'schema': 'cinterforge.cfos.app-requirements.v1',
                'complete': False, 'tools': [], 'packages': [],
                'pkg_config': [], 'files': [], 'versions': [],
            }
    source = candidate['platforms'][platform]
    # Provenance must remain checkout-relative in recipe candidates so moving
    # a source workspace doesn't change the generated build contract.
    evidence = [{key: value for key, value in row.items() if key != 'checkout'}
                for row in source['checkoutEvidence']]
    generated['inspection_generation'] = {
        'schema': 'cinterforge.inspect.recipe-generation.v1',
        'status': 'authored_recipe_preserved' if authored else 'scaffold_incomplete',
        'source_recipe_sha256': source['recipeSha256'],
        'source_checkout_status': analysis.get('repositoryStatus', 'unknown'),
        'runtime_state_reset': bool(authored),
        'authored_runtime_evidence': historical,
        'source_requirements': evidence,
        'unmapped_source_requirements': [
            {key: value for key, value in row.items() if key != 'checkout'}
            for row in source['unmappedCheckoutEvidence']],
        'source_command_evidence': source['adapterCommandEvidence'],
        'warning': 'Inspect evidence is not a dependency lock or Build/Test proof. '
                   'Review and validate before promoting this recipe to the app lifecycle.',
    }
    return generated




def audit_generated_recipe(analysis: dict, generated: dict, platform: str,
                           generated_path: Path | None = None) -> dict:
    """Audit an executable *plan*, independently of historical READY flags.

    No subprocesses, dependency provisioning, or application execution occur
    here. A complete static plan permits the first native Setup attempt; it
    does not establish that dependencies exist or that Build/Test will pass.
    """
    app = analysis['application']
    authored = isinstance(analysis['platforms'][platform].get('recipe'), dict)
    issues = []
    if not authored:
        issues.append('No authored platform recipe with a validated build adapter')
    identity = generated.get('identity') or {}
    if identity.get('application') != app or identity.get('platform') != platform:
        issues.append('Recipe identity does not match the application/platform')
    configuration = generated.get('configure') or {}
    if not isinstance(configuration.get('argv'), list) or not configuration['argv']:
        issues.append('Missing configure command')
    build = generated.get('build') or {}
    commands = build.get('argv_by_config') or {}
    if not isinstance(commands, dict) or any(
            not isinstance(commands.get(mode), list) or not commands[mode]
            for mode in ('Debug', 'Release')):
        issues.append('Missing Debug or Release build command')
    setup = generated.get('dependency_setup')
    setup_script = None
    if not isinstance(setup, dict):
        issues.append('Missing dependency_setup executor')
    elif setup.get('executor') == 'python':
        relative = setup.get('script')
        if (not isinstance(relative, str) or not relative or
                Path(relative).is_absolute() or '..' in Path(relative).parts):
            issues.append('Unsafe or missing dependency setup script declaration')
        else:
            setup_script = SUITE / relative
            if not setup_script.is_file():
                issues.append(f'Declared dependency setup script absent: {relative}')
    elif setup.get('executor') != 'prebuilt':
        issues.append(f'Unsupported dependency setup executor: {setup.get("executor")}')
    adapters = {}
    for phase in ('build', 'stage', 'test'):
        base = SUITE / 'apps' / app / platform
        located = next((p for p in (base / f'{phase}.sh', base / f'{phase}.py')
                        if p.is_file()), None)
        adapters[phase] = str(located.relative_to(SUITE)) if located else None
        if located is None:
            issues.append(f'Missing {phase} lifecycle adapter')
    original = analysis['platforms'][platform].get('recipe')
    conflicts = []
    if isinstance(original, dict):
        old_readiness = original.get('readiness') or {}
        old_build = original.get('build') or {}
        audit = old_readiness.get('setup_result') or {}
        if old_readiness.get('ready') is True and audit.get('ready') is False:
            conflicts.append('Authored readiness.ready=true conflicts with setup_result.ready=false')
        if old_build.get('verified') is True and audit.get('build_succeeded') is False:
            conflicts.append('Authored build.verified=true conflicts with setup_result.build_succeeded=false')
        if (old_readiness.get('configured') is True and
                isinstance(audit.get('configure'), dict) and
                audit['configure'].get('exit_code') not in (None, 0)):
            conflicts.append('Authored configured=true conflicts with configure failure')
    checkouts = analysis.get('repositoryStatus', 'unknown')
    if checkouts not in ('inspected', 'source_snapshot', 'internal_web_component'):
        issues.append(f'Source checkout not inspected ({checkouts})')
    source_config = generated.get('source_dependencies') or {}
    source_graph = {'status': 'not_declared', 'unresolved': [], 'validationError': None}
    if isinstance(source_config, dict) and source_config.get('schema'):
        if generated_path is None:
            source_graph['status'] = 'not_run'
        else:
            try:
                dependency_path = SUITE / 'shared/source/source_dependency_recipes.py'
                spec = importlib.util.spec_from_file_location('cinter_inspect_dependency_preflight', dependency_path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                report = module.dependency_report(generated_path)
                source_graph = {
                    'status': 'valid_declared_graph' if report['ready'] else 'invalid_declared_graph',
                    'unresolved': report['missing_pkgconfig'],
                    'validationError': report['validation_error'],
                }
                if not report['ready']:
                    issues.append('Declared source dependency graph is unresolved or invalid')
            except (ValueError, RuntimeError, KeyError, TypeError) as exc:
                source_graph = {'status': 'invalid_declared_graph',
                                'unresolved': [], 'validationError': str(exc)}
                issues.append('Declared source dependency graph failed preflight')
    status = 'plan_declared_for_native_validation' if not issues else 'incomplete_plan'
    package_config = generated.get('preparation') or {}
    return {
        'schema': 'cinterforge.inspect.recipe-acceptance.v1',
        'application': app, 'platform': platform,
        'planStatus': status, 'issues': issues,
        'historicalEvidenceConflicts': conflicts,
        'sourceDependencyGraph': source_graph,
        'declaredProviders': {
            'preparationPackages': len(package_config.get('packages') or []),
            'sourceRecipes': len(source_config.get('recipes') or []),
            'archiveRecipes': len(source_config.get('archives') or []),
            'binaryDependencies': len(generated.get('binary_dependencies') or []),
            'requiredPkgConfig': len(source_config.get('required_pkgconfig') or []),
        },
        'sourceCheckoutStatus': checkouts,
        'setupScript': str(setup_script.relative_to(SUITE)) if setup_script and setup_script.is_file() else None,
        'lifecycleAdapters': adapters,
        'nativeAcceptance': {phase: 'not_run_by_inspect' for phase in ('setup','build','stage','test')},
        'frozenUbuntuAcceptance': 'not_run_by_inspect',
        'ready': False, 'verified': False, 'promoted': False,
        'nextStep': (f'Run ./Cinter setup {app} {platform} Release on the native host '
                     'only after reviewing this candidate' if not issues else
                     'Resolve the enumerated plan issues before native Setup acceptance'),
    }


def write_inspected_recipes(analysis: dict, candidate: dict, destination: Path,
                            read_recipe) -> list[dict]:
    """Write app/platform recipe.json and a provenance comparison in Inspections.

    Never overwrite the original source recipe or execute Setup/Build here.
    Verify generated JSON using the exact reader used by the real lifecycle.
    """
    app = analysis['application']
    records = []
    for platform in PLATFORMS:
        original = analysis['platforms'][platform].get('recipe')
        authored = isinstance(original, dict) and 'error' not in original
        generated = generate_inspected_recipe(analysis, candidate, platform)
        path = destination / app / platform / 'recipe.json'
        write_atomic(path, generated)
        failures = []
        try:
            load = read_recipe(path, application=app, target=platform)
            if load is None:
                failures.append('Production recipe reader returned None')
        except (ValueError, TypeError, KeyError, OSError) as exc:
            failures.append(f'Production recipe reader rejected candidate: {exc}')
        # Preserve every executable authored instruction. Historical run-state
        # intentionally moves to inspection_generation.authored_runtime_evidence.
        preserved = (not authored or
                     _load_recipe_module().authored_instructions_preserved(generated, original))
        if not preserved:
            failures.append('An authored executable recipe instruction was changed')
        if not authored:
            failures.append('Missing authored build plan; generated scaffold is not build-ready')
        acceptance = audit_generated_recipe(analysis, generated, platform, path)
        write_atomic(path.parent / 'RecipeAcceptance.json', acceptance)
        record = {
            'application': app, 'platform': platform,
            'generatedRecipe': str(path),
            'generatedSha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'sourceRecipeSha256': candidate['platforms'][platform]['recipeSha256'],
            'authoredRecipeExists': authored,
            'authoredRecipePreserved': preserved,
            'runtimeReadinessReset': generated.get('inspection_generation', {}).get('runtime_state_reset') is True,
            'acceptanceReport': str(path.parent / 'RecipeAcceptance.json'),
            'candidatePlanStatus': acceptance['planStatus'],
            'candidateReady': acceptance['ready'],
            'historicalEvidenceConflicts': acceptance['historicalEvidenceConflicts'],
            'productionReaderAccepts': not any('reader' in issue for issue in failures),
            'sourceRequirementCount': len(candidate['platforms'][platform]['checkoutEvidence']),
            'unmappedSourceRequirements': len(candidate['platforms'][platform]['unmappedCheckoutEvidence']),
            'status': ('schema_valid_authored_recipe_preserved'
                       if not failures else 'incomplete_or_invalid'),
            'issues': failures,
            'promotedToBuildLifecycle': False,
            'buildAndTestVerifiedByInspect': False,
        }
        write_atomic(path.parent / 'RecipeComparison.json', record)
        records.append(record)
    return records


def legacy_update_data_evidence() -> dict:
    """Capture the *actual existing* IFPlayer/Web Linux acceptance entrypoint.

    The explicit producer and stage references are static evidence, not a claim
    that a conditional branch executed or that the operation already belongs to
    an app's new lifecycle recipe.
    """
    entry = SUITE.parent / 'orchestration' / 'update_data_pipeline.py'
    if not entry.is_file():
        return {'schemaVersion': 1, 'entrypoint': str(entry), 'status': 'missing',
                'producers': [], 'stages': []}
    text = entry.read_text(encoding='utf-8')
    producers, stages = [], []
    for line_no, line in enumerate(text.splitlines(), 1):
        for match in LEGACY_PRODUCER.finditer(line):
            name = match.group(1)
            if name.startswith(('interactiveFiction/', 'runtime/', 'craftmaster/')):
                producers.append({'script': name, 'line': line_no,
                                  'source': str(entry.relative_to(SUITE.parent)),
                                  'status': 'static_legacy_reference'})
        for match in LEGACY_STAGE.finditer(line):
            name = match.group(1)
            if any(token in name for token in ('interactive-fiction','collaboration',
                                               'ifplayer','frontend','client-vendor',
                                               'craftmaster')):
                stages.append({'name': name, 'line': line_no,
                               'source': str(entry.relative_to(SUITE.parent)),
                               'status': 'conditional_legacy_stage'})
    return {'schemaVersion': 1, 'entrypoint': str(entry.relative_to(SUITE.parent)),
            'sha256': hashlib.sha256(entry.read_bytes()).hexdigest(),
            'command': './Cinter setup --update-data --linux',
            'status': 'reference_only_not_migrated',
            'producers': producers, 'stages': stages,
            'interpretation': 'Historical Linux acceptance uses update-data; do not treat incomplete new recipes as its working implementation.'}


def summarize_requirement_candidates(candidates: list[dict]) -> dict:
    """Aggregate evidence, retaining platform provenance and version conflicts."""
    rows = []
    for item in candidates:
        for platform, result in item['platforms'].items():
            for declaration in result['declared']:
                rows.append({'application': item['application'], 'platform': platform,
                             **declaration})
    versions = {}
    for row in rows:
        if row['required'] is True and row.get('version'):
            key = (row['platform'], row['kind'], row['name'].casefold())
            versions.setdefault(key, set()).add(str(row['version']))
    conflicts = [{'platform': k[0], 'kind': k[1], 'name': k[2], 'versions': sorted(values)}
                 for k, values in sorted(versions.items()) if len(values) > 1]
    return {'schemaVersion': 1, 'status': 'review_only_not_a_build_lock',
            'applications': len(candidates), 'declarations': len(rows),
            'versionConflictsRequiringReview': conflicts,
            'platforms': {platform: {
                'applicationsWithRecipes': sum(item['platforms'][platform]['recipePresent'] for item in candidates),
                'applicationsWithSourceCheckouts': sum(item['sourceCheckoutStatus'] == 'inspected' for item in candidates),
                'completeFrozenUbuntuContracts': sum(item['platforms'][platform]['frozenUbuntuContractComplete'] for item in candidates),
                'declarations': sum(row['platform'] == platform for row in rows),
                'unmappedSourceChecks': sum(len(item['platforms'][platform]['unmappedCheckoutEvidence']) for item in candidates),
            } for platform in PLATFORMS},
            'warning': 'Requirements are not certified until rebuilt and tested in the public Setup/Build/Stage/Test lifecycle.'}


def write_linux_inspection(inspection_root: Path, candidates: list[dict],
                           recipe_records: list[dict], legacy: dict) -> dict:
    """Publish Linux's existing *source* inspection under its own platform folder.

    Host-side Inspect is not an Ubuntu guest, package-installation probe, or
    Build/Test. Preserve the proven legacy update-data references and the
    generated recipe comparisons without claiming any Linux runtime passed.
    The future guest inspector can add independent installed-image evidence.
    """
    linux_root = inspection_root / 'Linux'
    comparisons = {(row['application'], row['platform']): row
                   for row in recipe_records if row['platform'] == 'linux'}
    applications = []
    for candidate in candidates:
        app = candidate['application']
        evidence = candidate['platforms']['linux']
        comparison = comparisons.get((app, 'linux'))
        if comparison is None:
            raise ValueError(f'Missing generated Linux recipe comparison for {app}')
        status = ('authored_recipe_review_required' if comparison['authoredRecipeExists']
                  else 'generated_scaffold_incomplete')
        record = {
            'schemaVersion': 1,
            'platform': 'linux',
            'application': app,
            'status': status,
            'sourceRepositoryStatus': candidate['sourceCheckoutStatus'],
            'recipe': {
                'path': f'../../Sources/{app}/linux/recipe.json',
                'comparisonPath': f'../../Sources/{app}/linux/RecipeComparison.json',
                'authoredExists': comparison['authoredRecipeExists'],
                'sourceRecipeSha256': comparison['sourceRecipeSha256'],
                'generatedSha256': comparison['generatedSha256'],
                'readerAccepted': comparison['productionReaderAccepts'],
                'issues': comparison['issues'],
                'promotedToBuildLifecycle': False,
            },
            'requirements': {
                'declared': evidence['declared'],
                'sourceEvidence': evidence['checkoutEvidence'],
                'unmappedSourceEvidence': evidence['unmappedCheckoutEvidence'],
                'adapterCommands': evidence['adapterCommandEvidence'],
                'frozenUbuntuContractComplete': evidence['frozenUbuntuContractComplete'],
            },
            'verification': {
                'status': 'not_tested',
                'installedUbuntuImage': 'not_checked',
                'linuxSetup': 'not_run',
                'linuxBuild': 'not_run',
                'linuxStage': 'not_run',
                'linuxTest': 'not_run',
                'reason': 'Host source inspection cannot certify an installed Linux guest or staged binary.',
            },
            'legacyLinuxAcceptance': {
                'status': legacy['status'],
                'referencePath': '../LegacyUpdateDataEvidence.json',
                'note': 'Legacy update-data is a reference pipeline, not a verified app recipe.',
            },
        }
        write_atomic(linux_root / app / 'RequirementsInspection.json', record)
        applications.append({
            'application': app, 'status': status,
            'sourceRepositoryStatus': candidate['sourceCheckoutStatus'],
            'authoredRecipe': comparison['authoredRecipeExists'],
            'declaredRequirements': len(evidence['declared']),
            'sourceRequirements': len(evidence['checkoutEvidence']),
            'unmappedSourceRequirements': len(evidence['unmappedCheckoutEvidence']),
            'frozenUbuntuContractComplete': evidence['frozenUbuntuContractComplete'],
            'buildAndTestVerified': False,
            'report': f'{app}/RequirementsInspection.json',
        })
    overview = {
        'schemaVersion': 1, 'platform': 'linux',
        'mode': 'source_requirements_review_only',
        'applications': applications,
        'totalApplications': len(applications),
        'authoredLinuxRecipes': sum(a['authoredRecipe'] for a in applications),
        'completeFrozenUbuntuContracts': sum(a['frozenUbuntuContractComplete'] for a in applications),
        'verifiedApplicationBuilds': 0,
        'legacyAcceptance': {
            'status': legacy['status'],
            'producerReferences': len(legacy['producers']),
            'stageReferences': len(legacy['stages']),
            'report': 'LegacyUpdateDataEvidence.json',
        },
        'warning': 'Requirements collection does not prove Linux Setup/Build/Stage/Test. '
                   'The CFOS Ubuntu guest and applications must be tested separately.',
    }
    write_atomic(linux_root / 'LegacyUpdateDataEvidence.json', legacy)
    write_atomic(linux_root / 'Overview.json', overview)
    return overview


def write_atomic(path: Path, obj: object):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(obj,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    temp.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app',default='all',help='Creative Suite app adapter or all')
    parser.add_argument('--source-root',action='append',type=Path,default=[], help='Directory containing local Git clones; repeat as needed')
    parser.add_argument('--builds-root',type=Path,required=True)
    parser.add_argument('--inspection-root', type=Path, help='Generated source/recipe/artwork cache (default: Builds/CreativeSuite/Inspection)')
    parser.add_argument('--product-root', type=Path, help='Installed Steam CinterForge product root for read-only reference inspections')
    parser.add_argument('--installed-inspection', type=Path,
                        help='Enrich the existing installed Windows Inspection.json in place; emit no source reports')
    args = parser.parse_args()
    source_roots = args.source_root or [SUITE.parents[2]]
    apps = sorted(p.name for p in (SUITE/'apps').iterdir() if p.is_dir() and not p.name.startswith('.') and p.name not in {'audacity', 'cinterforge'})
    if args.app!='all':
        if args.app not in apps:
            parser.error(f'Unknown adapter {args.app}')
        apps = [args.app]
    if args.installed_inspection:
        if args.app != 'cfaudio':
            parser.error('--installed-inspection requires --app cfaudio')
        report = args.installed_inspection.resolve()
        if not report.is_file() or report.name != 'Inspection.json':
            parser.error(f'Existing installed Inspection.json is required: {report}')
        existing = json.loads(report.read_text(encoding='utf-8'))
        if existing.get('application') != 'CinterForge Audio' or existing.get('platform') != 'Windows':
            parser.error(f'Not a Windows CinterForge Audio installation inspection: {report}')
        data = inspect('cfaudio', source_roots, args.builds_root)
        existing['windowsDependencyInspection'] = data['windowsDependencyInspection']
        existing['sourceRepositories'] = data['repositories']
        existing['sourceRepositoryStatus'] = data['repositoryStatus']
        write_atomic(report, existing)
        counts = data['windowsDependencyInspection']['summary']
        print(f'[inspect] Ardour Windows dependency sources: '
              f'{counts["withLocalSource"]}/{counts["required"]}; report={report}', flush=True)
        return
    from branding_assets import sync as sync_branding, report as report_branding, write_catalog, copy_review_code
    destination = (args.inspection_root or args.builds_root / 'Inspection') / 'Sources'
    artwork_destination = destination / 'Artwork'
    for branding_app in apps:
        outcome = sync_branding(branding_app, None, source_roots, inspect=True, inspection_root=artwork_destination)
        report_branding(outcome, inspect=True)
    copy_review_code(artwork_destination, source_roots)
    write_catalog(artwork_destination, source_roots)
    from contributor_history import collect as collect_contributors
    overview = []
    requirement_candidates = []
    generated_recipe_records = []
    artwork_repositories: dict[str, Path] = {}
    read_recipe = _load_recipe_reader()
    for app in apps:
        data = inspect(app,source_roots,args.builds_root)
        write_atomic(destination/app/'RepositoryAnalysis.json',data)
        # Broad source artwork is separate from the curated Artwork contract.
        # Collect each discovered fork once, even when several adapters name it.
        for repository in data['repositories']:
            if repository.get('status') in {'inspected', 'source_snapshot'} and repository.get('path'):
                checkout = Path(repository['path'])
                artwork_repositories[checkout.name] = checkout
        if data['repositoryStatus'] == 'not_found' and data.get('expectedForkCheckout'):
            expected = data['expectedForkCheckout']
            artwork_repositories.setdefault(expected, source_roots[0] / expected)
        candidate = candidate_requirements_for_app(data)
        requirement_candidates.append(candidate)
        write_atomic(destination/app/'RequirementsCandidate.json', candidate)
        generated_recipe_records.extend(
            write_inspected_recipes(data, candidate, destination, read_recipe))
        git_checkouts = [Path(repo['path']) for repo in data['repositories']
                         if repo.get('status') == 'inspected' and repo.get('path')]
        contributors = collect_contributors(git_checkouts, destination/app)
        if data['repositoryStatus'] == 'internal_web_component':
            # An internal WebApp is not a separate Git repository. Do not emit a
            # misleading missing-Git warning; attribution belongs to CinterForge.
            contributors['status'] = 'shared_parent_repository'
            contributors['attributionRepository'] = 'CinterForge'
            write_atomic(destination/app/'Contributors.json', contributors)
            print(f'[source inspection] {app}: internal CinterForge WebApp; '
                  'contributors attributed to parent repository', flush=True)
        else:
            print(f'[source inspection] {app}: contributor history {contributors["status"]}, '
                  f'{contributors["contributors"]} contributors', flush=True)
        overview.append({'application':app,'repositoryStatus':data['repositoryStatus'],
                         'repositories':len(data['repositories']),
                         'platformRecipes':sorted(k for k,v in data['platforms'].items() if v['recipe'])})
        print(f'[source inspection] {app}: {data["repositoryStatus"]} ({len(data["repositories"])} repositories)',flush=True)
        if data['repositoryStatus'] == 'not_found' and data.get('expectedForkCheckout'):
            expected = data['expectedForkCheckout']
            print(f'[source inspection] {app}: expected maintained fork {expected} beneath '
                  f'{[str(root) for root in source_roots]} (no Git checkout found)', flush=True)
        if data['windowsDependencyInspection']:
            counts = data['windowsDependencyInspection']['summary']
            print(f'[source inspection] Ardour Windows dependencies: {counts["withLocalSource"]}/{counts["required"]} local sources identified (not build verification)', flush=True)
    # Converter is part of the WebApp checkout rather than a fork of its own.
    internal_webapp = SUITE.parents[2] / 'CinterForgeWebApp'
    if internal_webapp.is_dir():
        artwork_repositories[internal_webapp.name] = internal_webapp
    from source_artwork import collect as collect_source_artwork
    source_artwork = collect_source_artwork(
        artwork_repositories, args.builds_root / 'CinterForgeArtwork')
    print(f'[source artwork] {source_artwork["files"]} media assets; '
          f'copied={source_artwork["copied"]} updated={source_artwork["updated"]} '
          f'current={source_artwork["current"]} '
          f'preserved-edits={source_artwork["preserved"]}; '
          f'folder={source_artwork["directory"]}', flush=True)
    write_atomic(destination/'Overview.json',{'schemaVersion':1,'applications':overview})
    summary = summarize_requirement_candidates(requirement_candidates)
    write_atomic(destination/'RequirementsSummary.json', summary)
    generated_summary = {
        'schemaVersion': 1, 'kind': 'inspection_generated_recipe_comparison',
        'recipes': len(generated_recipe_records),
        'productionReaderAccepted': sum(r['productionReaderAccepts'] for r in generated_recipe_records),
        'authoredRecipesPreserved': sum(r['authoredRecipeExists'] and r['authoredRecipePreserved']
                                       for r in generated_recipe_records),
        'declaredNativePlans': sum(r['candidatePlanStatus'] == 'plan_declared_for_native_validation'
                                   for r in generated_recipe_records),
        'candidateReady': 0,
        'historicalReadinessConflicts': sum(bool(r['historicalEvidenceConflicts'])
                                           for r in generated_recipe_records),
        'incompleteOrInvalid': sum(r['status'] != 'schema_valid_authored_recipe_preserved'
                                   for r in generated_recipe_records),
        'mac': [r for r in generated_recipe_records if r['platform'] == 'mac'],
        'linux': [r for r in generated_recipe_records if r['platform'] == 'linux'],
        'win': [r for r in generated_recipe_records if r['platform'] == 'win'],
        'warning': 'These are review candidates. No recipe has been promoted or run by Inspect.',
    }
    write_atomic(destination/'GeneratedRecipesSummary.json', generated_summary)
    legacy = legacy_update_data_evidence()
    write_atomic(destination/'LegacyUpdateDataEvidence.json', legacy)
    linux_overview = write_linux_inspection(
        (args.inspection_root or args.builds_root / 'Inspection'),
        requirement_candidates, generated_recipe_records, legacy)
    print(f'[Linux inspection] reports: '
          f'{(args.inspection_root or args.builds_root / "Inspection") / "Linux"} '
          f'({linux_overview["totalApplications"]} apps; '
          f'{linux_overview["authoredLinuxRecipes"]} authored recipes; '
          'Linux runtime not yet tested)', flush=True)
    print(f'[inspect] requirements: {destination / "RequirementsSummary.json"} '
          f'({summary["declarations"]} declarations, review-only)', flush=True)
    print(f'[inspect] generated recipe.json: {generated_summary["recipes"]} targets; '
          f'{generated_summary["productionReaderAccepted"]} schema accepted; '
          f'{generated_summary["authoredRecipesPreserved"]} authored recipes preserved; '
          f'{generated_summary["declaredNativePlans"]} native plans awaiting Setup/Test; '
          f'comparison: {destination / "GeneratedRecipesSummary.json"}', flush=True)
    print(f'[inspect] legacy Linux acceptance: {len(legacy["producers"])} producer references; '
          f'{len(legacy["stages"])} stage references, not migrated', flush=True)
    print(f'[source inspection] reports: {destination}',flush=True)
    # Restore the established reference-install/distribution inspections alongside
    # source, artwork and contributor evidence. Never stage or install here.
    # Installed CFOS guest Inspection.json is produced by the original guest
    # inspector in its own runtime; do not fabricate or overwrite that evidence.
    inspection_root = args.inspection_root or args.builds_root / 'Inspection'
    failures = []
    if sys.platform == 'darwin':
        # The inspection cache is no longer below Steam Applications. The
        # installed product location must be supplied independently; never
        # derive it from Builds paths or invent a Steam location.
        product_root = args.product_root
        if product_root is None:
            print('[inspect] macOS reference distribution scan skipped: provide --product-root '
                  'to inspect installed bundles independently of the Builds cache', flush=True)
        mac_script = SUITE / 'shared/mac/reference_install_inspection.py'
        if product_root is not None:
            command = [sys.executable, '-u', str(mac_script),
                       '--product-root', str(product_root),
                       '--builds-root', str(args.builds_root),
                       '--inspection-root', str(inspection_root),
                       '--application', args.app]
            outcome = subprocess.run(command, check=False)
            if outcome.returncode:
                failures.append(f'macOS reference inspection (exit {outcome.returncode})')
                print(f'[inspect] macOS reference inspection FAILED (exit {outcome.returncode})', file=sys.stderr, flush=True)
    windows_script = SUITE / 'shared/win/inspect.py'
    command = [sys.executable, '-u', str(windows_script),
               '--builds-root', str(args.builds_root),
               '--inspection-root', str(inspection_root)]
    # The legacy Windows catalog uses publisher names, not adapter slugs.
    # Inspect the complete catalog for all; per-adapter source inspection remains scoped.
    if args.app == 'all':
        outcome = subprocess.run(command, check=False)
        if outcome.returncode:
            failures.append(f'Windows reference inspection (exit {outcome.returncode})')
            print(f'[inspect] Windows reference inspection FAILED (exit {outcome.returncode})', file=sys.stderr, flush=True)
    print(f'[inspect] persistent reports: {inspection_root}', flush=True)
    if failures:
        raise RuntimeError('Inspect incomplete: ' + '; '.join(failures))

if __name__=='__main__':
    main()
