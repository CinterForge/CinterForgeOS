#!/usr/bin/env python3
"""CFOS-owned Creative Suite application lifecycle for macOS, Linux and Windows.

All source is authored in CFOS. Generated state and operation logs belong in
sibling Builds. The CinterForgeServer shell entry forwards application commands
here without copying app recipes or implementing a second product lifecycle.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

SUITE = Path(__file__).resolve().parent
CFOS = SUITE.parents[1]
WORKSPACE = CFOS.parent
BUILDS = WORKSPACE / 'Builds'
SERVER = WORKSPACE / 'CinterForgeServer'
PHASES = ('setup', 'build', 'stage', 'test', 'publish', 'inspect', 'all')
PLATFORMS = ('mac', 'linux', 'win')


def application(name: str) -> Path:
    if not name or name in ('.', '..') or Path(name).name != name or '/' in name or '\\' in name:
        raise ValueError(f'Invalid Creative Suite application key: {name!r}')
    directory = SUITE / 'apps' / name
    if not directory.is_dir():
        raise ValueError(f'Unknown CFOS Creative Suite application: {name!r}')
    return directory


def run(argv: list[str], env: dict[str, str], dry_run: bool = False) -> None:
    print('[CFOS Creative Suite] ' + ' '.join(argv), flush=True)
    if not dry_run:
        subprocess.run(argv, check=True, env=env, cwd=WORKSPACE)


def call_script(base: Path, phase: str, env: dict[str, str], dry_run: bool, extra: list[str] | None = None) -> None:
    extra = extra or []
    # Native Windows uses the authored PowerShell adapter; Unix platform builds
    # use the authored shell adapter. Never interpret PowerShell as Bash.
    ps = base / (phase + '.ps1')
    sh = base / (phase + '.sh')
    if env['CINTER_TARGET_PLATFORM'] == 'win' and ps.is_file():
        shell = shutil.which('pwsh') or shutil.which('powershell')
        if not shell and not dry_run:
            raise RuntimeError(f'Windows adapter requires PowerShell: {ps}')
        run([shell or 'pwsh', '-NoProfile', '-File', str(ps), *extra], env, dry_run)
    elif sh.is_file():
        run(['bash', str(sh), *extra], env, dry_run)
    else:
        raise RuntimeError(f'No {phase} adapter found under {base}')


def selected_version(app: str, platform: str, env: dict[str, str], dry_run: bool) -> str:
    current = BUILDS / 'CreativeSuite/Built' / app / platform / 'CURRENT_VERSION'
    if current.is_file():
        return current.read_text().strip()
    if dry_run:
        return '2026'
    completed = subprocess.run(
        [sys.executable, str(SUITE / 'lifecycle.py'), 'select-build-version', app, platform],
        cwd=WORKSPACE, env=env, text=True, stdout=subprocess.PIPE, check=True,
    )
    version = completed.stdout.strip().splitlines()[-1]
    if not version or any(c not in '0123456789.' for c in version):
        raise RuntimeError(f'Invalid product version emitted by Creative Suite lifecycle: {version!r}')
    return version


def phase(action: str, app: str, platform: str, configuration: str, dry_run: bool) -> None:
    app_dir = application(app) / platform
    if not app_dir.is_dir():
        raise RuntimeError(f'{app}/{platform} has no authored CFOS application recipe')
    if configuration not in ('Debug', 'Release', 'New'):
        raise ValueError(f'Unsupported configuration: {configuration}')
    if not (SERVER / 'cf/io/CinterIO.py').is_file():
        raise RuntimeError('CinterForgeServer/cf is required as the shared workspace IO authority')
    env = dict(os.environ)
    env['PYTHONPATH'] = str(SERVER) + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    env['CINTER_TARGET_PLATFORM'] = platform
    env['CINTER_BUILD_CONFIGURATION'] = configuration
    env['CINTER_SCRIPTS_ROOT'] = str(SUITE)
    env['CINTER_HOPE_ROOT'] = str(SERVER)
    env['CINTER_REPO_ROOT'] = str(SERVER)
    env['CINTER_SERVER_ROOT'] = str(SERVER)
    env['CINTER_BUILDS_ROOT'] = str(BUILDS)
    if action == 'all':
        for subphase in ('setup', 'build', 'stage', 'test'):
            phase(subphase, app, platform, configuration, dry_run)
        return
    if action == 'inspect':
        run([sys.executable, str(SUITE / 'shared/source/repository_inspection.py'),
             '--app', app, '--source-root', str(WORKSPACE),
             '--builds-root', str(BUILDS / 'CreativeSuite'),
             '--inspection-root', str(BUILDS / 'CreativeSuite/Inspection')], env, dry_run)
        return
    if action == 'setup':
        command = [sys.executable, str(SUITE / 'shared/build/setup_toolchain.py'), app, platform, configuration]
        if dry_run:
            command.append('--dry-run')
        run(command, env, dry_run)
        return
    version = selected_version(app, platform, env, dry_run)
    env['CINTER_INSTALL_VERSION'] = version
    built = BUILDS / 'CreativeSuite/Built' / app / platform / version
    env['CINTER_BUILT_ROOT'] = str(built)
    env['CINTER_APP_VERSION'] = version
    lifecycle = [sys.executable, str(SUITE / 'lifecycle.py')]
    recipe = [sys.executable, str(SUITE / 'recipe.py')]
    if action == 'build':
        run([*recipe, 'build-start', app, platform, '--version', version], env, dry_run)
        try:
            call_script(app_dir, 'build', env, dry_run)
            run([*lifecycle, 'package', app, platform, '--version', version], env, dry_run)
        except (OSError, subprocess.CalledProcessError, RuntimeError):
            run([*recipe, 'build-failure', app, platform, '--version', version], env, dry_run)
            raise
        run([*recipe, 'build-success', app, platform, '--version', version], env, dry_run)
        if not dry_run:
            (BUILDS / 'CreativeSuite/Built' / app / platform).mkdir(parents=True, exist_ok=True)
            (BUILDS / 'CreativeSuite/Built' / app / platform / 'CURRENT_VERSION').write_text(version + '\n')
    elif action == 'stage':
        run([*lifecycle, 'stage', app, platform, '--version', version], env, dry_run)
    elif action == 'test':
        script = app_dir / 'test.sh'
        if script.is_file():
            call_script(app_dir, 'test', env, dry_run, ['headless' if platform == 'linux' else configuration])
        elif (SUITE / 'apps' / app / 'test/test.sh').is_file():
            fallback = SUITE / 'apps' / app / 'test'
            run(['bash', str(fallback / 'test.sh'), platform,
                 'headless' if platform == 'linux' else configuration], env, dry_run)
        elif platform == 'mac':
            run([sys.executable, str(SUITE / 'shared/mac/mac_installed_launch.py'), app], env, dry_run)
        elif platform == 'linux':
            run([sys.executable, str(SUITE / 'shared/linux/headless_installed_smoke.py'),
                 app, '--version', version], env, dry_run)
        else:
            raise RuntimeError(f'{app}/{platform}: missing native installed-image test; cannot claim PASS')
    elif action == 'publish':
        call_script(app_dir, 'publish', env, dry_run)
    else:
        raise ValueError(f'Unknown phase: {action}')


def main() -> int:
    parser = argparse.ArgumentParser(description='CFOS-owned Creative Suite application lifecycle')
    parser.add_argument('action', choices=PHASES)
    parser.add_argument('app')
    parser.add_argument('platform', choices=PLATFORMS)
    parser.add_argument('configuration', nargs='?', default='Release', choices=('Debug', 'Release', 'New'))
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    try:
        phase(args.action, args.app, args.platform, args.configuration, args.dry_run)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f'[CFOS Creative Suite] ERROR: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
