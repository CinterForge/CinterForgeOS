#!/usr/bin/env python3
"""Read-only CinterForgeOS reference environment baselines (not app installations)."""
import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

# Environment baselines are shared artifacts. Save them in the persistent
# Steam library so they can be inspected from macOS and from CFOS Konsole.
# If the shared directory is not writable, report the actual failure rather
# than silently saving a different copy inside the guest home directory.
steam_library_directory = Path('/run/steamapps/common/CinterForge')
BASELINE_DIRECTORY = steam_library_directory / 'Applications' / 'Logs' / 'EnvironmentBaselines'
FEX = Path('/usr/share/cinterforge/fex')

def command(*args):
    try:
        r = subprocess.run(args, text=True, capture_output=True, timeout=30)
        return {'exitCode': r.returncode, 'stdout': r.stdout[:100000], 'stderr': r.stderr[:20000]}
    except (OSError, subprocess.TimeoutExpired) as e:
        return {'error': str(e)}

from filesystem_inventory import inventory


# The host Steam library is deliberately excluded: it contains installers,
# previous reports, and applications unrelated to the guest OS installation.
# Kernel pseudo-filesystems are not meaningful installation footprints.
GUEST_EXCLUSIONS = (
    '/proc', '/sys', '/dev', '/run', '/tmp', '/mnt', '/media',
)

# Inventory the separately mounted operating-system trees explicitly.
# A root-device-only walk cannot see these mounts, even when it reports no
# errors. These are OS filesystems, not the separately inventoried FEX rootfs.
GUEST_MOUNT_ROOTS = ('/usr', '/etc', '/var', '/home', '/root', '/srv')


def authorize_guest_inventory():
    """Ask once in Konsole for the existing sudo authorization.

    No password is read or stored by Cinter; sudo prompts directly on the TTY.
    """
    if not sys.stdin.isatty():
        raise RuntimeError(
            "Protected SteamOS files require root access. Run Cinter baseline all "
            "interactively from Konsole so sudo can request authorization."
        )
    print("[baseline] SteamOS protected files require sudo access; "
          "sudo may ask for your password in Konsole.", flush=True)
    try:
        result = subprocess.run(["sudo", "-v"], timeout=120)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"SteamOS sudo authorization failed: {error}") from error
    if result.returncode:
        raise RuntimeError(
            "SteamOS sudo authorization was denied; Linux baseline remains incomplete."
        )


def privileged_guest_inventory(location):
    """Collect a protected OS mount via the same external script as root.

    sudo -n uses authorization obtained by authorize_guest_inventory().
    A missing privilege grant is an explicit error, never a silent omission.
    """
    program = [
        'sudo', '-n', sys.executable, str(Path(__file__).resolve()),
        '--inventory-root', location,
    ]
    print(f'[baseline] Privileged scan: {location} (sudo -n)', file=sys.stderr, flush=True)
    try:
        result = subprocess.run(
            program, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=3600,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f'Privileged inventory of {location} failed: {error}') from error
    if result.returncode != 0:
        raise RuntimeError(
            f'Privileged inventory of {location} failed (exit {result.returncode}): '
            f'{result.stderr.strip()[-1500:]}. '
            'SteamOS sudo authorization is unavailable or expired. '
            'No OS permissions were changed and no complete baseline was claimed.'
        )
    try:
        snapshot = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f'Invalid privileged inventory JSON for {location}') from error
    if snapshot.get('root') != location:
        raise RuntimeError(f'Privileged inventory returned wrong root for {location}')
    return snapshot


def guest_mount_inventories():
    snapshots = {}
    authorized = os.geteuid() == 0
    for location in GUEST_MOUNT_ROOTS:
        path = Path(location)
        if not path.is_dir():
            raise RuntimeError(f'Expected guest OS filesystem missing: {path}')
        # Try unprivileged first; only request privileged inventory when
        # errors show that normal access cannot provide a complete snapshot.
        snapshot = inventory(path, excluded_paths=GUEST_EXCLUSIONS)
        if snapshot['errorCount'] and os.geteuid() != 0:
            print(f'[baseline] {location}: retrying {snapshot["errorCount"]} '
                  'protected paths with sudo -n', file=sys.stderr, flush=True)
            if not authorized:
                authorize_guest_inventory()
                authorized = True
            snapshot = privileged_guest_inventory(location)
        snapshots[location] = snapshot
    return snapshots


def save(name, data):
    path = BASELINE_DIRECTORY / name / 'Baseline.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.json.tmp')
    temp.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')
    temp.replace(path)
    print(f'[baseline] {name}: {path}', flush=True)

def ensure_complete(snapshot, label):
    if snapshot['errorCount']:
        print(
            f'[baseline] WARNING: {label} contains {snapshot["errorCount"]} unreadable paths. '
            'See errors in Baseline.json; this capture is incomplete.',
            file=sys.stderr,
        )
    return snapshot['errorCount'] == 0


def fex_executable_metadata():
    # The FEX entrypoint runs an x86 program; --version is interpreted as a
    # program name and incorrectly reports `--version: command not found`.
    # Record the exact executable identity without executing a bogus command.
    executable = Path('/usr/bin/FEX')
    from filesystem_inventory import describe_path
    if not executable.is_file():
        raise RuntimeError(f'FEX executable missing: {executable}')
    return {'path': str(executable), **describe_path(executable)}


