#!/usr/bin/env python3
"""Non-destructive baseline of the native macOS publisher reference environment."""
import errno
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import stat
import sys


def command(*args):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=60)
        return {'exitCode': p.returncode, 'stdout': p.stdout[:250000], 'stderr': p.stderr[:20000]}
    except (OSError, subprocess.TimeoutExpired) as e:
        return {'error': str(e)}



def known_protected_location(path):
    """Classify macOS-managed private data, never arbitrary unreadable files.

    This is an explicitly bounded inventory exclusion, NOT elevated access.
    GoogleUpdater's public installation files remain in scope; only actual
    access-denied paths are excluded and individually reported.
    """
    path = Path(path)
    protected = (
        Path('/Library/Application Support/Apple/AssetCache/Data'),
        Path('/Library/Application Support/Apple/ParentalControls/Users'),
        Path('/Library/Application Support/Google/GoogleUpdater'),
        Path.home() / 'Library/Application Support/FileProvider',
    )
    return any(path == prefix or prefix in path.parents for prefix in protected)


def protected_permission_error(path, error):
    return (isinstance(error, OSError) and error.errno in (errno.EACCES, errno.EPERM)
            and known_protected_location(path))

def inventory(root, steamapps_root=None):
    """Inventory every entry below root, including SHA-256 file contents.

    Never follow symlinks or silently omit unreadable entries. Record the full
    relative pathname, file type, owner, permissions and link target.
    """
    entries = {}
    errors = {}
    root = Path(root)
    if not root.is_dir():
        return {'root': str(root), 'exists': False, 'entries': entries,
                'errors': {str(root): 'Directory does not exist'}, 'errorCount': 1}

    excluded_paths = {str(steamapps_root or (Path.home() / 'Library/Application Support/Steam/steamapps'))}
    excluded = []
    excluded_protected = {}
    # Developer sources are not an installed application reference environment.
    # Keep this exclusion explicit even if scan roots expand in the future.
    source_root = Path.home() / 'Source'
    if root == source_root or source_root in root.parents:
        return {'root': str(root), 'exists': True, 'entries': entries,
                'errors': errors, 'entryCount': 0, 'errorCount': 0,
                'excludedPaths': [str(root)]}

    def record_walk_error(error):
        path = Path(error.filename)
        if protected_permission_error(path, error):
            excluded_protected[str(path)] = str(error)
        else:
            errors[str(path)] = str(error)

    for directory, directories, files in os.walk(root, followlinks=False, onerror=record_walk_error):
        for directory_name in list(directories):
            candidate = str(Path(directory) / directory_name)
            if candidate in excluded_paths:
                directories.remove(directory_name)
                excluded.append(candidate)
        directories.sort()
        for name in sorted(directories + files):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            try:
                metadata = path.lstat()
                is_link = stat.S_ISLNK(metadata.st_mode)
                is_directory = stat.S_ISDIR(metadata.st_mode)
                is_file = stat.S_ISREG(metadata.st_mode)
                entry = {
                    'type': 'symlink' if is_link else 'directory' if is_directory else 'file' if is_file else 'special',
                    'size': metadata.st_size,
                    'mtimeNs': metadata.st_mtime_ns,
                    'mode': oct(stat.S_IMODE(metadata.st_mode)),
                    'uid': metadata.st_uid,
                    'gid': metadata.st_gid,
                }
                if is_link:
                    entry['target'] = os.readlink(path)
                if is_file:
                    digest = hashlib.sha256()
                    with path.open('rb') as source:
                        while True:
                            block = source.read(1024 * 1024)
                            if not block:
                                break
                            digest.update(block)
                    entry['sha256'] = digest.hexdigest()
                entries[relative] = entry
            except OSError as error:
                if protected_permission_error(path, error):
                    excluded_protected[str(path)] = str(error)
                else:
                    errors[relative] = str(error)
    return {'root': str(root), 'exists': True, 'entries': entries,
            'errors': errors, 'entryCount': len(entries), 'errorCount': len(errors),
            'excludedPaths': excluded,
            'excludedProtectedPaths': excluded_protected,
            'excludedProtectedCount': len(excluded_protected)}


