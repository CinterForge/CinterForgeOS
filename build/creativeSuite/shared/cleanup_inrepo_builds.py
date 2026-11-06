#!/usr/bin/env python3
"""Explicitly remove an obsolete Cinter/Builds tree, never the sibling Builds tree.

The real generated output is ../Builds. This command is intentionally separate
from build/stage/test: nothing is silently moved, merged or overwritten.
"""
from __future__ import annotations

import argparse
import filecmp
from pathlib import Path
import shutil
import subprocess
import sys


def _macos_metadata(path: Path) -> bool:
    # Finder metadata is not a generated build artifact. Never merge it into
    # the canonical external Builds tree or treat it as a build conflict.
    return path.name == '.DS_Store' or path.name.startswith('._')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cinter_root', type=Path)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--migrate', action='store_true',
                        help='Move obsolete generated files to ../Builds without overwriting existing output')
    args = parser.parse_args()
    root = args.cinter_root.resolve(strict=True)
    if not (root / 'Cinter').is_file() or not (root / 'CinterForge').is_dir():
        raise RuntimeError(f'Not a Cinter checkout: {root}')
    victim = root / 'Builds'
    external = root.parent / 'Builds'
    if victim.is_symlink():
        raise RuntimeError(f'Refusing to follow or remove symlink: {victim}')
    if not victim.exists():
        print(f'[Cinter cleanup-builds] Nothing to remove: {victim}', flush=True)
        return 0
    if not victim.is_dir() or victim.resolve() == external.resolve():
        raise RuntimeError(f'Unsafe in-repository Builds path: {victim}')
    tracked = subprocess.run(['git', '-C', str(root), 'ls-files', '--', 'Builds'],
                             capture_output=True, text=True, check=True)
    if tracked.stdout.strip():
        raise RuntimeError('Refusing to delete Git-tracked content inside Cinter/Builds')
    print(f'[Cinter cleanup-builds] Obsolete directory: {victim}', flush=True)
    print(f'[Cinter cleanup-builds] External build destination (untouched): {external}', flush=True)
    if args.migrate:
        # Refuse ambiguous merges before moving anything. Never overwrite newer
        # external output, and never silently discard distinct generated files.
        files = sorted(victim.rglob('*'))
        if any(path.is_symlink() for path in files):
            raise RuntimeError('Cannot automatically migrate Cinter/Builds containing symlinks; inspect manually')
        conflicts = []
        for source in files:
            if not source.is_file() or _macos_metadata(source):
                continue
            destination = external / source.relative_to(victim)
            if destination.exists() and (not destination.is_file() or
                                         not filecmp.cmp(source, destination, shallow=False)):
                conflicts.append(str(destination))
        if conflicts:
            raise RuntimeError('Refusing to overwrite distinct external build artifacts: ' +
                               ', '.join(conflicts[:10]))
        if args.dry_run:
            print(f'[Cinter cleanup-builds] DRY RUN: would migrate {len(files)} entries to {external}', flush=True)
            return 0
        count = 0
        ignored = 0
        for source in files:
            if not source.is_file():
                continue
            if _macos_metadata(source):
                source.unlink()
                ignored += 1
                continue
            destination = external / source.relative_to(victim)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                source.unlink()  # Verified identical above.
            else:
                shutil.move(str(source), str(destination))
            count += 1
        # Only empty directories remain, including the root itself.
        for directory in sorted((path for path in victim.rglob('*') if path.is_dir()),
                                key=lambda path: len(path.parts), reverse=True):
            directory.rmdir()
        victim.rmdir()
        print(f'[Cinter cleanup-builds] MIGRATED {count} files to {external}; discarded {ignored} Finder metadata files; removed obsolete {victim}', flush=True)
        return 0
    if args.dry_run:
        print('[Cinter cleanup-builds] DRY RUN: would delete obsolete Cinter/Builds', flush=True)
        return 0
    shutil.rmtree(victim)
    print(f'[Cinter cleanup-builds] DELETED: {victim}', flush=True)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f'[Cinter cleanup-builds] ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
