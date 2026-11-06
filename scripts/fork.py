#!/usr/bin/env python3
"""Fork an upstream source repository or clone the existing CinterForge fork.

Repository definitions are owned by CFOS in scripts/source_repositories.csv.
Local checkouts live beside CinterForgeOS; Setup only validates, never clones.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

ORGANIZATION = "CinterForge"
DEVELOPMENT_BRANCH = "cinterforge/cinterforge"
ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "scripts" / "source_repositories.csv"
SOURCE_ROOT = ROOT.parent  # One common parent for all maintained source repositories


# Canonical CinterForge fork names. The CSV identifies upstream projects;
# its upstream repository basename is never the name of a maintained fork.
MAINTAINED_FORKS = {
    "cfimage": "CinterForgeImage",
    "cfpaint": "CinterForgePaint",
    "cfvector": "CinterForgeVector",
    "cfdocument": "CinterForgeDocument",
    "cf3d": "CinterForge3D",
    "cfvideo": "CinterForgeVideo",
    "cfaudio": "CinterForgeAudio",
    "cfphoto": "CinterForgePhoto",
    "cfcode": "CinterForgeCode",
    "cfvoxel": "CinterForgeVoxel",
    "cfconverter": "CinterForgeConverter",
    "leveldb": "cinterforge-leveldb",
    "fex": "cinterforge-FEX",
    "cinterforgeos": "CinterForgeOS",
}


def destination_for(name: str, upstream_repo: str) -> str:
    """Resolve the maintained fork name from the app ID, not the upstream URL."""
    return MAINTAINED_FORKS.get(name.casefold().replace("-", "").replace(" ", ""), fork_destination(upstream_repo))


def fork_destination(repo_name: str) -> str:
    # SteamTinkerLaunch is deliberately branded differently from upstream.
    return "cinterforge-steamtinkerlaunch" if repo_name == "steamtinkerlaunch" else repo_name


def create_github_fork(upstream: str, repo_name: str, app_name: str) -> None:
    """Create upstream fork and rename only when the registry requests a distinct name."""
    command("gh", "repo", "fork", upstream, "--org", ORGANIZATION, "--clone=false")
    destination = destination_for(app_name, repo_name)
    if destination != repo_name:
        command("gh", "api", "-X", "PATCH", f"repos/{ORGANIZATION}/{repo_name}",
                "-f", f"name={destination}")


def load_repositories() -> dict[str, tuple[str, str, str, str]]:
    if not CSV.is_file():
        raise RuntimeError(f"CFOS source manifest missing: {CSV}")
    result: dict[str, tuple[str, str, str, str]] = {}
    with CSV.open(newline="", encoding="utf-8-sig") as fp:
        for row in csv.DictReader(fp):
            name = (row.get("Name") or "").strip()
            remote = (row.get("Git Repo URL") or "").strip()
            if not name or not remote:
                continue
            parsed = urlparse(remote)
            parts = parsed.path.removesuffix(".git").strip("/").split("/")
            if parsed.scheme != "https" or not parsed.hostname or not parts or not parts[-1]:
                raise RuntimeError(f"Invalid Git Repo URL for {name}: {remote}")
            repo = parts[-1]
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", repo) or repo in (".", ".."):
                raise RuntimeError(f"Invalid repository folder name for {name}: {repo}")
            key = name.casefold().replace(" ", "").replace("-", "")
            if key in result:
                raise RuntimeError(f"Duplicate CFOS source repository: {name}")
            # The CSV's Git Repo URL is the authoritative upstream. Some projects
            # also publish an official GitHub mirror suitable for GitHub forking.
            # Never infer a GitHub mirror from a non-GitHub URL.
            github_mirror = (row.get("GitHub Fork Source URL") or "").strip()
            if github_mirror:
                mirror_repo = github_path(github_mirror).rsplit("/", 1)[-1]
                if mirror_repo.casefold() != repo.casefold():
                    raise RuntimeError(
                        f"GitHub fork source for {name} has different repo name: {github_mirror}"
                    )
            acquisition = (row.get("Source Acquisition") or "source").strip().casefold()
            if acquisition not in ("source", "prebuilt"):
                raise RuntimeError(f"Invalid Source Acquisition for {name}: {acquisition}")
            result[key] = (remote, repo, github_mirror, acquisition)
    return result



def command(*argv: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(argv, text=True, capture_output=True, check=False, env=env)
    if result.returncode:
        raise RuntimeError(f"{' '.join(argv)} failed: {(result.stderr or result.stdout).strip()}")
    return result



def checkout_development_branch(target: Path) -> None:
    """Create/check out the CinterForge development branch in a fresh clone.

    When a fork already publishes origin/cinterforge/cinterforge, track it.
    Some upstream forks have their default branch named `cinterforge`; Git
    cannot keep refs/heads/cinterforge alongside refs/heads/cinterforge/cinterforge.
    Rename that fresh clone's local branch rather than losing its commit.
    Never change an existing checkout: this helper runs only after cloning.
    """
    remote_branch = f"refs/remotes/origin/{DEVELOPMENT_BRANCH}"
    has_remote = subprocess.run(
        ["git", "-C", str(target), "show-ref", "--verify", "--quiet", remote_branch],
        check=False,
    ).returncode == 0
    local_branch = command("git", "-C", str(target), "branch", "--show-current").stdout.strip()
    if local_branch == DEVELOPMENT_BRANCH:
        # A fork can already designate the requested branch as its default.
        pass
    elif local_branch == "cinterforge":
        # A local `cinterforge` ref blocks creation of `cinterforge/...`.
        # This is a brand-new clone, so renaming cannot affect user edits.
        command("git", "-C", str(target), "branch", "-m", DEVELOPMENT_BRANCH)
        if not has_remote:
            # Git preserves the renamed branch's upstream configuration. Do not
            # accidentally push this new branch into origin/cinterforge.
            command("git", "-C", str(target), "branch", "--unset-upstream", DEVELOPMENT_BRANCH)
    elif has_remote:
        command("git", "-C", str(target), "switch", "--track", "-c", DEVELOPMENT_BRANCH,
                f"origin/{DEVELOPMENT_BRANCH}")
    else:
        command("git", "-C", str(target), "switch", "-c", DEVELOPMENT_BRANCH)
    if has_remote:
        command("git", "-C", str(target), "branch", "--set-upstream-to",
                f"origin/{DEVELOPMENT_BRANCH}", DEVELOPMENT_BRANCH)
    branch = command("git", "-C", str(target), "branch", "--show-current").stdout.strip()
    if branch != DEVELOPMENT_BRANCH:
        raise RuntimeError(f"Expected branch {DEVELOPMENT_BRANCH!r}, got {branch!r}: {target}")
    print(f"[CFOS clone] CHECKED OUT: {target} [{DEVELOPMENT_BRANCH}]", flush=True)


def clone_without_lfs_smudge(source: str, target: Path) -> None:
    """Clone Git and checkout LFS pointer files without using a fork's LFS store.

    GitHub forks can contain Git LFS pointers whose binary objects were not
    copied into the fork's LFS storage. Fetching during checkout causes a 404
    and leaves an incomplete worktree. Asset acquisition is a separate step.
    """
    env = os.environ.copy()
    env["GIT_LFS_SKIP_SMUDGE"] = "1"
    print(f"[CFOS clone] Git LFS checkout downloads skipped (source objects may live upstream): {source}", flush=True)
    command("git", "clone", "--progress", source, str(target), env=env)
    checkout_development_branch(target)


def gh_info(full_name: str) -> dict | None:
    result = subprocess.run(["gh", "api", f"repos/{full_name}"], capture_output=True, text=True)
    if result.returncode == 0:
        return json.loads(result.stdout)
    if "404" in result.stderr:
        return None
    raise RuntimeError(f"Cannot inspect {full_name}: {(result.stderr or result.stdout).strip()}")


def github_path(url: str) -> str:
    parsed = urlparse(url)
    parts = parsed.path.removesuffix(".git").strip("/").split("/")
    if parsed.hostname != "github.com" or len(parts) != 2:
        raise RuntimeError(f"GitHub organization forks require a GitHub upstream: {url}")
    return "/".join(parts)



def external_git_source(url: str, mirror: str) -> bool:
    """A CSV upstream without a declared GitHub mirror needs local source preparation."""
    return not mirror and urlparse(url).hostname != "github.com"



def prepare_external_source(upstream_url: str, repo_name: str, name: str) -> None:
    """Prepare a fresh, pushable source snapshot without moving Git submodules.

    The upstream Git directory must stay in place: .git/modules owns the
    submodule gitdirs and moving/deleting it breaks nested submodules.
    An unborn development branch plus an empty index gives the next commit no parent,
    while retaining the pinned gitlinks. Submodules are fetched during setup.
    """
    target = SOURCE_ROOT / destination_for(name, repo_name)
    if target.exists() or target.is_symlink():
        # Never overwrite or mutate an existing directory. The user explicitly
        # prefers to delete the failed checkout and re-run from scratch.
        print(f"[CFOS fork] SKIP: existing local directory {target} (unchanged); remove it to start again", flush=True)
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["GIT_LFS_SKIP_SMUDGE"] = "1"
    print(f"[CFOS fork] Fetching current source {upstream_url} -> {target}", flush=True)
    result = subprocess.run(
        ["git", "clone", "--depth", "1", "--single-branch", "--no-tags", "--progress",
         upstream_url, str(target)], env=env, check=False,
    )
    if result.returncode:
        raise RuntimeError(f"Source clone failed ({result.returncode}); files preserved at {target}")

    # Keep submodules as pinned gitlinks; setup handles fetching them for builds.
    # There is no reason to download nested repositories for an initial commit.

    # Do not silently turn Git LFS pointer files into supposed source assets.
    lfs = subprocess.run(["git", "lfs", "version"], capture_output=True, text=True)
    if lfs.returncode == 0:
        # Submodules are not checked out here; inspect only the parent.
        root_lfs = command("git", "-C", str(target), "lfs", "ls-files").stdout
        if root_lfs.strip():
            raise RuntimeError(f"Git LFS pointers detected; no snapshot prepared. Checkout preserved at {target}")

    # Preserve the upstream commit tree (including gitlink entries) in the index.
    # Stage it as the first commit on an unborn development branch. No submodules need
    # to be initialized, moved, or flattened. GitLab refs stay local.
    if subprocess.run(["git", "-C", str(target), "show-ref", "--verify", "--quiet", f"refs/heads/{DEVELOPMENT_BRANCH}"]).returncode == 0:
        raise RuntimeError(f"Upstream checkout already has a {DEVELOPMENT_BRANCH} branch; refusing to overwrite it: {target}")
    snapshot_commit = command("git", "-C", str(target), "rev-parse", "HEAD").stdout.strip()
    snapshot_tree = command("git", "-C", str(target), "rev-parse", "HEAD^{tree}").stdout.strip()
    command("git", "-C", str(target), "remote", "rename", "origin", "upstream")
    command("git", "-C", str(target), "remote", "add", "origin", f"https://github.com/{ORGANIZATION}/{destination_for(name, repo_name)}.git")
    command("git", "-C", str(target), "symbolic-ref", "HEAD", f"refs/heads/{DEVELOPMENT_BRANCH}")
    # read-tree keeps 160000-mode gitlinks even if submodule folders are empty.
    # `git add -A` here can drop those gitlinks in an uninitialized clone.
    command("git", "-C", str(target), "read-tree", snapshot_tree)
    staged = command("git", "-C", str(target), "ls-files", "--stage").stdout
    links = sum(line.startswith("160000 ") for line in staged.splitlines())
    upstream_links = command("git", "-C", str(target), "ls-tree", "-r", snapshot_commit).stdout
    expected_links = sum(line.startswith("160000 ") for line in upstream_links.splitlines())
    if links != expected_links:
        raise RuntimeError(f"Expected {expected_links} submodule links, staged {links}; checkout preserved at {target}")
    print(f"[CFOS fork] READY: {target} ({links} original upstream submodule links staged)", flush=True)
    print("[CFOS fork] No commits or pushes made. Submodules remain uninitialized until setup.", flush=True)
    print(f"[CFOS fork] Commit and push: cd {target} && git commit -m 'Initial CinterForge source' && git push -u origin {DEVELOPMENT_BRANCH}", flush=True)


def check_fork_existing(existing: dict, upstream: str, destination_name: str) -> None:
    ancestors = {(existing.get("parent") or {}).get("full_name", "").casefold(),
                 (existing.get("source") or {}).get("full_name", "").casefold()}
    if not existing.get("fork") or upstream.casefold() not in ancestors:
        raise RuntimeError(f"{destination_name} exists but is not a fork of {upstream}; refusing to overwrite")



def validate_local_sources(repositories: dict[str, tuple[str, str, str, str]], workspace: Path) -> list[str]:
    """Read-only check for all required source checkouts, including broken paths.

    No GitHub/network access, checkout, branch modification or implicit clone.
    Directory existence alone is not proof of a usable source checkout.
    """
    missing: list[str] = []
    visited: set[str] = set()
    checked = 0
    for name, (_, upstream_repo, _, acquisition) in repositories.items():
        if acquisition != "source":
            print(f"[CFOS clone all] SKIP {name}: acquisition={acquisition}", flush=True)
            continue
        target = workspace / destination_for(name, upstream_repo)
        if str(target).casefold() in visited:
            continue
        visited.add(str(target).casefold())
        if target.is_symlink() or not target.is_dir() or not (target / '.git').exists():
            reason = 'missing' if not target.exists() and not target.is_symlink() else 'not a Git checkout'
            print(f"[CFOS clone all] MISSING {name}: {target} ({reason})", flush=True)
            missing.append(str(target))
            continue
        probe = subprocess.run(['git', '-C', str(target), 'rev-parse', '--is-inside-work-tree'],
                               capture_output=True, text=True, check=False)
        if probe.returncode or probe.stdout.strip() != 'true':
            print(f"[CFOS clone all] INVALID {name}: {target} (Git worktree unreadable)", flush=True)
            missing.append(str(target))
            continue
        print(f"[CFOS clone all] PRESENT {name}: {target}", flush=True)
        checked += 1
    print(f"[CFOS clone all] DRY RUN SUMMARY: present={checked} missing_or_invalid={len(missing)}", flush=True)
    if missing:
        print('[CFOS clone all] Ubuntu Setup is blocked. Run ./CFOS clone all to obtain missing forks. '
              'Existing broken checkout paths require manual repair; no source was changed.', file=sys.stderr, flush=True)
    return missing


def main() -> int:
    parser = argparse.ArgumentParser(description="CFOS-owned maintained source forks and clones")
    parser.add_argument("action", choices=("fork", "clone"))
    parser.add_argument("repository", nargs="?", default="all", help="CFOS source ID or all")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume-import", action="store_true", help="Deprecated: external sources now prepare a local checkout for manual push")

    args = parser.parse_args()
    if args.resume_import:
        parser.error("--resume-import is obsolete; local source preparation never pushes to GitHub")
    repositories = load_repositories()
    if args.repository.casefold() == 'all' and args.action == 'clone' and args.dry_run:
        return 1 if validate_local_sources(repositories, SOURCE_ROOT) else 0
    if args.repository.casefold() == "all" and args.action == "fork":
        # Inspect the organization first, including in dry-run. The command must
        # never create a duplicate or misidentify an unrelated repository as a fork.
        if not shutil.which("gh"):
            raise RuntimeError("GitHub CLI 'gh' is required; install it and run gh auth login")
        command("gh", "auth", "status")
        created = existing_count = excluded = unsupported = 0
        failures = []
        visited: set[str] = set()
        for name, (url, repo_name, mirror, acquisition) in repositories.items():
            if acquisition != "source":
                print(f"[CFOS fork all] SKIP {name}: acquisition={acquisition}", flush=True)
                excluded += 1
                continue
            fork_source = mirror or url
            try:
                external = external_git_source(url, mirror)
                upstream = None if external else github_path(fork_source)
                destination_name = f"{ORGANIZATION}/{destination_for(name, repo_name)}"
                if not external and github_path(fork_source).casefold() == destination_name.casefold():
                    print(f"[CFOS fork all] SKIP {name}: already CFOS-owned repository", flush=True)
                    existing_count += 1
                    continue
                if destination_name.casefold() in visited:
                    print(f"[CFOS fork all] SKIP {name}: repository already handled ({destination_name})", flush=True)
                    excluded += 1
                    continue
                visited.add(destination_name.casefold())
                if external:
                    target = SOURCE_ROOT / destination_for(name, repo_name)
                    if target.exists() or target.is_symlink():
                        print(f"[CFOS fork all] SKIP {name}: local source already exists {target} (unchanged)", flush=True)
                        existing_count += 1
                    elif args.dry_run:
                        print(f"[CFOS fork all] DRY RUN: prepare source {url} -> {target} (no GitHub push)", flush=True)
                        created += 1
                    else:
                        prepare_external_source(url, repo_name, name)
                        created += 1
                    continue
                existing = gh_info(destination_name)
                if existing is not None:
                    check_fork_existing(existing, upstream, destination_name)
                    print(f"[CFOS fork all] SKIP {name}: already forked https://github.com/{destination_name}", flush=True)
                    existing_count += 1
                    continue
                if gh_info(upstream) is None:
                    raise RuntimeError(f"Upstream GitHub repository unavailable: {upstream}")
                if args.dry_run:
                    print(f"[CFOS fork all] DRY RUN: fork {fork_source} -> https://github.com/{destination_name}", flush=True)
                else:
                    print(f"[CFOS fork all] CREATE {name}: {fork_source} -> https://github.com/{destination_name}", flush=True)
                    create_github_fork(upstream, repo_name, name)
                    print(f"[CFOS fork all] CREATED https://github.com/{destination_name}", flush=True)
                created += 1
            except (RuntimeError, OSError, ValueError, json.JSONDecodeError) as exc:
                print(f"[CFOS fork all] FAILED {name}: {exc}", file=sys.stderr, flush=True)
                failures.append(name)
        print(f"[CFOS fork all] {'DRY RUN ' if args.dry_run else ''}SUMMARY: "
              f"{'would_create' if args.dry_run else 'created'}={created} "
              f"already_forked={existing_count} prebuilt_or_duplicate={excluded} "
              f"unsupported={unsupported} failed={len(failures)}", flush=True)
        return 1 if failures else 0
    if args.repository.casefold() == "all":
        failures = []
        cloned = skipped = excluded = 0
        for name, (url, repo_name, mirror, acquisition) in repositories.items():
            target = SOURCE_ROOT / destination_for(name, repo_name)
            if acquisition != "source":
                print(f"[CFOS clone all] SKIP {name}: acquisition={acquisition}", flush=True)
                excluded += 1
                continue
            # 'Completely missing' means no directory or file at the destination.
            # Never inspect, repair, validate or contact a remote for existing paths.
            if target.exists() or target.is_symlink():
                if target.is_symlink() or not (target / '.git').exists():
                    print(f"[CFOS clone all] FAILED {name}: existing path is not a Git checkout: {target}; "
                          "repair it explicitly; no overwrite performed", file=sys.stderr, flush=True)
                    failures.append(name)
                    continue
                print(f"[CFOS clone all] SKIP {name}: already exists {target}", flush=True)
                skipped += 1
                continue
            # Imported external projects also clone from their CinterForge GitHub
            # copies after `fork all`; GitLab upstream remains authoritative in CSV.
            source = f"https://github.com/{ORGANIZATION}/{destination_for(name, repo_name)}.git"
            try:
                print(f"[CFOS clone all] CLONE {name}: {source} -> {target}", flush=True)
                target.parent.mkdir(parents=True, exist_ok=True)
                clone_without_lfs_smudge(source, target)
                cloned += 1
            except (RuntimeError, OSError) as exc:
                print(f"[CFOS clone all] FAILED {name}: {exc}", file=sys.stderr, flush=True)
                failures.append(name)
        print(f"[CFOS clone all] {'DRY RUN ' if args.dry_run else ''}SUMMARY: "
              f"clone={cloned} existing={skipped} prebuilt={excluded} failed={len(failures)}", flush=True)
        return 1 if failures else 0
    key = args.repository.casefold().replace(" ", "").replace("-", "")
    if key not in repositories:
        raise RuntimeError(f"Unknown CFOS source repository {args.repository!r}; available: {', '.join(sorted(repositories))}")
    url, repo_name, mirror, acquisition = repositories[key]
    if acquisition == "prebuilt":
        print(f"[Cinter {args.action}] {args.repository}: prebuilt components; fork/clone not applicable.", flush=True)
        print(f"[Cinter {args.action}] Use ./Cinter setup {args.repository.replace(chr(32), chr(45)).lower()} <mac|linux|win> for the acquisition policy.", flush=True)
        return 0
    # Clone never modifies a pre-existing target, including a partial checkout.
    # Check this before network access, GitHub verification, or authentication.
    target = SOURCE_ROOT / destination_for(key, repo_name)
    if args.action == "clone" and (target.exists() or target.is_symlink()):
        print(f"[CFOS clone] SKIP {args.repository}: target already exists {target} (unchanged)", flush=True)
        return 0
    fork_source = mirror or url
    external = external_git_source(url, mirror)
    upstream = None if external else github_path(fork_source)
    destination_name = f"{ORGANIZATION}/{destination_for(key, repo_name)}"
    destination_url = f"https://github.com/{destination_name}"
    if args.action == "fork" and external:
        target = SOURCE_ROOT / destination_for(key, repo_name)
        if args.dry_run:
            print(f"[CFOS fork] DRY RUN: prepare current source {url} -> {target} (no GitHub push)", flush=True)
        else:
            prepare_external_source(url, repo_name, key)
        return 0
    if args.dry_run:
        print(f"[Cinter {args.action}] DRY RUN: CSV upstream: {url}", flush=True)
        if mirror:
            print(f"[Cinter {args.action}] DRY RUN: official GitHub fork source: {mirror}", flush=True)
        if args.action == "fork":
            print(f"[CFOS fork] DRY RUN: fork {fork_source} -> {destination_url}", flush=True)
        else:
            print(f"[CFOS clone] DRY RUN: clone {destination_url} -> {SOURCE_ROOT / destination_for(key, repo_name)}; create/checkout {DEVELOPMENT_BRANCH} branch", flush=True)
        return 0
    if not shutil.which("gh"):
        raise RuntimeError("GitHub CLI 'gh' is required; install it and run gh auth login")
    command("gh", "auth", "status")
    first_party = upstream is not None and upstream.casefold() == destination_name.casefold()
    if not external and not first_party and gh_info(upstream) is None:
        raise RuntimeError(f"Upstream GitHub repository unavailable: {upstream}")
    existing = gh_info(destination_name)
    if args.action == "fork":
        if existing is not None:
            if not external:
                check_fork_existing(existing, upstream, destination_name)
            print(f"[CFOS fork] SKIP: existing verified fork: {destination_url}", flush=True)
            return 0
        print(f"[CFOS fork] Creating {destination_url} from {fork_source}", flush=True)
        create_github_fork(upstream, repo_name, name)
        print(f"[CFOS fork] Created {destination_url}", flush=True)
        return 0
    # Clone is intentionally independent of fork; no fork is created here.
    if existing is None:
        raise RuntimeError(f"Repository not found: {destination_name}; run './Cinter fork {args.repository}' first")
    if not external and not first_party:
        check_fork_existing(existing, upstream, destination_name)
    target.parent.mkdir(parents=True, exist_ok=True)
    clone_without_lfs_smudge(f"https://github.com/{destination_name}.git", target)
    print(f"[CFOS clone] Cloned https://github.com/{destination_name} -> {target}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[CFOS source] ERROR: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
