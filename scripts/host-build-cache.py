#!/usr/bin/env python3
"""Conservative persistent cache for expensive CFOS host driver builds.

check <stamp> <output> <source-repo> <build-script> [other inputs...]
record <stamp> <output> <source-repo> <build-script> [other inputs...]

A cache hit needs an existing nonempty output and identical source revision,
tracked working-tree edits, build recipe, and Xcode version. Dirty tracked files
are content-hashed, so a changed file is not hidden by a stable HEAD commit.
"""
import hashlib
import os
from pathlib import Path
import subprocess
import sys


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.DEVNULL)


def digest(repo, inputs):
    h = hashlib.sha256()
    h.update(b'cfos-host-build-cache-v1\0')
    h.update(git(repo, 'rev-parse', 'HEAD'))
    # Include the names and bytes of every changed/deleted tracked source file.
    changed = git(repo, 'ls-files', '-m', '-d', '-z').split(b'\0')
    for raw in sorted(set(filter(None, changed))):
        relative = os.fsdecode(raw)
        path = repo / relative
        h.update(raw + b'\0')
        h.update(hashlib.sha256(path.read_bytes()).digest() if path.is_file() else b'DELETED')
    # Untracked source changes must not be silently skipped. Exclude generated
    # ignored files through Git's standard exclude rules.
    untracked = git(repo, 'ls-files', '--others', '--exclude-standard', '-z').split(b'\0')
    for raw in sorted(filter(None, untracked)):
        path = repo / os.fsdecode(raw)
        if path.is_file():
            h.update(b'UNTRACKED\0' + raw + b'\0' + hashlib.sha256(path.read_bytes()).digest())
    for name in inputs:
        path = Path(name)
        if not path.is_file():
            raise RuntimeError(f'Missing build input: {path}')
        h.update(str(path.resolve()).encode() + b'\0')
        h.update(hashlib.sha256(path.read_bytes()).digest())
    h.update(subprocess.check_output(['xcodebuild', '-version']))
    h.update(os.environ.get('MACOSX_DEPLOYMENT_TARGET', '').encode())
    return h.hexdigest()


def main():
    if len(sys.argv) < 6 or sys.argv[1] not in ('check', 'record'):
        raise SystemExit('usage: host-build-cache.py check|record STAMP OUTPUT SOURCE INPUT...')
    _, mode, stamp_arg, output_arg, repo_arg, *inputs = sys.argv
    stamp, output, repo = Path(stamp_arg), Path(output_arg), Path(repo_arg)
    fingerprint = digest(repo, inputs)
    if mode == 'check':
        return 0 if output.is_file() and output.stat().st_size > 0 and stamp.is_file() and stamp.read_text().strip() == fingerprint else 1
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError(f'Cannot cache missing output: {output}')
    stamp.parent.mkdir(parents=True, exist_ok=True)
    temp = stamp.with_suffix(stamp.suffix + f'.tmp.{os.getpid()}')
    temp.write_text(fingerprint + '\n')
    temp.replace(stamp)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, subprocess.CalledProcessError, RuntimeError) as exc:
        print(f'CFOS host build cache: {exc}', file=sys.stderr)
        raise SystemExit(2)
