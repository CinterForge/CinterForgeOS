#!/bin/sh
# Cinter inspect — original publisher inspection and installed CinterForge app diagnostics.
set -eu

scripts_directory=/run/steamapps/common/CinterForge/Applications/Test/Scripts

if [ "${1:-}" != installed ]; then
    exec "$scripts_directory/dispatch/cinter-commands.sh" inspect "$@"
fi

# Inspect installed, built-from-source applications without adding another command file.
shift
exec_python=python3
command -v "$exec_python" >/dev/null 2>&1 || { echo '[Cinter inspect] python3 missing' >&2; exit 127; }
"$exec_python" - "$@" <<'PYTHON_INSPECT_INSTALLED'
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

APPLICATIONS = Path('/run/steamapps/common/CinterForge/Applications')
MAGICS = {b'\x7fELF': 'ELF', b'MZ': 'PE'}


def run(args, *, timeout=25, env=None):
    try:
        result = subprocess.run(args, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=timeout, env=env,
                                errors='replace')
        return {'argv': list(map(str, args)), 'exitCode': result.returncode,
                'output': result.stdout[-60000:]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'argv': list(map(str, args)), 'error': str(exc)}


def binary_type(path):
    try:
        with path.open('rb') as file:
            start = file.read(4)
        return next((kind for magic, kind in MAGICS.items() if start.startswith(magic)), None)
    except OSError:
        return None


def inspect_binary(path, platform):
    result = {'path': str(path), 'format': binary_type(path), 'bytes': path.stat().st_size}
    if platform == 'Linux':
        if shutil.which('readelf'):
            header = run(['readelf', '-h', str(path)])
            dynamic = run(['readelf', '-d', str(path)])
            result['elfHeader'] = header
            result['dynamicSection'] = dynamic
            output = dynamic.get('output', '')
            result['needed'] = re.findall(r'\(NEEDED\).*?\[(.*?)\]', output)
            result['runpaths'] = re.findall(r'\((?:RUNPATH|RPATH)\).*?\[(.*?)\]', output)
        else:
            result['inspectionLimitations'] = ['readelf is not installed']
    elif shutil.which('objdump'):
        result['peImports'] = run(['objdump', '-p', str(path)])
        result['needed'] = re.findall(r'^\s*DLL Name:\s*(\S+)',
                                      result['peImports'].get('output', ''), re.MULTILINE)
    else:
        result['inspectionLimitations'] = ['objdump is not installed']
    return result


def find_executables(root, platform):
    # Limit scope to executable installation areas; avoid scanning resource payloads.
    candidates = []
    for subdir in ('bin', 'lib', 'lib/ardour9', 'Program Files'):
        parent = root / subdir
        if not parent.is_dir():
            continue
        for path in parent.rglob('*'):
            if not path.is_file() or path.is_symlink():
                continue
            try:
                if path.stat().st_size > 0 and binary_type(path) == ('ELF' if platform == 'Linux' else 'PE'):
                    candidates.append(path)
            except OSError:
                continue
            if len(candidates) >= 800:
                return candidates
    return candidates


def runtime_probes(platform, root, main_executable):
    """Inspect the runtime actually used by the registered launcher, without rebuilding.

    Probe failures are evidence, not successful dependency resolution. Never alter
    installed application files, OS packages or the Wine reference baseline.
    """
    checks = {}
    if platform == 'Linux':
        fex_root = Path('/usr/share/cinterforge/fex/RootFS')
        loaders = [fex_root / 'lib64/ld-linux-x86-64.so.2',
                   fex_root / 'lib/x86_64-linux-gnu/ld-linux-x86-64.so.2']
        loader = next((path for path in loaders if path.is_file()), None)
        if main_executable and loader:
            env = os.environ.copy()
            env['FEX_ROOTFS'] = str(fex_root)
            env['LD_LIBRARY_PATH'] = ':'.join((str(root / 'lib'), str(root / 'lib/ardour9'),
                                               env.get('LD_LIBRARY_PATH', ''))).rstrip(':')
            command = ['Cinter', 'run-linux-amd64', str(loader), '--list', str(main_executable)]
            checks['dynamicLoader'] = run(command, timeout=30, env=env)
            checks['dynamicLoader']['loader'] = str(loader)
            checks['dynamicLoader']['environmentLibraryPath'] = env['LD_LIBRARY_PATH']
        else:
            checks['dynamicLoader'] = {
                'status': 'unavailable', 'loaderCandidates': [str(path) for path in loaders],
                'mainExecutable': str(main_executable) if main_executable else None,
            }
    else:
        # Exactly the same prefix and portable Wine runner as launch-cfaudio-windows.sh.
        prefix = Path(os.environ.get('WINEPREFIX') or
                      str(Path.home() / '.local/share/cinterforge/wine-cfaudio'))
        checks['winePrefix'] = str(prefix)
        if prefix.is_dir():
            env = os.environ.copy()
            env['WINEARCH'] = 'win64'
            env['WINEPREFIX'] = str(prefix)
            checks['wineBuiltin'] = run(['Cinter', 'cinterforge-wine', 'cmd.exe', '/c', 'ver'],
                                        timeout=35, env=env)
        else:
            checks['wineBuiltin'] = {'status': 'notRun', 'reason': 'Application prefix missing; inspector does not initialize it'}
    return checks


