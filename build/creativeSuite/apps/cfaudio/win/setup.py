#!/usr/bin/env python3
"""Prepare the Windows Audio dependency environment for CinterForge Setup.

This module has two responsibilities:

1. Read and validate the Windows dependency contract in ``recipe.json``.
2. Prepare and audit the Windows-target toolchain and dependencies.

The public lifecycle is intentionally straightforward::

    ./Cinter setup cfaudio win Release
    ./Cinter build cfaudio win Release

Setup uses Ardour's upstream build-tools source code for the GCC10/POSIX/MSVCRT
Windows dependency stack. Build consumes that installed stack. Cinter does not
install rolling MSYS2/UCRT binary packages or reimplement the upstream build.

Dependency preparation, patch application, and Windows-target ABI checks
are implemented in this module. Build only consumes the resulting sysroot.
"""

from __future__ import annotations

import argparse
import shutil
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any

import sys
import subprocess

# Resolve the Creative Suite root once so both lifecycle commands use the
# same shared container and patch infrastructure.
_RUNNING_CONTAINER_STAGE = len(sys.argv) > 1 and sys.argv[1] == "--container-stage"
if not _RUNNING_CONTAINER_STAGE:
    CREATIVE_SUITE_ROOT = next(
        directory for directory in Path(__file__).resolve().parents
        if directory.name == "creativeSuite"
    )
    if str(CREATIVE_SUITE_ROOT) not in sys.path:
        sys.path.insert(0, str(CREATIVE_SUITE_ROOT))
    from container import build_cached_image, run_mounted_image
    server_root = Path(__file__).resolve().parents[6]
    if str(server_root) not in sys.path:
        sys.path.insert(0, str(server_root))
    from cf.io.CinterIO import cinterforgeBuildsRootPath
else:
    CREATIVE_SUITE_ROOT = None
_SUITE = CREATIVE_SUITE_ROOT


# ---------------------------------------------------------------------------
# Public entry point: command line -> ordered setup stages -> success receipt
# ---------------------------------------------------------------------------
def _parse_command_line() -> argparse.Namespace:
    """Accept the existing lifecycle arguments without inventing a new CLI."""
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument(
        "--target", choices=("win", "windows"), default="win"
    )
    argument_parser.add_argument(
        "--prepare-dependencies", action="store_true"
    )
    command_line = argument_parser.parse_args()

    if not command_line.prepare_dependencies:
        argument_parser.error(
            "Windows dependency preparation belongs to Cinter setup"
        )

    return command_line


def _resolve_windows_build_directory() -> Path:
    """Resolve the versioned output directory using the shared build API.

    CINTER_HOPE_ROOT identifies the CinterForge checkout. Its sibling Builds
    directory holds generated dependencies; the source checkout stays clean.
    The lifecycle must supply a version rather than guessing one here.
    """
    from shared.build.build_runtime import product_build_directory

    installation_version = os.environ.get("CINTER_INSTALL_VERSION") or os.environ.get(
        "CINTER_APP_VERSION"
    )

    if not installation_version:
        raise RuntimeError(
            "Cinter must resolve CINTER_INSTALL_VERSION for Windows Audio Setup"
        )

    built = product_build_directory(
        cinterforgeBuildsRootPath(), "cfaudio", "win", installation_version
    )
    declared = os.environ.get("CINTER_BUILT_ROOT")
    if declared and Path(declared).resolve() != built.resolve():
        raise RuntimeError(
            f"Cinter Windows Audio Setup build-root mismatch: lifecycle={declared}; "
            f"canonical={built}. Refusing to split dependency state."
        )
    return built


