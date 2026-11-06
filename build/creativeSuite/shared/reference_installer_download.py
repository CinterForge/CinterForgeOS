#!/usr/bin/env python3
"""Acquire ORIGINAL publisher distributions, never CinterForge-built applications.

Uses the maintained input/Creative Suite.csv upstream catalog. The artifacts live in the
persistent Applications/UnforkedInstallers share visible to CinterForgeOS.
Downloads are atomic, cached, and never deleted by inspection.
"""
from __future__ import annotations
import argparse
import hashlib
import math
import json
import shutil
import sys
import time
import webbrowser
import zipfile
from pathlib import Path
from urllib.parse import urlparse, unquote
from urllib.request import Request, urlopen

SUITE = Path(__file__).resolve().parents[1]
from upstream_catalog import read_catalog
MAX_BYTES = 1_500_000_000
PLATFORMS = {'mac': 'macOS', 'macos': 'macOS', 'win': 'Windows', 'windows': 'Windows', 'linux': 'Linux'}


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _browser_artifact(downloads: Path, filename: str) -> Path | None:
    """Find Safari's original download OR the directory it auto-extracted.

    Do not guess among multiple matches; an ambiguous artifact is an error.
    """
    original = downloads / filename
    if original.is_file():
        return original
    if original.suffix.casefold() != '.zip':
        return None
    extracted = downloads / original.stem
    return extracted if extracted.is_dir() else None


def _directory_inventory(directory: Path) -> tuple[int, int, float] | None:
    """A stable fingerprint to avoid archiving an in-progress Safari extraction."""
    files = [p for p in directory.rglob('*') if p.is_file()]
    if not files:
        return None
    if any(p.suffix.casefold() in {'.download', '.crdownload', '.part', '.tmp'} for p in files):
        return None
    stats = [p.stat() for p in files]
    return len(files), sum(s.st_size for s in stats), max(s.st_mtime for s in stats)


def _restore_safari_zip(directory: Path, temporary: Path) -> None:
    """Recreate ZIP with original top-level folder, atomically when complete."""
    if temporary.exists():
        temporary.unlink()
    root = directory.parent
    with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for source in sorted(directory.rglob('*')):
            if source.is_symlink():
                raise RuntimeError(f'Cannot safely repack symlink in browser extraction: {source}')
            if source.is_file():
                archive.write(source, source.relative_to(root))
    with zipfile.ZipFile(temporary) as archive:
        if archive.testzip() is not None:
            raise RuntimeError(f'Browser-extracted ZIP failed verification: {directory}')
        if not archive.namelist():
            raise RuntimeError(f'Browser-extracted ZIP was empty: {directory}')


INSTALLER_SUFFIXES = {
    'macOS': {'.dmg', '.pkg', '.zip'},
    'Windows': {'.exe', '.msi', '.zip', '.7z'},
    'Linux': {'.appimage', '.deb', '.run', '.tar.xz', '.tar.gz', '.tgz', '.rpm', '.zip'},
}


def _manually_staged_installer(folder: Path, platform: str) -> Path | None:
    """Use a publisher installer already put into the shared unforked directory.

    Only accepted for browser download pages whose URL has no installer filename.
    Never silently select an arbitrary file when several candidates exist.
    """
    allowed = INSTALLER_SUFFIXES[platform]
    candidates = sorted(p for p in folder.iterdir() if p.is_file()
                        and p.suffix.lower() in allowed and p.stat().st_size > 0) if folder.is_dir() else []
    if len(candidates) > 1:
        raise RuntimeError('Multiple manually staged installers in ' + str(folder)
                           + ': ' + ', '.join(p.name for p in candidates))
    return candidates[0] if candidates else None


