#!/usr/bin/env python3
"""Standalone entrypoint for the original update-data Creative Suite smoke suite.

Run without update-data's acquisition/indexing workflow. Uses the exact same
linux_smoke_tests module; no parallel test implementation.
"""
from __future__ import annotations
import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

SUITE = Path(__file__).resolve().parent

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", type=Path, default=SUITE.parents[2])
    ap.add_argument("--guest-runner", type=Path, default=SUITE / "apps/cinterforge-os/linux/linux_guest.py")
    ap.add_argument("--application", help="Existing application name in linux_smoke_tests.OUTPUTS")
    ap.add_argument("--prepare", action="store_true", help="Install/prepare packages rather than reuse them")
    ap.add_argument("--installed-tests", type=Path, help="Installed application Tests directory receiving this run's real smoke artifacts")
    args = ap.parse_args()
    from shared.linux.linux_smoke_tests import OUTPUTS
    if args.application and args.application not in OUTPUTS:
        ap.error(f"unknown existing smoke application {args.application!r}; valid: {', '.join(OUTPUTS)}")
    command = [sys.executable, "-u", str(SUITE / "shared/linux/linux_smoke_tests.py"),
               "--repo-root", str(args.repo_root.resolve()), "--guest-runner", str(args.guest_runner.resolve())]
    if not args.prepare:
        command.append("--skip-prepare")
    if args.application:
        command.extend(["--application", args.application])
    if args.installed_tests is None:
        return subprocess.call(command)
    if not args.application:
        ap.error('--installed-tests requires one --application; never mix application results')
    tests = args.installed_tests.resolve()
    if not tests.is_dir() or not (tests / 'Examples').is_dir():
        ap.error(f'Stage the installed application tests first: {tests}')
    results = tests / 'Results'
    results.mkdir(exist_ok=True)
    # Overwrite stable names; do not accumulate timestamped diagnostics or
    # accept older results as proof of the current invocation.
    for existing in results.glob('SmokeTest.*'):
        if existing.is_file():
            existing.unlink()
    completed = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT)
    (results / 'SmokeTest.log').write_text(completed.stdout, encoding='utf-8')
    report_path = None
    try:
        from cf.io.CinterIO import cinterforgeBuildsRootPath
        report_path = cinterforgeBuildsRootPath(args.repo_root.resolve()) / 'CreativeSuite/Tests/SmokeTestReport.json'
    except (ImportError, OSError):
        pass
    entry = None
    if report_path and report_path.is_file():
        try:
            report = json.loads(report_path.read_text(encoding='utf-8'))
            entry = report.get('applications', {}).get(args.application)
        except (ValueError, OSError):
            pass
    passed = completed.returncode == 0 and isinstance(entry, dict) and entry.get('ok') is True
    (results / 'SmokeTest.json').write_text(json.dumps({
        'application': args.application,
        'environment': 'Ubuntu AMD64 reference document smoke',
        'installed_test_directory': str(tests),
        'test_uses_installed_binary': False,
        'passed': passed,
        'exit_code': completed.returncode,
        'validation': entry,
        'completed_utc': datetime.now(timezone.utc).isoformat(),
    }, indent=2) + '\n', encoding='utf-8')
    if completed.stdout:
        print(completed.stdout, end='', flush=True)
    return 0 if passed else (completed.returncode or 1)

if __name__ == "__main__":
    raise SystemExit(main())