def main() -> int:
    """Run dependency preparation through the existing Cinter Setup lifecycle."""
    _parse_command_line()

    # All dependency preparation is implemented here. Build never provisions
    # the sysroot and is not imported by Setup.
    windows_build_directory = _resolve_windows_build_directory()
    # A failed or interrupted Setup must never leave a success receipt behind.
    # Build accepts only a completed Setup for the *same* recipe and output root.
    receipt = windows_build_directory / "Dependencies" / "setup-ready.json"
    # Receipt is a success marker, not cached work. A failed rerun may leave
    # valid compiled libraries, but it must never leave a prior success marker.
    receipt.unlink(missing_ok=True)
    try:
        prepare(windows_build_directory)
    except BaseException:
        print_windows_dependency_summary(windows_build_directory)
        raise
    recipe_path = Path(__file__).with_name("recipe.json")
    closure_path = windows_build_directory / "Dependencies" / "msvcrt-binary-closure.json"
    closure = json.loads(closure_path.read_text(encoding="utf-8"))
    if not closure.get("binary_import_closure") or closure.get("unresolved"):
        raise RuntimeError("Windows Audio Setup did not complete the MSVCRT PE closure")
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(
        json.dumps(
            {
                "schema": "cfaudio.windows.setup.v1",
                "recipe_sha256": hashlib.sha256(recipe_path.read_bytes()).hexdigest(),
                "closure_sha256": hashlib.sha256(closure_path.read_bytes()).hexdigest(),
                "build_directory": str(windows_build_directory.resolve()),
                "target": "win-x86_64-msvcrt-gcc10",
            },
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    print_windows_dependency_summary(windows_build_directory)
    print(f"[cfaudio][win] Setup completed: {receipt}", flush=True)
    return 0



def print_windows_dependency_summary(built: Path) -> None:
    """Report upstream build-tools provenance and validated package closure."""
    folder = built / "Dependencies"
    lock = folder / "ardour-build-tools-lock.json"
    approval = folder / "msvcrt-approved-prefix.json"
    closure = folder / "msvcrt-binary-closure.json"
    def read(path: Path) -> dict:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {}
    source = read(lock)
    installed = read(approval)
    imports = read(closure)
    receipt = folder / "setup-ready.json"
    print("\n" + "=" * 78, flush=True)
    print(" CINTERFORGE AUDIO | ARDOUR UPSTREAM WINDOWS DEPENDENCY STACK", flush=True)
    print("=" * 78, flush=True)
    print(f" GCC10/POSIX/MSVCRT source: {source.get('commit', 'not fetched')}", flush=True)
    print(f" Stack approval: {installed.get('status', 'NOT VERIFIED')}", flush=True)
    print(f" PE imports: {'PASS' if receipt.is_file() and imports.get('binary_import_closure') and not imports.get('unresolved') else 'NOT VERIFIED'}", flush=True)
    print(f" Upstream lock: {lock}", flush=True)
    print("=" * 78 + "\n", flush=True)










def prepare(built: Path) -> None:
    """Build Ardour's own cross-dependency stack, then prove target closure.

    The 58-row historical dependency inventory is NOT a build script. Ardour's
    maintained x-mingw.sh owns the library versions, source patches and order;
    Cinter owns the container, Git pin, target prefix, ABI checks, and receipts.
    """
    validate_dependency_contract()
    engine, environment = select_build_backend()
    verify_locked_msvcrt_toolchain(built, engine, environment)
    build_ardour_upstream_stack(built, engine, environment)
    verify_windows_dependencies(built, engine, environment)
    approve_msvcrt_source_prefix(built, engine, environment)
    check_msvcrt_binary_closure(built, engine, environment)




def _ensure_upstream_pkgconfig_writable(dependencies_root: Path, side: str) -> None:
    """Verify that upstream sed -i can atomically rewrite target .pc files.

    The Windows prefix persists across Setup attempts and is bind-mounted into
    the GCC10 container. A stale directory mode or macOS volume ACL can allow
    reads but reject sed's temporary-file creation. Check both the host and
    container views instead of discovering the problem partway through XZ.
    Never chmod the whole prefix or delete cached dependencies.
    """
    directory = dependencies_root / 'UpstreamWindows-x64' / 'mingw64' / 'lib' / 'pkgconfig'
    if directory.is_symlink():
        raise RuntimeError(f'Unsafe Windows pkg-config symlink: {directory}')
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise RuntimeError(
            f'Windows pkg-config directory not creatable ({side}): {directory}: {error}'
        ) from error

    def write_and_rename_probe() -> None:
        temporary = None
        renamed = None
        try:
            # sed -i writes a new inode in this directory and renames it.
            with tempfile.NamedTemporaryFile(
                    dir=directory, prefix='cfaudio-write-check-', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(b'cfaudio-writable\n')
            renamed = temporary.with_name(temporary.name + '-renamed')
            os.replace(temporary, renamed)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            if renamed is not None:
                renamed.unlink(missing_ok=True)

    try:
        write_and_rename_probe()
    except PermissionError as error:
        # Only repair the owner-write/search bits on this generated directory.
        # Never relax the mode of the source checkout or entire Builds tree.
        mode = stat.S_IMODE(directory.stat().st_mode)
        repaired_mode = mode | stat.S_IWUSR | stat.S_IXUSR
        if repaired_mode != mode:
            try:
                directory.chmod(repaired_mode)
                write_and_rename_probe()
                print(f'[cfaudio][win] Restored pkg-config owner write permission '
                      f'({side}): {directory}', flush=True)
            except OSError as retry_error:
                raise RuntimeError(
                    f'Windows pkg-config bind mount is not writable ({side}): '
                    f'{directory}: {retry_error}'
                ) from retry_error
        else:
            raise RuntimeError(
                f'Windows pkg-config bind mount is not writable ({side}): '
                f'{directory}: {error}'
            ) from error
    except OSError as error:
        raise RuntimeError(
            f'Windows pkg-config write/rename failed ({side}): {directory}: {error}'
        ) from error
    print(f'[cfaudio][win] VERIFIED writable Windows pkg-config ({side})', flush=True)


def _repair_generated_upstream_write_permissions(root: Path) -> int:
    """Restore owner write/search on GENERATED dependency directories only.

    Upstream archives can unpack directories without owner write permission.
    GNU sed -i creates a temporary file beside its input, so a package may
    compile successfully and then fail with `./sedXXXXXX: Permission denied`.
    This is a filesystem repair, not a dependency or ABI change: never remove
    objects, change file contents, follow directory symlinks, or touch source.
    """
    repaired = 0
    for tree in (root / 'win-build-w64', root / 'ArdourBuildScratch',
                 root / 'ArdourSourceCache',
                 root / 'UpstreamWindows-x64' / 'mingw64'):
        if tree.is_symlink():
            raise RuntimeError(f'Unsafe upstream generated-directory symlink: {tree}')
        if not tree.exists():
            continue
        if not tree.is_dir():
            raise RuntimeError(f'Expected generated dependency directory: {tree}')
        for directory, subdirectories, _files in os.walk(tree, followlinks=False):
            current = Path(directory)
            if current.is_symlink():
                raise RuntimeError(f'Unsafe generated-directory symlink: {current}')
            # Never descend into symbolic links created by upstream packages.
            subdirectories[:] = [name for name in subdirectories
                                 if not (current / name).is_symlink()]
            original = stat.S_IMODE(current.stat().st_mode)
            required = original | stat.S_IWUSR | stat.S_IXUSR
            if original != required:
                try:
                    current.chmod(required)
                except OSError as error:
                    raise RuntimeError(
                        f'Cannot restore write access to generated upstream '
                        f'directory {current}: {error}') from error
                repaired += 1
    if repaired:
        print(f'[cfaudio][win] Restored owner write/search on {repaired} '
              'generated upstream directories; cached binaries preserved',
              flush=True)
    return repaired


def _is_upstream_sed_permission_failure(path: Path) -> bool:
    """Recognize only GNU sed in-place temporary-file permission failures."""
    with path.open('r', encoding='utf-8', errors='replace') as stream:
        import collections
        tail = ''.join(collections.deque(stream, maxlen=100))
    return bool(re.search(
        r'(?m)^/bin/sed: couldn.t open temporary file '
        r'.*\bsed[A-Za-z0-9]+: Permission denied\s*$', tail))


def build_ardour_upstream_stack(built: Path, engine: str, environment: dict) -> None:
    """Run upstream's real dependency builder without embedding shell source.

    The upstream checkout, its tool/version lock and all build/download state
    remain under the existing versioned Builds/Dependencies tree. The first
    successful checkout records its Git commit and script digest; subsequent
    runs reuse those exact bytes. Never auto-upgrade a pinned stack.
    """
    _, recipe = _read_dependency_recipe()
    upstream = recipe['upstream_build_tools']
    _ensure_upstream_pkgconfig_writable(built / 'Dependencies', 'macOS host')
    _run_container_stage(
        engine, _MSVCRT_IMAGE, ((built / 'Dependencies', '/work', False),),
        environment, '_stage_build_ardour_upstream_stack',
        {'CFAUDIO_UPSTREAM_GIT_URL': upstream['url'],
         'CFAUDIO_UPSTREAM_SCRIPT': upstream['script'],
         'CFAUDIO_UPSTREAM_REVISION': upstream['revision']},
        'Ardour GCC10/MSVCRT upstream dependency builder',
    )


def _compiler_libssp_import_archive() -> Path:
    """Find the Windows import archive owned by the PINNED POSIX GCC 10.

    GCC's private search path is not a libtool search path. Never substitute
    a Linux host archive, a UCRT archive, or a rolling package candidate.
    """
    compiler = 'x86_64-w64-mingw32-gcc'
    compiler_version = subprocess.check_output(
        [compiler, '-dumpfullversion'], text=True).strip()
    compiler_target = subprocess.check_output(
        [compiler, '-dumpmachine'], text=True).strip()
    archive_name = subprocess.check_output(
        [compiler, '-print-file-name=libssp.dll.a'], text=True).strip()
    archive = Path(archive_name)
    # Debian Bullseye's GCC 10 POSIX cross compiler reports "10-posix" from
    # -dumpfullversion. That is the compiler's package variant, NOT a newer
    # GCC release. The GCC-owned archive is stored under 10-posix too.
    # Check the compiler's target and the actual private-library directory,
    # rather than rejecting the pinned compiler for its valid version suffix.
    gcc10 = re.fullmatch(r'10(?:\.[0-9]+)*(?:-posix)?', compiler_version)
    gcc_private_directory = archive.parts[-4:-1]
    if (not gcc10 or compiler_target != 'x86_64-w64-mingw32' or
            gcc_private_directory != ('gcc', 'x86_64-w64-mingw32', '10-posix') or
            archive.name != 'libssp.dll.a' or not archive.is_absolute() or
            not archive.is_file() or archive.stat().st_size == 0):
        raise RuntimeError(
            'GCC 10 POSIX Windows libssp import archive verification failed: '
            f'version={compiler_version} target={compiler_target} '
            f'archive={archive_name}'
        )
    return archive


def _activate_gcc10_default_libssp_link() -> None:
    """Make every GCC 10 Windows executable link find its own fortified ABI.

    Ardour's pinned upstream dependencies do not all propagate Autoconf LIBS
    into their executable link rules (FLAC 1.3.2 is one example). Configure
    the compiler's standard *lib spec inside this disposable Docker container
    instead of patching each dependency, wrapping gcc, or changing the ABI.
    The installed compiler and its import archive remain the pinned GCC 10
    POSIX/MSVCRT versions; no host source or cached dependency is changed.
    """
    compiler = 'x86_64-w64-mingw32-gcc'
    archive = _compiler_libssp_import_archive()
    private_dir = archive.parent
    libgcc = Path(subprocess.check_output(
        [compiler, '-print-libgcc-file-name'], text=True).strip())
    if libgcc.parent != private_dir or not libgcc.is_file():
        raise RuntimeError('GCC 10 private specs path differs from compiler-owned libssp')
    specs_path = private_dir / 'specs'
    if specs_path.is_symlink():
        raise RuntimeError(f'Unsafe compiler specs symlink: {specs_path}')
    built_in = subprocess.check_output([compiler, '-dumpspecs'], text=True)
    # Preserve the compiler's full original spec set. Append -lssp to its
    # existing Windows CRT libraries, which GCC emits AFTER input objects.
    # Unlike upstream LIBS, this also covers FLAC executable link rules.
    original = re.search(r'(?m)^\*lib:\n([^\n]+)$', built_in)
    if original is None or built_in.count('\n*lib:\n') != 1:
        raise RuntimeError('Cannot locate unique GCC 10 default library spec')
    if re.search(r'(?<!\S)-lssp(?!\S)', original.group(1)):
        raise RuntimeError('GCC 10 default library spec already links libssp')
    activated = (built_in[:original.end(1)] + ' -lssp' +
                 built_in[original.end(1):])
    if specs_path.exists():
        if specs_path.read_text(encoding='utf-8') != activated:
            raise RuntimeError('Existing compiler specs differ; refusing to overwrite')
    else:
        specs_path.write_text(activated, encoding='utf-8')
    # Exercise the compiler EXACTLY as upstream invokes it: no extra LIBS,
    # LDFLAGS, -specs= or -lssp arguments. This rejects an unrecognized specs
    # file before spending minutes recompiling the FLAC stack.
    with tempfile.TemporaryDirectory(dir='/work') as temporary:
        probe = Path(temporary) / 'fortified.c'
        binary = Path(temporary) / 'fortified.exe'
        probe.write_text(
            '#include <stddef.h>\n'
            'extern void *__memmove_chk(void *, const void *, size_t, size_t);\n'
            'extern char *__strncpy_chk(char *, const char *, size_t, size_t);\n'
            'extern void *__memset_chk(void *, int, size_t, size_t);\n'
            'int main(void) { char dst[16] = {0}; char src[16] = "test";\n'
            '  __strncpy_chk(dst, src, 5, sizeof(dst));\n'
            '  __memmove_chk(dst + 1, dst, 4, sizeof(dst) - 1);\n'
            '  __memset_chk(dst + 6, 0, 4, sizeof(dst) - 6);\n'
            "  return dst[0] != 't'; }\n",
            encoding='utf-8')
        subprocess.run([compiler, '-O0', '-fno-builtin', str(probe),
                        '-o', str(binary)], check=True)
        imports = subprocess.check_output(
            ['x86_64-w64-mingw32-objdump', '-p', str(binary)], text=True)
        if not re.search(r'DLL Name:\s*libssp-0\.dll', imports, re.IGNORECASE):
            raise RuntimeError('Default GCC 10 link omitted libssp-0.dll')
        if not re.search(r'DLL Name:\s*msvcrt\.dll', imports, re.IGNORECASE):
            raise RuntimeError('Default GCC 10 link is not MSVCRT based')
        if re.search(r'DLL Name:\s*(?:ucrtbase|api-ms-win-crt-[^\s]+)\.dll',
                     imports, re.IGNORECASE):
            raise RuntimeError('Default GCC 10 link unexpectedly imports UCRT')
    print('[cfaudio][win] VERIFIED implicit GCC10 libssp linking '
          '(FLAC fortified string ABI)', flush=True)


def _stage_build_ardour_upstream_stack() -> None:
    """Use Ardour's OWN x-mingw.sh, not a generated or Cinter-derived script.

    This is the only approved source-build driver. Git clone is checked out
    into persistent Builds, then .git is discarded so no hidden state survives.
    A pinned digest and commit are recorded before running the upstream builder.
    """
    root = Path('/work')
    url = os.environ['CFAUDIO_UPSTREAM_GIT_URL']
    script_name = os.environ['CFAUDIO_UPSTREAM_SCRIPT']
    pinned_revision = os.environ['CFAUDIO_UPSTREAM_REVISION']
    if not re.fullmatch(r'[0-9a-f]{40}', pinned_revision):
        raise RuntimeError('Ardour build-tools must be pinned to an exact Git commit')
    if (url != 'git://git.ardour.org/ardour/ardour-build-tools.git'
            or script_name != 'x-mingw.sh'):
        raise RuntimeError('Unexpected upstream Ardour dependency source')
    source = root / 'ArdourBuildTools'
    script = source / script_name
    lock_path = root / 'ardour-build-tools-lock.json'
    built_path = root / 'ardour-upstream-stack-built.json'
    prefix = root / 'UpstreamWindows-x64' / 'mingw64'
    image_sha256 = hashlib.sha256(_MSVCRT_DOCKERFILE.encode('utf-8')).hexdigest()
    # Plain -lssp is rejected by XZ 5.2.2's generated libtool as a usable
    # shared inter-library dependency, so it produces only liblzma.a and
    # never generates liblzma.def.in. Forward it as a linker argument instead.
    #
    # -Wl,-lssp works for GCC and libtool, but XZ's liblzma.pc Makefile uses
    # a comma-delimited sed expression to substitute @LIBS@; its comma breaks
    # that substitution. Escaping the comma in the LIBS environment is also
    # wrong: configure expands it literally and GCC rejects -Wl\,-lssp.
    # -Xlinker -lssp is understood by GCC 10 and GNU libtool (the latter
    # forwards the argument directly without its -l dependency magic).
    # It has no sed delimiter and resolves the same compiler-owned import
    # archive. Keep this in LIBS, after objects, not in LDFLAGS.
    target_libs = '-Xlinker -lssp'
    libssp_source = _compiler_libssp_import_archive()
    libssp_digest = hashlib.sha256(libssp_source.read_bytes()).hexdigest()
    _activate_gcc10_default_libssp_link()
    stack_options = {
        'schema': 'cfaudio.windows.upstream-stack-options.v4',
        'compiler_image_sha256': image_sha256,
        'autoconf_LIBS': target_libs,
        'gcc10_libssp_import_sha256': libssp_digest,
        'gcc10_implicit_libssp': 'compiler-default-lib-spec-v1',
        'upstream_commit': pinned_revision,
    }
    options_path = root / 'ardour-upstream-stack-options.json'
    if not script.is_file():
        if lock_path.exists():
            raise RuntimeError('Locked Ardour build-tools source missing; refusing a moving replacement')
        checkout = root / 'ArdourBuildToolsCheckout'
        if checkout.exists():
            if not (checkout / '.git').is_dir():
                raise RuntimeError(f'Interrupted Git clone is not valid: {checkout}')
        else:
            # Both endpoints address Ardour's official build-tools repository.
            # HTTPS is a transport fallback, not a different dependency source.
            transports = (url, 'https://git.ardour.org/ardour/ardour-build-tools.git')
            errors = []
            for transport in transports:
                result = subprocess.run(
                    ['git', 'clone', '--single-branch', transport, str(checkout)],
                    check=False,
                )
                if result.returncode == 0:
                    print(f'[cfaudio][win] Ardour checkout transport: {transport}', flush=True)
                    break
                errors.append(f'{transport} (exit {result.returncode})')
                # Only a failed, newly created clone may be discarded; never
                # remove an existing cached checkout, source stack, or prefix.
                if checkout.exists():
                    shutil.rmtree(checkout)
            else:
                raise RuntimeError('Official Ardour build-tools fetch failed: ' + ', '.join(errors))
        revision = subprocess.check_output(
            ['git', '-C', str(checkout), 'rev-parse', 'HEAD'], text=True).strip()
        if not re.fullmatch(r'[0-9a-f]{40}', revision):
            raise RuntimeError('Upstream source did not resolve to a Git commit')
        # A fresh machine must select the same upstream revision as every
        # other machine, irrespective of when the clone was performed.
        subprocess.run(['git', '-C', str(checkout), 'checkout', '--detach',
                        pinned_revision], check=True)
        revision = subprocess.check_output(
            ['git', '-C', str(checkout), 'rev-parse', 'HEAD'], text=True).strip()
        if revision != pinned_revision:
            raise RuntimeError(f'Ardour build-tools revision mismatch: {revision}')
        if not (checkout / script_name).is_file():
            raise RuntimeError(f'Upstream Ardour builder missing {script_name}')
        shutil.copytree(checkout, source, ignore=lambda _dir, names: [name for name in names if name.startswith('.')])
        shutil.rmtree(checkout)
        source_digest = hashlib.sha256(script.read_bytes()).hexdigest()
        lock = {'schema': 'cfaudio.ardour-build-tools.lock.v1', 'origin': url,
                'commit': revision, 'script': script_name,
                'script_sha256': source_digest}
        temp = lock_path.with_suffix('.json.partial')
        temp.write_text(json.dumps(lock, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        temp.replace(lock_path)
        print(f'[cfaudio][win] LOCK upstream Ardour tools {revision}', flush=True)
    if not lock_path.is_file():
        raise RuntimeError('Unpinned upstream source tree; cannot certify source provenance')
    lock = json.loads(lock_path.read_text(encoding='utf-8'))
    if (lock.get('origin') != url or lock.get('script') != script_name or
            lock.get('commit') != pinned_revision or
            hashlib.sha256(script.read_bytes()).hexdigest() != lock.get('script_sha256')):
        raise RuntimeError('Existing Ardour build-tools source differs from pinned lock')
    had_completed_stack = built_path.is_file()
    if had_completed_stack:
        previous = json.loads(built_path.read_text(encoding='utf-8'))
        requirements_match = (
            previous.get('commit') == lock.get('commit') and
            previous.get('script_sha256') == lock.get('script_sha256') and
            previous.get('compiler_image_sha256') == image_sha256 and
            previous.get('toolchain') == 'gcc10-posix-msvcrt' and
            previous.get('prefix') == str(prefix) and
            previous.get('stack_options') == stack_options
        )
        installed_ssp = prefix / 'lib' / 'libssp.dll.a'
        matching_ssp = (not installed_ssp.is_symlink() and installed_ssp.is_file() and
                        hashlib.sha256(installed_ssp.read_bytes()).hexdigest() == libssp_digest)
        if (requirements_match and matching_ssp and
                (prefix / 'lib' / 'pkgconfig').is_dir() and
                any((prefix / 'lib' / 'pkgconfig').glob('*.pc'))):
            print(f'[cfaudio][win] REUSE upstream dependency build {lock["commit"]}', flush=True)
            return
        built_path.unlink()
        if not requirements_match:
            # Do not mix binaries built by different compiler images or source
            # revisions. The upstream source-download cache stays untouched.
            # An interrupted build without a completed receipt remains resumable.
            print('[cfaudio][win] Dependency requirements changed: rebuild Windows '
                  'prefix and scratch with pinned toolchain', flush=True)
            for directory in (prefix, root / 'ArdourBuildScratch',
                              root / 'win-build-w64'):
                if directory.exists():
                    if directory.is_symlink() or not directory.is_dir():
                        raise RuntimeError(f'Unsafe dependency build location: {directory}')
                    shutil.rmtree(directory)
        else:
            print('[cfaudio][win] Incomplete dependency build; resuming upstream builder',
                  flush=True)
    # An interrupted Autoconf configure has already written LIBS into its
    # Makefiles. A change to the link contract must discard those Makefiles,
    # not just pass new environment variables to the next make invocation.
    # The upstream source download cache and pinned tool checkout are retained.
    if options_path.is_file():
        previous_options = json.loads(options_path.read_text(encoding='utf-8'))
    else:
        previous_options = None
    if previous_options != stack_options:
        # Both spellings invoke the same GCC10 linker library. The preceding
        # run failed before installing XZ, so only its generated configuration
        # must be invalidated. Do not discard other installed dependencies or
        # the upstream source/download caches for this spelling-only repair.
        previous_other_options = (
            {key: value for key, value in previous_options.items()
             if key != 'autoconf_LIBS'}
            if isinstance(previous_options, dict) else None
        )
        current_other_options = {
            key: value for key, value in stack_options.items()
            if key != 'autoconf_LIBS'
        }
        xz_only = (
            not had_completed_stack and
            previous_other_options == current_other_options and
            previous_options.get('autoconf_LIBS') == '-Wl,-lssp'
        )
        # A failed FLAC link leaves only partial objects/Makefiles behind.
        # The GCC 10 default-library spec adds the compiler-owned libssp to
        # all future executable links without changing any compiled objects,
        # dependency versions, or the upstream Autoconf LIBS argument.
        previous_without_upgrade = (
            {key: value for key, value in previous_options.items()
             if key not in ('schema', 'gcc10_implicit_libssp')}
            if isinstance(previous_options, dict) else None
        )
        current_without_upgrade = {
            key: value for key, value in stack_options.items()
            if key not in ('schema', 'gcc10_implicit_libssp')
        }
        implicit_ssp_upgrade_only = (
            not had_completed_stack and
            isinstance(previous_options, dict) and
            previous_options.get('schema') ==
                'cfaudio.windows.upstream-stack-options.v3' and
            previous_without_upgrade == current_without_upgrade
        )
        if implicit_ssp_upgrade_only:
            print('[cfaudio][win] GCC10 implicit libssp link enabled; '
                  'reuse XZ and partial FLAC objects', flush=True)
            invalidated = ()
        elif xz_only:
            print('[cfaudio][win] Equivalent GCC10 libssp linker spelling: '
                  'reconfigure incomplete XZ only', flush=True)
            invalidated = (root / 'win-build-w64' / 'xz-5.2.2',)
        else:
            print('[cfaudio][win] Windows build requirements changed: '
                  'reconfigure dependency scratch and prefix', flush=True)
            invalidated = (root / 'win-build-w64', root / 'ArdourBuildScratch', prefix)
        for directory in invalidated:
            if directory.is_symlink():
                raise RuntimeError(f'Unsafe dependency build location: {directory}')
            if directory.exists():
                if not directory.is_dir():
                    raise RuntimeError(f'Expected build directory: {directory}')
                shutil.rmtree(directory)
        options_partial = options_path.with_suffix('.json.partial')
        options_partial.write_text(json.dumps(stack_options, indent=2,
                                             sort_keys=True) + '\n', encoding='utf-8')
        options_partial.replace(options_path)
    # Upstream controls the exact dependency order and patches. Supply only
    # environment and persistent scratch locations; never alter its script.
    source_cache = root / 'ArdourSourceCache'
    scratch = root / 'ArdourBuildScratch'
    for directory in (source_cache, scratch, prefix):
        directory.mkdir(parents=True, exist_ok=True)
    target_libdir = prefix / 'lib'
    target_libdir.mkdir(parents=True, exist_ok=True)
    imported_ssp = target_libdir / 'libssp.dll.a'
    if imported_ssp.is_symlink():
        raise RuntimeError('Compiler runtime import library must not be a symlink')
    if (not imported_ssp.is_file() or
            hashlib.sha256(imported_ssp.read_bytes()).hexdigest() != libssp_digest):
        shutil.copy2(libssp_source, imported_ssp)
    if hashlib.sha256(imported_ssp.read_bytes()).hexdigest() != libssp_digest:
        raise RuntimeError('Pinned GCC 10 libssp import library copy failed')
    print('[cfaudio][win] GCC10 libssp import library ready in Windows target prefix',
          flush=True)
    # After any requirements-driven prefix rebuild, check the *actual* target
    # directory as seen by the container. Upstream uses sed -i on .pc metadata.
    _ensure_upstream_pkgconfig_writable(root, 'GCC10 container')
    _repair_generated_upstream_write_permissions(root)
    env = dict(os.environ)
    env.update({'ROOT': str(root), 'PREFIX': str(prefix), 'SRCDIR': str(source_cache),
                'TMPDIR': str(scratch), 'XARCH': 'x86_64', 'MAKEFLAGS': '-j4',
                'CC': 'x86_64-w64-mingw32-gcc',
                'CXX': 'x86_64-w64-mingw32-g++',
                'PKG_CONFIG_LIBDIR': str(prefix / 'lib/pkgconfig') + ':' + str(prefix / 'share/pkgconfig'),
                'PKG_CONFIG_SYSROOT_DIR': '',
                'LIBS': target_libs})
    env.pop('PKG_CONFIG_PATH', None)
    print('[cfaudio][win] Autoconf Windows runtime link: LIBS=' +
          target_libs, flush=True)
    print(f'[cfaudio][win] EXEC UPSTREAM {script_name} @ {lock["commit"]}', flush=True)
    # Keep upstream's exact build command, source tree, and compiler flags.
    # Verbose compiler output is preserved in Builds rather than flooding the
    # editor's integrated terminal (or growing its renderer's scrollback).
    _run_upstream_build_logged(script, source, env, root)
    if (not imported_ssp.is_file() or
            hashlib.sha256(imported_ssp.read_bytes()).hexdigest() != libssp_digest):
        raise RuntimeError('Upstream dependency build changed the GCC 10 libssp import archive')
    pcdir = prefix / 'lib' / 'pkgconfig'
    if not pcdir.is_dir() or not any(pcdir.glob('*.pc')):
        raise RuntimeError('Upstream build completed without Windows target .pc files')
    receipt = {'schema': 'cfaudio.ardour-build-tools.build.v1',
               'commit': lock['commit'], 'script_sha256': lock['script_sha256'],
               'toolchain': 'gcc10-posix-msvcrt',
               'compiler_image_sha256': image_sha256, 'prefix': str(prefix),
               'stack_options': stack_options}
    temp = built_path.with_suffix('.json.partial')
    temp.write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    temp.replace(built_path)
    print('[cfaudio][win] Upstream dependency build finished: ' + str(prefix), flush=True)


def _run_upstream_build_logged(script: Path, source: Path,
                               environment: dict, dependencies_root: Path) -> None:
    """Run the pinned upstream builder with bounded terminal output.

    The complete output remains in Builds/CreativeSuite/.../Dependencies/Logs.
    On failure, surface the tail and preserve the original exit status. If
    Setup is interrupted, terminate the entire upstream process group so make
    and its compiler children are not orphaned inside the container.
    """
    import collections
    import signal
    import time

    logs = dependencies_root / 'Logs'
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / 'x-mingw-full.log'
    command = ['bash', str(script)]
    print(f'[cfaudio][win] Full upstream compiler output: {path}', flush=True)
    started = time.monotonic()
    # A first-time unpack may create a non-writable source directory after
    # preflight. Recover that specific generated-filesystem condition in the
    # same Setup invocation; retrying upstream preserves its completed work.
    for attempt in range(3):
        with path.open('a' if attempt else 'w', encoding='utf-8',
                       errors='replace') as stream:
            if attempt:
                stream.write(f'\n[cfaudio] Retrying after generated-directory '
                             f'permission repair ({attempt}/2)\n')
                stream.flush()
            process = subprocess.Popen(command, cwd=source, env=environment,
                                       stdout=stream, stderr=subprocess.STDOUT,
                                       start_new_session=True)
            try:
                while True:
                    try:
                        status = process.wait(timeout=30)
                        break
                    except subprocess.TimeoutExpired:
                        elapsed = int(time.monotonic() - started)
                        print(f'[cfaudio][win] Upstream dependency build working '
                              f'({elapsed}s); full output: {path}', flush=True)
            except BaseException:
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait()
                raise
        if not status:
            break
        if attempt < 2 and _is_upstream_sed_permission_failure(path):
            repaired = _repair_generated_upstream_write_permissions(dependencies_root)
            if repaired:
                print(f'[cfaudio][win] Upstream sed permission failure repaired '
                      f'in generated dependency tree; resuming pinned build '
                      f'({attempt + 1}/2)', flush=True)
                continue
            print('[cfaudio][win] sed cannot create its temporary file, but '
                  'generated directory modes are already owner-writable; '
                  'check bind-mount ACLs and full upstream log',
                  file=sys.stderr, flush=True)
        print(f'[cfaudio][win] Upstream builder FAILED (exit={status}); '
              f'last 60 lines of {path}:', file=sys.stderr, flush=True)
        with path.open('r', encoding='utf-8', errors='replace') as stream:
            for line in collections.deque(stream, maxlen=60):
                print(line, end='', file=sys.stderr)
        raise subprocess.CalledProcessError(status, command)
    print(f'[cfaudio][win] Upstream dependency build completed; '
          f'full output: {path}', flush=True)


def approve_msvcrt_source_prefix(built: Path, engine: str, environment: dict) -> None:
    """Approve only the upstream-build-tools checkout and verified prefix."""
    _run_container_stage(
        engine, _MSVCRT_IMAGE, ((built / "Dependencies", "/work", False),),
        environment, "_stage_approve_msvcrt_source_prefix", {},
        "Ardour upstream source stack provenance approval",
    )




def _stage_approve_msvcrt_source_prefix() -> None:
    """Verify upstream checkout digest and GCC10-built prefix in the container.

    This receipt proves that the selected upstream build-tools script ran and
    that target modules exist, not that its exact set matches an old inventory.
    The subsequent PE/import checks are separate, mandatory gates.
    """
    root = Path('/work')
    source = root / 'ArdourBuildTools' / 'x-mingw.sh'
    lock_path = root / 'ardour-build-tools-lock.json'
    successful = root / 'ardour-upstream-stack-built.json'
    prefix = root / 'UpstreamWindows-x64' / 'mingw64'
    manifest = root / 'msvcrt-approved-prefix.json'
    if not (source.is_file() and lock_path.is_file() and successful.is_file()):
        raise RuntimeError('Ardour upstream dependency source/build receipts missing')
    lock = json.loads(lock_path.read_text(encoding='utf-8'))
    state = json.loads(successful.read_text(encoding='utf-8'))
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    options_path = root / 'ardour-upstream-stack-options.json'
    if not options_path.is_file():
        raise RuntimeError('Pinned upstream Windows link-options receipt is missing')
    options = json.loads(options_path.read_text(encoding='utf-8'))
    installed_ssp = prefix / 'lib' / 'libssp.dll.a'
    if (sha != lock.get('script_sha256') or state.get('commit') != lock.get('commit')
            or state.get('script_sha256') != sha or
            state.get('stack_options') != options or
            options.get('schema') != 'cfaudio.windows.upstream-stack-options.v4' or
            options.get('gcc10_implicit_libssp') != 'compiler-default-lib-spec-v1' or
            options.get('autoconf_LIBS') != '-Xlinker -lssp' or
            options.get('gcc10_libssp_import_sha256') != hashlib.sha256(
                _compiler_libssp_import_archive().read_bytes()).hexdigest() or
            installed_ssp.is_symlink() or not installed_ssp.is_file() or
            hashlib.sha256(installed_ssp.read_bytes()).hexdigest() !=
                options.get('gcc10_libssp_import_sha256') or
            state.get('compiler_image_sha256') != hashlib.sha256(
                _MSVCRT_DOCKERFILE.encode('utf-8')).hexdigest() or not re.fullmatch(r'[0-9a-f]{40}', lock.get('commit', ''))):
        raise RuntimeError('Ardour upstream build-tools or compiler-owned library changed since lock')
    if not (prefix / 'lib' / 'pkgconfig').is_dir():
        raise RuntimeError(f'Upstream Windows library prefix not installed: {prefix}')
    data = {'schema': 'cfaudio.msvcrt.prefix.v3', 'toolchain': 'GCC 10 POSIX / MSVCRT',
            'source': 'Ardour ardour-build-tools/x-mingw.sh',
            'commit': lock['commit'], 'script_sha256': sha,
            'prefix': 'UpstreamWindows-x64/mingw64', 'status': 'upstream_build_and_modules_verified'}
    temp = manifest.with_suffix('.json.partial')
    temp.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    temp.replace(manifest)
    print('[cfaudio][win] APPROVED upstream Ardour stack at ' + lock['commit'], flush=True)




def _run_container_python(engine: str, image: str, mounts: tuple, environment: dict,
                          function: Any, variables: dict, label: str) -> None:
    """Invoke an existing Python function inside the build container; no shell/eval."""
    _run_container_stage(engine, image, mounts, environment,
                         function.__name__, variables, label)


def _run_container_stage(engine: str, image: str, mounts: tuple, environment: dict,
                         stage: str, variables: dict, label: str) -> None:
    """Mount this module read-only and dispatch to its actual stage functions."""
    source_dir = Path(__file__).resolve().parent
    run_mounted_image(
        engine=engine, image=image, platform="linux/amd64",
        mounts=(*mounts, (source_dir, "/cfaudio", True)),
        variables=variables,
        command=("python3", "/cfaudio/setup.py", "--container-stage", stage),
        env=environment, label=label,
    )










def _read_dependency_recipe() -> tuple[Path, dict[str, Any]]:
    """Read the recipe next to this file and locate its Windows section."""
    recipe_path = Path(__file__).with_name("recipe.json")
    recipe_document = json.loads(recipe_path.read_text(encoding="utf-8"))
    windows_dependencies = recipe_document.get("windows_cross_dependencies")

    if not isinstance(windows_dependencies, dict):
        raise ValueError(
            f"Missing windows_cross_dependencies in {recipe_path}"
        )

    return recipe_path, windows_dependencies




def _validate_pkg_config_modules(
    recipe_path: Path, windows_dependencies: dict[str, Any]
) -> None:
    """Require a nonempty, duplicate-free list of pkg-config module names."""
    module_names = windows_dependencies["required_pkg_config_modules"]

    if not isinstance(module_names, list) or any(
        not isinstance(module_name, str) or not module_name
        for module_name in module_names
    ):
        raise ValueError(f"{recipe_path}: invalid required_pkg_config_modules")

    if len(module_names) != len(set(module_names)):
        raise ValueError(f"{recipe_path}: duplicate required_pkg_config_modules")








def load_windows_dependencies() -> dict[str, Any]:
    """Return validated Windows dependency declarations from ``recipe.json``.

    ``build.py`` imports this public function. Keep its name and return value
    stable so that this refactor does not change the build-system interface.
    """
    recipe_path, windows_dependencies = _read_dependency_recipe()

    # Keep each check independent so failures identify the violated contract.
    _validate_pkg_config_modules(recipe_path, windows_dependencies)
    # Only the official Ardour build-tools script is an installation driver.
    authority = windows_dependencies.get('upstream_build_tools', {})
    if authority.get('url') != 'git://git.ardour.org/ardour/ardour-build-tools.git':
        raise ValueError(f'{recipe_path}: unexpected Ardour build-tools origin')
    if authority.get('script') != 'x-mingw.sh':
        raise ValueError(f'{recipe_path}: invalid upstream Windows stack entrypoint')
    if not re.fullmatch(r'[0-9a-f]{40}', authority.get('revision', '')):
        raise ValueError(f'{recipe_path}: upstream source must pin an exact commit')

    return windows_dependencies


# ---------------------------------------------------------------------------
# Windows dependency preparation
# ---------------------------------------------------------------------------
# The Ardour upstream x-mingw.sh script owns dependency installation. This
# module owns its invocation and the independent compiler/ABI/closure gates.
#
# Responsibility boundary:
#   Setup: prepare toolchain, install/patch dependencies, validate target ABI.
#   Build: compile Ardour using the already prepared Windows-target sysroot.
#

# Windows-target pkg-config modules are declared in recipe.json, not appended
# piecemeal to Python as upstream Waf reveals additional requirements.
_REQUIRED_TARGET_MODULES = (() if _RUNNING_CONTAINER_STAGE else tuple(
    _read_dependency_recipe()[1]['required_pkg_config_modules']
))








def validate_dependency_contract() -> None:
    """Validate upstream dependency origin and package-config contract."""
    load_windows_dependencies()


def select_build_backend() -> tuple[str, dict]:
    """Select the existing managed AMD64 container backend."""
    backend = _SUITE / "apps/cinterforge-os/linux"
    if not (backend / "managed_backend.py").is_file():
        raise RuntimeError(f"Managed headless Ubuntu backend unavailable: {backend}")
    sys.path.insert(0, str(backend))
    from managed_backend import ensure_backend  # type: ignore

    engine, environment = ensure_backend(cinterforgeBuildsRootPath() / "CreativeSuite/Built/cinterforge-os/linux/2026/Ubuntu")
    return engine, environment




# The original Ardour Windows release toolchain: Debian 11 GCC 10 POSIX/MSVCRT.
# Both Setup and Build use this one image; no UCRT image is provisioned.
_MSVCRT_IMAGE = "cinterforge-cfaudio-msvcrt-gcc10:debian11-amd64"
_MSVCRT_DOCKERFILE = r"""FROM debian:11.9-slim@sha256:acc5810124f0929ab44fc7913c0ad936b074cbd3eadf094ac120190862ba36c4
ENV DEBIAN_FRONTEND=noninteractive
# The entire upstream x-mingw.sh host prerequisite set must be provisioned
# BEFORE running the builder. Container recreation cannot rely on an APT
# index left behind by an earlier interactive session.
#
# Debian 11's GCC 10 and all packages come from one dated snapshot. APT
# signatures remain checked; only snapshot metadata expiry is disabled.
RUN printf '%s\n' \
    'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/20240601T000000Z/ bullseye main' \
    'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/20240601T000000Z/ bullseye-updates main' \
    'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian-security/20240601T000000Z/ bullseye-security main' \
    > /etc/apt/sources.list \
    && rm -f /etc/apt/sources.list.d/*.list \
    && apt-get -o Acquire::Check-Valid-Until=false -o APT::Update::Error-Mode=any update \
    && apt-get install -y --no-install-recommends \
    ca-certificates curl wget git make gcc g++ build-essential python3 python-is-python3 \
    python3-setuptools python3-packaging pkg-config yasm nasm \
    cmake ninja-build meson autoconf automake autoconf-archive libtool mm-common \
    bison flex patch file gettext xz-utils zstd unzip zip libarchive-tools \
    ed nsis subversion ocaml-nox gperf texinfo \
    gcc-mingw-w64-x86-64-posix g++-mingw-w64-x86-64-posix \
    binutils-mingw-w64-x86-64 mingw-w64 mingw-w64-tools \
    && update-alternatives --set x86_64-w64-mingw32-gcc /usr/bin/x86_64-w64-mingw32-gcc-posix \
    && update-alternatives --set x86_64-w64-mingw32-g++ /usr/bin/x86_64-w64-mingw32-g++-posix
# Deliberately retain /var/lib/apt/lists: upstream's unmodified builder
# performs apt-get install as root and does not first refresh the index.
# All host packages it requests are already installed by this image.
"""


def _check_locked_msvcrt_toolchain_in_container() -> None:
    import re
    import subprocess
    import tempfile
    from pathlib import Path

    cc, cxx = 'x86_64-w64-mingw32-gcc', 'x86_64-w64-mingw32-g++'
    for compiler in (cc, cxx):
        version = subprocess.check_output([compiler, '-dumpfullversion'], text=True).strip()
        print(f'[cfaudio][win] compiler={compiler} version={version}', flush=True)
        if not re.match(r'^10(?:$|[.\-])', version):
            raise RuntimeError(f'Expected GCC 10 cross compiler; got {version}')
    details = subprocess.run([cc, '-v'], text=True, capture_output=True, check=True)
    if 'Thread model: posix' not in details.stderr + details.stdout:
        raise RuntimeError('Expected POSIX thread model')
    # Confirm the pinned compiler owns a real Windows libssp import archive.
    # GNU libtool's file-magic check is bypassed using -Xlinker -lssp in LIBS.
    _compiler_libssp_import_archive()
    # The probe belongs to the shared Builds mount, never an implicit /tmp.
    with tempfile.TemporaryDirectory(dir='/work') as temporary:
        source = Path(temporary) / 'probe.c'
        target = Path(temporary) / 'probe.exe'
        source.write_text('int main(void) { return 0; }\n')
        subprocess.run([cc, str(source), '-o', str(target)], check=True)
        imports = subprocess.check_output(
            ['x86_64-w64-mingw32-objdump', '-p', str(target)], text=True)
        # FLAC 1.3.2's fortified MinGW string.h calls __memset_chk.
        # Catch a missing/mismatched GCC libssp before compiling FLAC.
        fortified = Path(temporary) / 'fortify.c'
        fortified_exe = Path(temporary) / 'fortify.exe'
        fortified.write_text(
            '#include <stddef.h>\n'
            'extern void *__memset_chk(void *, int, size_t, size_t);\n'
            'int main(void) { char b[16]; return '
            '__memset_chk(b, 0, sizeof b, sizeof b) != b; }\n'
        )
        # Exercise precisely the link option supplied through upstream LIBS.
        subprocess.run([cc, str(fortified), '-o', str(fortified_exe),
                        '-Xlinker', '-lssp'], check=True)
        fortify_imports = subprocess.check_output(
            ['x86_64-w64-mingw32-objdump', '-p', str(fortified_exe)], text=True)
        if re.search(r'DLL Name:\s*(?:ucrtbase|api-ms-win-crt-[^\s]+)\.dll',
                     fortify_imports, re.IGNORECASE):
            raise RuntimeError('MinGW GCC 10 libssp probe linked against UCRT')
    if not re.search(r'DLL Name:\s*msvcrt\.dll', imports, re.IGNORECASE):
        raise RuntimeError('Compiler output does not import MSVCRT')
    if re.search(r'DLL Name:\s*(?:ucrtbase|api-ms-win-crt-[^\s]+)\.dll',
                 imports, re.IGNORECASE):
        raise RuntimeError('Compiler output unexpectedly imports UCRT')
    print('[cfaudio][win] VERIFIED GCC 10 POSIX/MSVCRT and libssp fortified-string link',
          flush=True)


def verify_locked_msvcrt_toolchain(built: Path, engine: str, environment: dict) -> None:
    """Verify the pinned compiler's actual PE imports without touching dependencies."""
    dependencies = built / 'Dependencies'
    dependencies.mkdir(parents=True, exist_ok=True)
    dockerfile = dependencies / 'Dockerfile.msvcrt-gcc10'
    if not dockerfile.exists() or dockerfile.read_text() != _MSVCRT_DOCKERFILE:
        dockerfile.write_text(_MSVCRT_DOCKERFILE)
    build_cached_image(engine=engine, image=_MSVCRT_IMAGE, dockerfile=dockerfile,
                       context=dependencies, platform='linux/amd64', architecture='amd64',
                       env=environment, label='Ardour pinned GCC 10/MSVCRT compiler')
    _run_container_python(engine, _MSVCRT_IMAGE, ((dependencies, '/work', False),),
                          environment, _check_locked_msvcrt_toolchain_in_container, {},
                          'Ardour GCC 10/MSVCRT PE ABI preflight')


def check_msvcrt_binary_closure(built: Path, engine: str, environment: dict) -> None:
    """Verify DLL import closure against installed Windows-target libraries."""
    _run_container_stage(engine, _MSVCRT_IMAGE,
                         ((built / "Dependencies", "/work", False),),
                         environment, "_stage_check_msvcrt_binary_closure", {},
                         "Ardour GCC 10/MSVCRT recursive PE import closure")




















def _verify_windows_dependencies_in_container() -> None:
    import os
    import re
    import subprocess
    import tempfile
    from pathlib import Path

    cc = 'x86_64-w64-mingw32-gcc'
    print('[cfaudio][win] Host architecture:', os.uname().machine, flush=True)
    print('[cfaudio][win] Cross compiler:',
          subprocess.check_output([cc, '--version'], text=True).splitlines()[0], flush=True)
    with tempfile.TemporaryDirectory(dir='/work') as temporary:
        source = Path(temporary) / 'toolchain-smoke.c'
        executable = Path(temporary) / 'toolchain-smoke.exe'
        source.write_text('int main(void) { return 0; }\n')
        subprocess.run([cc, '-o', str(executable), str(source)], check=True)
        imports = subprocess.check_output(
            ['x86_64-w64-mingw32-objdump', '-p', str(executable)], text=True)
    if not re.search(r'DLL Name:\s*msvcrt\.dll', imports, re.IGNORECASE):
        raise RuntimeError('Windows cross compiler output does not import MSVCRT')
    if re.search(r'DLL Name:\s*(?:ucrtbase|api-ms-win-crt-[^\s]+)\.dll',
                 imports, re.IGNORECASE):
        raise RuntimeError('Mixed MSVCRT/UCRT target is forbidden')
    prefix = '/work/UpstreamWindows-x64/mingw64'
    env = dict(os.environ)
    env.update(PKG_CONFIG_LIBDIR=f'{prefix}/lib/pkgconfig:{prefix}/share/pkgconfig',
               PKG_CONFIG_SYSTEM_LIBRARY_PATH='', PKG_CONFIG_SYSTEM_INCLUDE_PATH='',
               PKG_CONFIG_SYSROOT_DIR='')
    env.pop('PKG_CONFIG_PATH', None)
    missing = []
    for module in env['TARGET_MODULES'].split():
        if subprocess.run(['pkg-config', '--exists', module], env=env).returncode:
            print(f'[cfaudio][win] TARGET MISSING {module}', flush=True)
            missing.append(module)
            continue
        flags = subprocess.check_output(['pkg-config', '--cflags', '--libs', module],
                                        env=env, text=True)
        if any(token.startswith(('-I/usr/', '-L/usr/', '-I/opt/', '-L/opt/'))
               for token in flags.split()):
            raise RuntimeError(f'[cfaudio][win] INVALID host dependency path: {module} {flags}')
        version = subprocess.check_output(['pkg-config', '--modversion', module],
                                          env=env, text=True).strip()
        print(f'[cfaudio][win] TARGET READY {module} {version}', flush=True)
    if missing:
        raise RuntimeError(f'{len(missing)} Windows-target dependency modules missing: '
                           + ', '.join(missing))
    print('[cfaudio][win] Windows x64 toolchain and target dependencies ready', flush=True)


def verify_windows_dependencies(built: Path, engine: str, environment: dict) -> None:
    """Verify GCC/MSVCRT target ABI and required Windows pkg-config modules."""
    _run_container_python(engine, _MSVCRT_IMAGE,
                          ((built / 'Dependencies', '/work', False),), environment,
                          _verify_windows_dependencies_in_container,
                          {'TARGET_MODULES': ' '.join(_REQUIRED_TARGET_MODULES)},
                          'Ardour Windows cross-compile preflight')




















def _stage_check_msvcrt_binary_closure() -> None:
    """Execute this stage inside the existing Windows build container."""
    import collections, hashlib, json, os, pathlib, re, subprocess
    root = pathlib.Path('/work')
    prefix = root / 'UpstreamWindows-x64' / 'mingw64'
    manifest = root / 'msvcrt-approved-prefix.json'
    report_path = root / 'msvcrt-binary-closure.json'
    if not manifest.is_file() or not prefix.is_dir():
        raise RuntimeError('Approved MSVCRT prefix and manifest are required')
    state = json.loads(manifest.read_text())
    if state.get('toolchain') != 'GCC 10 POSIX / MSVCRT':
        raise RuntimeError('Approved prefix has the wrong compiler/runtime identity')
    # System API DLLs are provided by native Windows or Proton, never bundled.
    system = set(('advapi32', 'bcrypt', 'cfgmgr32', 'comctl32', 'comdlg32',
        'crypt32', 'd2d1', 'd3d11', 'd3d12', 'dbghelp', 'dinput8', 'dnsapi',
        'dsound', 'dwmapi', 'gdi32', 'gdiplus', 'hid', 'imm32', 'iphlpapi',
        'kernel32', 'kernelbase', 'mpr', 'msimg32', 'msvcrt', 'netapi32',
        'ntdll', 'ole32', 'oleaut32', 'opengl32', 'pdh', 'powrprof',
        'psapi', 'rpcrt4', 'secur32', 'setupapi', 'shell32', 'shlwapi',
        'strmiids', 'ucrtbase_FORBIDDEN', 'urlmon', 'user32', 'userenv',
        'usp10', 'uxtheme', 'version', 'winhttp', 'wininet', 'winmm',
        'winspool', 'ws2_32', 'wsock32', 'wtsapi32', 'xinput1_3'))
    def norm(name):
        return name.casefold().removesuffix('.dll')
    files = sorted(p for p in prefix.rglob('*') if p.is_file() and p.suffix.lower() in ('.dll', '.exe'))
    if not files:
        raise RuntimeError('No PE executables or DLLs in approved prefix')
    by_name = collections.defaultdict(list)
    for path in files:
        if path.suffix.lower() == '.dll':
            by_name[path.name.casefold()].append(path)
    # The GCC 10 toolchain supplies these during the Build package stage.
    # They are not Windows system DLLs, nor prebuilt MSYS2 dependencies.
    compiler_owned = {}
    for dll in ('libstdc++-6.dll', 'libgcc_s_seh-1.dll',
                'libwinpthread-1.dll', 'libssp-0.dll'):
        selected = subprocess.check_output(
            ['x86_64-w64-mingw32-g++', f'-print-file-name={dll}'], text=True,
        ).strip()
        path = pathlib.Path(selected)
        if not path.is_absolute() or not path.is_file():
            raise RuntimeError(f'GCC 10 compiler runtime missing: {dll}: {selected}')
        if 'pei-x86-64' not in subprocess.check_output(
                ['x86_64-w64-mingw32-objdump', '-f', str(path)], text=True):
            raise RuntimeError(f'GCC 10 compiler runtime has wrong PE architecture: {path}')
        compiler_owned[dll] = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                               'source': str(path)}
    missing = []
    external = collections.defaultdict(set)
    imports_by_file = {}
    for path in files:
        output = subprocess.run(['x86_64-w64-mingw32-objdump', '-p', str(path)],
                                capture_output=True, text=True)
        if output.returncode:
            raise RuntimeError(f'Cannot inspect PE imports: {path}: {output.stderr}')
        names = re.findall(r'^\s*DLL Name:\s*(\S+)', output.stdout, re.M | re.I)
        imports_by_file[str(path.relative_to(prefix))] = names
        for dll in names:
            key = dll.casefold()
            if re.match(r'(?:api-ms-win-crt-|ext-ms-win-crt-|ucrtbase\.dll)', key):
                missing.append({'consumer': str(path.relative_to(prefix)),
                                'dll': dll, 'reason': 'UCRT forbidden by GCC 10/MSVCRT lock'})
            elif key in compiler_owned:
                continue
            elif key in by_name:
                continue
            elif norm(key) in system or key.startswith(('api-ms-win-', 'ext-ms-win-')):
                external[key].add(str(path.relative_to(prefix)))
            else:
                missing.append({'consumer': str(path.relative_to(prefix)),
                                'dll': dll, 'reason': 'not bundled and not classified as Windows/Proton API'})
    report = {
        'schema': 'cfaudio.msvcrt.binary-closure.v1',
        'toolchain': 'GCC 10 POSIX / MSVCRT',
        'prefix': 'UpstreamWindows-x64/mingw64',
        'pe_file_count': len(files),
        'imports': imports_by_file,
        'compiler_runtime': compiler_owned,
        'runtime_provided': {k: sorted(v) for k,v in sorted(external.items())},
        'unresolved': missing,
        'binary_import_closure': not missing,
        'limitations': ['Does not verify imported export symbols',
                        'Does not inspect LoadLibrary dynamic dependencies',
                        'Proton runtime closure must be tested against installed CinterForgeOS Proton',
                        'Does not establish GCC 10 C++ ABI or patched-source closure'],
    }
    temporary = report_path.with_suffix('.json.partial')
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    os.replace(temporary, report_path)
    print(f'[cfaudio][win][msvcrt] PE closure: {len(files)} binaries, {len(missing)} unresolved imports; report={report_path}', flush=True)
    for entry in missing[:40]:
        print(f"[cfaudio][win][msvcrt] MISSING {entry['consumer']} -> {entry['dll']} ({entry['reason']})", flush=True)
    if missing:
        raise RuntimeError(f'MSVCRT binary closure incomplete: {len(missing)} missing imports')

















def _run_container_stage_main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("Container stage requires exactly one registered function name")
    stages = {
        "_stage_build_ardour_upstream_stack": _stage_build_ardour_upstream_stack,
        "_stage_approve_msvcrt_source_prefix": _stage_approve_msvcrt_source_prefix,
        "_stage_check_msvcrt_binary_closure": _stage_check_msvcrt_binary_closure,
        "_check_locked_msvcrt_toolchain_in_container": _check_locked_msvcrt_toolchain_in_container,
        "_verify_windows_dependencies_in_container": _verify_windows_dependencies_in_container,
    }
    try:
        stage = stages[sys.argv[2]]
    except KeyError as error:
        raise SystemExit(f"Unknown container stage: {sys.argv[2]}") from error
    stage()
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_container_stage_main() if _RUNNING_CONTAINER_STAGE else main())
