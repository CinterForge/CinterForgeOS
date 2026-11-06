#!/usr/bin/env python3
"""Inspect native macOS reference installations and Suite-managed reference copies.

Scan /Applications and ~/Applications (the native application locations) in
addition to the existing Baseline-managed reference install. Inspect is read-only.
InstallInspect uses the existing Suite DMG downloader which currently copies
into its managed reference location, not /Applications.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
import plistlib
import subprocess
import sys
import zipfile
import xml.parsers.expat
from pathlib import Path
from types import SimpleNamespace

# Import the existing shared repository inspector by filename, without importing
# the server or changing Python's site-package resolution.

SUITE = Path(__file__).resolve().parents[2]
SERVER = SUITE / 'Baseline' / 'server.py'
SOURCE_INSPECTOR = SUITE / 'shared' / 'source' / 'repository_inspection.py'
DISTRIBUTION_INSPECTOR = SUITE / 'shared' / 'mac' / 'distribution_inspection.py'


def command(*args: str) -> dict:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=90)
        return {'exitCode': result.returncode, 'stdout': result.stdout[:100000], 'stderr': result.stderr[:10000]}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {'error': str(error)}


def inventory(root: Path) -> dict:
    items = []
    if root.exists():
        for item in sorted(root.rglob('*')):
            try:
                stat = item.lstat()
                relative = str(item.relative_to(root))
                kind = 'symlink' if item.is_symlink() else 'directory' if item.is_dir() else 'file' if item.is_file() else 'other'
                record = {'path': relative, 'type': kind, 'mode': oct(stat.st_mode & 0o7777), 'size': stat.st_size}
                if kind == 'symlink':
                    record['target'] = os.readlink(item)
                if kind == 'file':
                    digest = hashlib.sha256()
                    with item.open('rb') as stream:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                            digest.update(chunk)
                    record['sha256'] = digest.hexdigest()
                if kind == 'file' and item.suffix in {'.plist'} and item.name == 'Info.plist':
                    try:
                        data = plistlib.loads(item.read_bytes())
                        record['bundleIdentity'] = {key: data.get(key) for key in ('CFBundleIdentifier', 'CFBundleExecutable', 'CFBundleVersion', 'CFBundleShortVersionString', 'LSMinimumSystemVersion')}
                    except (ValueError, OSError, TypeError, xml.parsers.expat.ExpatError):
                        pass
                items.append(record)
            except OSError as error:
                items.append({'path': str(item.relative_to(root)), 'error': str(error)})
    return {'root': str(root), 'files': items, 'fileCount': len(items)}


def native_bundles(name: str) -> list[Path]:
    """Find app bundles by identity, without treating the production tree as reference."""
    candidates = []
    for base in (Path('/Applications'), Path.home() / 'Applications'):
        if not base.is_dir():
            continue
        for item in sorted(base.glob('*.app')):
            if item.name.casefold() == f'{name}.app'.casefold():
                candidates.append(item)
    return candidates


def native_inventory(name: str) -> dict:
    """Inventory all native matching bundles, preserving their absolute paths."""
    roots = native_bundles(name)
    entries = []
    for root in roots:
        snapshot = inventory(root)
        for item in snapshot['files']:
            item['path'] = str(root / item['path'])
        entries.extend(snapshot['files'])
    return {'locations': [str(path) for path in roots], 'files': entries,
            'fileCount': len(entries)}


def combined_inventory(name: str, managed_location: Path) -> dict:
    native = native_inventory(name)
    managed = inventory(managed_location)
    for item in managed['files']:
        item['path'] = str(managed_location / item['path'])
    return {'locations': native['locations'] + ([str(managed_location)] if managed_location.is_dir() else []),
            'nativeLocations': native['locations'],
            'managedLocation': str(managed_location),
            'files': native['files'] + managed['files'],
            'fileCount': native['fileCount'] + managed['fileCount']}


def load_server(product_root: Path):
    # When invoked as a script, Python prepends shared/mac to sys.path.
    # Its packaging.py shadows the third-party `packaging` package used by
    # the existing Baseline installer import chain (marshmallow, etc.).
    # Keep this directory out of top-level import resolution: the server is
    # loaded by absolute filename below and does not need it on sys.path.
    script_dir = Path(__file__).resolve().parent
    sys.path[:] = [entry for entry in sys.path
                   if not entry or Path(entry).resolve() != script_dir]
    os.environ['CINTERFORGE_PRODUCT_ROOT'] = str(product_root)
    sys.path.insert(0, str(SUITE / 'shared'))
    sys.path.insert(0, str(SERVER.parent))
    spec = importlib.util.spec_from_file_location('cinterforge_baseline_server_inspection', SERVER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'Cannot load existing Suite installer: {SERVER}')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def inspect_local_source(adapter: str, builds_root: Path) -> dict:
    spec = importlib.util.spec_from_file_location('creative_suite_source_inspector', SOURCE_INSPECTOR)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'Cannot load source inspector: {SOURCE_INSPECTOR}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # CreativeSuite Builds is nested one level below the shared Builds root.
    source_roots = [builds_root.resolve().parent.parent]
    result = module.inspect(adapter, source_roots, builds_root)
    collection = module.collect_installation_sources(result['repositories'], builds_root / 'Inspection' / 'macOS' / adapter)
    print(f'[mac inspection] {adapter}: source files copied={collection["filesCopied"]}; folder={collection["directory"]}', flush=True)
    return {
        'repositoryStatus': result['repositoryStatus'],
        'repositories': [{
            'path': repo['path'], 'status': repo.get('status'), 'origin': repo.get('origin'),
            'commit': repo.get('commit'),
            'installationEvidence': repo.get('installationEvidence', {}),
        } for repo in result['repositories']],
        'checkedSourceRoots': result['checkedSourceRoots'],
    }


def inspect_local_distributions(storage: Path, name: str, catalog_entry: dict | None, report_dir: Path) -> dict:
    spec = importlib.util.spec_from_file_location('creative_suite_distribution_inspector', DISTRIBUTION_INSPECTOR)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'Cannot load distribution inspector: {DISTRIBUTION_INSPECTOR}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.inspect_distributions(storage, name, catalog_entry, report_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--product-root', type=Path, required=True)
    parser.add_argument('--builds-root', type=Path, required=True)
    parser.add_argument('--application', default='all')
    parser.add_argument('--inspection-root', type=Path, help='Write directly into persistent Steam Applications/Logs/Inspections')
    parser.add_argument('--install', action='store_true', help='Invoke existing Baseline/server.py macOS download/installer if needed')
    args = parser.parse_args()
    if sys.platform != 'darwin':
        parser.error('macOS reference installation inspection requires a macOS host')
    root = args.product_root.expanduser().resolve()
    if root.name != 'CinterForge' or not root.is_dir():
        parser.error(f'Expected existing macOS product folder named CinterForge: {root}')
    # Inspect is read-only: importing Baseline/server.py here needlessly loads the
    # model catalog and the complete CinterForge imaging dependency graph.
    # The installer catalog and persistent storage convention are sufficient.
    catalog_spec = importlib.util.spec_from_file_location(
        'creative_suite_upstream_catalog', SUITE / 'shared' / 'upstream_catalog.py')
    if catalog_spec is None or catalog_spec.loader is None:
        raise RuntimeError('Creative Suite upstream catalog is unavailable')
    catalog_module = importlib.util.module_from_spec(catalog_spec)
    catalog_spec.loader.exec_module(catalog_module)
    catalog = catalog_module.read_catalog(installer_only=True)
    # Repository inspection follows the complete Creative Suite adapter catalog;
    # the legacy Baseline installer covers only a subset. Keep the two distinct.
    apps = {entry['name']: entry for entry in catalog}
    adapters = sorted(p.name for p in (SUITE / 'apps').iterdir()
                      if p.is_dir() and not p.name.startswith('.') and p.name not in {'audacity', 'blockbench', 'code-oss', 'cinterforge-converter', 'cinterforge'})
    by_adapter = {name.casefold().replace('-', '').replace(' ', ''): name for name in apps}
    targets = {}
    for adapter in adapters:
        display = by_adapter.get(adapter.casefold().replace('-', '').replace(' ', ''), adapter)
        targets[adapter] = (display, display if display in apps else None)
    storage = root / 'Applications'
    server = load_server(root) if args.install else SimpleNamespace(APPS=apps, STORAGE=storage)
    if args.application.lower() == 'all':
        selected = adapters
    else:
        selected = [a for a, (display, _) in targets.items()
                    if args.application.casefold() in (a.casefold(), display.casefold())]
        if not selected:
            parser.error(f'Unknown Creative Suite application: {args.application}')
    print(f'[mac inspection] suite source adapters: {len(selected)}; Baseline reference installers: {len(apps)}', flush=True)
    print('[mac inspection] native scan scopes: /Applications and ~/Applications', flush=True)
    print(f'[mac inspection] managed reference scope: {server.STORAGE / "macOS" / "<Application>" / "Applications"}', flush=True)
    print('[mac inspection] production staged installations are excluded', flush=True)
    failures = 0
    overview = []
    for adapter in selected:
        name, installer_name = targets[adapter]
        folder = server.STORAGE / 'macOS' / name
        location = folder / 'Applications'
        print(f'[mac inspection] {name}: native bundles: {native_bundles(name) or "none found"}; managed: {location} ({"present" if location.is_dir() else "missing"})', flush=True)
        report_dir = (args.inspection_root or args.builds_root / 'Inspection') / 'macOS' / name
        report_dir.mkdir(parents=True, exist_ok=True)
        before = combined_inventory(name, location)
        (report_dir / 'Before.json').write_text(json.dumps(before, indent=2) + '\n')
        state_file = folder / 'state.json'
        try:
            state = json.loads(state_file.read_text()) if state_file.is_file() else {}
        except (OSError, ValueError):
            state = {}
        attempted = False
        if args.install and installer_name and (state.get('phase') != 'installed' or not location.is_dir() or not any(location.glob('*.app'))):
            attempted = True
            print(f'[mac inspection] installing {name} through existing Suite installer', flush=True)
            server.run_install(installer_name, 'macOS')
            try:
                state = json.loads(state_file.read_text())
            except (OSError, ValueError):
                state = {'phase': 'error', 'error': 'Installer state unavailable'}
        after = combined_inventory(name, location)
        (report_dir / 'After.json').write_text(json.dumps(after, indent=2) + '\n')
        old = {item['path']: item for item in before['files']}
        new = {item['path']: item for item in after['files']}
        changes = {'added': sorted(new.keys() - old.keys()), 'removed': sorted(old.keys() - new.keys()), 'modified': sorted(p for p in old.keys() & new.keys() if old[p] != new[p])}
        (report_dir / 'InstallationDelta.json').write_text(json.dumps(changes, indent=2) + '\n')
        bundles = []
        for bundle in native_bundles(name) + (sorted(location.glob('*.app')) if location.is_dir() else []):
            binaries = sorted((bundle / 'Contents' / 'MacOS').glob('*')) if (bundle / 'Contents' / 'MacOS').is_dir() else []
            bundles.append({'path': str(bundle), 'codesign': command('codesign', '-dv', '--verbose=2', str(bundle)),
                            'executables': [{'path': str(binary), 'file': command('file', str(binary)), 'linkedLibraries': command('otool', '-L', str(binary))} for binary in binaries if binary.is_file()]})
        distribution = inspect_local_distributions(server.STORAGE, name, apps.get(installer_name) if installer_name else None, report_dir)
        print(f'[mac inspection] {name}: distributions={distribution["archiveCount"]} status={distribution["status"]}; folder={distribution["downloadLocation"]}', flush=True)
        source_analysis = inspect_local_source(adapter, args.builds_root)
        (report_dir / 'SourceInstallationEvidence.json').write_text(json.dumps(source_analysis, indent=2) + '\n')
        print(f'[mac inspection] {name}: source repositories={len(source_analysis["repositories"])} status={source_analysis["repositoryStatus"]}; evidence={report_dir / "SourceInstallationEvidence.json"}', flush=True)
        result = {'distributionInspection': {'status': distribution['status'], 'archiveCount': distribution['archiveCount']}, 'sourceInspection': source_analysis, 'application': name, 'adapter': adapter, 'referenceInstallerAvailable': bool(installer_name), 'platform': 'macOS', 'referenceInstaller': str(SERVER), 'installRequested': args.install,
                  'installAttempted': attempted, 'installState': state, 'bundles': bundles, 'changes': changes,
                  'nativeLocations': after['nativeLocations'], 'managedReferenceLocation': str(location),
                  'note': 'Native /Applications and ~/Applications plus managed Baseline reference copy. Production staged applications excluded. Baseline DMG installer does not install into /Applications.'}
        (report_dir / 'InstallInspection.json').write_text(json.dumps(result, indent=2) + '\n')
        overview.append({'application': name, 'adapter': adapter,
                         'nativeBundles': after['nativeLocations'],
                         'managedReferencePresent': location.is_dir(),
                         'fileCount': after['fileCount'],
                         'distributionStatus': distribution['status'],
                         'downloadedDistributions': distribution['archiveCount'],
                         'repositoryStatus': source_analysis['repositoryStatus'],
                         'repositoriesFound': len(source_analysis['repositories']),
                         'referenceInstallerAvailable': bool(installer_name),
                         'installAttempted': attempted,
                         'installState': state.get('phase', 'absent'),
                         'installationChanges': {key: len(value) for key, value in changes.items()},
                         'report': str(report_dir / 'InstallInspection.json')})
        print(f'[mac inspection] {name}: managedState={state.get("phase", "absent")}, nativeBundles={len(after["nativeLocations"])}, files={after["fileCount"]}, added={len(changes["added"])}; report={report_dir / "InstallInspection.json"}', flush=True)
        if args.install and not installer_name:
            print(f'[mac inspection] {name}: no Baseline installer; source inspection completed (not an install failure)', flush=True)
        if attempted and state.get('phase') != 'installed':
            failures += 1
    inspection_root = args.inspection_root or args.builds_root / 'Inspection'
    summary_file = inspection_root / 'macOS' / 'Overview.json'
    summary_file.write_text(json.dumps({'schemaVersion': 1, 'applications': overview,
                                        'referenceScopes': ['/Applications', str(Path.home() / 'Applications')],
                                        'note': 'Existing bundle inventories do not establish installation deltas or prove that no files were installed elsewhere.'},
                                       indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(f'[mac inspection] overview: {summary_file}', flush=True)
    archive = args.builds_root / 'Inspection.zip'
    # Always replace the previous archive, without copying it into the tree
    # being archived. A temporary sibling file avoids leaving partial ZIPs.
    temporary = archive.with_suffix('.zip.tmp')
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as output:
            for file in sorted(inspection_root.rglob('*')):
                if file.is_file() and not file.is_symlink() and file.name not in {'.DS_Store', 'Thumbs.db'}:
                    output.write(file, file.relative_to(args.builds_root).as_posix())
        temporary.replace(archive)
        print(f'[mac inspection] compressed inspection: {archive} ({archive.stat().st_size:,} bytes)', flush=True)
    finally:
        temporary.unlink(missing_ok=True)
    print(f'[mac inspection] reports: {inspection_root / "macOS"}', flush=True)
    return 1 if failures else 0

if __name__ == '__main__':
    raise SystemExit(main())
