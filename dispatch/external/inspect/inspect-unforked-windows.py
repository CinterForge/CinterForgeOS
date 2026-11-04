#!/usr/bin/env python3
"""Snapshot original Windows publisher installs inside the CinterForgeOS FEX/Wine guest.

Host Applications is read-only; reports go to the separately writable Logs mount.
Never inspects or installs source-built CinterForge application distributions.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "baseline"))
import time

INSTALLERS = Path('/run/steamapps/common/CinterForge/Applications/UnforkedInstallers/Windows')
REPORTS = Path('/run/steamapps/common/CinterForge/Applications/Logs/UnforkedInspection/Windows')
WINE = Path(os.environ.get('CINTERFORGE_TEST_SCRIPTS_DIRECTORY', str(Path(__file__).resolve().parent.parent))) / 'wine/cinterforge-wine.sh'
PREFIXES = Path.home() / '.local/share/cinterforge/reference-wine'


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'baseline'))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'baseline'))
from filesystem_inventory import inventory, filesystem_delta


def snapshot(prefix):
    """Record the entire isolated Wine prefix, including registry and hashes."""
    result = inventory(prefix)
    if not result['exists']:
        # A fresh application has no prefix before its first installation.
        return {}
    if result['errors']:
        raise RuntimeError(
            f"Cannot trust Windows snapshot: {len(result['errors'])} paths were unreadable: "
            f"{list(result['errors'])[:5]}"
        )
    return result['entries']


def save_json(file, value):
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_name(file.name + '.tmp')
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    tmp.replace(file)


def execute(command, prefix, log):
    env = dict(os.environ, WINEPREFIX=str(prefix), WINEDEBUG='-all')
    with log.open('w') as out:
        out.write('Command: ' + ' '.join(map(str, command)) + '\n')
        out.flush()
        try:
            rc = subprocess.call([str(x) for x in command], env=env, stdout=out, stderr=subprocess.STDOUT)
        except OSError as exc:
            out.write('Execution failed: ' + str(exc) + '\n')
            rc = 127
        out.write('\nExit code: ' + str(rc) + '\n')
    return rc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('application', help='Original publisher application directory name')
    parser.add_argument('phase', choices=('before', 'install', 'after', 'launch', 'launchafter'))
    parser.add_argument('--installer', help='Exact downloaded EXE/MSI filename (required for install)')
    parser.add_argument('--exe', help='Installed Windows executable path (required for launch)')
    args = parser.parse_args()
    name = args.application
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 _.-]{0,99}', name) or name in ('.', '..'):
        parser.error('Unsafe application name')
    out = REPORTS / name
    prefix = PREFIXES / name
    out.mkdir(parents=True, exist_ok=True)
    if args.phase in ('install', 'launch'):
        if not WINE.is_file():
            parser.error('CinterForgeOS Wine/FEX runner missing: ' + str(WINE))
        if args.phase == 'install':
            if not args.installer or Path(args.installer).name != args.installer:
                parser.error('Install requires a single --installer filename')
            installer = INSTALLERS / name / args.installer
            if not installer.is_file() or installer.suffix.lower() not in ('.exe', '.msi'):
                parser.error('Original EXE/MSI installer not found: ' + str(installer))
            before = out / 'Before.json'
            if not before.is_file():
                parser.error('Capture before snapshot first; do not install without a baseline')
            if json.loads(before.read_text())['prefix'] != str(prefix):
                parser.error('Before.json refers to a different prefix')
            if prefix.exists() and snapshot(prefix) != json.loads(before.read_text())['filesystem']:
                parser.error('Wine prefix changed since baseline; recapture before installation')
            command = [WINE, 'msiexec', '/i', installer] if installer.suffix.lower() == '.msi' else [WINE, installer]
            rc = execute(command, prefix, out / 'Installer.log')
            save_json(out / 'InstallExecution.json', {'installer': str(installer), 'exitCode': rc,
                       'status': 'exited_successfully' if rc == 0 else 'failed', 'prefix': str(prefix)})
        else:
            if not args.exe:
                parser.error('Launch requires --exe with actual installed program path')
            if not (out / 'After.json').is_file():
                parser.error('Capture after-install snapshot first')
            rc = execute([WINE, args.exe], prefix, out / 'FirstLaunch.log')
            save_json(out / 'LaunchExecution.json', {'program': args.exe, 'exitCode': rc,
                       'status': 'exited_successfully' if rc == 0 else 'failed'})
        print(f'[unforked-win] {name} {args.phase}: exit={rc}; reports={out}', flush=True)
        return rc
    current = snapshot(prefix)
    label = {'before':'Before', 'after':'After', 'launchafter':'FirstLaunchAfter'}[args.phase]
    save_json(out / (label + '.json'), {'schemaVersion': 1, 'application': name, 'phase': args.phase,
              'prefix': str(prefix), 'filesystem': current})
    if args.phase != 'before':
        prior = 'Before' if args.phase == 'after' else 'After'
        original = out / (prior + '.json')
        if not original.is_file():
            parser.error(f'{prior}.json missing; capture that phase first')
        old = json.loads(original.read_text())['filesystem']
        delta = filesystem_delta({'entries': old}, {'entries': current})
        result = {'schemaVersion': 1, 'application': name, 'prefix': str(prefix),
                  'baseline': prior, 'phase': args.phase, 'filesystemDelta': delta,
                  'counts': {k: len(v) for k, v in delta.items()},
                  'registryFiles': ['system.reg', 'user.reg', 'userdef.reg']}
        save_json(out / ('InstallationDelta.json' if args.phase == 'after' else 'FirstLaunchDelta.json'), result)
        print(f'[unforked-win] {name} {args.phase}: {result["counts"]}; reports={out}', flush=True)
    else:
        for stale in ('After.json', 'InstallationDelta.json', 'FirstLaunchAfter.json', 'FirstLaunchDelta.json', 'InstallExecution.json', 'LaunchExecution.json'):
            (out / stale).unlink(missing_ok=True)
        print(f'[unforked-win] {name} baseline: {len(current)} entries; reports={out}', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