def runtime_diagnosis(platform, root, launch):
    """Diagnose known *classes* of startup failures from the real launcher output.

    Evidence and limitations are saved alongside suggested next investigations;
    no dependencies are installed and no Wine prefixes are modified here.
    """
    output = launch.get('output', '')
    result = {'status': 'notClassified', 'evidence': [], 'checks': {}, 'nextActions': []}
    if platform == 'Linux':
        missing = sorted(set(re.findall(
            r'error while loading shared libraries:\s*([^:\s]+):\s*cannot open', output)))
        if missing:
            result['status'] = 'missingRuntimeLibraries'
            result['evidence'] = [f'Dynamic loader cannot resolve {name}' for name in missing]
            fex_root = Path('/usr/share/cinterforge/fex/RootFS')
            search_roots = [root / 'lib', root / 'lib64', root / 'usr/lib',
                            fex_root / 'lib/x86_64-linux-gnu',
                            fex_root / 'usr/lib/x86_64-linux-gnu',
                            fex_root / 'usr/local/lib', fex_root / 'lib64']
            for name in missing:
                locations = []
                for directory in search_roots:
                    if directory.is_dir():
                        # Search bounded, conventional runtime dirs; do not traverse the OS.
                        for candidate in directory.glob(name):
                            locations.append(str(candidate))
                        if directory == root / 'lib':
                            for candidate in (root / 'lib/ardour9').glob(name):
                                locations.append(str(candidate))
                result['checks'][name] = {'searchedDirectories': [str(p) for p in search_roots],
                                          'foundAt': sorted(set(locations))}
            result['nextActions'] = [
                'Determine which existing build dependency provides each missing SONAME.',
                'Check the staged dependency payload and the FEX runtime loader search paths.',
                'Repair the shared dependency/build/stage recipe; do not create library symlinks blindly.',
            ]
    else:
        prefix = Path(os.environ.get('WINEPREFIX') or
                      str(Path.home() / '.local/share/cinterforge/wine-cfaudio'))
        required = [prefix / 'system.reg', prefix / 'user.reg', prefix / 'drive_c/windows/system32']
        result['checks']['applicationWinePrefix'] = {
            'path': str(prefix), 'exists': prefix.is_dir(),
            'requiredPaths': [{'path': str(p), 'exists': p.exists()} for p in required],
        }
        result['checks']['wineRunner'] = {
            'path': '/usr/share/cinterforge/fex/wine-executable.txt',
            'exists': Path('/usr/share/cinterforge/fex/wine-executable.txt').is_file(),
        }
        if 'could not load kernel32.dll' in output.lower():
            result['status'] = 'wineCoreDllFailure'
            result['evidence'] = ['Wine could not load kernel32.dll (c0000135).']
            result['nextActions'] = [
                'Validate the actual application WINEPREFIX; the clean baseline prefix is separate.',
                'Run the existing Wine runner with a simple Windows built-in under the same prefix.',
                'Inspect Wine runtime DLL payload and FEX library resolution before changing Ardour.',
            ]
        elif 'c0000135' in output.lower():
            result['status'] = 'windowsDependencyFailure'
            result['evidence'] = ['Wine returned NTSTATUS c0000135 (DLL not found).']
            result['nextActions'] = ['Inspect the failing Windows DLL resolution trace.']
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('platform', choices=['Linux', 'Windows'])
    parser.add_argument('application', help='Installed application folder name')
    parser.add_argument('--version', help='Exact installed version; default is latest sorted directory')
    parser.add_argument('--launch', action='store_true', help='Run existing Cinter launcher to capture real startup result')
    parser.add_argument('--timeout', type=int, default=45, help='Launch timeout in seconds (default 45)')
    args = parser.parse_args()
    base = APPLICATIONS / args.platform / args.application
    if not base.is_dir():
        parser.error(f'Installed application is missing: {base}')
    versions = sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: p.name)
    if args.version:
        root = base / args.version
        if root not in versions:
            parser.error(f'Installed version not found: {root}')
    elif versions:
        root = versions[-1]
    else:
        parser.error(f'No installed version directories: {base}')
    executables = find_executables(root, args.platform)
    # For Ardour, prefer the actual main engine; retain discovered module inventory separately.
    main_candidates = [p for p in executables if ('cfaudio' in p.name.lower()) and
                       (p.name.lower().endswith('.exe') if args.platform == 'Windows'
                        else p.name.startswith('cfaudio-'))]
    mains = main_candidates or [p for p in executables if p.parent.name == 'bin']
    diagnostics = {
        'schemaVersion': 1, 'capturedAt': datetime.now(timezone.utc).isoformat(),
        'application': args.application, 'platform': args.platform, 'installedRoot': str(root),
        'availableVersions': [p.name for p in versions],
        'hostArchitecture': os.uname().machine, 'binaryCount': len(executables),
        'mainExecutableCandidates': [str(p) for p in mains],
        'binaries': [inspect_binary(p, args.platform) for p in mains[:8]],
        'availableTools': {tool: shutil.which(tool) for tool in
                           ('readelf', 'objdump', 'ldd', 'FEX', 'wine', 'coredumpctl')},
        'limitations': [],
    }
    if len(executables) >= 800:
        diagnostics['limitations'].append('Binary discovery was capped at 800 files')
    if not mains:
        diagnostics['limitations'].append('No likely main executable identified; do not infer launch readiness')
    if args.launch:
        # Use the existing external launcher, never invent runtime setup or call the engine directly.
        if args.application != 'CinterForge Audio':
            parser.error('--launch currently requires an existing registered launcher; only CinterForge Audio is registered')
        launcher = 'launch-cfaudio-linux' if args.platform == 'Linux' else 'launch-cfaudio-windows'
        diagnostics['launch'] = run(['Cinter', launcher, str(root)], timeout=args.timeout)
        diagnostics['runtimeDiagnosis'] = runtime_diagnosis(args.platform, root, diagnostics['launch'])
        diagnostics['runtimeDiagnosis']['checks']['runtimeProbes'] = runtime_probes(
            args.platform, root, mains[0] if mains else None)
        probe = diagnostics['runtimeDiagnosis']['checks']['runtimeProbes']
        if args.platform == 'Windows':
            builtin = probe['wineBuiltin']
            if builtin.get('exitCode') == 0:
                diagnostics['runtimeDiagnosis']['evidence'].append(
                    'Wine cmd.exe starts under the application prefix; investigate application DLL loading.')
                if diagnostics['runtimeDiagnosis']['status'] == 'wineCoreDllFailure':
                    diagnostics['runtimeDiagnosis']['status'] = 'wineWorksButApplicationFails'
            elif builtin.get('exitCode') is not None or builtin.get('error'):
                diagnostics['runtimeDiagnosis']['evidence'].append(
                    'Wine cmd.exe also fails under the application prefix; the failure is not Ardour-specific.')
                diagnostics['runtimeDiagnosis']['status'] = 'wineEnvironmentFailure'
        else:
            loader = probe['dynamicLoader']
            if loader.get('exitCode') is not None:
                diagnostics['runtimeDiagnosis']['evidence'].append(
                    f'FEX ELF loader --list exit={loader["exitCode"]}; inspect checks.runtimeProbes.dynamicLoader.output.')
        diagnostics['limitations'].append('Timed-out launches may leave child processes; inspect process logs') if 'error' in diagnostics['launch'] else None
    output = APPLICATIONS / 'Logs' / 'Inspections' / args.application / args.platform / root.name
    output.mkdir(parents=True, exist_ok=True)
    report = output / 'Inspection.json'
    # Host-side source inspection is appended to this same installed-app report.
    # A subsequent runtime inspection must retain it instead of silently erasing
    # verified source findings gathered on the host (which the guest cannot see).
    if report.is_file():
        previous = json.loads(report.read_text(encoding='utf-8'))
        if (previous.get('application') == args.application and
                previous.get('platform') == args.platform and
                previous.get('installedRoot') == str(root)):
            for key in ('windowsDependencyInspection', 'sourceRepositories',
                        'sourceRepositoryStatus'):
                if key in previous:
                    diagnostics[key] = previous[key]
    temporary = report.with_suffix('.tmp')
    temporary.write_text(json.dumps(diagnostics, indent=2, sort_keys=True) + '\n')
    temporary.replace(report)
    print(f'[inspect] platform={args.platform} version={root.name} binaries={len(executables)} mains={len(mains)}')
    if args.launch:
        launch = diagnostics['launch']
        print(f'[inspect] launcher exit={launch.get("exitCode", "unknown")} error={launch.get("error", "none")}')
        print(f'[inspect] diagnosis: {diagnostics["runtimeDiagnosis"]["status"]}')
    print(f'[inspect] report: {report}')
    return 0 if (mains and ('launch' not in diagnostics or diagnostics['launch'].get('exitCode') == 0)) else 1


if __name__ == '__main__':
    sys.exit(main())
PYTHON_INSPECT_INSTALLED