def acquire(url: str, destination: Path, *, browser: bool = False, progress=None) -> str:
    """Shared HTTPS publisher downloader for CLI and Baseline installer."""
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.netloc:
        raise ValueError('Publisher installer URL must be HTTPS')
    if parsed.netloc.lower() in ('release-assets.githubusercontent.com', 'objects.githubusercontent.com'):
        raise ValueError('Transient signed download URL is not a publisher URL')
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and destination.stat().st_size > 0:
        if progress:
            progress(destination.stat().st_size, destination.stat().st_size, state='cached')
        return checksum(destination)
    temporary = destination.with_name(destination.name + '.part')
    if browser:
        downloads = Path.home() / 'Downloads'
        # Reuse browser output already downloaded before a prior interrupted run.
        artifact = _browser_artifact(downloads, destination.name)
        if artifact is None:
            if not webbrowser.open(url):
                raise RuntimeError(f'Cannot open publisher download: {url}')
            if progress:
                progress(0, 0, state='browser-opened')
        elif progress:
            progress(0, 0, state='browser-found')
        deadline = time.monotonic() + 15 * 60
        previous = None
        stable_since = None
        last_report = 0.0
        while time.monotonic() < deadline:
            artifact = _browser_artifact(downloads, destination.name)
            signature = None
            if artifact is not None:
                if artifact.is_dir():
                    signature = _directory_inventory(artifact)
                else:
                    stats = artifact.stat()
                    if stats.st_size > 1024 * 1024 and stats.st_size <= MAX_BYTES:
                        signature = (1, stats.st_size, stats.st_mtime)
            now = time.monotonic()
            if signature != previous:
                stable_since = now
                previous = signature
            elif signature and stable_since is not None and now - stable_since >= 8:
                try:
                    if artifact.is_dir():
                        if destination.suffix.casefold() != '.zip':
                            raise RuntimeError(f'Cannot repack directory as non-ZIP installer: {destination.name}')
                        if progress:
                            progress(signature[1], 0, state='browser-repacking')
                        _restore_safari_zip(artifact, temporary)
                    else:
                        shutil.copy2(artifact, temporary)
                    if temporary.stat().st_size == 0:
                        raise RuntimeError(f'Publisher download is empty: {artifact}')
                    temporary.replace(destination)
                    if progress:
                        progress(destination.stat().st_size, destination.stat().st_size, state='complete')
                    return checksum(destination)
                finally:
                    temporary.unlink(missing_ok=True)
            if progress and now - last_report >= 5:
                progress(signature[1] if signature else 0, 0,
                         state='browser-extracted' if artifact is not None and artifact.is_dir() else 'browser-wait')
                last_report = now
            time.sleep(2)
        raise TimeoutError(f'Publisher installer or extracted folder not found in ~/Downloads: {destination.name}')
    digest = hashlib.sha256()
    total = 0
    try:
        request = Request(url, headers={'User-Agent': 'CinterForge-CreativeSuite/1.0'})
        with urlopen(request, timeout=90) as response, temporary.open('wb') as output:
            if 'text/html' in response.headers.get('Content-Type', '').lower():
                raise RuntimeError('Publisher returned HTML rather than an installer')
            expected = int(response.headers.get('Content-Length') or 0)
            if progress:
                progress(0, expected, state='connected')
            last_report = time.monotonic()
            if expected > MAX_BYTES:
                raise ValueError('Publisher installer exceeds size limit')
            while block := response.read(256 * 1024):
                total += len(block)
                if total > MAX_BYTES:
                    raise ValueError('Publisher installer exceeds size limit')
                output.write(block)
                digest.update(block)
                if progress and time.monotonic() - last_report >= 2:
                    progress(total, expected, state='downloading')
                    last_report = time.monotonic()
        if total == 0:
            raise RuntimeError('Publisher returned empty installer')
        temporary.replace(destination)
        if progress:
            progress(total, expected, state='complete')
        return digest.hexdigest()
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser(description='Download original upstream publisher installers into persistent Applications/UnforkedInstallers')
    ap.add_argument('--product-root', type=Path, required=True)
    ap.add_argument('--platform', choices=['mac', 'linux', 'win', 'all'], required=True)
    ap.add_argument('--application', default='all')
    args = ap.parse_args()
    root = args.product_root.expanduser().resolve()
    if root.name != 'CinterForge' or not root.is_dir():
        ap.error('Product root must be the existing persistent CinterForge directory')
    items = read_catalog(installer_only=True)
    chosen = [item for item in items if args.application.casefold() in ('all', item['name'].casefold())]
    if not chosen:
        ap.error(f'No upstream publisher installer catalog entry for {args.application}')
    platforms = ['mac', 'linux', 'win'] if args.platform == 'all' else [args.platform]
    outroot = root / 'Applications' / 'UnforkedInstallers'
    failures = 0
    apt_targets = []
    for platform in platforms:
        for item in chosen:
            name = item['name']
            url = item.get('links', {}).get({'mac': 'macos', 'linux': 'linux', 'win': 'windows'}[platform])
            if not url:
                print(f'[unforked] {name}/{platform}: no upstream installer URL (skipped)', flush=True)
                continue
            # Linux CSV entries include package-manager instructions (apt/ppa),
            # not downloadable files. Preserve the package-manager installation
            # workflow in Ubuntu; do not pretend those entries are installers.
            if platform == 'linux' and not url.startswith('https://'):
                if name in ('Inkscape', 'Scribus'):
                    apt_targets.append(name)
                    print(f'[unforked] {name}/linux: queued original Ubuntu APT package capture', flush=True)
                else:
                    folder = outroot / 'Linux' / name
                    existing = _manually_staged_installer(folder, 'Linux')
                    print(f'[unforked] {name}/linux: ' + (f'publisher installer staged: {existing}' if existing else f'Ubuntu package reference {url} (no publisher file)'), flush=True)
                continue
            filename = unquote(Path(urlparse(url).path).name)
            if not filename or filename in ('.', '..') or '/' in filename or '\\' in filename:
                print(f'[unforked] ERROR {name}: unsafe publisher filename', file=sys.stderr)
                failures += 1
                continue
            target = outroot / PLATFORMS[platform] / name / filename
            try:
                # A URL such as /download?platform=osx is a *web page*, not a
                # file named "download". The publisher may require payment or
                # a manual demo download. Never hang waiting for that page.
                page_url = not any(filename.lower().endswith(suffix) for suffix in INSTALLER_SUFFIXES[PLATFORMS[platform]])
                if page_url:
                    staged = _manually_staged_installer(target.parent, PLATFORMS[platform])
                    if staged is None:
                        print(f'[unforked] {name}/{platform}: MANUAL DOWNLOAD REQUIRED: {url}', flush=True)
                        print(f'[unforked] {name}/{platform}: place the original publisher installer in {target.parent}', flush=True)
                        continue
                    target = staged
                    print(f'[unforked] {name}/{platform}: manually staged publisher installer: {target}', flush=True)
                else:
                    print(f'[unforked] {name}/{platform}: {target}', flush=True)
                start = time.monotonic()
                last_bytes = 0
                last_time = start
                def report(downloaded: int, expected: int, *, state: str = 'downloading') -> None:
                    nonlocal last_bytes, last_time
                    now = time.monotonic()
                    elapsed = max(now - start, 0.001)
                    rate = downloaded / elapsed
                    amount = f'{downloaded / (1024 * 1024):.1f} MiB'
                    if expected:
                        amount += f' / {expected / (1024 * 1024):.1f} MiB ({downloaded / expected * 100:.1f}%)'
                    if state == 'browser-extracted':
                        amount += ' (Safari extracted ZIP; waiting for stable folder)'
                    elif state == 'browser-repacking':
                        amount += ' (repacking extracted directory into ZIP)'
                    elif state == 'browser-found':
                        amount = 'existing browser download or extracted folder found'
                    elif state == 'browser-wait':
                        amount += ' (waiting for publisher browser download)'
                    elif state == 'browser-opened':
                        amount = 'publisher download opened in browser; monitoring ~/Downloads'
                    elif state == 'cached':
                        amount += ' (cached, verifying SHA-256)'
                    elif state == 'connected':
                        amount = 'connected; receiving installer'
                    elif state == 'downloading':
                        amount += f' at {rate / (1024 * 1024):.2f} MiB/s'
                    print(f'[unforked] {name}/{platform}: {state}: {amount}', flush=True)
                    last_bytes, last_time = downloaded, now
                cached = target.is_file() and target.stat().st_size > 0
                mode = item.get('downloadMode') == 'browser' and platform != 'linux'
                digest = acquire(url, target, browser=mode, progress=report)
                print(f'[unforked] {name}/{platform}: {"cached" if cached else "downloaded"} sha256={digest}', flush=True)
            except Exception as exc:
                failures += 1
                print(f'[unforked] ERROR {name}/{platform}: {exc}', file=sys.stderr, flush=True)
    if apt_targets:
        # Reuse the EXISTING headless Creative Suite guest installer, including
        # its baseline/package/post-install inspection. No second APT workflow.
        import subprocess
        # The Linux guest runner and filesystem authority require Cinter/CinterForge,
        # not the Cinter checkout or the directory above it.
        source_root = SUITE.parents[2]
        server_root = source_root / 'CinterForgeServer'
        if str(server_root) not in sys.path:
            sys.path.insert(0, str(server_root))
        from cf.io.CinterIO import cinterforgeBuildsRootPath
        runner = SUITE / 'apps/cinterforge-os/linux/linux_guest.py'
        builds_root = cinterforgeBuildsRootPath(source_root)
        inspection_root = builds_root / 'CreativeSuite/Linux/Inspection'
        for name in apt_targets:
            print(f'[unforked] {name}/linux: running existing headless reference installer and package inventory', flush=True)
            command = [sys.executable, '-u', str(runner), '--repo-root', str(source_root),
                       '--install', '--application', name, '--architecture', 'amd64',
                       '--source-type', 'apt-repository', '--source', f'apt:{name.lower()}']
            result = subprocess.run(command)
            if result.returncode:
                failures += 1
                print(f'[unforked] ERROR {name}/linux: reference guest install failed ({result.returncode})', file=sys.stderr, flush=True)
                continue
            captured = inspection_root / name / 'DebianPackages'
            packages = sorted(captured.glob('*.deb')) if captured.is_dir() else []
            if not packages:
                failures += 1
                print(f'[unforked] ERROR {name}/linux: headless installer produced no .deb package archives at {captured}', file=sys.stderr, flush=True)
                continue
            destination = outroot / 'Linux' / name
            destination.mkdir(parents=True, exist_ok=True)
            for archive in packages:
                target = destination / archive.name
                temporary = target.with_name(target.name + '.part')
                try:
                    shutil.copyfile(archive, temporary)
                    temporary.replace(target)
                    print(f'[unforked] {name}/linux: captured {target.name} sha256={checksum(target)}', flush=True)
                finally:
                    temporary.unlink(missing_ok=True)
            print(f'[unforked] {name}/linux: {len(packages)} original Ubuntu/Debian reference packages retained', flush=True)
    print(f'[unforked] installer root: {outroot}', flush=True)
    print('[unforked] Linux APT reference packages are captured by the existing headless installer.', flush=True)
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
