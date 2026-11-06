"""Recipe-owned Creative Suite artwork baseline and checkout synchronization.

Inspect seeds missing baseline files; never overwrites Kevin's edits. Setup uses
hash comparison and copies only changed artwork, preserving relative paths.
"""
from __future__ import annotations
import hashlib
import csv
import json
import shutil
from pathlib import Path

SUITE = Path(__file__).resolve().parents[2]
BASELINE = SUITE / 'Baseline' / 'assets' / 'creativeSuite'



def _artwork_branding(recipe: dict) -> dict | None:
    """Artwork contracts specify a source repo and icon/splash lists.

    Product branding (e.g. Audio bundle name) is not an asset contract.
    """
    branding = recipe.get('branding')
    if (isinstance(branding, dict) and isinstance(branding.get('repository'), str)
            and isinstance(branding.get('icons'), list)
            and isinstance(branding.get('splashes'), list)):
        return branding
    return None


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def tree_digest(path: Path) -> str:
    h = hashlib.sha256()
    for file in sorted(p for p in path.rglob('*') if p.is_file()):
        h.update(file.relative_to(path).as_posix().encode())
        h.update(digest(file).encode())
    return h.hexdigest()


def write_catalog(destination: Path, source_roots: list[Path]) -> None:
    """Write inspection asset/code index; never modify authored repositories."""
    rows = []
    for recipe_path in sorted((SUITE / 'apps').glob('*/*/recipe.json')):
        recipe = json.loads(recipe_path.read_text(encoding='utf-8'))
        branding = _artwork_branding(recipe)
        if branding is None:
            continue
        repo = branding['repository']
        checkout = next((root / repo for root in source_roots if (root / repo).is_dir()), None)
        code = branding.get('splash_code', {})
        for kind, entries in (('icon', branding['icons']), ('splash', branding['splashes'])):
            for relative in dict.fromkeys(entries):
                file = checkout / relative if checkout else None
                rows.append({
                    'app': recipe_path.parent.parent.name, 'platform': recipe_path.parent.name,
                    'repository': repo, 'type': kind, 'asset_path': relative,
                    'inspection_copy': f'{repo}/{relative}',
                    'source_exists': 'yes' if file and file.exists() else 'no',
                    'source_sha256': digest(file) if file and file.is_file() else '',
                    'loader_code': '; '.join(code.get('loader', [])) if kind == 'splash' else '',
                    'renderer_code': '; '.join(code.get('renderer', [])) if kind == 'splash' else '',
                    'startup_code': '; '.join(code.get('startup', [])) if kind == 'splash' else '',
                    'resource_manifest': '; '.join(code.get('manifest', [])) if kind == 'splash' else '',
                    'review_notes': code.get('note', '') if kind == 'splash' else '',
                })
    destination.mkdir(parents=True, exist_ok=True)
    with (destination / 'artwork.csv').open('w', encoding='utf-8', newline='') as output:
        writer = csv.DictWriter(output, fieldnames=['app', 'platform', 'repository', 'type',
            'asset_path', 'inspection_copy', 'source_exists', 'source_sha256',
            'loader_code', 'renderer_code', 'startup_code', 'resource_manifest', 'review_notes'])
        writer.writeheader()
        writer.writerows(rows)


def copy_review_code(destination: Path, source_roots: list[Path]) -> dict:
    """Copy referenced implementation into external inspection, preserving repo paths."""
    results = {'copied': [], 'missing': []}
    for recipe_path in sorted((SUITE / 'apps').glob('*/*/recipe.json')):
        branding = _artwork_branding(json.loads(recipe_path.read_text(encoding='utf-8')))
        if branding is None:
            continue
        repo = branding['repository']
        checkout = next((root / repo for root in source_roots if (root / repo).is_dir()), None)
        for kind in ('loader', 'renderer', 'startup', 'manifest'):
            for relative in branding.get('splash_code', {}).get(kind, []):
                rel = Path(relative)
                if rel.is_absolute() or '..' in rel.parts:
                    results['missing'].append(f'{repo}/{relative} (unsafe path)')
                    continue
                source = checkout / rel if checkout else None
                target = destination / repo / 'code' / rel
                if source is None or not source.is_file():
                    results['missing'].append(f'{repo}/{relative}')
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.is_file() or digest(target) != digest(source):
                    shutil.copy2(source, target)
                    results['copied'].append(f'{repo}/code/{relative}')
    return results


