"""Inspect-owned, non-destructive repository media collection for Kevin.

Source artwork is a development resource, not a generated application asset.
It is copied to Builds/CreativeSuite/CinterForgeArtwork, never to the
curated Inspection/Sources/Artwork directory. This collector does not depend
on an installed Converter, and only augments its baseline list with verified
readable formats exposed by the installed Converter engines when available.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from functools import lru_cache
from pathlib import Path

# Common extensions backed by ImageMagick, FFmpeg or Assimp. Converter's live
# capabilities (below) add other extensions; a code-only host need not have
# any conversion tool installed to discover standard media in sources.
BASE_EXTENSIONS = frozenset((
    'png jpg jpeg jpe jfif gif webp bmp dib tiff tif tga ico icns svg svgz'
    ' avif heic heif apng jxl jp2 j2k jpf jpx pcx ppm pgm pbm pnm pam'
    ' psd psb xcf ora kra ktx ktx2 dds exr hdr tpic pict pct eps ps pdf ai xpm xbm'
    ' dng cr2 cr3 nef arw raf orf rw2 srw raw'
    ' fbx dae glb gltf obj mtl 3ds blend ply stl dxf lwo lws lxo x3d'
    ' wrl vrml abc usd usda usdc usdz ifc off ac ac3d md2 md3 md5mesh'
    ' md5anim smd vta mdm b3d bsp x glb gltf'
    ' otf ttf woff woff2 wav mp3 aiff aif flac ogg oga opus m4a wma aac mid midi'
    ' mp4 mov m4v mkv webm avi mpg mpeg m2v mts m2ts ts ogv flv wmv'
    ' 3gp 3g2 mxf dv vob'
).split())
# Never descend into Git internals or previously generated/dependency trees.
# Media in authored tests, examples and resources remains in scope.
SKIP_DIRECTORIES = frozenset((
    '.git', '.hg', '.svn', 'node_modules', 'bower_components', '.pnpm-store',
    '.venv', 'venv', '__pycache__', '.tox', '.mypy_cache', '.pytest_cache',
    'target', 'builds', 'out', 'output', 'deriveddata',
    '.next', '.nuxt', '.gradle', '.idea', '.vs', '.vscode',
))


# Converter engines also accept generic code/data/text names (e.g. C, TXT,
# MAP, M). Those MUST NOT cause an artwork scraper to copy source code or
# documentation merely because ImageMagick can render them as text.
NON_ARTWORK_EXTENSIONS = frozenset({
    'c', 'h', 'm', 'r', 'txt', 'text', 'map', 'js', 'ts', 'py', 'sh', 'bash',
    'html', 'css', 'json', 'yaml', 'yml', 'toml', 'ini', 'cfg', 'md',
    'http', 'https', 'data', 'file', 'null', 'caption', 'label',
    'pattern', 'canvas', 'clip', 'plasma', 'inline', 'gradient',
})


@lru_cache(maxsize=1)
def converter_extensions() -> tuple[frozenset[str], dict]:
    """Supplement common artwork extensions with installed readable formats.

    The Converter inventory distinguishes supported read modes from unknown
    CraftMaster reference formats. FFmpeg's non-extension identifiers are only
    candidates; the baseline is intentionally independent of installed tools.
    """
    try:
        from cf.csRuntime import suite_formats
        inventory = suite_formats.inventory()
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        return BASE_EXTENSIONS, {'status': 'baseline_only', 'error': str(exc)}
    result = set(BASE_EXTENSIONS)
    for row in inventory.get('formats', ()):
        if (not row.get('read') or
                row.get('type') not in {'Images / Graphics', '3D / Models', 'Audio', 'Video / Containers'}):
            continue
        name = str(row.get('name', '')).lstrip('.').lower()
        if (name not in NON_ARTWORK_EXTENSIONS and
                re.fullmatch(r'[a-z0-9][a-z0-9.+_-]{0,11}', name)):
            result.add(name)
    return frozenset(result), {'status': 'converter_probed',
                               'errors': inventory.get('errors', {})}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _load_manifest(destination: Path) -> dict:
    path = destination / 'Manifest.json'
    if not path.is_file():
        return {}
    try:
        manifest = json.loads(path.read_text(encoding='utf-8'))
        if manifest.get('schemaVersion') == 1:
            return manifest.get('files', {})
    except (ValueError, OSError, TypeError):
        pass
    return {}


def _candidates(checkout: Path, supported: frozenset[str]):
    """Yield regular media without following symlinks into other checkouts."""
    for base, dirs, files in os.walk(checkout, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d.casefold() not in SKIP_DIRECTORIES
                         and not (Path(base) / d).is_symlink())
        for name in sorted(files):
            if Path(name).suffix.casefold().lstrip('.') not in supported:
                continue
            source = Path(base) / name
            if not source.is_symlink() and source.is_file():
                yield source


def collect(checkouts: dict[str, Path], destination: Path, *,
            extensions: frozenset[str] | None = None) -> dict:
    """Copy all source media, caching by metadata and preserving Kevin's edits.

    On source changes, a destination edited since its last synchronized hash
    is left intact and reported as a conflict. Deleted upstream files remain
    in place (Kevin may be using them), flagged as orphaned for review.
    Manifest stores only repository-relative paths; never publish host paths.
    """
    supported, format_evidence = (converter_extensions() if extensions is None
                                   else (extensions, {'status': 'test_or_explicit_extensions'}))
    old = _load_manifest(destination)
    records = {}
    totals = {'copied': 0, 'updated': 0, 'current': 0, 'preserved': 0,
              'missingRepositories': 0, 'unreadable': 0, 'orphaned': 0}
    for name, checkout in sorted(checkouts.items()):
        if not checkout.is_dir():
            totals['missingRepositories'] += 1
            continue
        for source in _candidates(checkout, supported):
            relative = source.relative_to(checkout).as_posix()
            key = f'{name}/{relative}'
            target = destination / key
            previous = old.get(key, {})
            try:
                metadata = source.stat()
                # Reuse hash and destination if nothing in either file changed.
                same_source = (previous.get('size') == metadata.st_size and
                               previous.get('mtimeNs') == metadata.st_mtime_ns)
                if (same_source and target.is_file() and
                        target.stat().st_size == metadata.st_size and
                        target.stat().st_mtime_ns == metadata.st_mtime_ns):
                    records[key] = previous
                    totals['current'] += 1
                    continue
                fingerprint = sha256(source)
                record = {'repository': name, 'path': relative, 'bytes': metadata.st_size,
                          'size': metadata.st_size, 'mtimeNs': metadata.st_mtime_ns,
                          'sha256': fingerprint, 'status': 'current'}
                if target.is_file():
                    target_hash = sha256(target)
                    if target_hash == fingerprint:
                        totals['current'] += 1
                    elif previous.get('sha256') and target_hash == previous['sha256']:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source, target)
                        totals['updated'] += 1
                    else:
                        record['status'] = 'preserved_local_edit'
                        totals['preserved'] += 1
                        records[key] = record
                        continue
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
                    totals['copied'] += 1
                records[key] = record
            except OSError as exc:
                totals['unreadable'] += 1
                records[key] = {'repository': name, 'path': relative,
                                'status': 'source_unreadable', 'error': str(exc)}
    # Missing source checkouts must not erase their previous catalog entries.
    for key, previous in old.items():
        if key not in records:
            records[key] = {**previous, 'status': 'source_not_seen'}
            totals['orphaned'] += 1
    manifest = {'schemaVersion': 1, 'kind': 'cinterforge_source_artwork',
                'description': 'Repository source media for CinterForgeArtwork; curated Artwork remains in Inspection.',
                'files': dict(sorted(records.items())), 'counts': totals,
                'formats': {'extensions': sorted(supported), 'evidence': format_evidence}}
    destination.mkdir(parents=True, exist_ok=True)
    temporary = destination / f'Manifest.json.tmp-{os.getpid()}'
    try:
        temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        os.replace(temporary, destination / 'Manifest.json')
    finally:
        temporary.unlink(missing_ok=True)
    return {'directory': str(destination), **totals, 'files': len(records)}
