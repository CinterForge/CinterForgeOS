#!/usr/bin/env python3
"""Reusable developer GUI smoke transport for *installed* Steam apps.

The CinterForgeOS guest sees a read-only Steam install and a separate writable
request channel through virtiofs. No SSH, copying, Builds runtime path, or VM
image change at test time. Host never treats process start as a GUI pass.
"""
from __future__ import annotations
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid


def external_guest_control() -> Path:
    cinter_root = Path(__file__).resolve().parents[5]
    if cinter_root.name != 'Cinter' or not (cinter_root / 'CinterForge').is_dir():
        raise RuntimeError(f'Cannot identify Cinter source root from {__file__}')
    control = (cinter_root.parent / 'Builds/CreativeSuite/DeveloperGuestTests').resolve()
    if cinter_root == control or cinter_root in control.parents:
        raise RuntimeError(f'Guest test output cannot be inside Cinter: {control}')
    control.mkdir(parents=True, exist_ok=True)
    return control



def _guest_evidence(path: Path, token: str, required: tuple[str, ...]) -> dict[str, str]:
    if not path.is_file():
        raise RuntimeError(f'Guest did not produce required evidence: {path}')
    fields: dict[str, str] = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        pair = line.split('\t', 1)
        if len(pair) != 2 or not pair[0] or pair[0] in fields:
            raise RuntimeError(f'Malformed guest evidence in {path}: {line!r}')
        fields[pair[0]] = pair[1]
    if fields.get('token') != token or any(not fields.get(key) for key in required):
        raise RuntimeError(f'Incomplete or mismatched guest evidence in {path}: {fields}')
    return fields


def _verify_steamos_identity(control: Path, token: str) -> dict[str, str]:
    identity = _guest_evidence(control / f'identity-{token}.tsv', token,
                              ('kernel', 'architecture', 'os_id', 'os_name', 'boot_id',
                               'session_type', 'desktop', 'inventory_count'))
    if identity['kernel'] != 'Linux' or identity['architecture'] not in ('aarch64', 'arm64'):
        raise RuntimeError(f'Guest evidence is not SteamOS Linux ARM64: {identity}')
    if identity['os_id'].lower() not in ('steamos', 'holo'):
        raise RuntimeError(f'Guest evidence is not SteamOS: {identity}')
    if 'kde' not in identity['desktop'].lower() and 'plasma' not in identity['desktop'].lower():
        raise RuntimeError(f'Guest evidence is not a KDE session: {identity}')
    if identity['session_type'].lower() not in ('wayland', 'x11'):
        raise RuntimeError(f'Guest evidence lacks a graphical session: {identity}')
    print('[CinterForgeOS mac Guest] CONFIRMED inside SteamOS: '
          f"{identity['os_name']} kernel={identity['kernel']} arch={identity['architecture']} "
          f"boot={identity['boot_id']} session={identity['session_type']} "
          f"desktop={identity['desktop']} session_id={identity.get('session_id', 'unknown')}", flush=True)
    return identity

def _stop_test_vm(proc: subprocess.Popen, launcher_log: Path) -> None:
    """Release the VM disk after every test, including failed handshakes.

    Never SIGKILL a VM: it may be in the middle of guest filesystem I/O.
    """
    if proc.poll() is not None:
        return
    print(f'[Installed GUI] Closing test-owned SteamOS VM (pid={proc.pid})', flush=True)
    proc.terminate()
    try:
        proc.wait(timeout=45)
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f'SteamOS VM did not exit after graceful termination (pid={proc.pid}); '
            f'quit it normally before another test. Disk must remain locked. '
            f'launcher_log={launcher_log}')
    print(f'[Installed GUI] Test-owned SteamOS VM stopped (exit={proc.returncode}); disk released', flush=True)


