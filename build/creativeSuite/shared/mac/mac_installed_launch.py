#!/usr/bin/env python3
"""Minimal Release GUI acceptance: launch the real Steam-installed macOS bundle.

This is intentionally a launch test only.  Semantic document/edit/export smoke
is a separate, later acceptance stage.  Never execute a Built/Staged copy.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import time



def _version_key(path: Path) -> tuple[int, ...]:
    if not re.fullmatch(r'\d{4}(?:\.\d+)*', path.name):
        raise ValueError(f'Not an installed product version: {path}')
    return tuple(map(int, path.name.split('.')))


def _processes() -> dict[int, str]:
    output = subprocess.check_output(['/bin/ps', '-axo', 'pid=,command='], text=True)
    processes = {}
    for line in output.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) == 2 and fields[0].isdigit():
            processes[int(fields[0])] = fields[1]
    return processes



def _visible_window_pids(pids: set[int]) -> set[int]:
    """Find real on-screen GUI windows using macOS CoreGraphics, not `open` status."""
    if not pids:
        return set()
    swift = r"""
import CoreGraphics
import Foundation
let wanted: Set<Int> = [__TARGET_PIDS__]
let windows = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []
for window in windows {
    guard let pid = window[kCGWindowOwnerPID as String] as? Int,
          wanted.contains(pid),
          let layer = window[kCGWindowLayer as String] as? Int,
          layer == 0,
          let bounds = window[kCGWindowBounds as String] as? [String: Any],
          let width = bounds["Width"] as? NSNumber,
          let height = bounds["Height"] as? NSNumber,
          width.doubleValue > 100, height.doubleValue > 100 else { continue }
    print(pid)
}
"""
    # A nonzero Swift exit is a test infrastructure error, never a GUI PASS.
    # `swift -e SCRIPT -- PID` interprets PID as a source filename with
    # some Swift driver versions. Embed validated integer PIDs in the script.
    swift = swift.replace('__TARGET_PIDS__', ', '.join(str(pid) for pid in sorted(pids)))
    proc = subprocess.run(
        ['/usr/bin/swift', '-e', swift],
        text=True, capture_output=True, timeout=45,
    )
    if proc.returncode:
        raise RuntimeError(f'CoreGraphics window inspection failed: {proc.stderr.strip()}')
    return {int(line) for line in proc.stdout.splitlines() if line.strip().isdigit()}

def _record_launch_failure_diagnostics(log: Path, started_at: float, bundle: Path, app: str) -> None:
    """Preserve macOS evidence for a launch that vanished before showing a window."""
    with log.open('a', encoding='utf-8') as stream:
        stream.write('\n=== macOS launch failure diagnostics ===\n')
        stream.write(f'bundle={bundle}\n')
        reports = Path.home() / 'Library/Logs/DiagnosticReports'
        if reports.is_dir():
            recent = sorted(
                (item for item in reports.iterdir()
                 if item.is_file()
                 and (app.lower() in item.name.lower() or bundle.stem.lower() in item.name.lower())
                 and item.stat().st_mtime >= started_at - 10),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
            for report in recent[:5]:
                stream.write(f'crash_report={report}\n')
                # Keep the authoritative full report on disk and include its
                # opening diagnosis without flooding the per-app result log.
                with report.open('r', encoding='utf-8', errors='replace') as source:
                    stream.write(source.read(16000))
                stream.write('\n')
        try:
            result = subprocess.run(
                ['/usr/bin/log', 'show', '--last', '2m', '--style', 'compact',
                 '--predicate',
                 'process == "amfid" OR process == "syspolicyd" '
                 'OR eventMessage CONTAINS[c] "signature" '
                 'OR eventMessage CONTAINS[c] "dyld"'],
                text=True, capture_output=True, timeout=25,
            )
            stream.write(f'unified_log_exit={result.returncode}\n')
            stream.write((result.stdout + result.stderr)[-32000:])
            stream.write('\n')
        except (OSError, subprocess.TimeoutExpired) as error:
            stream.write(f'unified_log_error={error}\n')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('app', help='CinterForge application identifier')
    parser.add_argument('--version', default=os.environ.get('CINTER_INSTALL_VERSION') or os.environ.get('CINTER_APP_VERSION'))
    parser.add_argument('--settle-seconds', type=float, default=15.0)
    args = parser.parse_args()
    if sys.platform != 'darwin':
        raise RuntimeError('Native macOS installed launch test requires macOS')
    suite = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(suite))
    from lifecycle import steamapps, product_name  # Use the Stage identity and Steam library.
    if not re.fullmatch(r'[a-z][a-z0-9-]*', args.app):
        raise RuntimeError(f'Invalid application identifier: {args.app!r}')

    install_root = steamapps() / 'common' / 'CinterForge' / 'Applications' / 'macOS' / product_name(args.app)
    if not install_root.is_dir():
        raise RuntimeError(f'Application has not been staged: {install_root}')
    if args.version:
        if not re.fullmatch(r'\d{4}(?:\.\d+)*', args.version):
            raise RuntimeError(f'Invalid installed product version: {args.version!r}')
        version = args.version
    else:
        versions = [p for p in install_root.iterdir() if p.is_dir() and re.fullmatch(r'\d{4}(?:\.\d+)*', p.name)]
        if not versions:
            raise RuntimeError(f'No installed versions in {install_root}')
        version = max(versions, key=_version_key).name
    installed = install_root / version
    if not installed.is_dir():
        raise RuntimeError(f'Requested version is not installed: {installed}')
    bundles = sorted(installed.glob('*.app'))
    if len(bundles) != 1:
        raise RuntimeError(f'Expected one installed .app in {installed}; got {bundles}')
    bundle = bundles[0]
    info_path = bundle / 'Contents/Info.plist'
    if not info_path.is_file():
        raise RuntimeError(f'Installed macOS bundle has no Info.plist: {bundle}')
    with info_path.open('rb') as stream:
        info = plistlib.load(stream)
    name = info.get('CFBundleExecutable')
    if not isinstance(name, str) or not name or '/' in name or name in ('.', '..'):
        raise RuntimeError(f'Missing/invalid CFBundleExecutable in {info_path}')
    executable = bundle / 'Contents/MacOS' / name
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise RuntimeError(f'Installed app executable missing/not executable: {executable}')
    results = installed / 'Tests' / 'Results'
    results.mkdir(parents=True, exist_ok=True)
    log = results / 'LaunchTest.log'
    report = results / 'LaunchTest.json'
    report.unlink(missing_ok=True)
    before = _processes()
    started_at = time.time()
    print(f'[mac launch] Opening installed {args.app}: {bundle}', flush=True)
    launch = subprocess.run(['/usr/bin/open', '-n', '-a', str(bundle)], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
    log.write_text(f'command=/usr/bin/open -n -a {bundle}\nexit={launch.returncode}\n{launch.stdout}', encoding='utf-8')
    if launch.returncode:
        raise RuntimeError(f'Launch Services failed ({launch.returncode}); see {log}')
    # The launcher may exec the actual application binary under Resources/bin.
    # A PID from `open` is not sufficient: GIMP previously crashed during dyld
    # initialization while this test incorrectly reported PASS.
    prefix = str(bundle / 'Contents') + '/'
    deadline = time.monotonic() + max(5.0, args.settle_seconds)
    observed: set[int] = set()
    visible: set[int] = set()
    while time.monotonic() < deadline:
        now = _processes()
        candidates = {
            pid for pid, command in now.items()
            if pid not in before and prefix in command
        }
        observed.update(candidates)
        if candidates:
            visible = _visible_window_pids(candidates)
            if visible:
                # Do not accept a window from an already-terminated process.
                current = _processes()
                visible.intersection_update(current)
                if visible:
                    break
        time.sleep(0.5)
    if not visible:
        current = _processes()
        remaining = sorted(observed & current.keys())
        detail = (f'No visible installed GUI window after {args.settle_seconds:g}s; '
                  f'observed PIDs={sorted(observed)}, remaining PIDs={remaining}')
        with log.open('a', encoding='utf-8') as stream:
            stream.write(detail + '\n')
            for pid in remaining:
                stream.write(f'pid={pid} command={current[pid]}\n')
        _record_launch_failure_diagnostics(log, started_at, bundle, args.app)
        raise RuntimeError(f'{detail}; macOS crash and dyld diagnostics saved to {log}')
    surviving = sorted(visible)
    result = {'application': args.app, 'platform': 'mac', 'configuration': 'Release',
              'kind': 'installed-GUI-visible-window', 'version': version, 'bundle': str(bundle),
              'executable': str(executable), 'pids': surviving, 'passed': True,
              'checked_at': datetime.now(timezone.utc).isoformat()}
    report.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(f'[mac launch] PASS {args.app}/{version}; installed process(es) {surviving}; visible GUI window confirmed', flush=True)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f'[mac launch] FAIL: {exc}', file=sys.stderr, flush=True)
        sys.exit(1)