def main():
    # Baseline belongs beside the actual installed apps, never the default
    # home Steam library when Stage uses a registered external volume.
    suite = Path(__file__).resolve().parents[2]
    if str(suite) not in sys.path:
        sys.path.insert(0, str(suite))
    from lifecycle import steamapps
    steam_root = steamapps()
    steam = steam_root / 'common/CinterForge/Applications'
    log = steam / 'Logs/EnvironmentBaselines/macOS/Baseline.json'
    if not steam.is_dir():
        print(f'ERROR: Persistent CinterForge Applications location missing: {steam}', file=sys.stderr)
        return 1
    data = {'schemaVersion': 2, 'environment': 'macOS-native', 'architecture': platform.machine(),
            'system': command('sw_vers'), 'hardware': command('uname', '-a'),
            'installedPackages': command('/usr/sbin/pkgutil', '--pkgs'),
            'homebrew': command('brew', 'list', '--versions'),
            'locations': {str(root): inventory(root, steam_root) for root in (
                Path('/Applications'), Path.home() / 'Applications',
                Path('/Library/Application Support'), Path('/Library/Frameworks'),
                Path('/Library/LaunchAgents'), Path('/Library/LaunchDaemons'),
                Path('/Library/Receipts'), Path('/usr/local'), Path('/opt/homebrew'),
                Path.home() / 'Library/Application Support',
                Path.home() / 'Library/Preferences',
                Path.home() / 'Library/LaunchAgents')},
            'sharedInstallers': (steam / 'UnforkedInstallers/macOS').is_dir(),
            'note': 'Includes content SHA-256 for every readable regular file in the selected installation locations.'}
    incomplete = {name: details['errors'] for name, details in data['locations'].items()
                  if details['errorCount'] and details['exists']}
    protected_exclusions = {name: details.get('excludedProtectedPaths', {})
                            for name, details in data['locations'].items()
                            if details.get('excludedProtectedPaths')}
    data['inventorySummary'] = {
        'entries': sum(len(location['entries']) for location in data['locations'].values()),
        'sha256Files': sum(sum('sha256' in entry for entry in location['entries'].values())
                           for location in data['locations'].values()),
        'errors': sum(location['errorCount'] for location in data['locations'].values()
                      if location['exists']),
        'excludedProtectedPaths': sum(len(paths) for paths in protected_exclusions.values()),
    }
    data['coverageLimitations'] = {
        'macOSProtectedPaths': protected_exclusions,
        'note': ('These exact unreadable OS/application-managed paths were excluded. '
                 'This baseline is complete for its declared readable scope, '
                 'but does not represent a full byte-for-byte snapshot of macOS.')
    }
    data['fullyInventoried'] = not bool(protected_exclusions) and not bool(incomplete)
    data['complete'] = not bool(incomplete) and data['inventorySummary']['sha256Files'] > 0
    if incomplete:
        print(f'ERROR: Baseline contains unreadable paths in {list(incomplete)}', file=sys.stderr)
    log.parent.mkdir(parents=True, exist_ok=True)
    temporary = log.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(data, sort_keys=True, indent=2) + '\n')
    temporary.replace(log)
    summary = data['inventorySummary']
    print(f"[baseline] macOS: schema v2; {summary['entries']} entries, "
          f"{summary['sha256Files']} SHA-256 hashes, {summary['errors']} errors; "
          f"protected exclusions={summary['excludedProtectedPaths']}; "
          f"complete={data['complete']}; fullyInventoried={data['fullyInventoried']}", flush=True)
    print(f'[baseline] macOS: {log}', flush=True)
    return 0 if data['complete'] else 1

if __name__ == '__main__':
    sys.exit(main())
