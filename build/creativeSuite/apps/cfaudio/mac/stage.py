#!/usr/bin/env python3
"""Re-sign relocated Ardour Mach-O payloads after all packaging/copy operations.

Run directly to repair an already installed developer build.  Also called by
Creative Suite staging on the final copied app, before it becomes Installed.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

MAGIC = {b'\xfe\xed\xfa\xce', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf',
         b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca',
         b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca'}


def sign_bundle(bundle: Path) -> None:
    if sys.platform != 'darwin':
        raise RuntimeError('Ardour code-signature repair requires macOS')
    info = bundle / 'Contents/Info.plist'
    main = bundle / 'Contents/MacOS/Ardour9'
    if not info.is_file() or not main.is_file():
        raise RuntimeError(f'Not an Ardour9 application bundle: {bundle}')
    binaries = []
    macos_helpers = []
    for p in bundle.rglob('*'):
        if not p.is_file() or p.is_symlink():
            continue
        with p.open('rb') as stream:
            is_macho = stream.read(4) in MAGIC
        if is_macho:
            binaries.append(p)
        elif (p.parent == bundle / 'Contents/MacOS'
              and os.access(p, os.X_OK)):
            # Upstream Ardour installs executable shell helpers here.
            # codesign treats these as nested code and rejects the main
            # executable if they lack their own signatures.
            macos_helpers.append(p)
    if main not in binaries:
        raise RuntimeError(f'Invalid Mach-O Ardour entry point: {main}')
    nested = sorted((p for p in bundle.rglob('*') if p.is_dir() and not p.is_symlink()
                     and p.suffix in ('.app', '.framework', '.bundle', '.xpc', '.appex')),
                    key=lambda p: len(p.parts), reverse=True)
    # Sign loose libraries and plugins first; executable second; enclosing app last.
    binaries.sort(key=lambda p: (p == main, -len(p.parts), str(p)))
    sign = ['/usr/bin/codesign', '--force', '--sign', '-', '--timestamp=none']
    for p in macos_helpers:
        subprocess.run([*sign, str(p)], check=True)
    for p in binaries:
        if p == main:
            continue  # Signing the outer .app signs its main executable.
        subprocess.run([*sign, str(p)], check=True)
    for p in nested:
        subprocess.run([*sign, str(p)], check=True)
    subprocess.run([*sign, str(bundle)], check=True)
    for p in [*macos_helpers, *binaries]:
        subprocess.run(['/usr/bin/codesign', '--verify', '--strict', str(p)], check=True)
    for p in nested:
        subprocess.run(['/usr/bin/codesign', '--verify', '--strict', str(p)], check=True)
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', '--verbose=2', str(bundle)], check=True)
    print(f'[cfaudio][mac] Re-signed and verified {len(binaries)} Mach-O files and '
          f'{len(macos_helpers)} executable helpers in {bundle}', flush=True)



def repair_bundle(bundle: Path, *, version: str | None = None) -> None:
    """Repair the full relocatable Mach-O closure BEFORE applying signatures.

    Re-signing alone cannot repair @rpath dependencies left by a historical
    package.  dyld reports "no LC_RPATH's found" even for a properly signed
    executable when the original install names were not made bundle-relative.
    """
    suite = next(parent for parent in Path(__file__).resolve().parents
                 if parent.name == 'creativeSuite')
    sys.path.insert(0, str(suite))
    from lifecycle import root_from_source
    from shared.mac.dependency_closure import close_application_dependencies
    if version is None:
        # Direct repair of a versioned Steam install: Ardour9.app lives in
        # Applications/macOS/CinterForge Audio/<version>/.
        version = (os.environ.get('CINTER_INSTALL_VERSION') or
                   os.environ.get('CINTER_APP_VERSION') or
                   (bundle.parent.name if bundle.parent.name != 'payload' else '2026'))
    prefix = (root_from_source().parent / 'Builds/CreativeSuite/Built/cfaudio/mac'
              / version / 'Dependencies')
    if not (prefix / 'lib').is_dir():
        raise RuntimeError(f'Ardour private dependency prefix not found: {prefix}')
    close_application_dependencies(bundle, prefix)
    sign_bundle(bundle)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, help='Explicit existing Ardour9.app path')
    args = parser.parse_args()
    if args.bundle:
        repair_bundle(args.bundle.resolve())
        return
    # Use the existing authoritative Steam library selection; do not search
    # arbitrary directories or modify another application's installation.
    suite = next(parent for parent in Path(__file__).resolve().parents if parent.name == 'creativeSuite')
    sys.path.insert(0, str(suite))
    from lifecycle import installation_directory
    installed = installation_directory('cfaudio', 'mac')
    versions = sorted((p for p in installed.iterdir() if p.is_dir()), reverse=True) if installed.is_dir() else []
    if not versions:
        raise RuntimeError(f'No installed Ardour versions in {installed}')
    bundle = versions[0] / 'Ardour9.app'
    repair_bundle(bundle, version=versions[0].name)


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f'[cfaudio][mac] SIGNATURE ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