def _startup_diagnostics(launcher_log: Path) -> str:
    """Surface pre-KDE mount and provisioning evidence, not shutdown noise."""
    if not launcher_log.is_file():
        return "launcher log absent"
    lines = launcher_log.read_text(encoding='utf-8', errors='replace').splitlines()
    keywords = ('[CinterForgeOS mounts]', 'cinterforge-kde',
                'select native cinterforge', 'create uid 1000 homedir',
                'Applications VirtioFS attached', 'development test control shared',
                'run-Applications.mount', 'run-DeveloperTests.mount',
                'provisioning:', 'failed to mount', 'home remains unwritable')
    selected = [line for line in lines if any(key.lower() in line.lower() for key in keywords)]
    return '\n'.join(selected[-70:]) if selected else 'no CinterForgeOS mount/startup diagnostics in guest console'


def _start_vm_and_probe(vm: Path, os_bundle: Path, environment: dict[str, str],
                        control: Path, timeout: float) -> tuple[subprocess.Popen, Path, dict[str, str]]:
    token = uuid.uuid4().hex
    request = control / 'request'
    result = control / f'result-{token}'
    if request.exists():
        print(f'[Installed GUI] Discarding abandoned guest request: {request}', flush=True)
        request.unlink()
    # Refresh the host-side smoke logic every run. SteamOS's KDE autostart
    # bridge is immutable, but the script it executes is loaded live through
    # the already-mounted DeveloperTests virtiofs channel.
    script = (Path(__file__).resolve().parent.parent /
              'apps/cinterforge-os/linux/kde_gui_smoke_agent.sh')
    if not script.is_file() or not script.stat().st_size:
        raise RuntimeError(f'Host KDE smoke agent missing: {script}')
    published = control / 'kde_gui_smoke_agent.sh'
    temp = control / '.kde_gui_smoke_agent.sh.tmp'
    temp.write_bytes(script.read_bytes())
    temp.chmod(0o755)
    temp.replace(published)
    print(f'[Installed GUI] Updated live KDE smoke script: {published}', flush=True)
    # Remove stale evidence BEFORE VM startup. Otherwise a fast guest can
    # write its first diagnostic and have the host erase it after launching.
    agent_log = control / 'guest-agent.log'
    agent_log.unlink(missing_ok=True)
    (control / 'enabled').touch()
    environment['CINTER_DEV_GUEST_TEST_CONTROL'] = str(control)
    launcher_log = control / 'launcher.log'
    print(f'[Installed GUI] Starting CinterForgeOS guest server: {vm}', flush=True)
    with launcher_log.open('wb') as output:
        proc = subprocess.Popen([str(vm)], env=environment, cwd=os_bundle,
                                stdout=output, stderr=subprocess.STDOUT)
    print(f'[Installed GUI] Native launcher log: {launcher_log}', flush=True)
    # A unique token must be handled by the KDE autostart agent, not a stale log.
    print('[Installed GUI] Waiting for KDE guest agent handshake', flush=True)
    last_report = time.monotonic()
    probe = control / f'.request-{token}'
    probe.write_text('\t'.join((token, 'Probe', '-', '-', '-', '-')) + '\n', encoding='utf-8')
    probe.replace(request)
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if result.exists():
                response = result.read_text(encoding='utf-8', errors='replace').strip()
                if not response.startswith('PASS\t'):
                    raise RuntimeError(f'KDE guest readiness failed: {response}; {launcher_log}')
                identity = _verify_steamos_identity(control, token)
                inventory = control / f'inventory-{token}.tsv'
                if not inventory.is_file():
                    raise RuntimeError(f'KDE agent responded without Applications inventory: {inventory}')
                rows = inventory.read_text(encoding='utf-8', errors='replace').splitlines()
                if not identity['inventory_count'].isdigit() or int(identity['inventory_count']) != len(rows):
                    raise RuntimeError(f'Guest identity inventory count mismatch: {identity["inventory_count"]} != {len(rows)}')
                print(f'[CinterForgeOS mac Applications] KDE guest sees {len(rows)} installed application version(s):',
                      flush=True)
                for row in rows:
                    fields = row.split('\t')
                    if len(fields) != 3:
                        raise RuntimeError(f'Malformed KDE Applications inventory row: {row!r}')
                    print(f'[CinterForgeOS mac Applications] {fields[0]}/{fields[1]}: {fields[2]}', flush=True)
                print(f'[Installed GUI] KDE guest handshake PASS: {response}', flush=True)
                return proc, launcher_log, identity
            if proc.poll() is not None:
                tail = launcher_log.read_text(encoding='utf-8', errors='replace')[-3000:]
                raise RuntimeError(f'CinterForgeOS exited before KDE handshake (exit {proc.returncode}); '
                                   f'launcher_log={launcher_log}; startup={_startup_diagnostics(launcher_log)!r}; tail={tail!r}')
            if time.monotonic() - last_report >= 15:
                last_report = time.monotonic()
                observed = agent_log.read_text(encoding='utf-8', errors='replace')[-1200:] if agent_log.is_file() else 'no guest agent log'
                print(f'[Installed GUI] Waiting: VM running; guest_agent={observed!r}; '
                      f'request_pending={request.exists()}', flush=True)
            time.sleep(1)
        tail = launcher_log.read_text(encoding='utf-8', errors='replace')[-3000:]
        observed = agent_log.read_text(encoding='utf-8', errors='replace')[-2000:] if agent_log.is_file() else 'no guest agent log'
        raise RuntimeError(f'KDE guest handshake timed out; guest_agent={observed!r}; '
                           f'launcher_log={launcher_log}; startup={_startup_diagnostics(launcher_log)!r}; tail={tail!r}')
    except BaseException:
        # Previously a failed KDE handshake left steamac-vm running, so every
        # subsequent invocation hit the legitimate single-writer disk lock.
        _stop_test_vm(proc, launcher_log)
        raise
    finally:
        if request.exists() and token in request.read_text(encoding='utf-8', errors='replace'):
            request.unlink()
        result.unlink(missing_ok=True)
        (control / f'inventory-{token}.tsv').unlink(missing_ok=True)
        (control / f'inventory-{token}.paths').unlink(missing_ok=True)
        (control / f'inventory-{token}.error').unlink(missing_ok=True)
        # Retain identity evidence for debugging, including on failure.