def linux():
    root = FEX / 'RootFS'
    if not root.is_dir():
        raise RuntimeError(f'FEX rootfs not found: {root}')
    print('[baseline] Linux collector: protected mounts use interactive sudo authorization when needed', flush=True)
    data = {'schemaVersion': 2, 'environment': 'Linux-FEX', 'architecture': platform.machine(),
            'osRelease': Path('/etc/os-release').read_text() if Path('/etc/os-release').exists() else '',
            'fexExecutable': fex_executable_metadata(), 'rootfs': inventory(root),
            'guestFilesystem': inventory('/', excluded_paths=GUEST_EXCLUSIONS),
            'guestMountedFilesystems': guest_mount_inventories(),
            'sharedInstallers': Path('/run/steamapps/common/CinterForge/Applications/UnforkedInstallers/Linux').is_dir()}
    rootfs_complete = ensure_complete(data['rootfs'], 'Linux FEX rootfs')
    guest_complete = ensure_complete(data['guestFilesystem'], 'SteamOS guest')
    mounted_complete = all(
        ensure_complete(snapshot, f'SteamOS mounted {location}')
        for location, snapshot in data['guestMountedFilesystems'].items()
    )
    data['complete'] = rootfs_complete and guest_complete and mounted_complete
    save('Linux', data)
    if not data['complete']:
        raise RuntimeError('Linux inventory is incomplete; see saved Baseline.json errors')

def initialize_reference_wine_prefix(wine, prefix):
    """Initialize an otherwise pristine reference prefix using installed Wine.

    Do not borrow a publisher application's prefix or alter the Wine payload.
    The baseline must record an actual initialized state, not an empty folder.
    """
    if (prefix / 'system.reg').is_file() and (prefix / 'user.reg').is_file():
        return {'state': 'already-initialized'}
    if prefix.exists() and any(prefix.iterdir()):
        raise RuntimeError(f'Reference Wine prefix is partially initialized: {prefix}')
    prefix.parent.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ, WINEPREFIX=str(prefix), WINEARCH='win64',
                       WINEDEBUG='-all', WINEDLLOVERRIDES='mscoree,mshtml=')
    # Wine can spawn service processes which inherit stdout/stderr. Using
    # capture_output=True keeps communicate() waiting for those descendant
    # pipe handles even after wineboot itself exits. Send output to a regular
    # log file instead: subprocess.run then waits only for the wineboot PID.
    log_path = BASELINE_DIRECTORY / 'Windows' / 'WineInitialization.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open('ab') as log:
            process = subprocess.run(
                [str(wine), 'wineboot', '--init'], env=environment,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                timeout=180,
            )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(
            f'Reference Wine initialization failed: {error}; inspect {log_path}'
        ) from error
    if process.returncode != 0:
        raise RuntimeError(
            f'Reference Wine initialization exit={process.returncode}: '
            f'see {log_path}')
    if not (prefix / 'system.reg').is_file() or not (prefix / 'user.reg').is_file():
        raise RuntimeError(f'Wine exited successfully without a usable prefix: {prefix}')
    return {'state': 'initialized', 'exitCode': process.returncode}


def windows():
    wine = Path(os.environ.get('CINTERFORGE_TEST_SCRIPTS_DIRECTORY', str(Path(__file__).resolve().parent.parent))) / 'wine/cinterforge-wine.sh'
    if not wine.is_file():
        raise RuntimeError(f'Wine launcher not found: {wine}')
    prefix = Path.home() / '.local/share/cinterforge/reference-wine/clean-win64'
    initialization = initialize_reference_wine_prefix(wine, prefix)
    data = {'schemaVersion': 2, 'environment': 'Windows-Wine-FEX',
            'wineLauncher': str(wine), 'wineVersion': command(str(wine), '--version'),
            'referencePrefix': str(prefix), 'initialization': initialization,
            'prefixes': inventory(prefix), 'winePayload': inventory(FEX / 'Wine'),
            'sharedInstallers': Path('/run/steamapps/common/CinterForge/Applications/UnforkedInstallers/Windows').is_dir(),
            'note': 'Clean reference Wine prefix is separate from all per-application publisher prefixes.'}
    wine_complete = ensure_complete(data['winePayload'], 'Wine payload')
    prefix_complete = ensure_complete(data['prefixes'], 'Clean Wine reference prefix')
    data['complete'] = wine_complete and prefix_complete
    save('Windows', data)
    if not data['complete']:
        raise RuntimeError('Windows inventory is incomplete; see saved Baseline.json errors')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('platform', nargs='?', choices=('all', 'linux', 'win'))
    parser.add_argument('--inventory-root', choices=GUEST_MOUNT_ROOTS,
                        help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.inventory_root:
        if os.geteuid() != 0:
            parser.error('--inventory-root requires root')
        snapshot = inventory(args.inventory_root, excluded_paths=GUEST_EXCLUSIONS)
        json.dump(snapshot, sys.stdout, separators=(',', ':'))
        return 0 if snapshot['errorCount'] == 0 else 1
    if args.platform is None:
        parser.error('platform is required')
    failures = 0
    for target, callback in (('linux', linux), ('win', windows)):
        if args.platform not in ('all', target):
            continue
        try:
            callback()
        except (OSError, RuntimeError) as e:
            print(f'[baseline] ERROR {target}: {e}', file=sys.stderr)
            failures += 1
    return 1 if failures else 0

if __name__ == '__main__':
    sys.exit(main())
