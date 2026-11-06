"""Read-only historical contributor statistics for Creative Suite source inspection.

Count lines from non-merge commits reachable through all Git refs. Results are
cached across applications and extended only when refs move forward. Merge
identities *after* Git mailmap normalization so one person with several emails
has a single row. This never changes a source repository.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import unicodedata
from collections import defaultdict

FIELDS = ('rank', 'contributor', 'email', 'lines_changed', 'lines_added',
          'lines_deleted', 'commits', 'emails')
CACHE_VERSION = 4


def _date(timestamp):
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat() if timestamp is not None else None


def _git(checkout, *args, timeout=60, check=True):
    return subprocess.run(['git', '-C', str(checkout), *args], capture_output=True,
                          timeout=timeout, check=check)


def _refs(checkout, head):
    """Include HEAD because `git log --all` also follows detached HEAD."""
    result = _git(checkout, 'show-ref', check=False)
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr.decode(errors='replace'))
    refs = {}
    for line in result.stdout.decode('ascii', errors='replace').splitlines():
        sha, ref = line.split(' ', 1)
        refs[ref] = sha
    refs['HEAD'] = head
    return refs


def _mailmap_fingerprint(checkout):
    # Mailmap may be supplied via the working tree, Git's info directory, or
    # config. Changes must invalidate identity and contribution caches.
    digest = hashlib.sha256()
    for path in (checkout / '.mailmap',):
        digest.update(path.read_bytes() if path.is_file() else b'')
        digest.update(b'\0')
    info_path = _git(checkout, 'rev-parse', '--git-path', 'info/mailmap').stdout.decode().strip()
    if info_path:
        info = Path(info_path)
        if not info.is_absolute():
            info = checkout / info
        digest.update(info.read_bytes() if info.is_file() else b'')
    digest.update(b'\0')
    config = _git(checkout, 'config', '--get-regexp', r'^mailmap\.', check=False)
    digest.update(config.stdout)
    return digest.hexdigest()


def _fingerprint(refs, mailmap):
    return hashlib.sha256(json.dumps([CACHE_VERSION, sorted(refs.items()), mailmap],
                                     separators=(',', ':')).encode()).hexdigest()


def _fast_forward(checkout, old_refs, new_refs):
    """Incremental history is safe only if no previously reachable ref disappears.

    A moved ref must be a fast-forward. Otherwise a force push, deleted branch,
    or altered tag could remove historical commits and their line totals.
    """
    if not isinstance(old_refs, dict) or not old_refs:
        return False
    for ref, old_sha in old_refs.items():
        new_sha = new_refs.get(ref)
        if not new_sha:
            return False
        if new_sha == old_sha:
            continue
        if _git(checkout, 'merge-base', '--is-ancestor', old_sha, new_sha, check=False).returncode != 0:
            return False
    return True


def _scan(checkout, old_tips=()):
    """Stream numstat once; exclude cached ancestors when refs fast-forward."""
    cmd = ['git', '-C', str(checkout), 'log', '--all', '--no-merges',
           '--use-mailmap', '--numstat', '--format=%x1e%aN%x1f%aE%x1f%at']
    if old_tips:
        cmd += ['--not', *sorted(set(old_tips))]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    counts = defaultdict(lambda: [0, 0, 0])
    first = last = None
    current = None
    commits = 0
    start = time.monotonic()
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.decode('utf-8', errors='replace').rstrip('\r\n')
        if '\x1e' in line:
            fields = line.split('\x1e', 1)[1].split('\x1f')
            current = (fields[0].strip(), fields[1].strip()) if len(fields) >= 2 else None
            if len(fields) >= 3 and fields[2].strip().isdigit():
                stamp = int(fields[2].strip())
                first = stamp if first is None else min(first, stamp)
                last = stamp if last is None else max(last, stamp)
            if current:
                counts[current][2] += 1
                commits += 1
                if commits % 10000 == 0:
                    print(f'[contributors] {checkout.name}: {commits:,} new commits '
                          f'({time.monotonic()-start:.0f}s)', flush=True)
        elif current and '\t' in line:
            parts = line.split('\t', 2)
            if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
                counts[current][0] += int(parts[0])
                counts[current][1] += int(parts[1])
    proc.stdout.close()
    stderr = proc.stderr.read().decode('utf-8', errors='replace') if proc.stderr else ''
    if proc.stderr:
        proc.stderr.close()
    if proc.wait() != 0:
        raise RuntimeError(stderr.strip() or 'git log failed')
    return counts, commits, first, last


def _load_cache(cache_file):
    try:
        return json.loads(cache_file.read_text(encoding='utf-8')) if cache_file.is_file() else None
    except (OSError, ValueError, TypeError):
        return None


def _write_cache(cache_file, data):
    temp = cache_file.with_name(f'{cache_file.name}.{os.getpid()}.tmp')
    try:
        temp.write_text(json.dumps(data, separators=(',', ':')), encoding='utf-8')
        os.replace(temp, cache_file)
    finally:
        temp.unlink(missing_ok=True)


def _normalize_name(value):
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


def _consolidate(totals):
    """Collapse exact normalized full names, or emails shared across aliases.

    Different people can have identical names: add explicit .mailmap entries if
    that occurs. Do not merge by given name, partial match, or email domain.
    """
    identities = list(totals)
    parent = list(range(len(identities)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def connect(a, b):
        parent[root(b)] = root(a)

    names = {}
    emails = {}
    for index, (name, email) in enumerate(identities):
        normalized_name = _normalize_name(name)
        normalized_email = email.strip().casefold()
        if normalized_name and normalized_name not in {'unknown', '(unknown)', 'n/a'}:
            if normalized_name in names:
                connect(names[normalized_name], index)
            else:
                names[normalized_name] = index
        # A shared, non-placeholder address is strong alias evidence.
        if normalized_email and normalized_email not in {'unknown', 'noreply@github.com', 'none', '(none)'}:
            if normalized_email in emails:
                connect(emails[normalized_email], index)
            else:
                emails[normalized_email] = index

    grouped = defaultdict(list)
    for index, identity in enumerate(identities):
        grouped[root(index)].append(identity)
    results = []
    for members in grouped.values():
        counts = [sum(totals[item][i] for item in members) for i in range(3)]
        canonical = sorted(members, key=lambda pair: (-totals[pair][2],
                           -(totals[pair][0] + totals[pair][1]), pair[0].casefold(), pair[1].casefold()))[0]
        aliases = sorted({item[1] for item in members if item[1]}, key=str.casefold)
        names_list = sorted({item[0] for item in members if item[0]}, key=str.casefold)
        results.append({'name': ' '.join(canonical[0].split()), 'email': canonical[1], 'emails': aliases,
                        'names': names_list, 'added': counts[0], 'deleted': counts[1],
                        'commits': counts[2]})
    return sorted(results, key=lambda row: (-(row['added'] + row['deleted']),
                  -row['commits'], row['name'].casefold(), row['email'].casefold()))


def collect(checkouts: list[Path], output: Path) -> dict:
    """Write one Contributors.csv per app; source repositories remain untouched."""
    output.mkdir(parents=True, exist_ok=True)
    # Multiple apps may point at the same checkout. A single cache saves repeat
    # scans. Cache stays in Builds/Inspection, never in a source checkout.
    cache_dir = output.parent / 'ContributorsCache'
    cache_dir.mkdir(parents=True, exist_ok=True)
    totals = defaultdict(lambda: [0, 0, 0])
    repositories = []
    for checkout in dict.fromkeys(Path(path).resolve() for path in checkouts):
        try:
            head = _git(checkout, 'rev-parse', '--show-toplevel', timeout=30).stdout.decode().strip()
            if Path(head).resolve() != checkout:
                repositories.append({'path': str(checkout), 'status': 'not_repository_root'})
                continue
            commit = _git(checkout, 'rev-parse', 'HEAD', timeout=30).stdout.decode().strip()
            refs = _refs(checkout, commit)
            mailmap = _mailmap_fingerprint(checkout)
            fingerprint = _fingerprint(refs, mailmap)
            cache_name = hashlib.sha256(str(checkout).encode()).hexdigest() + '.json'
            cache_file = cache_dir / cache_name
            cached = _load_cache(cache_file)
            if cached is None:
                # Reuse an app-local legacy cache when its exact fingerprint
                # matches, rather than forcing an unnecessary initial rescan.
                cached = _load_cache(output / 'ContributorsCache' / cache_name)
            status = 'inspected'
            if cached and cached.get('fingerprint') == fingerprint:
                status = 'cached'
                data = cached
            else:
                incremental = bool(cached and cached.get('cache_version') == CACHE_VERSION
                                   and cached.get('mailmap') == mailmap
                                   and cached.get('first_timestamp') is not None
                                   and cached.get('last_timestamp') is not None
                                   and _fast_forward(checkout, cached.get('refs'), refs))
                if incremental:
                    print(f'[contributors] {checkout.name}: scanning new history only...', flush=True)
                    old_tips = set(cached['refs'].values())
                    local, new_commits, first, last = _scan(checkout, old_tips)
                    previous = {(item['name'], item['email']): list(item['counts'])
                                for item in cached['authors']}
                    for identity, counts in local.items():
                        before = previous.setdefault(identity, [0, 0, 0])
                        for index, amount in enumerate(counts):
                            before[index] += amount
                    first = min(cached['first_timestamp'], first) if first is not None else cached['first_timestamp']
                    last = max(cached['last_timestamp'], last) if last is not None else cached['last_timestamp']
                    count = cached['commits'] + new_commits
                    new_total = int(_git(checkout, 'rev-list', '--count', '--all', '--not',
                                         *sorted(old_tips), timeout=120).stdout.decode().strip())
                    all_commits = cached['statistics']['total_commits'] + new_total
                    status = 'incremental'
                else:
                    print(f'[contributors] {checkout.name}: scanning Git history...', flush=True)
                    previous, count, first, last = _scan(checkout)
                    all_commits = int(_git(checkout, 'rev-list', '--count', '--all', timeout=120).stdout.decode().strip())
                local_stats = {
                    'total_commits': all_commits, 'non_merge_commits': count,
                    'merge_commits': all_commits - count,
                    'first_commit_date': _date(first), 'last_commit_date': _date(last),
                    'repository_age_days': (last - first) // 86400 if first is not None and last is not None else None,
                    'lines_added': sum(c[0] for c in previous.values()),
                    'lines_deleted': sum(c[1] for c in previous.values()),
                    'lines_changed': sum(c[0] + c[1] for c in previous.values()),
                    'contributors': len(previous),
                }
                data = {'cache_version': CACHE_VERSION, 'fingerprint': fingerprint,
                        'refs': refs, 'mailmap': mailmap,
                        'first_timestamp': first, 'last_timestamp': last,
                        'commits': count, 'statistics': local_stats,
                        'authors': [{'name': key[0], 'email': key[1], 'counts': counts}
                                    for key, counts in previous.items()]}
                _write_cache(cache_file, data)
                print(f'[contributors] {checkout.name}: {status} ({count:,} commits)', flush=True)
            if status == 'cached' and not cache_file.is_file():
                _write_cache(cache_file, data)
            for item in data['authors']:
                identity = (item['name'], item['email'])
                for index, amount in enumerate(item['counts']):
                    totals[identity][index] += amount
            repositories.append({'path': str(checkout), 'commit': commit,
                                 'status': status, 'commits': data['commits'],
                                 'statistics': data['statistics']})
            if status == 'cached':
                print(f'[contributors] {checkout.name}: cached ({data["commits"]:,} commits)', flush=True)
        except (OSError, subprocess.SubprocessError, RuntimeError, KeyError, ValueError, TypeError) as exc:
            repositories.append({'path': str(checkout), 'status': 'error', 'error': str(exc)})

    ranked = _consolidate(totals)
    with (output / 'Contributors.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        for rank, item in enumerate(ranked, 1):
            writer.writerow(dict(rank=rank, contributor=item['name'], email=item['email'],
                                 lines_changed=item['added'] + item['deleted'],
                                 lines_added=item['added'], lines_deleted=item['deleted'],
                                 commits=item['commits'], emails='; '.join(item['emails'])))
    stats_rows = [dict(repository=r['path'], **r['statistics']) for r in repositories if r.get('statistics')]
    with (output / 'RepositoryStatistics.csv').open('w', encoding='utf-8', newline='') as stream:
        columns = ('repository', 'total_commits', 'non_merge_commits', 'merge_commits',
                   'first_commit_date', 'last_commit_date', 'repository_age_days',
                   'contributors', 'lines_added', 'lines_deleted', 'lines_changed')
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(stats_rows)
    (output / 'RepositoryStatistics.json').write_text(json.dumps({
        'measurement': 'all locally reachable Git refs; repository age measured between earliest and latest non-merge author dates',
        'repositories': stats_rows,
        'limitations': 'History may be shallow or incomplete; author dates may differ from commit dates; Git cannot measure non-code contributions.'
    }, indent=2) + '\n', encoding='utf-8')
    result = {'metric': 'historical_lines_added_plus_deleted',
              'scope': 'all locally reachable refs, non-merge commits; Git mailmap applied',
              'notes': 'Contributor aliases merged by normalized full name and shared non-placeholder email. '
                       'Different people with identical names require explicit .mailmap disambiguation. '
                       'Binary files, renames, formatting and squashes can distort line totals.',
              'repositories': repositories, 'statistics': stats_rows,
              'contributors': len(ranked),
              'identities': [dict(contributor=item['name'], email=item['email'],
                                  emails=item['emails'], names=item['names'],
                                  commits=item['commits'], lines_added=item['added'],
                                  lines_deleted=item['deleted']) for item in ranked],
              'status': 'inspected' if any(r['status'] in ('inspected', 'cached', 'incremental')
                                           for r in repositories) else 'git_history_unavailable'}
    (output / 'Contributors.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result
