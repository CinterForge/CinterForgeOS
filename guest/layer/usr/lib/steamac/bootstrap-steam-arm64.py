#!/usr/bin/env python3
"""Install Valve's official latest native ARM64 client into persistent Steam home.

No redistributed Valve files, guessed static version, rootfs modifications,
Steam account reset, pacman operation, or cross-architecture workaround.
"""
import os
import pathlib
import re
import shutil
import sys
import tempfile
import urllib.request
import zipfile

root = pathlib.Path(sys.argv[1]).resolve()
if root != pathlib.Path.home() / '.local/share/Steam':
    raise SystemExit('[Steam bootstrap] ERROR: unexpected Steam destination')
cmdline = pathlib.Path('/proc/cmdline').read_text()
if 'steamac.steam_client=deckbeta' in cmdline:
    channel = 'steamdeck_publicbeta'
elif 'steamac.steam_client=deck' in cmdline:
    channel = 'steamdeck_stable'
else:
    channel = 'publicbeta'
manifest_url = 'https://client-update.steamstatic.com/steam_client_' + channel + '_linuxarm64' 
print('[Steam bootstrap] Reading official Valve client manifest:', manifest_url, flush=True)
with urllib.request.urlopen(manifest_url, timeout=30) as response:
    manifest = response.read().decode('utf-8')
filenames = re.findall(r'bins_linuxarm64_linuxarm64\.zip\.[0-9a-fA-F]{40}', manifest)
if len(set(filenames)) != 1:
    raise SystemExit('[Steam bootstrap] ERROR: manifest lacks one unambiguous current ARM64 ZIP')
filename = filenames[0]
url = 'https://client-update.steamstatic.com/' + filename
root.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='steam-arm64-', dir=root) as tmp:
    archive = pathlib.Path(tmp) / 'client.zip'
    print('[Steam bootstrap] Downloading:', url, flush=True)
    with urllib.request.urlopen(url, timeout=120) as response, archive.open('wb') as output:
        shutil.copyfileobj(response, output)
    with zipfile.ZipFile(archive) as z:
        # ZIP file entries may contain backslashes from Valve's archive.
        for member in z.infolist():
            path = pathlib.PurePosixPath(member.filename.replace('\\', '/'))
            if not path.parts or path.parts[0] != 'steamrtarm64' or '..' in path.parts:
                continue
            target = pathlib.Path(tmp).joinpath(*path.parts)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(member) as src, target.open('wb') as dst:
                    shutil.copyfileobj(src, dst)
                mode = (member.external_attr >> 16) & 0o777
                target.chmod(mode if mode else 0o644)
    staged = pathlib.Path(tmp) / 'steamrtarm64'
    if not (staged / 'steam').is_file():
        raise SystemExit('[Steam bootstrap] ERROR: official archive has no steamrtarm64/steam')
    (staged / 'steam').chmod(0o755)
    # Keep an existing working runtime untouched; repair a missing/incomplete
    # runtime only. Move across the same filesystem for atomic publication.
    destination = root / 'steamrtarm64'
    if destination.exists():
        if (destination / 'steam').is_file():
            (destination / 'steam').chmod(0o755)
            print('[Steam bootstrap] Existing runtime won race; preserving it')
            sys.exit(0)
        # A failed prior bootstrap may have left data here: leave those files
        # untouched and fill only missing files, never erase an installation.
        for source in staged.rglob('*'):
            relative = source.relative_to(staged)
            target = destination / relative
            if source.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(target))
    else:
        staged.rename(destination)
print('[Steam bootstrap] Valve native ARM64 client installed:', root / 'steamrtarm64/steam', flush=True)
