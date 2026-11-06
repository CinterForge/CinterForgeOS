#!/usr/bin/env python3
"""Capture upstream APT .deb reference packages into the shared installer store.

Run in the existing Ubuntu AMD64 guest/container with the persistent Applications
share mounted at /mnt/cinter-apps. This downloads but does not install software.
APT sources (including an Inkscape PPA) must already be configured in that guest.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

PACKAGES = {'Inkscape': 'inkscape', 'Scribus': 'scribus'}

def run(args: list[str], cwd: Path | None = None) -> str:
    print('[unforked apt] ' + ' '.join(args), flush=True)
    return subprocess.check_output(args, cwd=cwd, text=True, stderr=subprocess.STDOUT)

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('application', choices=['all', *PACKAGES], nargs='?', default='all')
    parser.add_argument('--applications-root', type=Path, default=Path('/mnt/cinter-apps'))
    args = parser.parse_args()
    if run(['dpkg', '--print-architecture']).strip() != 'amd64':
        parser.error('Requires the existing Ubuntu AMD64 guest, not the macOS host or ARM64 SteamOS')
    root = args.applications_root / 'UnforkedInstallers' / 'Linux'
    if not args.applications_root.is_dir():
        parser.error('Shared Applications mount missing: ' + str(args.applications_root))
    names = list(PACKAGES) if args.application == 'all' else [args.application]
    failures = 0
    for name in names:
        package = PACKAGES[name]
        try:
            policy = run(['apt-cache', 'policy', package])
            if 'Candidate: (none)' in policy or 'Candidate:' not in policy:
                raise RuntimeError(f'No APT candidate for {package}; configure the original Ubuntu repository first')
            if name == 'Inkscape' and 'inkscape.dev' not in policy and 'inkscape' not in run(['apt-cache', 'show', package]):
                raise RuntimeError('Cannot establish Inkscape package identity')
            folder = root / name
            folder.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='cinter-apt-') as scratch:
                run(['apt-get', 'download', package], cwd=Path(scratch))
                debs = list(Path(scratch).glob('*.deb'))
                if len(debs) != 1:
                    raise RuntimeError(f'Expected one downloaded DEB for {package}, found {len(debs)}')
                original = debs[0]
                destination = folder / original.name
                if not destination.exists():
                    shutil.copy2(original, destination)
                digest = hashlib.sha256(destination.read_bytes()).hexdigest()
                report = {'schemaVersion': 1, 'application': name, 'package': package,
                          'packagePolicy': policy, 'installer': destination.name, 'sha256': digest,
                          'note': 'Only top-level DEB; dependencies must be captured separately for offline installation'}
                tmp = folder / 'AptCapture.json.tmp'
                tmp.write_text(json.dumps(report, indent=2) + '\n')
                tmp.replace(folder / 'AptCapture.json')
                print(f'[unforked apt] {name}: {destination} sha256={digest}', flush=True)
        except Exception as exc:
            failures += 1
            print(f'[unforked apt] ERROR {name}: {exc}', flush=True)
    return int(failures != 0)

if __name__ == '__main__':
    raise SystemExit(main())
