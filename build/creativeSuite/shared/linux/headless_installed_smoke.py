#!/usr/bin/env python3
"""Execute installed Linux AMD64 application smoke tests in headless CFOS Ubuntu.

No SteamOS, FEX, display server, or GUI session is involved. Both macOS build
hosts use the same installed Ubuntu rootfs and managed Docker/Colima backend.
"""
from __future__ import annotations
import argparse
import json
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys

SUITE = Path(__file__).resolve().parents[2]
OS_LINUX = SUITE / 'apps/cinterforge-os/linux'
sys.path.insert(0, str(OS_LINUX))
sys.path.insert(0, str(SUITE))
from managed_backend import ensure_backend, source_directory  # noqa: E402
from ubuntu_pipeline import paths as ubuntu_paths, ARCHIVE as UBUNTU_ARCHIVE  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('application')
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--exe', help='Exact executable relative to installed application')
    selection.add_argument('--exe-glob', help='Glob matching exactly one executable relative to installed application')
    parser.add_argument('--version', required=True)
    args = parser.parse_args()
    cinter = Path(os.environ['CINTER_HOPE_ROOT']).resolve()
    # Share the exact installed-distro resolver used by `Cinter test cinterforge-os linux`.
    # Ubuntu is installed beside the other Creative Suite apps under the actual Steam library.
    distro = ubuntu_paths(cinter)[1] / UBUNTU_ARCHIVE
    if not distro.is_file():
        raise RuntimeError(f'Installed CFOS Ubuntu rootfs missing: {distro}; stage cinterforge-os linux first')
    # The same persistent installed-app contract is used by both build hosts.
    # Match the existing Creative Suite Stage contract, not a guessed path.
    from lifecycle import installation_directory
    installation = installation_directory(args.application, 'linux') / args.version
    if not installation.is_dir():
        raise RuntimeError(f'Installed Linux application missing: {installation}; stage it before Debug testing')
    tests = installation / "Tests"
    launcher = tests / "RunSmokeTest.sh"
    if not launcher.is_file() or not (tests / "Examples").is_dir():
        raise RuntimeError(f"Installed test content missing from Stage: {tests}")
    results = tests / "Results"
    results.mkdir(parents=True, exist_ok=True)
    # These files describe the latest test, never accumulate per-run directories.
    result_json = results / "SmokeTest.json"
    result_log = results / "SmokeTest.log"
    # The application, not an arbitrary executable-name prefix, defines its
    # headless test target. Ardour ships several separate `ardour*` utilities.
    # Select its actual versioned engine binary, just as the Windows GUI
    # smoke does; other applications must declare their own test launcher.
    executable_patterns = {'cfaudio': 'lib/ardour9/ardour-[0-9]*', 'ardour': 'lib/ardour9/ardour-[0-9]*', 'blender': 'blender'}
    pattern = args.exe or args.exe_glob or executable_patterns.get(args.application)
    if not pattern:
        raise RuntimeError(
            f'No Linux Debug executable contract for {args.application}; '
            'provide --exe or --exe-glob in the application test configuration')
    relative = Path(pattern)
    if relative.is_absolute() or '..' in relative.parts:
        raise RuntimeError(f'Unsafe installed executable specification: {pattern}')
    if args.exe:
        matches = [installation / relative] if (installation / relative).is_file() else []
    else:
        matches = sorted(p for p in installation.glob(pattern) if p.is_file() and os.access(p, os.X_OK))
    if len(matches) != 1:
        raise RuntimeError(
            f'Expected exactly one installed Linux Debug executable matching {pattern!r} '
            f'in {installation}; matches: {matches}')
    binary = matches[0]
    relative_binary = binary.relative_to(installation).as_posix()
    # Ardour's engine is installed with private shared objects rather than into
    # Ubuntu's system library search path. Waf's install prefix contains those
    # libraries, but executing the engine directly needs the same library path
    # that Ardour's bin/ardour9 launcher normally prepares. Validate the actual
    # installed payload and use its paths; never borrow build-tree libraries.
    library_paths: list[str] = []
    if args.application in ('cfaudio', 'ardour'):
        shared = sorted(p for p in installation.rglob('libardourcp.so*') if p.is_file())
        if not shared:
            raise RuntimeError(
                f'Installed Ardour is incomplete: libardourcp.so is missing from {installation}')
        for directory in (installation / 'lib/ardour9', installation / 'lib',
                          *(p.parent for p in shared)):
            if directory.is_dir():
                guest_directory = '/installed/' + directory.relative_to(installation).as_posix()
                if guest_directory not in library_paths:
                    library_paths.append(guest_directory)
    runner, env = ensure_backend(source_directory(cinter))
    print(f'[Linux Debug headless] CFOS Ubuntu={distro} app={binary}', flush=True)
    # Import the installed distro, NOT a mutable development image.
    with distro.open('rb') as stream:
        result = subprocess.run([runner, 'image', 'import', '--platform', 'linux/amd64', '-'],
                                stdin=stream, env=env, text=True, capture_output=True, check=True)
    image = result.stdout.strip()
    if not image.startswith('sha256:'):
        raise RuntimeError(f'Unexpected Docker image import result: {result.stdout!r} {result.stderr!r}')
    actual = subprocess.check_output([runner, 'image', 'inspect', '--format', '{{.Os}}/{{.Architecture}}', image],
                                     text=True, env=env).strip()
    if actual != 'linux/amd64':
        raise RuntimeError(f'Expected Ubuntu linux/amd64, got {actual}')
    # Inspect the real installed ELF and execute a non-graphical application
    # version probe; a successful "file" or "ldd" alone is not a smoke pass.
    script = ('set -eu; test "$(dpkg --print-architecture)" = amd64; '
              'test -z "${DISPLAY:-}"; test -z "${WAYLAND_DISPLAY:-}"; '
              'file -b "$1" | grep -q "ELF 64-bit.*x86-64"; '
              'timeout 40 "$1" --version')
    if library_paths:
        print(f'[Linux Debug headless] Installed application library path: {":".join(library_paths)}', flush=True)
    command = [runner, 'run', '--rm', '--pull=never', '--platform', 'linux/amd64',
                    '--network', 'none', '--mount', f'type=bind,source={installation},target=/installed,readonly',
                    '--env', 'DISPLAY=', '--env', 'WAYLAND_DISPLAY=',
                    *(['--env', f'LD_LIBRARY_PATH={":".join(library_paths)}'] if library_paths else []), image,
                    '/bin/sh', '-ec', script, 'headless-check', '/installed/' + relative_binary]
    # Tests and inputs are read from the staged /installed/Tests tree, not the
    # update-data workspace. Keep test results on the persistent host install.
    completed = subprocess.run(command, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    result_log.write_text(completed.stdout, encoding='utf-8')
    result_json.write_text(json.dumps({
        'application': args.application, 'version': args.version,
        'platform': 'Linux', 'mode': 'Debug', 'environment': 'Ubuntu AMD64 headless',
        'installed': str(installation), 'test_content': str(tests),
        'executable': relative_binary, 'exit_code': completed.returncode,
        'passed': completed.returncode == 0,
        'completed_utc': datetime.now(timezone.utc).isoformat(),
    }, indent=2) + '\n', encoding='utf-8')
    if completed.stdout:
        print(completed.stdout, end='', flush=True)
    if completed.returncode:
        raise RuntimeError(f'Installed smoke exited {completed.returncode}; see {result_log}')
    print('[Linux Debug headless] PASS: installed AMD64 application executed inside headless CFOS Ubuntu', flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f'[Linux Debug headless] FAIL: {error}', file=sys.stderr)
        raise SystemExit(1)
