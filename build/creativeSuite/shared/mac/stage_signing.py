"""Generic macOS signature validation at the Stage boundary.

Stage signs the final bundled artifact before installation or publication.
No app's source checkout or dependency setup is used here.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


_SOURCE_BUNDLE_APPS = frozenset({
    'gimp', 'inkscape', 'kdenlive', 'krita', 'scribus', 'rapidraw'
})


def sign_mac_source_bundle(app: str, platform: str, extracted: Path) -> None:
    """Sign a complete source-built macOS bundle before it is installed."""
    if platform != 'mac' or app not in _SOURCE_BUNDLE_APPS:
        return
    if sys.platform != 'darwin':
        raise RuntimeError(f'{app}/mac Stage must run on macOS')
    bundles = list(extracted.glob('*.app'))
    if len(bundles) != 1:
        raise RuntimeError(f'Expected exactly one bundle to sign: {extracted}')
    identity = os.environ.get('CINTER_MAC_CODESIGN_IDENTITY', '-').strip()
    if not identity:
        raise RuntimeError('CINTER_MAC_CODESIGN_IDENTITY cannot be empty')
    bundle = bundles[0]
    subprocess.run(['codesign', '--force', '--deep', '--sign', identity, str(bundle)], check=True)
    subprocess.run(['codesign', '--verify', '--deep', '--strict', '--verbose=2', str(bundle)], check=True)
    print(f'SIGNED_STAGED_APP {bundle} identity={identity}', flush=True)
