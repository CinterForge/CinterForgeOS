#!/usr/bin/env python3
"""CFOS Ubuntu's upstream Creative Suite baseline (separate from fork builds).

The authoritative Linux packages are installed into the SAME immutable Ubuntu
AMD64 image that supplies the compiler, Wine and KDE. Test executes their real
installed entry points in the STAGED image; it does not mistake package
metadata, downloaded installers, or forked binaries for a runnable baseline.

Windows publisher installers remain external curated inputs. A real Windows
application can be certified only after its installed PE is discovered and
launched using Wine; this module never guesses a silent-install flag or treats
Wine's own Notepad test as proof that upstream GIMP/Kdenlive/etc work.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

# Name, Noble package, actual upstream command (NOT cinterforge-branded names).
UPSTREAM_LINUX = (
    ('GIMP', 'gimp', 'gimp'),
    ('Krita', 'krita', 'krita'),
    ('Inkscape', 'inkscape', 'inkscape'),
    ('Scribus', 'scribus', 'scribus'),
    ('Blender', 'blender', 'blender'),
    ('Kdenlive', 'kdenlive', 'kdenlive'),
    ('Ardour', 'ardour', 'ardour'),
    ('FFmpeg', 'ffmpeg', 'ffmpeg'),
)
SCHEMA = 'cinterforge.cfos.upstream-ubuntu.v1'


def version_args(binary: str) -> tuple[str, ...]:
    """Use each publisher's real version flag, not one guessed CLI switch."""
    # FFmpeg uses -version; --version fails with 'Unrecognized option'.
    return ('-version',) if binary == 'ffmpeg' else ('--version',)


def docker_test_commands(image: str, docker: str, archive: Path) -> list[list[str]]:
    """No host ports, no source/build-tree app binaries, only the installed OS."""
    from ubuntu_adapter import steamapps_mount_args
    base = [docker, 'run', '--rm', '--pull=never', '--platform', 'linux/amd64',
            '--network', 'none', '--shm-size', '512m',
            *steamapps_mount_args(archive), image]
    # The TEST runs in the installed image as its nonroot desktop user. The
    # application version command must execute successfully (not just exist).
    commands = []
    for name, package, binary in UPSTREAM_LINUX:
        # bash positional args keep all app identifiers separate from script.
        script = '''set -euo pipefail
app="$1"; pkg="$2"; command="$3"
test "$(dpkg --print-architecture)" = amd64
dpkg-query -W -f='${Status}' "$pkg" | grep -qx 'install ok installed'
command -v "$command" >/dev/null
# Invoke the actual upstream program in an X11 session, without network.
# Probe output is retained for diagnostics and cannot be spoofed by apt alone.
timeout 75 xvfb-run -a "$command" "$4" >/tmp/cfos-upstream-version 2>&1 || {
    cat /tmp/cfos-upstream-version >&2; exit 1;
}
test -s /tmp/cfos-upstream-version
printf '[CFOS upstream Linux] PASS %s (%s): ' "$app" "$pkg"
head -c 160 /tmp/cfos-upstream-version | tr '\n' ' '
printf '\n'
'''
        commands.append([*base[:-1], '--user', 'cinterforge', image,
                         'bash', '-ec', script, '--', name, package, binary, *version_args(binary)])
    return commands


def test(cfos: Path, archive: Path, docker: str, environment: dict[str, str], image: str) -> None:
    """Test the actual upstream Linux apps in the installed Ubuntu distro."""
    receipt = cfos.parent / 'Builds/Logs/CFOS/ubuntu-upstream-linux.json'
    receipt.parent.mkdir(parents=True, exist_ok=True)
    # Windows publisher builds are not Linux packages. Their separate
    # acquisition/install/GUI execution is pending until actual publisher
    # installer inputs are staged, and must NEVER inherit a Linux PASS.
    windows = {
        'status': 'pending-publisher-install-and-gui-acceptance',
        'installer_inputs': verify_windows_sources(cfos.parent / 'Builds'),
    }
    results = []
    try:
        for spec, command in zip(UPSTREAM_LINUX, docker_test_commands(image, docker, archive)):
            print(f'[CFOS upstream Linux] testing actual {spec[0]} executable', flush=True)
            subprocess.run(command, env=environment, check=True)
            results.append({'name': spec[0], 'package': spec[1], 'status': 'passed'})
        metadata = {'schema': SCHEMA, 'status': 'passed', 'applications': results, 'windows': windows}
    except (subprocess.CalledProcessError, OSError) as error:
        metadata = {'schema': SCHEMA, 'status': 'failed', 'applications': results, 'windows': windows,
                    'error': str(error)}
        receipt.write_text(json.dumps(metadata, indent=2, sort_keys=True) + '\n')
        raise RuntimeError(f'Upstream Linux executable acceptance failed: {error}') from error
    receipt.write_text(json.dumps(metadata, indent=2, sort_keys=True) + '\n')
    print(f'[CFOS upstream Linux] PASS {len(results)} upstream application executions: {receipt}', flush=True)
    print('[CFOS upstream Windows] PENDING: publisher EXE/MSI installation and actual Wine application acceptance are separate gates', flush=True)


def verify_windows_sources(builds: Path) -> dict[str, list[str]]:
    """Inventory pre-approved upstream Windows installers; never download/fake."""
    install_root = builds / 'CreativeSuite/UnforkedInstallers/Windows'
    result: dict[str, list[str]] = {}
    for name, _, _ in UPSTREAM_LINUX:
        if name == 'FFmpeg':
            continue
        directory = install_root / name
        if not directory.is_dir() or directory.is_symlink():
            result[name] = []
            continue
        files = sorted(p.name for p in directory.iterdir()
                       if p.is_file() and not p.is_symlink() and
                       p.suffix.lower() in ('.exe', '.msi'))
        result[name] = files
    return result
