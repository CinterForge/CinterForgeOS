#!/usr/bin/env python3
"""Read-only inventory of original macOS publisher installer archives.

Inspect only the managed UnforkedInstallers location. Never execute an
installer, download files, or treat a staged CinterForge app as an upstream
reference distribution.
"""
from __future__ import annotations

import hashlib
import json
import plistlib
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlparse

SUFFIXES = ('.dmg', '.pkg', '.zip', '.tar.gz', '.tar.xz', '.tgz', '.tbz2')


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _archive_info(path: Path) -> dict:
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            return {'format': 'zip', 'entries': [
                {'path': item.filename, 'bytes': item.file_size, 'crc32': f'{item.CRC:08x}'}
                for item in archive.infolist()
            ]}
    return {'format': path.suffix.lower().lstrip('.') or 'unknown',
            'note': 'Publisher archive inventoried without execution or mounting'}


def inspect_distributions(storage: Path, name: str, catalog_entry: dict | None,
                          report_dir: Path) -> dict:
    """Return the fields consumed by reference_install_inspection.py."""
    storage = Path(storage)
    folder = storage / 'UnforkedInstallers' / 'macOS' / name
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    url = ((catalog_entry or {}).get('links') or {}).get('macos', '')
    expected = Path(unquote(urlparse(url).path)).name if url else ''
    packages = []
    if folder.is_dir():
        for path in sorted(folder.iterdir()):
            if not path.is_file() or path.is_symlink():
                continue
            if not path.name.lower().endswith(SUFFIXES):
                continue
            packages.append({
                'path': str(path), 'bytes': path.stat().st_size,
                'sha256': _sha256(path), 'matchesCatalogName': path.name == expected,
                'contents': _archive_info(path),
            })
    status = 'inspected' if packages else 'no_downloaded_distribution'
    result = {
        'application': name, 'status': status, 'archiveCount': len(packages),
        'downloadLocation': str(folder), 'catalogURL': url,
        'packages': packages, 'mode': 'read_only',
    }
    destination = report_dir / 'DistributionContents.json'
    temporary = destination.with_name(destination.name + '.incoming')
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    temporary.replace(destination)
    return result