def sync(app: str, platform: str | None, source_roots: list[Path], *, inspect: bool,
         inspection_root: Path | None = None) -> dict:
    results = {'application': app, 'platform': platform or 'all', 'copied': [],
               'current': [], 'missing': [], 'dirty': [], 'errors': []}
    if inspect and inspection_root is None:
        raise ValueError('Inspect requires an external inspection_root')
    recipes = sorted((SUITE / 'apps' / app).glob('*/recipe.json'))
    for recipe_path in recipes:
        if platform and recipe_path.parent.name != platform:
            continue
        recipe = json.loads(recipe_path.read_text(encoding='utf-8'))
        branding = _artwork_branding(recipe)
        if branding is None:
            continue
        repo_name = branding['repository']
        candidates = [root / repo_name for root in source_roots]
        checkout = next((p for p in candidates if p.is_dir()), None)
        if checkout is None:
            results['errors'].append(f'{repo_name}: checkout not found in {source_roots}')
            continue
        for relative in dict.fromkeys(branding['icons'] + branding['splashes']):
            rel = Path(relative)
            if rel.is_absolute() or '..' in rel.parts:
                results['errors'].append(f'Unsafe branding path: {relative}')
                continue
            source = checkout / rel
            central = (inspection_root if inspect else BASELINE) / repo_name / rel
            label = f'{repo_name}/{relative}'
            if inspect:
                if source.is_dir():
                    if not central.exists():
                        shutil.copytree(source, central)
                        results['copied'].append(label)
                    elif tree_digest(source) != tree_digest(central):
                        shutil.copytree(source, central, dirs_exist_ok=True)
                        results['copied'].append(label + " (inspection refreshed)")
                    else:
                        results['current'].append(label)
                    continue
                if not source.is_file():
                    results['missing'].append(label + ' (upstream missing)')
                elif not central.exists():
                    central.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, central)
                    results['copied'].append(label)
                elif digest(central) != digest(source):
                    shutil.copy2(source, central)
                    results['copied'].append(label + " (inspection refreshed)")
                else:
                    results['current'].append(label)
            elif central.is_dir():
                if source.is_dir() and tree_digest(central) == tree_digest(source):
                    results['current'].append(label)
                else:
                    shutil.copytree(central, source, dirs_exist_ok=True)
                    results['copied'].append(label + ' (directory)')
            elif not central.is_file():
                results['missing'].append(label + ' (baseline missing; run Inspect)')
            elif source.is_file() and digest(central) == digest(source):
                results['current'].append(label)
            else:
                source.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(central, source)
                results['copied'].append(label)
    return results


def report(results: dict, *, inspect: bool) -> None:
    action = 'inspect' if inspect else 'setup'
    for label in results['copied']:
        print(f'[branding {action}] COPIED {label}', flush=True)
    for label in results['dirty']:
        print(f'[branding {action}] DIRTY (central artwork preserved) {label}', flush=True)
    for label in results['missing']:
        print(f'[branding {action}] MISSING {label}', flush=True)
    for error in results['errors']:
        print(f'[branding {action}] ERROR {error}', flush=True)
    if not results['copied']:
        print(f'[branding {action}] Artwork up to date; no copies needed', flush=True)
    print(f'[branding {action}] copied={len(results["copied"])} current={len(results["current"])} dirty={len(results["dirty"])} missing={len(results["missing"])}', flush=True)
