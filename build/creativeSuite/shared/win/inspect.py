#!/usr/bin/env python3
"""Read-only Windows publisher-package inventory for the Creative Suite.

Inventory only locally acquired distributions. Never execute untrusted Windows
installers on macOS; executable installation is a separate FEX/Wine guest test.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import os
import shutil
import struct
import subprocess
import zipfile
from pathlib import Path
from urllib.parse import urlparse

SUITE = Path(__file__).resolve().parents[2]
CATALOG = SUITE / 'input' / 'Creative Suite.csv'
EXTENSIONS = {'.exe', '.msi', '.msix', '.msixbundle', '.appx', '.appxbundle', '.zip', '.7z', '.tar', '.gz'}
EXCLUDED = {'cinterforge', 'blockbench', 'code-oss', 'converter', 'cinterforge converter', 'openusd', 'natron', 'tenacity'}


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as source:
        for part in iter(lambda: source.read(1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()


def pe_info(path: Path) -> dict:
    """Inspect an EXE/DLL header without loading or executing the file."""
    with path.open('rb') as source:
        if source.read(2) != b'MZ':
            return {'format': 'not_pe'}
        source.seek(0x3c)
        off = struct.unpack('<I', source.read(4))[0]
        if off > path.stat().st_size - 24:
            return {'format': 'invalid_pe_offset'}
        source.seek(off)
        if source.read(4) != b'PE\0\0':
            return {'format': 'not_pe'}
        machine = struct.unpack('<H', source.read(2))[0]
        return {'format': 'pe', 'machine': hex(machine), 'architecture':
                {0x8664: 'x86_64', 0x14c: 'x86', 0xaa64: 'arm64'}.get(machine, 'unknown')}


def contents(path: Path) -> dict:
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            return {'format': 'zip', 'entries': [{'path': i.filename, 'size': i.file_size,
                      'crc': f'{i.CRC:08x}', 'mode': oct((i.external_attr >> 16) & 0o7777)}
                      for i in archive.infolist()]}
    if path.suffix.lower() == '.exe':
        return pe_info(path)
    # List an MSI/7z/EXE installer with a locally present archive reader, if possible.
    seven = shutil.which('7z') or shutil.which('7zz')
    if seven:
        try:
            completed = subprocess.run([seven, 'l', '-slt', str(path)], text=True,
                                       capture_output=True, timeout=90)
            return {'format': '7z-list', 'exitCode': completed.returncode,
                    'listing': completed.stdout[:2_000_000], 'error': completed.stderr[:2000]}
        except (subprocess.TimeoutExpired, OSError) as error:
            return {'format': 'inspection_error', 'error': str(error)}
    return {'format': 'opaque_installer', 'inspection': '7z/7zz not installed; no execution attempted'}


def write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.incoming')
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    temp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--builds-root', type=Path, required=True, help='External Builds/CreativeSuite path')
    parser.add_argument('--application', default='all')
    parser.add_argument('--inspection-root', type=Path, help='Write directly into persistent Steam Applications/Logs/Inspections')
    args = parser.parse_args()
    builds = args.builds_root.resolve()
    if not CATALOG.is_file():
        parser.error(f'Creative Suite catalog missing: {CATALOG}')
    with CATALOG.open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    report_root = (args.inspection_root or builds / 'Inspection') / 'Windows'
    summary = []
    selected = False
    for entry in rows:
        name = (entry.get('Name') or '').strip()
        if not name or name.casefold() in EXCLUDED:
            continue
        if args.application.casefold() not in ('all', name.casefold()):
            continue
        selected = True
        url = (entry.get('Windows x64 URL') or '').strip()
        filename = Path(urlparse(url).path).name
        candidates = [
            builds / 'Downloads' / 'Applications' / 'Windows' / name,
            builds / 'Windows' / 'Downloads' / name,
            builds / 'Windows' / name,
        ]
        packages = []
        seen = set()
        for folder in candidates:
            if not folder.is_dir():
                continue
            for artifact in sorted(folder.iterdir()):
                if not artifact.is_file() or artifact.suffix.lower() not in EXTENSIONS:
                    continue
                real = artifact.resolve()
                if real in seen:
                    continue
                seen.add(real)
                item = {'path': str(artifact), 'bytes': artifact.stat().st_size,
                        'sha256': digest(artifact), 'matchesCatalogName': artifact.name == filename}
                item['contents'] = contents(artifact)
                packages.append(item)
        status = 'inspected' if packages else 'no_downloaded_distribution'
        record = {'application': name, 'status': status, 'catalogURL': url,
                  'searchedDirectories': [str(path) for path in candidates],
                  'packages': packages,
                  'runtime': 'not_tested; launch verification requires the SteamOS FEX/Wine guest'}
        write(report_root / name / 'DistributionContents.json', record)
        summary.append({'application': name, 'status': status, 'distributions': len(packages)})
        print(f'[Windows inspection] {name}: {status}, packages={len(packages)}', flush=True)
    if not selected:
        parser.error(f'Application not present in active catalog: {args.application}')
    write(report_root / 'Overview.json', {'applications': summary, 'mode': 'read_only',
          'note': 'Windows execution requires the FEX/Wine guest; a downloaded package is not a successful installation.'})
    print(f'[Windows inspection] reports: {report_root}', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
