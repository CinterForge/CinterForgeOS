"""Complete, deterministic filesystem snapshots for publisher installation analysis.

Only explicitly named volatile/mounted paths are excluded. Errors are recorded,
never silently discarded. Reading files to hash them does not modify source files.
"""

import hashlib
import os
from pathlib import Path
import stat


def describe_path(path, include_hashes=True):
    """Record the information needed to identify additions and modifications."""
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode):
        entry_type = 'symlink'
    elif stat.S_ISDIR(metadata.st_mode):
        entry_type = 'directory'
    elif stat.S_ISREG(metadata.st_mode):
        entry_type = 'file'
    else:
        entry_type = 'special'

    entry = {
        'type': entry_type,
        'size': metadata.st_size,
        'mode': oct(stat.S_IMODE(metadata.st_mode)),
        'uid': metadata.st_uid,
        'gid': metadata.st_gid,
        'mtimeNs': metadata.st_mtime_ns,
        'linkCount': metadata.st_nlink,
    }
    if entry_type == 'symlink':
        entry['target'] = os.readlink(path)
    if entry_type == 'file' and include_hashes:
        digest = hashlib.sha256()
        with path.open('rb') as source:
            while True:
                block = source.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
        entry['sha256'] = digest.hexdigest()
    return entry


def inventory(root, excluded_paths=(), include_hashes=True):
    """Scan one tree, staying on its filesystem and never following symlinks.

    Explicit exclusions are absolute paths. Discovered mount boundaries and
    errors are reported so that missing coverage cannot be mistaken for success.
    """
    root = Path(root)
    excluded = {str(Path(location)) for location in excluded_paths}
    result = {
        'root': str(root),
        'exists': root.is_dir(),
        'entries': {},
        'excludedPaths': [],
        'errors': {},
    }
    if not root.is_dir():
        result['errors'][str(root)] = 'Inventory root does not exist or is not a directory'
        return result

    root_device = root.stat().st_dev

    def visit(path):
        path_text = str(path)
        if path_text in excluded:
            result['excludedPaths'].append(path_text)
            return

        relative_path = '.' if path == root else path.relative_to(root).as_posix()
        try:
            metadata = path.lstat()
            if stat.S_ISDIR(metadata.st_mode) and metadata.st_dev != root_device:
                result['excludedPaths'].append(path_text + ' (different filesystem)')
                return
            entry = describe_path(path, include_hashes)
            result['entries'][relative_path] = entry
            if entry['type'] != 'directory':
                return
            with os.scandir(path) as iterator:
                names = sorted(item.name for item in iterator)
            for name in names:
                visit(path / name)
        except (OSError, PermissionError) as error:
            result['errors'][relative_path] = str(error)

    visit(root)
    result['entryCount'] = len(result['entries'])
    result['errorCount'] = len(result['errors'])
    result['excludedPaths'].sort()
    return result


def filesystem_delta(before, after):
    """Compare hashed snapshots, including permissions, owners and symlinks."""
    previous = before['entries']
    current = after['entries']
    previous_paths = set(previous)
    current_paths = set(current)
    return {
        'added': {path: current[path] for path in sorted(current_paths - previous_paths)},
        'deleted': {path: previous[path] for path in sorted(previous_paths - current_paths)},
        'modified': {
            path: {'before': previous[path], 'after': current[path]}
            for path in sorted(previous_paths & current_paths)
            if previous[path] != current[path]
        },
    }