def test_cinterforge_os_kde(os_bundle: Path, steam_root: Path, *, timeout: float = 150) -> None:
    """Prove KDE started, then launch the Steam client inside the guest."""
    vm = os_bundle / 'Contents/MacOS/cfos-vm'
    if not vm.is_file():
        raise RuntimeError(f'Installed CinterForgeOS launcher missing: {vm}')
    # CFOS disk checks belong to the CFOS stage implementation, not shared mac stage.
    import importlib.util
    stage_path = Path(__file__).resolve().parents[2] / 'apps/cinterforge-os/stage_source.py'
    spec = importlib.util.spec_from_file_location('cfos_stage_source', stage_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'Cannot import CFOS disk validator: {stage_path}')
    cfos_stage = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cfos_stage)
    disk = cfos_stage.steamos_disk(os_bundle.parent)
    cfos_stage.validate_steamos_disk(disk)
    control = external_guest_control()
    env = os.environ.copy()
    env['CINTER_STEAMOS_DISK'] = str(disk)
    print(f'[CinterForgeOS mac GUI] Using verified SteamOS disk: {disk}', flush=True)
    applications = steam_root / 'Applications'
    if not applications.is_dir():
        raise RuntimeError(f'Installed Applications directory missing: {applications}')
    env['CINTER_DEV_APPLICATIONS_DIR'] = str(applications)
    print(f'[CinterForgeOS mac GUI] Attaching installed Applications share: {applications}', flush=True)
    print(f'[CinterForgeOS mac GUI] External guest-control directory: {control}', flush=True)
    proc, launcher_log, probe_identity = _start_vm_and_probe(vm, os_bundle, env, control, timeout)
    try:
        token = uuid.uuid4().hex
        request = control / 'request'
        result = control / f'result-{token}'
        if request.exists():
            raise RuntimeError(f'Another KDE guest request is pending: {request}')
        request_tmp = control / f'.request-{token}'
        request_tmp.write_text('\t'.join((token, 'Application', 'Steam', '-', '-', '-')) + '\n', encoding='utf-8')
        request_tmp.replace(request)
        print('[CinterForgeOS mac GUI] KDE verified; launching Steam in SteamOS', flush=True)
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                if result.exists():
                    response = result.read_text(encoding='utf-8', errors='replace').strip()
                    if response.startswith('PASS\t'):
                        window = _guest_evidence(control / f'window-{token}.tsv', token,
                                                 ('window_class', 'window_id', 'boot_id'))
                        if window['boot_id'] != probe_identity['boot_id']:
                            raise RuntimeError('Steam window evidence came from a different guest boot')
                        if window['window_class'].lower() != 'steam':
                            raise RuntimeError(f'Unexpected guest window class: {window}')
                        print(f'[CinterForgeOS mac Guest] Steam KDE window id={window["window_id"]} '
                              f'class={window["window_class"]} boot={window["boot_id"]}', flush=True)
                        print(f'[CinterForgeOS mac GUI] PASS: {response}', flush=True)
                        return
                    raise RuntimeError(f'Steam GUI smoke failed: {response}; '
                                       f'guest_output={control / ("output-"+token+".log")}')
                if proc.poll() is not None:
                    raise RuntimeError(f'CinterForgeOS launcher exited before Steam GUI smoke '
                                       f'(exit {proc.returncode}); launcher_log={launcher_log}')
                time.sleep(1)
            raise RuntimeError(f'Steam GUI smoke timed out; launcher_log={launcher_log}; '
                               f'guest_output={control / ("output-"+token+".log")}')
        finally:
            if request.exists() and token in request.read_text(encoding='utf-8', errors='replace'):
                request.unlink()
            result.unlink(missing_ok=True)

    finally:
        _stop_test_vm(proc, launcher_log)

