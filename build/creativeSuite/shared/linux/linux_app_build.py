#!/usr/bin/env python3
"""Creative Suite Linux build adapters. No legacy pipeline imports.

Build creates a genuine Linux AMD64 payload at CINTER_BUILT_ROOT/Output.
Sources are explicit supplied release archives or local authored file references;
Ubuntu remains a separate headless TEST runtime, not an application install path.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import os
from pathlib import Path
import shutil
import stat
import sys
import tarfile
import tempfile
import urllib.request
from urllib.parse import urlparse, unquote
import zipfile

APPLICATIONS = {
    'ardour': 'CinterForge Audio',
    'blender': 'CinterForge 3D',
    'blockbench': 'CinterForge Model',
    'cfconverter': 'CinterForge Converter',
    'code-oss': 'CinterForge Code',
    'gimp': 'CinterForge Image',
    'inkscape': 'CinterForge Vector',
    'kdenlive': 'CinterForge Video',
    'krita': 'CinterForge Paint',
    'rapidraw': 'CinterForge Photo',
    'scribus': 'CinterForge Document',
}

class BuildError(RuntimeError):
    pass

def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open('rb') as src:
        for block in iter(lambda: src.read(1024 * 1024), b''):
            sha.update(block)
    return sha.hexdigest()

def safe_extract(archive: Path, destination: Path) -> None:
    """Extract a release tarball, including validated relative symlinks.

    Blender distributions need internal library symlinks. Reject links which
    escape the archive and any archive member nested below a symlink.
    """
    import posixpath
    with tarfile.open(archive, 'r:*') as bundle:
        members = bundle.getmembers()
        names = set()
        symlinks = set()
        for member in members:
            name = member.name
            path = Path(name)
            if not name or path.is_absolute() or '..' in path.parts or not (
                member.isfile() or member.isdir() or member.issym() or member.islnk()
            ):
                raise BuildError(f'Unsafe tar entry: {name}')
            normalized = posixpath.normpath(name)
            if normalized in names:
                raise BuildError(f'Duplicate tar entry: {name}')
            names.add(normalized)
            if member.issym():
                symlinks.add(normalized)
                target = posixpath.normpath(posixpath.join(posixpath.dirname(normalized), member.linkname))
                if not member.linkname or posixpath.isabs(member.linkname) or target == '..' or target.startswith('../'):
                    raise BuildError(f'Escaping tar symlink: {name} -> {member.linkname}')
            if member.islnk():
                target = posixpath.normpath(member.linkname)
                if not member.linkname or target == '..' or target.startswith('../') or target.startswith('/'):
                    raise BuildError(f'Escaping tar hardlink: {name} -> {member.linkname}')
        for member in members:
            parts = posixpath.normpath(member.name).split('/')
            if any('/'.join(parts[:index]) in symlinks for index in range(1, len(parts))):
                raise BuildError(f'Tar entry beneath symlink: {member.name}')
        # Python 3.11 tarfile has no extraction_filter; use explicit verified
        # member types and disallow symlink-parent traversal above.
        bundle.extractall(destination)


def catalog_source(slug: str) -> str:
    """Acquire release locations from the checked-in Creative Suite CSV.

    This is the authored catalog already used to generate Suite UI/plan data;
    do not invent latest-version URLs or maintain a second download registry.
    """
    csv_path = Path(__file__).resolve().parents[2] / 'input' / 'Creative Suite.csv'
    if not csv_path.is_file():
        raise BuildError(f'Authored Creative Suite catalog missing: {csv_path}')
    matches = []
    with csv_path.open(encoding='utf-8-sig', newline='') as stream:
        for row in csv.DictReader(stream):
            # Match by the explicit authored catalog name, never row order or
            # a positional offset. The Suite brand differs from product names.
            catalog_names = {
                'ardour': 'Ardour', 'blender': 'Blender',
                'blockbench': 'Blockbench', 'cfconverter': 'CinterForge Converter',
                'code-oss': 'Code OSS', 'gimp': 'GIMP',
                'inkscape': 'Inkscape', 'kdenlive': 'Kdenlive',
                'krita': 'Krita', 'rapidraw': 'RapidRAW', 'scribus': 'Scribus',
            }
            display = (row.get('Name') or '').strip().casefold()
            if display == catalog_names[slug].casefold():
                matches.append((row.get('Linux ARM64 URL') or '').strip())
    if len(matches) != 1:
        raise BuildError(f'Expected one exact Linux source for {slug} in {csv_path}, found {len(matches)}. Missing entries must be authored explicitly; never shift to a neighboring app.')
    source = matches[0]
    if source.startswith(('apt:', 'sudo add-apt-repository ')):
        raise BuildError(
            f'{APPLICATIONS[slug]} catalog specifies a Linux repository ({source}), not a portable '
            'x86-64 application release. Repository-based application packaging needs an explicit '
            'AMD64 container build recipe; it cannot be treated as a portable archive.'
        )
    parsed = urlparse(source)
    filename = unquote(Path(parsed.path).name)
    if parsed.scheme != 'https' or not parsed.netloc or not filename:
        raise BuildError(f'Invalid catalog Linux release for {slug}: {source!r}')
    if not any(marker in filename.lower() for marker in ('x86_64', 'x64', 'amd64')):
        raise BuildError(f'Catalog source is not an explicit Linux AMD64 release for {slug}: {source}')
    if filename.lower().endswith('.deb'):
        raise BuildError(
            f'{APPLICATIONS[slug]} catalog provides a .deb installer, not a self-contained '
            'application. Requires a container packaging recipe and dependencies.'
        )
    if not filename.lower().endswith(('.tar.gz', '.tar.xz', '.tar.bz2', '.tgz', '.appimage', '.zip')):
        raise BuildError(f'Unsupported catalog Linux distribution for {slug}: {filename}')
    return source


def download_release(url: str, destination: Path, checksum: str) -> None:
    print(f'[Linux build] downloading {url}', flush=True)
    if not checksum:
        print('[Linux build] NOTICE: catalog has no pinned SHA256; recording downloaded bytes only', flush=True)
    request = urllib.request.Request(url, headers={'User-Agent': 'CinterForge-CreativeSuite/1.0'})
    with urllib.request.urlopen(request, timeout=120) as response, destination.open('wb') as stream:
        shutil.copyfileobj(response, stream)
    if not destination.stat().st_size:
        raise BuildError(f'Empty Linux distribution: {url}')
    if checksum and digest(destination) != checksum:
        destination.unlink()
        raise BuildError(f'SHA256 mismatch: {url}')


def copy_source(source: Path, output: Path) -> None:
    if source.is_dir():
        for entry in source.iterdir():
            target = output / entry.name
            if entry.is_symlink():
                raise BuildError(f'Symlink source requires a packaged archive: {entry}')
            if entry.is_dir():
                shutil.copytree(entry, target, symlinks=True)
            else:
                shutil.copy2(entry, target)
        return
    lower = source.name.lower()
    if lower.endswith(('.tar.gz', '.tar.xz', '.tar.bz2', '.tgz')):
        safe_extract(source, output)
    elif lower.endswith(('.appimage', '.zip')):
        if lower.endswith('.zip'):
            with zipfile.ZipFile(source) as bundle:
                for info in bundle.infolist():
                    member = Path(info.filename)
                    if member.is_absolute() or '..' in member.parts or stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK:
                        raise BuildError(f'Unsafe ZIP entry: {info.filename}')
                bundle.extractall(output)
        else:
            target = output / source.name
            shutil.copy2(source, target)
            target.chmod(target.stat().st_mode | stat.S_IXUSR)
    else:
        # A .deb is an installer, not a self-contained runnable Linux application.
        raise BuildError(f'{source.suffix} is not a self-contained Linux release; need a portable distribution')

def build(slug: str) -> None:
    if slug not in APPLICATIONS:
        raise BuildError(f'Unknown active Linux application: {slug}')
    output_root = os.environ.get('CINTER_BUILT_ROOT')
    if not output_root:
        raise BuildError('CINTER_BUILT_ROOT is required (supplied by ./Cinter build)')
    root = Path(output_root).expanduser().resolve()
    output = root / 'Output'
    release = os.environ.get('CINTER_APP_ARCHIVE', '').strip()
    url = os.environ.get('CINTER_APP_URL', '').strip()
    if release and url:
        raise BuildError('Specify only one of CINTER_APP_ARCHIVE and CINTER_APP_URL')
    if not release and not url:
        url = catalog_source(slug)
    checksum = os.environ.get('CINTER_APP_SHA256', '').strip().lower()
    with tempfile.TemporaryDirectory(prefix=f'cinter-{slug}-', dir=root) as tmp:
        temporary = Path(tmp)
        if url:
            if not url.startswith('https://') or (checksum and (len(checksum) != 64 or not all(c in '0123456789abcdef' for c in checksum))):
                raise BuildError('HTTPS URL and valid optional CINTER_APP_SHA256 required')
            release_path = temporary / unquote(Path(urlparse(url).path).name)
            if not release_path.name:
                raise BuildError('URL has no artifact filename')
            # Host downloads are cached alongside each generated build; the
            # catalog names concrete versioned releases, never latest redirects.
            cache = root / 'Source' / release_path.name
            cache.parent.mkdir(parents=True, exist_ok=True)
            if cache.is_file() and cache.stat().st_size and (not checksum or digest(cache) == checksum):
                print(f'[Linux build] reusing {cache}', flush=True)
            else:
                download_release(url, cache.with_name(cache.name + '.download'), checksum)
                cache.with_name(cache.name + '.download').replace(cache)
            release_path = cache
        else:
            release_path = Path(release).expanduser().resolve()
        if not release_path.exists():
            raise BuildError(f'Linux release missing: {release_path}')
        if release_path.is_file() and checksum and digest(release_path) != checksum:
            raise BuildError(f'SHA256 mismatch: {release_path}')
        candidate = temporary / 'Output'
        candidate.mkdir()
        copy_source(release_path, candidate)
        if not any(candidate.iterdir()):
            raise BuildError(f'Empty Linux release for {slug}')
        # Commit only after a complete candidate exists; preserve prior Output if replacement fails.
        previous = root / 'Output.previous'
        if previous.exists():
            raise BuildError(f'Previous incomplete replacement requires inspection: {previous}')
        if output.exists():
            output.rename(previous)
        try:
            candidate.rename(output)
        except BaseException:
            if previous.exists():
                previous.rename(output)
            raise
        if previous.exists():
            shutil.rmtree(previous)
    print(f'[Linux build] {APPLICATIONS[slug]}: {output}')

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('slug', choices=sorted(APPLICATIONS))
    args = parser.parse_args()
    try:
        build(args.slug)
    except (BuildError, OSError, ValueError, tarfile.TarError, zipfile.BadZipFile) as exc:
        print(f'[Linux build] ERROR: {exc}', file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
