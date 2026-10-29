#!/usr/bin/env python3
"""Publish Steamac's pinned, patched host dependency sources as developer-owned GitHub repos.

Needs git, gh (authenticated), and network. Does not touch the build worktree,
existing build outputs, CinterForgeOS Xcode project, or persistent SteamOS disk.

Usage:
  python3 scripts/create-patched-forks.py                 # create/clone/apply/commit/push
  python3 scripts/create-patched-forks.py --fork-only     # create GitHub repos only, no clone/patch
  python3 scripts/create-patched-forks.py --no-push       # local preparation only
  python3 scripts/create-patched-forks.py --only libkrun
  python3 scripts/create-patched-forks.py --owner ORG
  python3 scripts/create-patched-forks.py --fork-only --dry-run --owner CinterForge
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def run(*args: str, cwd: Path | None = None, capture: bool = False, env=None) -> str:
    cmd = list(map(str, args))
    print('+', ' '.join(cmd), flush=True)
    p = subprocess.run(cmd, cwd=cwd, env=env, text=True,
                       stdout=subprocess.PIPE if capture else None,
                       stderr=subprocess.PIPE if capture else None)
    if p.returncode:
        if capture:
            print(p.stdout, file=sys.stderr)
            print(p.stderr, file=sys.stderr)
        raise RuntimeError(f'Command failed ({p.returncode}): {" ".join(cmd)}')
    return (p.stdout or '').strip()


def setting(path: Path, key: str) -> str:
    match = re.search(rf'^{re.escape(key)}=(\S+)\s*$', path.read_text(), re.M)
    if not match:
        raise RuntimeError(f'Missing {key} in {path}')
    return match.group(1)


def untouched_no_checkout_clone(checkout: Path, status: str) -> bool:
    """Recognize an initial `git clone --no-checkout` without masking real edits.

    Git reports every tracked file as deleted until the first checkout. This is
    not a dirty user worktree. Only accept it before ANY HEAD movement and when
    there are no added or modified/untracked files on disk.
    """
    if not status:
        return False
    if any(line[:2] not in (' D', 'D ') for line in status.splitlines()):
        return False
    last_head_action = run('git', 'reflog', '-1', '--format=%gs', 'HEAD',
                           cwd=checkout, capture=True)
    if not last_head_action.startswith('clone: from '):
        return False
    # A no-checkout clone may leave the index empty; HEAD still names the files.
    tracked = run('git', 'ls-tree', '-r', '--name-only', 'HEAD',
                  cwd=checkout, capture=True)
    paths = tracked.splitlines()
    return bool(paths) and all(not (checkout / name).exists() for name in paths)


def specifications():
    host = ROOT / 'host'
    return {
        'libkrun': (setting(host/'libkrun/build.sh', 'REPO'), setting(host/'libkrun/build.sh','COMMIT'), [host/'libkrun/patches'], None),
        'virglrenderer': (setting(host/'virglrenderer/build.sh','REPO'), setting(host/'virglrenderer/build.sh','COMMIT'), [host/'virglrenderer/patches'], 'virgl'),
        'moltenvk': (setting(host/'moltenvk/build.sh','MVK_REPO'), setting(host/'moltenvk/build.sh','MVK_COMMIT'), [host/'moltenvk/patches/moltenvk'], None),
        'spirv-cross': (setting(host/'moltenvk/build.sh','SPVC_REPO'), setting(host/'moltenvk/build.sh','SPVC_COMMIT'), [host/'moltenvk/patches/spirv-cross'], None),
        'kosmickrisp-mesa': (setting(host/'kosmickrisp/build.sh','MESA_REPO'), setting(host/'kosmickrisp/build.sh','MESA_COMMIT'), [host/'kosmickrisp/patches'], None),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--owner', help='GitHub account/org for the forks (default: authenticated user)')
    ap.add_argument('--checkout-dir', type=Path, default=Path.home()/'Source')
    ap.add_argument('--only', choices=list(specifications()))
    ap.add_argument('--no-push', action='store_true', help='Prepare locally without creating repositories or pushing')
    ap.add_argument('--fork-only', action='store_true', help='Create GitHub forks/repositories without cloning or applying patches')
    ap.add_argument('--dry-run', action='store_true', help='Display planned operations without changing local files or GitHub')
    ap.add_argument('--prepare-only', action='store_true', help='Clone/apply patches locally, without pushing (same as --no-push)')
    args = ap.parse_args()
    if args.fork_only and (args.no_push or args.prepare_only):
        ap.error('--fork-only cannot be combined with --no-push/--prepare-only')
    args.no_push = args.no_push or args.prepare_only
    owner = args.owner or ('<authenticated-GitHub-user>' if args.dry_run else run('gh','api','user','--jq','.login',capture=True))
    specs = specifications()
    if args.only:
        specs = {args.only: specs[args.only]}
    if not args.dry_run:
        args.checkout_dir.mkdir(parents=True, exist_ok=True)
    else:
        print('DRY RUN: no commands executed; no directories, repositories, branches, or files modified.')

    for name, (upstream, base, patch_dirs, special) in specs.items():
        patches = sorted(p for d in patch_dirs for p in d.glob('*.patch'))
        if not patches:
            raise RuntimeError(f'No patches found for {name}')
        digest = hashlib.sha256((base+'\n').encode()+b''.join(p.read_bytes() for p in patches)).hexdigest()[:12]
        branch = f'cinterforge/patched-{digest}'
        repo = f'{owner}/cinterforge-{name}'
        checkout = args.checkout_dir / f'cinterforge-{name}'
        print(f'\n===== {name}: {len(patches)} patches, {branch} =====', flush=True)
        if args.dry_run:
            kind = 'GitHub fork' if upstream.startswith('https://github.com/') else 'GitHub mirror repository'
            print(f'UPSTREAM: {upstream}')
            print(f'PINNED COMMIT: {base}')
            print(f'REPOSITORY: {repo} ({kind})')
            print(f'LOCAL CHECKOUT: {checkout}')
            print(f'PATCHES: {len(patches)}')
            for patch in patches:
                print(f'  {patch.relative_to(ROOT)}')
            if args.no_push:
                print('PLAN: no GitHub creation or push (local-only mode).')
            else:
                print(f'PLAN: reuse {repo} if it exists; otherwise create {kind}.')
            if args.fork_only:
                print('PLAN: no clone, checkout, patch application, commit, or push (--fork-only).')
            else:
                print('PLAN: clone upstream if missing, or reuse the existing clean checkout.')
                print(f'PLAN: fetch pinned revision {base}, create/reuse branch {branch}.')
                if special == 'virgl':
                    print('PLAN: reproduce pinned upstream virglrenderer merge.')
                print('PLAN: apply patches and commit only if branch is not already prepared.')
                if not args.no_push:
                    print(f'PLAN: push {branch} to {repo}.')
            continue
        if not args.no_push:
            exists = subprocess.run(['gh','repo','view',repo,'--json','name'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode == 0
            if not exists:
                if upstream.startswith('https://github.com/'):
                    upstream_slug = upstream.removeprefix('https://github.com/').removesuffix('.git')
                    # Use GitHub's actual fork relationship for GitHub-hosted projects.
                    actual = run('gh','api','user','--jq','.login',capture=True)
                    fork_cmd = ['gh','repo','fork',upstream_slug,'--fork-name',f'cinterforge-{name}','--clone=false']
                    if owner.lower() != actual.lower():
                        fork_cmd.extend(['--org',owner])
                    run(*fork_cmd)
                else:
                    # GitLab upstream cannot be forked using GitHub API; create a GitHub mirror repository.
                    run('gh','repo','create',repo,'--private','--description',f'CinterForge patched source based on {upstream}')
        if args.fork_only:
            print(f'REPOSITORY: {repo} (source checkout and patches deferred)')
            continue
        newly_cloned = False
        if not (checkout/'.git').is_dir():
            if checkout.exists() and any(checkout.iterdir()):
                raise RuntimeError(f'Refusing to overwrite non-repository directory {checkout}')
            run('git','clone','--no-checkout','--origin','upstream',upstream,str(checkout))
            newly_cloned = True
        elif 'upstream' not in run('git','remote',cwd=checkout,capture=True).splitlines():
            run('git','remote','add','upstream',upstream,cwd=checkout)
        elif run('git','remote','get-url','upstream',cwd=checkout,capture=True) != upstream:
            raise RuntimeError(f'Existing upstream remote differs in {checkout}; inspect manually')
        status = run('git','status','--porcelain','--untracked-files=all',cwd=checkout,capture=True)
        if status and not (newly_cloned or untouched_no_checkout_clone(checkout, status)):
            raise RuntimeError(f'Checkout has uncommitted changes; refusing to overwrite: {checkout}')
        if status:
            print('Initial --no-checkout clone detected; tracked files are not yet checked out.', flush=True)
        if not args.no_push:
            remotes = run('git','remote',cwd=checkout,capture=True).splitlines()
            url = f'https://github.com/{repo}.git'
            if 'origin' not in remotes:
                run('git','remote','add','origin',url,cwd=checkout)
            elif run('git','remote','get-url','origin',cwd=checkout,capture=True) != url:
                raise RuntimeError(f'Existing origin differs in {checkout}; inspect manually')

        branches = run('git','branch','--list',branch,cwd=checkout,capture=True)
        if branches:
            run('git','checkout',branch,cwd=checkout)
            print('Existing prepared branch reused; no patches reapplied.')
        else:
            run('git','fetch','upstream',base,cwd=checkout)
            run('git','checkout','-b',branch,base,cwd=checkout)
            if special == 'virgl':
                build = ROOT/'host/virglrenderer/build.sh'
                upstream_commit = setting(build,'UPSTREAM_COMMIT')
                run('git','fetch',setting(build,'UPSTREAM_REPO'),upstream_commit,cwd=checkout)
                merge = subprocess.run(['git','merge','--no-ff','--no-edit',upstream_commit],cwd=checkout)
                if merge.returncode:
                    run(sys.executable,ROOT/'host/virglrenderer/merge-resolve.py',cwd=checkout)
                    run('git','-c','user.name=CinterForge','-c','user.email=build@cinterforge.local','commit','--no-edit',cwd=checkout)
            for patch in patches:
                run('git','apply','--whitespace=nowarn',patch,cwd=checkout)
            run('git','add','-A',cwd=checkout)
            staged = run('git','diff','--cached','--name-only',cwd=checkout,capture=True)
            if not staged:
                raise RuntimeError(f'No modifications after applying {name} patches')
            if staged:
                run('git','-c','user.name=CinterForge','-c','user.email=build@cinterforge.local',
                    'commit','-m',f'CinterForge: apply {len(patches)} pinned patches to {base[:12]}',cwd=checkout)
        if not args.no_push:
            run('git','push','--set-upstream','origin',branch,cwd=checkout)
        print(f'READY: {checkout} ({branch})' + (f' -> {repo}' if not args.no_push else ' (local only)'))

if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError) as exc:
        sys.exit(f'ERROR: {exc}')