def start_installed_guest_gui(installation: Path, executable: str, window_class: str,
                              os_bundle: Path, *, platform: str, timeout: float = 150) -> None:
    if platform not in ('Linux', 'Windows'):
        raise RuntimeError(f'Unsupported guest GUI test platform: {platform}')
    # Resolve via the same Steam library contract as Stage. Never default
    # to ~/Library when CinterForge is installed in another Steam library.
    suite = Path(__file__).resolve().parents[1]
    if str(suite) not in sys.path:
        sys.path.insert(0, str(suite))
    from lifecycle import steamapps as resolve_steamapps
    steam_root = resolve_steamapps() / 'common/CinterForge'
    installation = installation.resolve()
    try:
        relative = installation.relative_to(steam_root.resolve())
    except ValueError as error:
        raise RuntimeError(f'GUI test must use real Steam-installed app: {installation}') from error
    parts = relative.parts
    if len(parts) != 4 or parts[:2] != ('Applications', platform):
        raise RuntimeError(f'Unexpected installed {platform} app layout: {relative}')
    if not re.fullmatch(r'[A-Za-z0-9_.+-]+', window_class):
        raise RuntimeError('Window class must be a literal identifier')
    executable_relative = Path(executable)
    if (executable_relative.is_absolute() or '..' in executable_relative.parts
            or not (installation / executable_relative).is_file()):
        raise RuntimeError(f'Installed executable missing or unsafe: {executable}')
    vm = os_bundle / 'Contents/MacOS/cfos-vm'
    if not vm.is_file():
        raise RuntimeError(f'Installed CinterForgeOS launcher missing: {vm}')
    # The guest-control channel is generated state, never Cinter source data.
    # Derive it from this script's actual repository path rather than an
    # inherited environment variable (which may point at an obsolete tree).
    cinter_root = Path(__file__).resolve().parents[5]
    if cinter_root.name != 'Cinter' or not (cinter_root / 'CinterForge').is_dir():
        raise RuntimeError(f'Cannot identify Cinter source root from {__file__}')
    external_builds = cinter_root.parent / 'Builds'
    control = external_guest_control()
    obsolete = cinter_root / 'Builds' / 'CreativeSuite' / 'DeveloperGuestTests'
    inherited = os.environ.get('CINTER_DEV_GUEST_TEST_CONTROL')
    if inherited and Path(inherited).expanduser().resolve() != control:
        print(f'[Installed GUI] Ignoring stale guest-control override: {inherited}', flush=True)
    if control == cinter_root or cinter_root in control.parents:
        raise RuntimeError(f'Guest-control output inside source is forbidden: {control}')
    print(f'[Installed GUI] External guest-control directory: {control}', flush=True)
    if obsolete.exists():
        print(f'[Installed GUI] Obsolete in-source guest-control artifacts found: {obsolete}; '
              'never using them', flush=True)
    control.mkdir(parents=True, exist_ok=True)
    marker = control / 'enabled'
    marker.touch()
    # Stale requests from interrupted tests must not block the next run.
    # The token-specific result still prevents accepting an old pass.
    stale_request = control / 'request'
    if stale_request.exists():
        print('[Installed GUI] Removing abandoned request from previous test', flush=True)
        stale_request.unlink()
    token = uuid.uuid4().hex
    request = control / 'request'
    if request.exists():
        raise RuntimeError('Another installed application GUI test is pending')
    result = control / f'result-{token}'
    environment = os.environ.copy()
    applications = steam_root / 'Applications'
    if not applications.is_dir():
        raise RuntimeError(f'Installed Applications directory missing: {applications}')
    environment['CINTER_DEV_APPLICATIONS_DIR'] = str(applications)
    environment['CINTER_DEV_GUEST_TEST_CONTROL'] = str(control)
    # Prove this particular guest boot responds from KDE; an appended readiness
    # log is not a handshake (and can be left behind by a previous VM).
    proc, launcher_log, _identity = _start_vm_and_probe(vm, os_bundle, environment, control, timeout)
    try:
        # Only now request the actual installed application launch inside the guest.
        request_tmp = control / f'.request-{token}'
        request_tmp.write_text('\t'.join([token, parts[1], parts[2], parts[3],
                                            executable_relative.as_posix(), window_class]) + '\n', encoding='utf-8')
        request_tmp.replace(request)
        print(f'[Installed GUI] Waiting for KDE semantic window check: {window_class}; timeout={timeout:g}s', flush=True)
        started = time.monotonic()
        until = started + timeout
        next_report = started + 10
        try:
            while time.monotonic() < until:
                if result.exists():
                    contents = result.read_text(encoding='utf-8', errors='replace').strip()
                    if contents.startswith('PASS\t'):
                        print('[Installed GUI] ' + contents, flush=True)
                        return
                    raise RuntimeError(f'Guest GUI test failed: {contents}; log: {control / ("output-"+token+".log")}')
                if proc.poll() is not None:
                    raise RuntimeError(f'CinterForgeOS VM launcher exited before GUI verification (exit {proc.returncode}); launcher_log={launcher_log}')
                now = time.monotonic()
                if now >= next_report:
                    tail = ''
                    try:
                        tail = launcher_log.read_text(encoding='utf-8', errors='replace')[-1000:]
                    except OSError:
                        pass
                    print(f'[Installed GUI] Still waiting ({now-started:.0f}s); request_present={request.exists()}; '
                          f'launcher_alive={proc.poll() is None}; control={control}', flush=True)
                    if tail:
                        print(f'[Installed GUI] Launcher log tail:\n{tail}', flush=True)
                    next_report = now + 10
                time.sleep(1)
            tail = launcher_log.read_text(encoding='utf-8', errors='replace')[-3000:] if launcher_log.exists() else ''
            raise RuntimeError(f'No guest response after {timeout:g}s; request_present={request.exists()}; '
                               f'launcher_log={launcher_log}; control={control}; launcher_tail={tail!r}')
        finally:
            request.unlink(missing_ok=True) if request.exists() and token in request.read_text(encoding='utf-8') else None
            result.unlink(missing_ok=True)

    finally:
        _stop_test_vm(proc, launcher_log)

def start_installed_linux_gui(installation: Path, executable: str, window_class: str,
                              os_bundle: Path, *, timeout: float = 150) -> None:
    start_installed_guest_gui(installation, executable, window_class, os_bundle,
                              platform='Linux', timeout=timeout)
