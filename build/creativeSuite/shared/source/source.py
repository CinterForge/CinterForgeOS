"""Generic Git source acquisition for isolated, incremental Creative Suite builds.

Never modifies the maintained source repository. Generated checkouts live only in
versioned Builds directories, and dirty build sources fail instead of being reset.
"""
from __future__ import annotations
from pathlib import Path
import subprocess
from ..build.build_runtime import validate_fork, run_visible

DEVELOPMENT_BRANCH = "cinterforge/cinterforge"


def validate_checkout(checkout: Path, *, expected_origin: str, required_file: str = "waf") -> None:
    validate_fork(checkout, expected_origin=expected_origin, required_file=required_file)


def resolve_revision(checkout: Path, revision: str = "HEAD") -> str:
    if not revision or any(char in revision for char in "\n\r\0"):
        raise ValueError("Invalid Git revision")
    return subprocess.check_output(
        ["git", "rev-parse", "--verify", f"{revision}^{{commit}}"],
        cwd=checkout, text=True,
    ).strip()


def source_revision(checkout: Path) -> str:
    return resolve_revision(checkout)


def prepare_build_checkout(checkout: Path, destination: Path, *, revision: str,
                           required_file: str = "waf",
                           generated_directories: tuple[str, ...] = ("build",),
                           label: str = "source", local_only: bool = False) -> Path:
    """Clone/reuse a local fork into Builds; refuse local edits and retain Waf cache.

    A revision must exist in the maintained checkout. We fetch only from that
    local checkout, never download different source or amend the fork.
    """
    checkout = checkout.resolve(strict=True)
    destination = destination.absolute()
    if destination.resolve() == checkout:
        raise RuntimeError("Build checkout cannot be the maintained source repository")
    commit = resolve_revision(checkout, revision)
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        run_visible(["git", "clone", "--local", str(checkout), str(destination)], label=label)
    if not (destination / ".git").is_dir() or not (destination / required_file).is_file():
        raise RuntimeError(f"Invalid isolated build checkout: {destination}")
    origin = subprocess.check_output(
        ["git", "remote", "get-url", "origin"], cwd=destination, text=True,
    ).strip()
    if Path(origin).resolve() != checkout:
        raise RuntimeError(f"Build checkout has unexpected origin: {origin} (expected {checkout})")
    # Waf's untracked object cache is permitted; other edits are not.
    cmd = ["git", "status", "--porcelain", "--untracked-files=all", "--", "."]
    cmd += [f":(exclude){directory}" for directory in generated_directories]
    dirty = subprocess.check_output(cmd, cwd=destination, text=True).strip()
    if dirty:
        raise RuntimeError(f"Build source contains unexpected changes: {destination}\n{dirty}")
    if not local_only:
        run_visible(["git", "fetch", "--tags", "origin"], cwd=destination, label=label)
    run_visible(["git", "checkout", "--detach", commit], cwd=destination, label=label)
    if resolve_revision(destination) != commit:
        raise RuntimeError(f"Source revision mismatch at {destination}")
    return destination


def resolve_maintained_source(repository: Path, recipe_path: Path) -> Path:
    """Resolve an existing maintained checkout from recipe metadata, without I/O mutations.

    The checkout is a sibling of the CinterForge repository, independent of
    the user's home directory, current working directory, and Git remotes.
    """
    import json

    repository = Path(repository).resolve(strict=True)
    if repository.name != "CinterForge" or not (repository / "CinterForgeServer").is_dir():
        raise RuntimeError(f"Expected CinterForge repository root: {repository}")
    recipe = json.loads(Path(recipe_path).read_text(encoding="utf-8"))
    checkout_name = recipe.get("source", {}).get("checkout_name")
    if (not isinstance(checkout_name, str) or checkout_name in ("", ".", "..")
            or Path(checkout_name).name != checkout_name or "\\" in checkout_name):
        raise RuntimeError(f"Invalid source.checkout_name: {recipe_path}")
    checkout = repository.parent / checkout_name
    if not (checkout / "waf").is_file():
        raise RuntimeError(f"Maintained source missing at {checkout}; expected local sibling "
                           f"declared by {recipe_path}. No source download is permitted.")
    return checkout.resolve()


def refresh_generated_fork_snapshot(source: Path, fork: Path, *, branch: str = "cinterforge/cinterforge") -> str:
    """Refresh a generated build checkout from an authored sibling fork.

    Never fetch/reset the authored fork.  A build snapshot under Builds may be
    detached/reset to the exact committed branch tip; the sibling repository is
    always writable and retains all user edits/branches.  No network access is
    required after the fork has been cloned through Cinter.
    """
    def git_checked(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(['git', *args], text=True, capture_output=True, check=True)

    fork = fork.resolve(strict=True)
    source = source.resolve()
    builds = fork.parent / "Builds"
    if not source.is_relative_to(builds.resolve()):
        raise RuntimeError(f"Refusing to replace non-Builds source with generated snapshot: {source}")
    if fork == source or source.is_relative_to(fork):
        raise RuntimeError(f"Build snapshot must be separate from authored fork: {fork}")
    if not (fork / ".git").exists():
        raise RuntimeError(f"CinterForge fork is not a Git checkout: {fork}")
    ref = f"refs/heads/{branch}"
    commit = git_checked( "-C", str(fork), "rev-parse", "--verify", f"{ref}^{{commit}}").stdout.strip()
    if not commit:
        raise RuntimeError(f"Missing committed {branch} branch in {fork}")
    if not (source / ".git").exists():
        if source.exists():
            # Do not erase an unknown existing directory: this function must
            # only manage its own Git build snapshot.
            raise RuntimeError(f"Existing non-Git source cannot be replaced: {source}")
        source.parent.mkdir(parents=True, exist_ok=True)
        git_checked( "clone", "--no-hardlinks", "--no-checkout", str(fork), str(source))
    # Any old upstream/remote source now receives the exact local fork commit;
    # branch authoring changes do not leak into the generated build checkout.
    git_checked( "-C", str(source), "fetch", "--no-tags", str(fork), ref)
    git_checked( "-C", str(source), "checkout", "--detach", "FETCH_HEAD")
    git_checked( "-C", str(source), "reset", "--hard", "FETCH_HEAD")
    actual = git_checked( "-C", str(source), "rev-parse", "HEAD").stdout.strip()
    if actual != commit:
        raise RuntimeError(f"Generated snapshot revision mismatch: {actual} != {commit}")
    return commit

