#!/usr/bin/env python3
"""Creative Suite artifact contract: Built ZIP -> Staged -> Steam Installed.

No network, source builds or installer execution occurs in stage/test.
"""
from __future__ import annotations
import argparse
import errno
from datetime import datetime
import os
from pathlib import Path
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
import time


# Product identity and Stage signing are lifecycle responsibilities.
_PRODUCT_NAMES = {
    'cfaudio': 'CinterForge Audio',
    'cinterforge-os': 'CinterForgeOS',
    'cf3d': 'CinterForge 3D',
    'cfimage': 'CinterForge Image',
    'cfvector': 'CinterForge Vector',
    'cfvideo': 'CinterForge Video',
    'cfpaint': 'CinterForge Paint',
    'cfdocument': 'CinterForge Document',
    'rapidraw': 'CinterForge Photo',
    'cfphoto': 'CinterForge Photo',
}

_PLATFORM_NAMES = {'mac': 'macOS', 'linux': 'Linux', 'win': 'Windows'}


def platform_name(platform: str) -> str:
    return _PLATFORM_NAMES[platform]


def product_name(app: str) -> str:
    return _PRODUCT_NAMES.get(app, app)


def launcher_name(app: str) -> str:
    # The OS app keeps the human-facing space in its Finder launcher.
    if app in ('cinterforge-os', 'cinterforgeos'):
        return 'CinterForge OS'
    return product_name(app)


_SOURCE_BUNDLE_APPS = frozenset({
    'cfimage', 'cfvector', 'cfvideo', 'cfpaint', 'cfdocument', 'cfphoto', 'rapidraw', 'cf3d'
})


def _mac_bundle_macho_files(bundle: Path) -> list[Path]:
    """Find embedded Mach-O binaries, including launch helpers under Resources.

    macOS does not necessarily consider Contents/Resources/bin a nested-code
    location when signing a bundle with --deep. Every executable image needs
    its own valid signature after dependency relocation has finished.
    """
    macho_magic = {b'\xfe\xed\xfa\xce', b'\xce\xfa\xed\xfe',
                   b'\xfe\xed\xfa\xcf', b'\xcf\xfa\xed\xfe',
                   b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca',
                   b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca'}
    files = []
    for directory, _, names in os.walk(bundle):
        for name in names:
            path = Path(directory) / name
            if path.is_symlink() or not path.is_file():
                continue
            # Python's config directory includes Mach-O relocatable .o files.
            # They are linker inputs, not executable code and cannot be signed.
            if path.suffix == '.o':
                continue
            with path.open('rb') as stream:
                magic = stream.read(4)
            if magic in macho_magic:
                files.append(path)
    # Deepest binaries first, then sign the outer application last.
    return sorted(files, key=lambda path: (-len(path.parts), str(path)))


def mac_bundle_branding(app: str, extracted: Path) -> None:
    """Apply recipe-owned product identity before Stage signs the bundle."""
    recipe = (Path(__file__).resolve().parent / 'apps' / app / 'mac' / 'recipe.json')
    if not recipe.is_file():
        return
    import json
    identity = json.loads(recipe.read_text()).get('branding', {})
    name = identity.get('bundle_name')
    if not name:
        return
    bundles = list(extracted.glob('*.app'))
    if len(bundles) != 1:
        raise RuntimeError(f'Expected one macOS bundle in {extracted}: {bundles}')
    bundle = bundles[0]
    desired = extracted / (name + '.app')
    if bundle != desired:
        if desired.exists():
            raise RuntimeError(f'Branded bundle already exists: {desired}')
        bundle.rename(desired)
    plist = desired / 'Contents' / 'Info.plist'
    with plist.open('rb') as stream:
        info = plistlib.load(stream)
    info['CFBundleName'] = name
    info['CFBundleDisplayName'] = name
    if identity.get('bundle_identifier'):
        info['CFBundleIdentifier'] = identity['bundle_identifier']
    # Preserve CFBundleExecutable: the actual Mach-O binary and upstream helper
    # paths remain unchanged until the maintained fork can rename them safely.
    with plist.open('wb') as stream:
        plistlib.dump(info, stream)
    print(f'[mac][brand] {desired.name} identifier={info.get("CFBundleIdentifier")}', flush=True)


def sign_mac_source_bundle(app: str, platform: str, extracted: Path) -> None:
    """Sign every embedded Mach-O followed by the final source-built bundle."""
    if platform != 'mac':
        return
    mac_bundle_branding(app, extracted)
    if app not in _SOURCE_BUNDLE_APPS and app != 'cfaudio':
        return
    if sys.platform != 'darwin':
        raise RuntimeError(f'{app}/mac Stage must run on macOS')
    bundles = list(extracted.glob('*.app'))
    if len(bundles) != 1:
        raise RuntimeError(f'Expected exactly one bundle to sign: {extracted}')
    identity = os.environ.get('CINTER_MAC_CODESIGN_IDENTITY', '-').strip()
    if not identity:
        raise RuntimeError('CINTER_MAC_CODESIGN_IDENTITY cannot be empty')
    bundle = bundles[0]
    if app == 'cfaudio':
        from shared.mac.dependency_closure import relocate_packaged_bundle
        relocate_packaged_bundle(bundle)
    if app == 'cfvideo':
        from apps.cfvideo.mac.package import validate_splash_qml_runtime
        validate_splash_qml_runtime(bundle)
    # Qt/Craft can place linker-only .prl metadata and static .a archives
    # alongside loadable plugins. Neither is runtime code; codesign treats
    # these files as invalid nested code when signing the application binary.
    # Do not remove dynamic libraries, frameworks, or actual Qt plugins.
    build_only = set(bundle.rglob('*.prl'))
    plugins = bundle / 'Contents/PlugIns'
    if plugins.is_dir():
        build_only.update(plugins.rglob('*.a'))
    for metadata in sorted(build_only):
        if metadata.is_symlink() or not metadata.is_file():
            raise RuntimeError(f'Unexpected Qt build-only entry: {metadata}')
        metadata.unlink()
    # Assert the signature inputs are clean before signing anything, rather
    # than discovering stray archives only after hundreds of signatures.
    leftovers = list(bundle.rglob('*.prl'))
    if plugins.is_dir():
        leftovers.extend(plugins.rglob('*.a'))
    if leftovers:
        raise RuntimeError(f'Qt build-only files remain in signed bundle: {leftovers}')
    if build_only:
        print(f'[mac][sign] Excluded {len(build_only)} Qt linker-only '
              'files from staged runtime bundle', flush=True)
    binaries = _mac_bundle_macho_files(bundle)
    if not binaries:
        raise RuntimeError(f'No Mach-O images found in staged app: {bundle}')
    # codesign emits one "--prepared"/"--validated" line for every nested
    # image (often hundreds of Qt/MLT plugins). Keep Stage output bounded;
    # retain the complete subprocess diagnostic if any signing step fails.
    def checked_codesign(*args: str) -> None:
        command = ['codesign', *args]
        result = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        if result.returncode:
            raise RuntimeError(
                f"macOS codesign failed (exit {result.returncode}): "
                f"{' '.join(command)}\n{result.stdout}{result.stderr}"
            )

    # A main executable may contain nested helper executables (Ardour9 has
    # Contents/MacOS/ardour9-export). codesign validates those subcomponents
    # when signing the main executable, so helpers must be signed first.
    with (bundle / 'Contents/Info.plist').open('rb') as stream:
        main_name = plistlib.load(stream).get('CFBundleExecutable')
    if not main_name:
        raise RuntimeError(f'Missing CFBundleExecutable in {bundle}')
    main_binary = bundle / 'Contents' / 'MacOS' / main_name
    main_is_macho = main_binary in binaries
    if not main_is_macho:
        # Older cached Vector bundles contain a shell launcher. macOS can
        # seal an executable script as a bundle resource; it cannot sign that
        # script as an independent Mach-O image. Do not relax validation for
        # arbitrary files or missing executables.
        if (app != 'cfvector' or not main_binary.is_file()
                or main_binary.is_symlink()
                or not os.access(main_binary, os.X_OK)
                or not main_binary.open('rb').read(2) == b'#!'):
            raise RuntimeError(f'Main executable is not a Mach-O file: {main_binary}')
        print(f'[mac][sign] Sealing legacy Vector script launcher in app bundle: {main_binary}', flush=True)
    for binary in binaries:
        if binary != main_binary:
            checked_codesign('--force', '--sign', identity, str(binary))
    # Sign the primary executable only after all its embedded helpers.
    if main_is_macho:
        checked_codesign('--force', '--deep', '--sign', identity, str(main_binary))
    checked_codesign('--force', '--deep', '--sign', identity, str(bundle))
    # A bundle-level --deep verification alone can miss nonstandard executables.
    for binary in binaries:
        if binary != main_binary:
            checked_codesign('--verify', '--strict', str(binary))
    # The primary executable is sealed as part of the application bundle.
    # Verify it in bundle context rather than as a standalone Mach-O.
    checked_codesign('--verify', '--deep', '--strict', '--verbose=2', str(bundle))
    print(f'SIGNED_STAGED_APP {bundle} identity={identity} embedded_macho={len(binaries)}', flush=True)


def root_from_source() -> Path:
    """Shared sibling workspace containing Builds and all source repositories."""
    return Path(__file__).resolve().parents[3]


def version_value(raw: str) -> str:
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', raw) or raw in ('.', '..'):
        raise ValueError(f'Invalid version: {raw!r}')
    return raw


def _steam_library_paths(config: Path) -> list[Path]:
    """Read external Steam libraries from Valve's libraryfolders.vdf."""
    if not config.is_file():
        return []
    data = config.read_text(encoding='utf-8-sig', errors='replace')
    # Valve KeyValues strings support backslash-escaped quotes/backslashes.
    paths = re.findall(r'"path"\s*"((?:\\.|[^"\\])*)"', data, re.I)
    roots = []
    for entry in paths:
        value = re.sub(r'\\([\\"])', r'\1', entry)
        folder = Path(value).expanduser()
        if folder.is_absolute():
            # The VDF contains *library* roots, not steamapps roots.
            roots.append(folder / 'steamapps')
    return roots


def _explicit_steamapps() -> Path | None:
    """Honor explicitly selected Steam installations without path guessing."""
    steamapps_root = os.environ.get('CINTER_STEAM_APPS_ROOT', '').strip()
    app_root = (os.environ.get('CINTERFORGE_STEAM_APP_INSTALL_DIR', '').strip()
                or os.environ.get('CINTERFORGE_PRODUCT_ROOT', '').strip())
    result = None
    if steamapps_root:
        result = Path(steamapps_root).expanduser().resolve()
        if result.name != 'steamapps':
            raise RuntimeError(f'CINTER_STEAM_APPS_ROOT must name a steamapps directory: {result}')
    if app_root:
        installed = Path(app_root).expanduser().resolve()
        if installed.name != 'CinterForge' or installed.parent.name != 'common' or installed.parent.parent.name != 'steamapps':
            raise RuntimeError(f'CINTERFORGE_STEAM_APP_INSTALL_DIR must name steamapps/common/CinterForge: {installed}')
        from_app = installed.parent.parent
        if result is not None and result != from_app:
            raise RuntimeError(f'Conflicting Steam roots: {result} and {from_app}')
        result = from_app
    if result is not None and not (result / 'common/CinterForge').is_dir():
        raise RuntimeError(f'Selected Steam library has no installed CinterForge: {result}')
    return result


def steamapps() -> Path:
    """Find the one actual CinterForge Steam library, including external disks.

    Never choose the first matching library silently when multiple CinterForge
    installations exist. Setup/Stage/Test must all target the same installation.
    """
    explicit = _explicit_steamapps()
    if explicit is not None:
        print(f'[Creative Suite] Steam installation: {explicit}', file=sys.stderr, flush=True)
        return explicit
    if sys.platform == 'darwin':
        roots = [Path.home() / 'Library/Application Support/Steam/steamapps']
    elif os.name == 'nt':
        raise RuntimeError('Select the installed Steam library with CINTER_STEAM_APPS_ROOT')
    else:
        roots = [Path.home() / '.local/share/Steam/steamapps',
                 Path.home() / '.steam/steam/steamapps']

    # Steam stores libraryfolders.vdf in steamapps and, on some versions, in
    # Steam/config. Both originate at the known Steam root and may list volumes.
    for default in tuple(roots):
        for config in (default / 'libraryfolders.vdf',
                       default.parent / 'config/libraryfolders.vdf'):
            roots.extend(_steam_library_paths(config))
    candidates = {}
    for root in roots:
        resolved = root.expanduser().resolve()
        candidates[resolved] = resolved
    installed = [root for root in candidates if (root / 'common/CinterForge').is_dir()]
    if not installed:
        raise RuntimeError('CinterForge Steam installation not found in registered libraries: '
                           + ', '.join(str(root) for root in candidates)
                           + '. Select the external library with CINTER_STEAM_APPS_ROOT.')

    # A live CFOS host mount identifies which of several installed libraries
    # is actually in use by the guest. A stale or invalid report is ignored.
    mounted = []
    for root in installed:
        report = (root / 'common/CinterForge/Applications/Logs/'
                  'CinterForgeOS/Boot/SteamappsMount.log')
        if not report.is_file():
            continue
        try:
            fields = dict(line.split(': ', 1) for line in report.read_text().splitlines()
                          if ': ' in line)
            if (fields.get('Guest') == '/run/steamapps'
                    and fields.get('Mode') == 'read/write'
                    and Path(fields.get('Host', '')).resolve() == root):
                mounted.append(root)
        except (OSError, ValueError):
            continue
    if len(mounted) == 1:
        result = mounted[0]
    elif len(installed) == 1:
        result = installed[0]
    else:
        raise RuntimeError('Multiple installed CinterForge Steam libraries: '
                           + ', '.join(str(p) for p in installed)
                           + '. Select the correct one with CINTER_STEAM_APPS_ROOT.')
    print(f'[Creative Suite] Steam installation: {result}', file=sys.stderr, flush=True)
    return result


def installation_directory(app: str, platform: str) -> Path:
    """Use lifecycle product identity for installed directory naming."""

    return (steamapps() / 'common/CinterForge/Applications'
            / platform_name(platform) / product_name(app))


def migrate_legacy_install(app: str, platform: str, version: str, destination: Path) -> bool:
    """Relocate a previously staged internal-name install, never overwrite."""
    legacy = steamapps() / 'common/CinterForge/Applications' / platform_name(platform) / app / version
    if not legacy.exists():
        legacy = steamapps() / 'common/CinterForge/Applications' / platform / app / version
    if not legacy.is_dir() or destination.exists():
        return False
    require_payload(legacy, platform)
    destination.parent.mkdir(parents=True, exist_ok=True)
    legacy.rename(destination)
    print(f'RENAMED_INSTALLED {legacy} -> {destination}')
    return True

def require_payload(path: Path, platform: str) -> None:
    if not path.is_dir() or not any(path.iterdir()):
        raise RuntimeError(f'Empty or missing complete application payload: {path}')
    if platform == 'mac':
        bundles = list(path.glob('*.app'))
        if len(bundles) != 1 or not (bundles[0] / 'Contents/Info.plist').is_file():
            raise RuntimeError(f'Expected one complete top-level .app bundle in: {path}')
        with (bundles[0] / 'Contents/Info.plist').open('rb') as fp:
            info = plistlib.load(fp)
        if not (bundles[0] / 'Contents/MacOS' / str(info.get('CFBundleExecutable', ''))).is_file():
            raise RuntimeError(f'Application executable missing in: {bundles[0]}')


def require_cinterforge_os_runtime(path: Path) -> None:
    """Validate the actual Xcode-signed CinterForgeOS ZIP or staged product.

    Build packages the complete native application and an initial dispatcher.
    Stage may additionally create application-owned Tests next to the bundle.
    The guest boot images must remain inside the signed app resources.
    """
    allowed = {'CinterForge OS.app', 'CinterDispatcher', 'TestScripts', 'Tests'}
    unexpected = sorted(entry.name for entry in path.iterdir()
                        if entry.name not in allowed)
    if unexpected:
        raise RuntimeError(f'CinterForgeOS distribution includes unexpected files: {unexpected}')

    dispatcher = path / 'CinterDispatcher'
    if not dispatcher.is_file() or dispatcher.stat().st_size == 0:
        raise RuntimeError(f'CinterForgeOS distribution missing guest Cinter dispatcher: {dispatcher}')

    test_scripts = path / 'TestScripts'
    required_test_scripts = (
        'baseline/baseline-environments.py',
        'baseline/filesystem_inventory.py',
        'inspect/inspect-unforked-windows.py',
        'test/test-x86-runtime.sh',
        'run/run-linux-amd64.sh',
        'wine/cinterforge-wine.sh',
        'inspect/inspect-x86-runtime.sh',
        'dispatch/cinter-commands.sh',
        'baseline/baseline.sh',
        'inspect/inspect.sh',
        'test/test.sh',
        'installl/installers.sh',
    )
    if not test_scripts.is_dir() or test_scripts.is_symlink():
        raise RuntimeError(f'CinterForgeOS test scripts missing: {test_scripts}')
    for script_name in required_test_scripts:
        script_path = test_scripts / script_name
        if not script_path.is_file() or script_path.stat().st_size == 0:
            raise RuntimeError(f'CinterForgeOS test script missing: {script_path}')

    app = path / 'CinterForge OS.app'
    if not app.is_dir() or app.is_symlink():
        raise RuntimeError(f'CinterForgeOS bundle missing or invalid: {app}')
    resources = app / 'Contents/Resources'
    required_resources = (
        'Image', 'initramfs.cpio.gz', 'steamac-layer.img',
        'steamdeck-images.pem', 'gvproxy', 'desync',
        'cinterforge-os-splash/index.html',
        'cinterforge-os-splash/splash.css',
        'cinterforge-os-splash/splash.js',
        'cinterforge-os-splash/cinterforge-logo.png',
    )
    for name in required_resources:
        entry = resources / name
        if not entry.is_file() or entry.stat().st_size == 0:
            raise RuntimeError(f'Incomplete CinterForgeOS signed bundle resource: {entry}')

    executable = app / 'Contents/MacOS/cfos-vm'
    if not executable.is_file() or executable.stat().st_size == 0:
        raise RuntimeError(f'CinterForgeOS native host executable missing: {executable}')
    frameworks = app / 'Contents/Frameworks'
    for name in ('libkrun.1.dylib', 'libvirglrenderer.1.dylib',
                 'libepoxy.0.dylib', 'libMoltenVK.dylib',
                 'libvulkan_kosmickrisp.dylib'):
        entry = frameworks / name
        if not entry.is_file() or entry.stat().st_size == 0:
            raise RuntimeError(f'CinterForgeOS signed framework missing: {entry}')


def prepare_cinterforge_os_xcode_stage(destination: Path, version: str) -> None:
    """Stage the native app already built in Xcode, with external Cinter tools.

    Xcode is the CFOS macOS builder. Stage must not rely on a separate
    ./Cinter build command or on a ZIP created before that Xcode build.
    Nothing is written into the signed application bundle.
    """
    cinter_root = root_from_source()
    built_root = cinter_root / 'Builds/CreativeSuite/Built/cinterforge-os/mac'
    xcode_app = (built_root / 'Steamac/work/out/XcodeProduct/Release'
                 / 'CinterForge OS.app')
    if not (xcode_app / 'Contents/MacOS/cfos-vm').is_file():
        raise RuntimeError(f'CinterForgeOS Xcode Release build missing: {xcode_app}')

    external_sources = (Path(__file__).resolve().parent / 'apps/cinterforge-os/external')
    dispatcher_source = external_sources / 'dispatch/cinter-dispatcher.sh'
    # The external directory is the sole authoritative source for Cinter tools.
    # Stage all executable commands and their adjacent Python dependencies.
    required_scripts = tuple(sorted(
        source.relative_to(external_sources).as_posix()
        for source in external_sources.rglob('*')
        if source.is_file() and source.name != 'cinter-dispatcher.sh'
        and not any(part.startswith('.') for part in source.relative_to(external_sources).parts)
        and '__pycache__' not in source.parts
    ))
    for source in (dispatcher_source, *(external_sources / name for name in required_scripts)):
        if not source.is_file() or source.stat().st_size == 0:
            raise RuntimeError(f'Missing external CFOS Cinter tool for Stage: {source}')

    destination.mkdir(parents=True)
    shutil.copytree(xcode_app, destination / 'CinterForge OS.app', symlinks=True)
    shutil.copy2(dispatcher_source, destination / 'CinterDispatcher')
    scripts_destination = destination / 'TestScripts'
    scripts_destination.mkdir()
    for script_name in required_scripts:
        script_destination = scripts_destination / script_name
        script_destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(external_sources / script_name, script_destination)
    # Setup compiles the Windows WGL probe alongside FEX into the persistent
    # Steamac build workspace. Xcode Stage already consumes its native .app
    # directly from that same workspace, rather than the versioned Cinter ZIP.
    # Consume the completed probe there too; Stage never compiles anything.
    probe_binary = (built_root / 'Steamac/work/out/fex-userspace/Windows'
                    / 'cinterforge-gl-probe.exe')
    if not probe_binary.is_file() or probe_binary.stat().st_size == 0:
        raise RuntimeError(f'Windows WGL probe missing from Setup build: {probe_binary}. Run ./Cinter setup cinterforge-os mac before Stage.')
    shutil.copy2(probe_binary, scripts_destination / 'test/cinterforge-gl-probe.exe')
    print(f'[CinterForgeOS stage] Windows WGL probe: {probe_binary}', flush=True)
    print(f'[CinterForgeOS stage] Xcode-built app: {xcode_app}', flush=True)
    print('[CinterForgeOS stage] External Cinter commands prepared outside the signed app', flush=True)


def preserve_legacy_steamos_disk(installed: Path) -> None:
    """Put the legacy version-root disk in the persistent SteamOS subdirectory.

    Nothing in the version's SteamOS directory is replaceable stage content.
    """
    disk = installed / 'steamos.img'
    if not disk.exists():
        return
    destination = installed / 'SteamOS' / 'steamos.img'
    if destination.exists():
        raise RuntimeError(f'Two SteamOS disks exist; refusing to stage: {disk} and {destination}')
    if not disk.is_file() or disk.stat().st_size < 1024 ** 3:
        raise RuntimeError(f'Invalid legacy SteamOS disk; refusing to stage: {disk}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    disk.rename(destination)
    print(f'[CinterForgeOS] PRESERVED {disk} -> {destination}', flush=True)


def replace_installed_version(staged: Path, installed: Path) -> None:
    """Replace the app/boot-support payload, never the installed SteamOS directory.

    Stage into a sibling; park the old version for rollback. Move SteamOS into
    the new version *without copying*; on failure restore it before re-raising.
    """
    installed.parent.mkdir(parents=True, exist_ok=True)
    preserve_legacy_steamos_disk(installed)
    with tempfile.TemporaryDirectory(prefix='.cinter-install-', dir=installed.parent) as td:
        candidate = Path(td) / 'payload'
        shutil.copytree(staged, candidate, symlinks=True)
        require_payload(candidate, 'mac')
        require_cinterforge_os_runtime(candidate)
        # The signed .app owns its boot images; never relocate signed resources.
        previous = Path(td) / 'previous'
        if installed.exists():
            installed.rename(previous)
        preserved = previous / 'SteamOS'
        destination = candidate / 'SteamOS'
        try:
            if preserved.exists():
                if not preserved.is_dir() or preserved.is_symlink() or destination.exists():
                    raise RuntimeError(f'Unexpected persistent SteamOS directory: {preserved}')
                preserved.rename(destination)
            candidate.rename(installed)
        except BaseException:
            # Do not leave the user's SteamOS disk in TemporaryDirectory.
            if destination.exists():
                destination.rename(preserved)
            if previous.exists():
                previous.rename(installed)
            raise
        print(f'REPLACED_INSTALLED {installed} (signed app refreshed; SteamOS preserved)')


def provision_cinterforge_os_dispatcher(staged: Path) -> None:
    """Install the external router and migrate known obsolete guest-path routers.

    Never replace an arbitrary user-authored dispatcher. Old routers that
    invoke retired /usr/lib/cinterforge operational scripts cannot work and
    must be migrated automatically by the normal Stage operation.
    """
    source = staged / 'CinterDispatcher'
    if not source.is_file() or source.stat().st_size == 0:
        raise RuntimeError(f'Missing staged CFOS dispatcher: {source}')

    destination = steamapps() / 'common/CinterForge/Applications/Test/Dispatcher/Cinter'
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if not destination.is_file() or destination.is_symlink():
            raise RuntimeError(f'Invalid installed CFOS dispatcher: {destination}')

        installed_contents = destination.read_bytes()
        current_contents = source.read_bytes()
        if installed_contents == current_contents:
            destination.chmod(0o755)
            print(f'[CinterForgeOS stage] VERIFIED external dispatcher: {destination}', flush=True)
            return

        retired_guest_commands = (
            b'/usr/lib/cinterforge/baseline-environments',
            b'/usr/lib/cinterforge/cinter-commands',
            b'/usr/lib/cinterforge/baseline',
            b'script_path="$scripts_directory/$requested_command"',
        )
        if not any(command in installed_contents for command in retired_guest_commands):
            print(f'[CinterForgeOS stage] PRESERVED user dispatcher: {destination}', flush=True)
            return

        # Preserve the exact retired dispatcher. Earlier attempts may already
        # have saved a DIFFERENT retired version under the first backup name.
        # Never treat that history as a reason to leave broken routing active.
        backup = destination.with_name('Cinter.before-external-routing')
        if backup.exists() and backup.read_bytes() != installed_contents:
            backup_number = 2
            while True:
                candidate = destination.with_name(
                    f'Cinter.before-external-routing.{backup_number}'
                )
                if not candidate.exists():
                    backup = candidate
                    break
                if candidate.read_bytes() == installed_contents:
                    backup = candidate
                    break
                backup_number += 1
        if not backup.exists():
            with backup.open('xb') as output:
                output.write(installed_contents)

        replacement = destination.with_name('Cinter.stage-new')
        if replacement.exists():
            raise RuntimeError(f'Unexpected dispatcher staging file: {replacement}')
        try:
            shutil.copy2(source, replacement)
            replacement.chmod(0o755)
            replacement.replace(destination)
        finally:
            replacement.unlink(missing_ok=True)
        print(f'[CinterForgeOS stage] MIGRATED retired dispatcher: {destination}', flush=True)
        return

    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o755)
    try:
        with os.fdopen(descriptor, 'wb') as output, source.open('rb') as input_file:
            shutil.copyfileobj(input_file, output)
        destination.chmod(0o755)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    print(f'[CinterForgeOS stage] INSTALLED initial dispatcher: {destination}', flush=True)


def provision_cinterforge_os_test_scripts(staged: Path, previous_staged: Path | None = None) -> None:
    """Install macOS-editable Konsole test tools on first Stage, preserving edits.

    Re-stage replaces a script only when it matches the script installed by
    the previous stage. User-edited scripts and the live dispatcher remain
    untouched. All implementation files remain outside the guest OS.
    """
    source_directory = staged / 'TestScripts'
    destination_directory = steamapps() / 'common/CinterForge/Applications/Test/Scripts'
    destination_directory.mkdir(parents=True, exist_ok=True)

    # Earlier releases staged commands as files directly under Scripts/.
    # The organized layout now needs directories with those same names.
    # Preserve each old implementation outside the active command tree before
    # creating the directory; otherwise mkdir raises FileExistsError.
    legacy_directory = destination_directory.parent / 'LegacyScripts'
    required_directories = sorted(
        {source.relative_to(source_directory).parts[0]
         for source in source_directory.rglob('*')
         if source.is_file() and len(source.relative_to(source_directory).parts) > 1}
    )
    for directory_name in required_directories:
        old_command = destination_directory / directory_name
        if not old_command.exists() and not old_command.is_symlink():
            continue
        if old_command.is_dir():
            continue
        if not old_command.is_file() or old_command.is_symlink():
            raise RuntimeError(f'Unexpected legacy CFOS command path: {old_command}')
        legacy_directory.mkdir(parents=True, exist_ok=True)
        archived_command = legacy_directory / directory_name
        backup_number = 1
        while archived_command.exists() or archived_command.is_symlink():
            archived_command = legacy_directory / f'{directory_name}.{backup_number}'
            backup_number += 1
        old_command.rename(archived_command)
        print(f'[CinterForgeOS stage] ARCHIVED flat command: {old_command} -> {archived_command}', flush=True)

    for source in sorted(source_directory.rglob('*')):
        if source.is_dir():
            continue
        if not source.is_file() or source.is_symlink():
            raise RuntimeError(f'Invalid staged CFOS test script: {source}')

        destination = destination_directory / source.relative_to(source_directory)
        destination.parent.mkdir(parents=True, exist_ok=True)
        executable = source.name != 'filesystem_inventory.py'
        file_mode = 0o755 if executable else 0o644
        try:
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, file_mode)
        except FileExistsError:
            previous_script = (previous_staged / 'TestScripts' / source.relative_to(source_directory)
                               if previous_staged is not None else None)
            if (previous_script is not None and previous_script.is_file()
                    and destination.is_file()
                    and destination.read_bytes() == previous_script.read_bytes()):
                # Compare against the prior staged file, not a generated manifest.
                # Atomic replace avoids a partially written executable.
                replacement = destination.with_name(destination.name + '.stage-new')
                try:
                    if replacement.exists():
                        raise RuntimeError(f'Unexpected stage temporary file: {replacement}')
                    shutil.copy2(source, replacement)
                    replacement.chmod(file_mode)
                    replacement.replace(destination)
                finally:
                    replacement.unlink(missing_ok=True)
                print(f'[CinterForgeOS stage] UPDATED unchanged test script: {destination}', flush=True)
            elif source.relative_to(source_directory).as_posix() in (
                'dispatch/cinter-commands.sh', 'test/test.sh',
                'launch/launch-with-log.sh', 'launch/launch-cfaudio-linux.sh',
                'launch/launch-cfaudio-windows.sh'
            ):
                # This is the required command router, not an optional user
                # extension. An obsolete copy makes valid Cinter commands fail.
                # Preserve its exact bytes before atomically installing the
                # current repository router; never silently leave it stale.
                if not destination.is_file() or destination.is_symlink():
                    raise RuntimeError(f'Invalid installed Cinter command router: {destination}')
                if destination.read_bytes() != source.read_bytes():
                    archive = destination_directory.parent / 'LegacyScripts'
                    archive.mkdir(parents=True, exist_ok=True)
                    backup = archive / f'{source.name}.before-stage'
                    index = 1
                    while backup.exists() or backup.is_symlink():
                        backup = archive / f'{source.name}.before-stage.{index}'
                        index += 1
                    shutil.copy2(destination, backup)
                    replacement = destination.with_name(destination.name + '.stage-new')
                    try:
                        if replacement.exists() or replacement.is_symlink():
                            raise RuntimeError(f'Unexpected stage temporary file: {replacement}')
                        shutil.copy2(source, replacement)
                        replacement.chmod(file_mode)
                        replacement.replace(destination)
                    finally:
                        replacement.unlink(missing_ok=True)
                    print(f'[CinterForgeOS stage] UPDATED required Cinter command: {destination}; prior copy: {backup}', flush=True)
                else:
                    destination.chmod(file_mode)
                    print(f'[CinterForgeOS stage] VERIFIED required Cinter command: {destination}', flush=True)
            elif source.relative_to(source_directory).as_posix() in (
                'baseline/baseline-environments.py', 'inspect/inspect.sh'
            ):
                # Managed command entry points must be current: an old inspect.sh
                # silently routes "inspect installed" to legacy publisher inspection.
                # Preserve installed edits before replacing the incompatible entry point.
                archive = destination_directory.parent / 'LegacyScripts'
                archive.mkdir(parents=True, exist_ok=True)
                backup = archive / f'{source.name}.before-stage'
                index = 1
                while backup.exists() or backup.is_symlink():
                    backup = archive / f'{source.name}.before-stage.{index}'
                    index += 1
                shutil.copy2(destination, backup)
                replacement = destination.with_name(destination.name + '.stage-new')
                try:
                    if replacement.exists():
                        raise RuntimeError(f'Unexpected stage temporary file: {replacement}')
                    shutil.copy2(source, replacement)
                    replacement.chmod(file_mode)
                    replacement.replace(destination)
                finally:
                    replacement.unlink(missing_ok=True)
                print(f'[CinterForgeOS stage] UPDATED managed test command: {destination}; prior copy: {backup}', flush=True)
            else:
                print(f'[CinterForgeOS stage] PRESERVED user test script: {destination}', flush=True)
            continue

        try:
            with os.fdopen(descriptor, 'wb') as output, source.open('rb') as input_file:
                shutil.copyfileobj(input_file, output)
            print(f'[CinterForgeOS stage] INSTALLED test script: {destination}', flush=True)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise

    # Retire the two old Ardour launcher names. They are no longer valid public
    # commands; archive their original bytes outside the active Scripts tree.
    for retired_name in ('launch-ardour-linux.sh', 'launch-ardour-windows.sh'):
        retired = destination_directory / 'launch' / retired_name
        if not retired.exists() and not retired.is_symlink():
            continue
        if not retired.is_file() or retired.is_symlink():
            raise RuntimeError(f'Unexpected retired CFOS launcher: {retired}')
        legacy_directory.mkdir(parents=True, exist_ok=True)
        archived = legacy_directory / retired_name
        number = 1
        while archived.exists() or archived.is_symlink():
            archived = legacy_directory / f'{retired_name}.{number}'
            number += 1
        retired.rename(archived)
        print(f'[CinterForgeOS stage] RETIRED obsolete launcher: {retired} -> {archived}', flush=True)

    # Verify the complete active route, not just the dispatcher: old test.sh
    # can route back to Cinter, and missing launchers fail before Ardour starts.
    for relative in ('dispatch/cinter-commands.sh', 'test/test.sh',
                     'launch/launch-with-log.sh', 'launch/launch-cfaudio-linux.sh',
                     'launch/launch-cfaudio-windows.sh'):
        expected = source_directory / relative
        installed = destination_directory / relative
        if not expected.is_file() or not installed.is_file() or installed.is_symlink():
            raise RuntimeError(f'CFOS Stage missing required command: {installed}')
        if installed.read_bytes() != expected.read_bytes() or not os.access(installed, os.X_OK):
            raise RuntimeError(f'CFOS Stage left obsolete or nonexecutable command: {installed}')

    # A successful Stage must never silently leave the active router stale.
    router_source = source_directory / 'dispatch/cinter-commands.sh'
    router_installed = destination_directory / 'dispatch/cinter-commands.sh'
    if not router_source.is_file() or not router_installed.is_file():
        raise RuntimeError('CFOS Stage did not install the required Cinter command router')
    if router_source.read_bytes() != router_installed.read_bytes():
        raise RuntimeError(f'CFOS Stage left an obsolete Cinter command router: {router_installed}')
    if not os.access(router_installed, os.X_OK):
        raise RuntimeError(f'CFOS Stage router is not executable: {router_installed}')
    print(f'[CinterForgeOS stage] VERIFIED current command routing: {router_installed}', flush=True)


def portable_symlink(source: Path, item: Path) -> str:
    """Archive links relative to their installed location, never a build root.

    Upstream install tools may create absolute links inside their temporary
    installation prefix (for example /work/Install/lib/...). Resolve those
    against files in the payload, requiring a unique, longest matching path.
    External and ambiguous absolute links are rejected, never silently copied.
    """
    link = os.readlink(item)
    if not os.path.isabs(link):
        resolved = (item.parent / link).resolve()
        if not resolved.is_relative_to(source.resolve()):
            raise RuntimeError(f'Symlink escapes distribution: {item} -> {link}')
        return link

    # Compare complete path components, not arbitrary string suffixes. At
    # least two components must match to avoid guessing from a basename.
    parts = Path(link).parts[1:]
    matches: list[tuple[int, Path]] = []
    for length in range(2, len(parts) + 1):
        candidate = source.joinpath(*parts[-length:])
        if candidate.exists() and candidate.resolve().is_relative_to(source.resolve()):
            matches.append((length, candidate))
    if not matches:
        raise RuntimeError(f'Absolute symlink cannot be relocated inside payload: {item} -> {link}')
    longest = max(length for length, _ in matches)
    targets = [candidate for length, candidate in matches if length == longest]
    if len(targets) != 1:
        raise RuntimeError(f'Ambiguous absolute symlink in distribution: {item} -> {link}')
    return os.path.relpath(targets[0], start=item.parent)


def archive_zip(source: Path, archive: Path, *, exclude: frozenset[str] = frozenset(),
                root_name: str | None = None) -> None:
    archive.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(item for item in source.rglob('*')
                   if not any(item.relative_to(source).as_posix() == omitted or
                              item.relative_to(source).as_posix().startswith(omitted + '/')
                              for omitted in exclude))
    total = sum(item.stat().st_size for item in files if item.is_file() and not item.is_symlink())
    processed = 0
    reported = 0
    interval = max(total // 20, 64 * 1024 * 1024)
    began = time.monotonic()
    print(f'[Cinter package] Compressing {source} -> {archive.name}: '
          f'{total / (1024 ** 3):.2f} GiB uncompressed', flush=True)
    fd, tmp_name = tempfile.mkstemp(prefix='.cinter-zip-', suffix='.zip', dir=archive.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with zipfile.ZipFile(tmp, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as z:
            for item in files:
                relative = item.relative_to(source).as_posix()
                if root_name is not None:
                    relative = f'{root_name}/{relative}'
                if item.is_symlink():
                    link = portable_symlink(source, item)
                    info = zipfile.ZipInfo(relative)
                    info.create_system = 3
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    z.writestr(info, link)
                    continue
                if item.is_dir():
                    continue
                info = zipfile.ZipInfo.from_file(item, arcname=relative)
                info.compress_type = zipfile.ZIP_DEFLATED
                with item.open('rb') as f, z.open(info, 'w', force_zip64=True) as dest:
                    while True:
                        block = f.read(8 * 1024 * 1024)
                        if not block:
                            break
                        dest.write(block)
                        processed += len(block)
                        if processed - reported >= interval or processed == total:
                            elapsed = max(time.monotonic() - began, 0.001)
                            print(f'[Cinter package] {processed / (1024 ** 3):.2f}/'
                                  f'{total / (1024 ** 3):.2f} GiB '
                                  f'({100 * processed / max(total, 1):.0f}%), '
                                  f'{processed / (1024 ** 2) / elapsed:.0f} MiB/s; '
                                  f'current={relative}', flush=True)
                            reported = processed
        tmp.replace(archive)
        print(f'[Cinter package] Complete: {archive} '
              f'({archive.stat().st_size / (1024 ** 3):.2f} GiB)', flush=True)
    finally:
        tmp.unlink(missing_ok=True)



def _os_image_sidecar(archive: Path) -> Path:
    """Deterministic raw-disk companion to the OS distribution ZIP."""
    return archive.with_suffix('.img')


def _clone_os_image(source: Path, destination: Path) -> None:
    """Copy the raw guest disk without a byte-by-byte copy on APFS."""
    if not source.is_file() or source.stat().st_size == 0:
        raise RuntimeError(f'SteamOS raw disk missing: {source}')
    if source.resolve() == destination.resolve():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.cinter-disk-', suffix='.img', dir=destination.parent)
    os.close(fd)
    tmp = Path(temporary)
    tmp.unlink()
    try:
        if sys.platform == 'darwin':
            subprocess.run(['cp', '-c', str(source), str(tmp)], check=True)
        else:
            shutil.copy2(source, tmp)
        if tmp.stat().st_size != source.stat().st_size:
            raise RuntimeError(f'SteamOS raw disk size mismatch: {source} -> {tmp}')
        tmp.replace(destination)
    finally:
        tmp.unlink(missing_ok=True)


def _attach_os_image(archive: Path, extracted: Path) -> None:
    """Stage only the paired raw disk, never search other build/install caches."""
    sidecar = _os_image_sidecar(archive)
    if not sidecar.is_file():
        raise RuntimeError(f'Missing required SteamOS disk companion: {sidecar}; download both published artifacts')
    if (extracted / 'steamos.img').exists():
        raise RuntimeError(f'SteamOS disk duplicated inside distribution ZIP: {archive}')
    print(f'[Cinter stage] Attaching raw SteamOS disk: {sidecar}', flush=True)
    _clone_os_image(sidecar, extracted / 'steamos.img')


def extract_zip(archive: Path, target: Path) -> None:
    if not archive.is_file():
        raise RuntimeError(f'Completed ZIP missing; stage never builds: {archive}')
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.cinter-stage-', dir=target.parent) as td:
        payload = Path(td) / 'payload'
        payload.mkdir()
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                name = info.filename.replace('\\', '/')
                pure = Path(name)
                if not name or name.startswith('/') or any(p in ('..', '') for p in pure.parts) or re.match(r'^[A-Za-z]:', name):
                    raise RuntimeError(f'Unsafe ZIP member: {name}')
                mode = (info.external_attr >> 16) & 0o170000
                if mode not in (0, stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK):
                    raise RuntimeError(f'Unsupported ZIP member (link/device): {name}')
            for info in z.infolist():
                path = payload / info.filename
                if info.is_dir():
                    path.mkdir(parents=True, exist_ok=True)
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                mode_type = (info.external_attr >> 16) & 0o170000
                if mode_type == stat.S_IFLNK:
                    link = z.read(info).decode('utf-8')
                    if os.path.isabs(link) or not (path.parent / link).resolve().is_relative_to(payload.resolve()):
                        raise RuntimeError(f'ZIP symlink escapes application: {info.filename}')
                    path.symlink_to(link)
                    continue
                with z.open(info) as src, path.open('wb') as dst:
                    shutil.copyfileobj(src, dst, length=1024*1024)
                mode = (info.external_attr >> 16) & 0o777
                if mode:
                    path.chmod(mode)
        if target.exists():
            raise RuntimeError(f'Staged version already exists (will not overwrite): {target}')
        payload.rename(target)


def _verify_mac_install_copy(staged: Path, candidate: Path) -> None:
    """Reject an altered or unsigned candidate before publishing its install path."""
    staged_bundles = sorted(path.name for path in staged.glob('*.app'))
    candidate_bundles = sorted(path.name for path in candidate.glob('*.app'))
    if staged_bundles != candidate_bundles:
        raise RuntimeError(f'Mac bundle names changed during install copy: '
                           f'{staged_bundles} -> {candidate_bundles}')
    for name in candidate_bundles:
        source = staged / name
        bundle = candidate / name
        # Compare all regular Mach-O payloads as well as signatures. This
        # catches an installation copy that alters the actual signed bytes.
        import hashlib
        source_images = {str(p.relative_to(source)): p
                         for p in _mac_bundle_macho_files(source)}
        copied_images = {str(p.relative_to(bundle)): p
                         for p in _mac_bundle_macho_files(bundle)}
        if source_images.keys() != copied_images.keys():
            raise RuntimeError(f'Mach-O inventory changed during installation: {name}')
        with (bundle / 'Contents/Info.plist').open('rb') as stream:
            main_name = plistlib.load(stream).get('CFBundleExecutable')
        main_relative = f'Contents/MacOS/{main_name}'
        for relative, original in source_images.items():
            copied = copied_images[relative]
            if hashlib.sha256(original.read_bytes()).digest() != hashlib.sha256(copied.read_bytes()).digest():
                raise RuntimeError(f'Mach-O bytes changed during installation: {copied}')
            if relative != main_relative:
                result = subprocess.run(['codesign', '--verify', '--strict', str(copied)],
                                        capture_output=True, text=True)
                if result.returncode:
                    raise RuntimeError(f'Installed candidate has invalid embedded code: {copied}\n'
                                       f'{result.stdout}{result.stderr}')
        result = subprocess.run(['codesign', '--verify', '--deep', '--strict',
                                 '--verbose=2', str(bundle)], capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(f'Installed candidate bundle signature invalid: {bundle}\n'
                               f'{result.stdout}{result.stderr}')


def installed_copy(staged: Path, installed: Path) -> None:
    """Validate a metadata-preserving copy before making it the live install."""
    installed.parent.mkdir(parents=True, exist_ok=True)
    if installed.exists():
        raise RuntimeError(f'Installed version already exists (will not overwrite): {installed}')
    with tempfile.TemporaryDirectory(prefix='.cinter-install-', dir=installed.parent) as td:
        candidate = Path(td) / 'payload'
        if sys.platform == 'darwin':
            # ditto preserves macOS resource forks, extended attributes and
            # bundle metadata; shutil.copytree is not a macOS app installer.
            result = subprocess.run(['ditto', '--rsrc', '--extattr',
                                     str(staged), str(candidate)],
                                    capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError(f'ditto installation copy failed:\n'
                                   f'{result.stdout}{result.stderr}')
            _verify_mac_install_copy(staged, candidate)
        else:
            shutil.copytree(staged, candidate, symlinks=True)
        # Promotion happens only after the candidate is completely validated.
        candidate.rename(installed)


def stage_smoke_examples(app: str, platform: str, extracted: Path) -> None:
    """Place existing application-owned scripts alongside the staged product."""
    from shared.test.installed_test_staging import stage as stage_installed_tests
    stage_installed_tests(app, platform, extracted, suite_root=Path(__file__).resolve().parent)


def preserve_test_results(existing: Path, fresh: Path) -> None:
    """Same-version restage must not delete developer smoke-test results."""
    prior = existing / 'Tests' / 'Results'
    target = fresh / 'Tests' / 'Results'
    if prior.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        shutil.copytree(prior, target, dirs_exist_ok=True, symlinks=True)


def restage_version(archive: Path, staged: Path, installed: Path, platform: str, app: str) -> None:
    """Re-stage a same-version build without deleting either previous copy first.

    Unpack and validate before touching the existing stage or installation.
    Restore both if installation fails; never make a second product version.
    """
    staged.parent.mkdir(parents=True, exist_ok=True)
    installed.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.cinter-restage-', dir=staged.parent) as td:
        work = Path(td)
        fresh = work / 'fresh'
        extract_zip(archive, fresh)
        require_payload(fresh, platform)
        sign_mac_source_bundle(app, platform, fresh)
        stage_smoke_examples(app, platform, fresh)
        if installed.exists():
            preserve_test_results(installed, fresh)
        old_stage = work / 'previous-stage'
        old_install = installed.parent / ('.' + installed.name + '.previous-stage')
        if old_install.exists():
            raise RuntimeError(f'Incomplete previous installation replacement: {old_install}')
        had_stage = staged.exists()
        had_install = installed.exists()
        if had_stage:
            staged.rename(old_stage)
        try:
            fresh.rename(staged)
            if had_install:
                installed.rename(old_install)
            try:
                installed_copy(staged, installed)
            except BaseException:
                if installed.exists():
                    shutil.rmtree(installed)
                if had_install and old_install.exists():
                    old_install.rename(installed)
                raise
        except BaseException:
            if staged.exists():
                shutil.rmtree(staged)
            if had_stage and old_stage.exists():
                old_stage.rename(staged)
            raise
        # The previous staged tree is no longer needed after the replacement
        # succeeds.  Do not leave it for TemporaryDirectory's implicit cleanup:
        # macOS can create .DS_Store files during a recursive deletion, causing
        # its final rmdir to raise ENOTEMPTY *after* Stage has succeeded.
        for obsolete in (old_stage if had_stage else None,
                         old_install if had_install else None):
            if obsolete is None:
                continue
            for attempt in range(5):
                try:
                    shutil.rmtree(obsolete)
                    break
                except OSError as exc:
                    if exc.errno not in (errno.ENOTEMPTY, errno.EEXIST) or attempt == 4:
                        raise
                    time.sleep(0.1 * (attempt + 1))
        print(f'RESTAGED {staged} (same version)')
        print(f'REPLACED_INSTALLED {installed} (same version)')



def finalize_blender_install(platform: str, version: str, installed: Path) -> None:
    """Reject a mislabeled Blender install and retire only known legacy paths.

    Stage already installed the verified version before this is called. Never
    silently rename an upstream bundle as evidence of a source rebrand.
    """
    if platform == 'mac':
        bundles = list(installed.glob('*.app'))
        if len(bundles) != 1 or bundles[0].name != 'CinterForge 3D.app':
            raise RuntimeError(f'Blender macOS install must contain CinterForge 3D.app: {bundles}')
    platform_name = {'mac': 'macOS', 'linux': 'Linux', 'win': 'Windows'}[platform]
    applications = steamapps() / 'common/CinterForge/Applications'
    for old in (applications / platform_name / 'cf3d' / version,
                applications / platform / 'cf3d' / version):
        if not old.exists() or old == installed:
            continue
        if old.is_symlink():
            raise RuntimeError(f'Refusing to remove symlink legacy install: {old}')
        require_payload(old, platform)
        # An old install is only ours when it contains a recognizable Blender
        # payload. Do not remove unrelated user data in a similarly named tree.
        if platform == 'mac':
            known = any(bundle.name in ('Blender.app', 'CinterForge 3D.app')
                        for bundle in old.glob('*.app'))
        else:
            known = any(p.name.lower() in ('cf3d', 'cf3d.exe') for p in old.rglob('*') if p.is_file())
        if not known:
            raise RuntimeError(f'Unrecognized legacy Blender installation: {old}')
        # Preserve prior smoke results across migration.
        preserve_test_results(old, installed)
        shutil.rmtree(old)
        if not any(old.parent.iterdir()):
            old.parent.rmdir()
        print(f'RETIRED_LEGACY_INSTALLED {old}', flush=True)


def retire_blender_legacy_mac_launcher() -> None:
    old = Path.home() / 'Applications/cf3d.app'
    if not old.exists():
        return
    if not (old / 'Contents/Resources/CinterForgeManagedLauncher').is_file():
        print(f'RETAIN_UNMANAGED_LAUNCHER {old}', flush=True)
        return
    shutil.rmtree(old)
    print(f'RETIRED_LEGACY_LAUNCHER {old}', flush=True)


def mac_launcher(name: str, installed: Path) -> None:
    if sys.platform != 'darwin':
        print('Launcher deferred: not running on macOS')
        return
    app = next(installed.glob('*.app'))
    application_dir = Path.home() / 'Applications'
    application_dir.mkdir(parents=True, exist_ok=True)
    launcher = application_dir / f'{name}.app'
    owned = launcher / 'Contents/Resources/CinterForgeManagedLauncher'
    if launcher.exists() and not owned.is_file():
        raise RuntimeError(f'Refusing to replace unmanaged launcher: {launcher}')
    with tempfile.TemporaryDirectory(prefix='.cinter-launch-', dir=application_dir) as td:
        dest = Path(td) / launcher.name
        binary = dest / 'Contents/MacOS/CinterForgeLauncher'
        binary.parent.mkdir(parents=True)
        resources = dest / 'Contents/Resources'
        resources.mkdir()
        # Finder double-click executes a shell wrapper; real application remains inside Steam.
        import shlex
        binary.write_text('#!/bin/sh\nexec /usr/bin/open ' + shlex.quote(str(app)) + '\n')
        binary.chmod(0o755)
        (resources / 'CinterForgeManagedLauncher').write_text('Creative Suite managed launcher\n')
        with (app / 'Contents/Info.plist').open('rb') as fp:
            source_info = plistlib.load(fp)
        icon = source_info.get('CFBundleIconFile', '')
        iconfile = app / 'Contents/Resources' / icon
        if icon and not iconfile.exists() and not icon.endswith('.icns'):
            iconfile = iconfile.with_suffix('.icns')
        info = dict(CFBundleName=name, CFBundleDisplayName=name,
                    CFBundleIdentifier='io.cinterforge.creativesuite.' + re.sub(r'[^a-z0-9]+', '-', name.lower()),
                    CFBundlePackageType='APPL', CFBundleExecutable='CinterForgeLauncher')
        if iconfile.is_file():
            shutil.copy2(iconfile, resources / 'AppIcon.icns')
            info['CFBundleIconFile'] = 'AppIcon.icns'
        with (dest / 'Contents/Info.plist').open('wb') as fp:
            plistlib.dump(info, fp)
        if launcher.exists():
            shutil.rmtree(launcher)
        dest.rename(launcher)
    print(f'LAUNCHER {launcher}')



def retire_cfphoto_legacy_identity(version: str, installed: Path) -> None:
    """Retire only verified previous cfphoto installs and managed launchers.

    Do this after a successful installation, never before it.  Preserve test
    results and refuse to delete an unknown or symlinked application tree.
    """
    applications = steamapps() / 'common/CinterForge/Applications/macOS'
    for old in (applications / 'cfphoto' / version,
                applications / 'CinterForge RAW' / version):
        if not old.exists() or old == installed:
            continue
        if old.is_symlink() or not old.is_dir():
            raise RuntimeError(f'Refusing to retire unexpected legacy cfphoto path: {old}')
        bundles = list(old.glob('*.app'))
        if len(bundles) != 1 or bundles[0].name not in ('CinterForge RAW.app', 'CinterForge Photo.app'):
            raise RuntimeError(f'Refusing to retire unrecognized legacy cfphoto install: {old}')
        preserve_test_results(old, installed)
        shutil.rmtree(old)
        if not any(old.parent.iterdir()):
            old.parent.rmdir()
        print(f'RETIRED_LEGACY_INSTALLED {old}', flush=True)
    for name in ('cfphoto', 'CinterForge RAW'):
        old = Path.home() / 'Applications' / (name + '.app')
        if not old.exists():
            continue
        if old.is_symlink() or not (old / 'Contents/Resources/CinterForgeManagedLauncher').is_file():
            print(f'RETAIN_UNMANAGED_LAUNCHER {old}', flush=True)
            continue
        shutil.rmtree(old)
        print(f'RETIRED_LEGACY_LAUNCHER {old}', flush=True)


def finalize_staged_application(app: str, platform: str, version: str, installed: Path) -> None:
    """Apply installed-product checks and managed Finder launcher consistently."""
    if app == 'cf3d':
        finalize_blender_install(platform, version, installed)
    if platform == 'mac':
        mac_launcher(launcher_name(app), installed)
        if app == 'cfphoto':
            retire_cfphoto_legacy_identity(version, installed)
        if app == 'cf3d':
            retire_blender_legacy_mac_launcher()


def stage_cinterforge_os(staged: Path, installed: Path, version: str, app: str,
                         platform: str) -> None:
    """Stage the already-built Xcode OS product and preserve the last installation."""
    staged.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.cinter-os-stage-', dir=staged.parent) as td:
        fresh = Path(td) / 'staged'
        # The macOS application is built in Xcode. Stage consumes the
        # existing Xcode product and externally stored Cinter command
        # sources, never an older Cinter-generated ZIP.
        prepare_cinterforge_os_xcode_stage(fresh, version)
        require_payload(fresh, platform)
        require_cinterforge_os_runtime(fresh)
        # The guest EROFS and KDE defaults were produced by Build;
        # Stage only validates and installs the completed ZIP.
        stage_smoke_examples(app, platform, fresh)
        if installed.exists():
            preserve_test_results(installed, fresh)
        old = Path(td) / 'previous'
        if staged.exists():
            staged.rename(old)
        try:
            fresh.rename(staged)
        except BaseException:
            if old.exists():
                old.rename(staged)
            raise
        # The Swift launcher owns first-run disk discovery, user consent, and
        # Valve download/provisioning. Stage only installs the built application.
        # replace_installed_version retains existing user data without creating a disk.
        replace_installed_version(staged, installed)
        provision_cinterforge_os_dispatcher(staged)
        provision_cinterforge_os_test_scripts(staged, old if old.exists() else None)
    finalize_staged_application(app, platform, version, installed)
    print(f'STAGED {staged}\nINSTALLED {installed}')



def stage_standard_application(archive: Path, staged: Path, installed: Path,
                               app: str, platform: str, version: str) -> None:
    """Stage an ordinary app; retain its same-version replacement semantics."""
    if staged.exists() or installed.exists():
        restage_version(archive, staged, installed, platform, app)
    else:
        extract_zip(archive, staged)
        require_payload(staged, platform)
        sign_mac_source_bundle(app, platform, staged)
        stage_smoke_examples(app, platform, staged)
        installed_copy(staged, installed)
    finalize_staged_application(app, platform, version, installed)
    print(f'STAGED {staged}\nINSTALLED {installed}')


def export_staged_zip(staged: Path, builds: Path, app: str, platform: str, version: str) -> Path:
    """Publish a self-contained, Stage-validated application ZIP under Zipped/.

    Built ZIPs are the *inputs* to Stage. On macOS the staged bundle may have
    additional branding, rewritten library paths and final signatures. Export
    that final staged payload, not the older unsigned/unbranded Built ZIP.
    Archive creation is atomic, so a failure preserves the previous export.
    """
    require_payload(staged, platform)
    destination = builds / 'Zipped' / f'{app}-{platform}-{version}.zip'
    archive_zip(staged, destination, exclude=frozenset({'Logs', 'Tests'}))
    print(f'ZIPPED_STAGED {destination}', flush=True)
    return destination


def select_build_version(built: Path) -> str:
    """Select the existing product version, advancing only for explicit New builds.

    Version selection is pure: neither Build nor Stage creates state here.
    """
    marker = built / 'CURRENT_VERSION'
    current = version_value(marker.read_text().strip()) if marker.is_file() else '2026'
    if os.environ.get('CINTER_BUILD_CONFIGURATION') != 'New':
        return current
    if current == '2026':
        candidate = datetime.now().strftime('%Y.%m')
    elif re.fullmatch(r'\d{4}\.\d{1,2}', current):
        candidate = datetime.now().strftime('%Y.%m.%d')
    else:
        match = re.fullmatch(r'(\d{4}\.\d{1,2}\.\d{1,2})(?:\.(\d+))?', current)
        if not match:
            raise RuntimeError(f'Unrecognized product version for New: {current}')
        candidate = f'{match.group(1)}.{int(match.group(2) or 0) + 1}'
    return version_value(candidate)


def resolve_lifecycle_version(opts: argparse.Namespace, built: Path) -> str:
    """Resolve a version from the selected artifact, never from intermediate Output.

    Explicit versions win; package requires Build's version; Stage selects a
    completed ZIP; other commands resolve the most recent installed version.
    """
    if opts.version:
        return version_value(opts.version)
    if opts.command == 'package':
        raise RuntimeError('Build must set the timestamp once before running the adapter')
    if opts.command == 'stage':
        prefix = f'{opts.app}-{opts.platform}-'
        selected_zip = os.environ.get('CINTER_APP_ZIP')
        if selected_zip:
            filename = Path(selected_zip).name
            if not filename.startswith(prefix) or not filename.endswith('.zip'):
                raise RuntimeError(f'ZIP filename must match {prefix}<version>.zip: {filename}')
            return version_value(filename[len(prefix):-4])
        candidates = [path for path in built.glob(f'{prefix}*.zip') if path.is_file()]
        if not candidates:
            raise RuntimeError(f'No completed ZIP found in {built}; build first')
        return version_value(max(candidates, key=lambda path: path.name).name[len(prefix):-4])

    directory = installation_directory(opts.app, opts.platform)
    versions = [path.name for path in directory.iterdir() if path.is_dir()] if directory.is_dir() else []
    if not versions:
        raise RuntimeError(f'No installed versions for {opts.app} {opts.platform} in {directory}')
    return version_value(max(versions))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('command', choices=('package','stage','test/test.sh','resolve-version','resolve-steamapps','select-build-version'))
    ap.add_argument('app')
    ap.add_argument('platform', choices=('mac','win','linux'))
    ap.add_argument('--version', default=os.environ.get('CINTER_INSTALL_VERSION') or os.environ.get('CINTER_APP_VERSION'))
    ap.add_argument('--zip', dest='archive', type=Path)
    ap.add_argument('--built-payload', type=Path)
    opts = ap.parse_args()
    if opts.command == 'resolve-steamapps':
        print(steamapps())
        return
    builds = root_from_source() / 'Builds/CreativeSuite'
    built = builds / 'Built' / opts.app / opts.platform
    if opts.command == 'select-build-version':
        print(select_build_version(built))
        return
    version = resolve_lifecycle_version(opts, built)
    if opts.command == 'resolve-version':
        print(version)
        return
    name = f'{opts.app}-{opts.platform}-{version}.zip'
    archive = (opts.archive or (Path(os.environ['CINTER_APP_ZIP']) if opts.command == 'stage' and os.environ.get('CINTER_APP_ZIP') else built / name))
    staged = builds / 'Staged' / opts.platform / opts.app / version
    if opts.command == 'package':
        payload = opts.built_payload or built / version / 'Output'
        require_payload(payload, opts.platform)
        os_mac_package = opts.platform == 'mac' and opts.app.lower() in ('cinterforgeos', 'cinterforge-os')
        if os_mac_package:
            if (payload / 'steamos.img').exists():
                raise RuntimeError(f'VM disk must not be in the distributable: {payload}')
            # Build must not publish a ZIP that Stage will inevitably reject.
            # External tools are part of the CFOS distribution, not optional
            # files to be copied from a source checkout during Stage.
            require_cinterforge_os_runtime(payload)
            archive_zip(payload, archive)
        elif opts.platform == 'mac' and opts.app == 'cfimage':
            # Meson leaves the build prefix (bin/, lib/, share/, etc.) next to
            # the assembled GIMP bundle. All runtime assets are already copied
            # into Contents/Resources and its Mach-O dependencies are closed.
            # Install only the self-contained application, never those inputs.
            bundle = payload / 'CinterForge Image.app'
            required = (
                bundle / 'Contents/Info.plist',
                bundle / 'Contents/MacOS/cfimage-launcher',
                bundle / 'Contents/Resources/cfimage-launcher.sh',
                bundle / 'Contents/Resources/share/gimp/3.0',
                bundle / 'Contents/Resources/lib/gimp/3.0/plug-ins',
                bundle / 'Contents/Resources/lib/babl-0.1',
                bundle / 'Contents/Resources/lib/gegl-0.4',
            )
            if not bundle.is_dir() or bundle.is_symlink():
                raise RuntimeError(f'Image application bundle missing or unsafe: {bundle}')
            for item in required:
                if not item.exists():
                    raise RuntimeError(f'Incomplete Image application bundle: {item}')
            with (bundle / 'Contents/Info.plist').open('rb') as handle:
                info = plistlib.load(handle)
            if info.get('CFBundleIdentifier') != 'io.cinterforge.image':
                raise RuntimeError(f'Unexpected Image bundle identity: {bundle}')
            # Archive the completed bundle directly. A copied temporary bundle
            # can be mutated by concurrent build processes during cleanup,
            # causing TemporaryDirectory to fail after a successful ZIP.
            archive_zip(bundle, archive, root_name=bundle.name)
        elif opts.platform == 'mac' and opts.app == 'cfvector':
            # Inkscape's CMake prefix remains in Built/Output as packaging
            # input. Its bin/, lib/, share/, etc. must NEVER become installed
            # siblings of the self-contained CinterForge Vector.app.
            bundle = payload / 'CinterForge Vector.app'
            required = (
                bundle / 'Contents/Info.plist',
                bundle / 'Contents/MacOS/cfvector-launcher',
                bundle / 'Contents/Resources/bin/inkscape',
                bundle / 'Contents/Resources/bin/inkview',
                bundle / 'Contents/Resources/bin/dbus-run-session',
                bundle / 'Contents/Resources/bin/dbus-daemon',
                bundle / 'Contents/Resources/share/dbus-1/session.conf',
                bundle / 'Contents/Resources/share/inkscape',
            )
            if not bundle.is_dir() or bundle.is_symlink():
                raise RuntimeError(f'Vector application bundle missing or unsafe: {bundle}')
            for item in required:
                if not item.exists():
                    raise RuntimeError(f'Incomplete Vector application bundle: {item}')
            with (bundle / 'Contents/Info.plist').open('rb') as handle:
                info = plistlib.load(handle)
            if (info.get('CFBundleIdentifier') != 'io.cinterforge.vector'
                    or info.get('CFBundleExecutable') != 'cfvector-launcher'):
                raise RuntimeError(f'Unexpected Vector bundle identity: {bundle}')
            with tempfile.TemporaryDirectory(prefix='cfvector-package-', dir=archive.parent) as td:
                distribution = Path(td)
                shutil.copytree(bundle, distribution / bundle.name, symlinks=True)
                archive_zip(distribution, archive)
        elif opts.platform == 'mac' and opts.app == 'cfvideo':
            # Kdenlive's CMake install prefix contains bin/, lib/, share/ and
            # Tests/ next to the completed portable application. Those are
            # packaging inputs, never part of the installed distribution.
            bundle = payload / 'CinterForge Video.app'
            if not bundle.is_dir() or bundle.is_symlink():
                raise RuntimeError(f'Video application bundle missing or unsafe: {bundle}')
            for relative in ('Contents/Info.plist', 'Contents/MacOS'):
                if not (bundle / relative).exists():
                    raise RuntimeError(f'Incomplete Video application bundle: {bundle / relative}')
            with tempfile.TemporaryDirectory(prefix='cfvideo-package-', dir=archive.parent) as td:
                distribution = Path(td)
                shutil.copytree(bundle, distribution / bundle.name, symlinks=True)
                archive_zip(distribution, archive)
        elif opts.platform == 'mac' and opts.app == 'cfpaint':
            # Krita's CMake install also contains bin/, lib/, include/ and
            # share/ alongside the finished .app. They are build inputs, not
            # installed siblings. The app owns all runtime files internally.
            bundle = payload / 'CinterForge Paint.app'
            marker = bundle / 'Contents/Resources/kritaplugins'
            if not marker.is_dir():
                raise RuntimeError(f'Paint bundle resources missing: {marker}')
            with tempfile.TemporaryDirectory(prefix='cfpaint-package-', dir=archive.parent) as td:
                distribution = Path(td)
                # Copy without modifying the completed build payload.
                shutil.copytree(bundle, distribution / bundle.name, symlinks=True)
                archive_zip(distribution, archive)
        else:
            archive_zip(payload, archive)
        print(f'BUILT_ZIP {archive}')
    elif opts.command == 'stage':
        installed = installation_directory(opts.app, opts.platform) / version
        # A previous lifecycle version installed under internal identifiers
        # (Applications/linux/cfaudio). Correct it without rebuilding or
        # repackaging and without replacing a current installation.
        if migrate_legacy_install(opts.app, opts.platform, version, installed):
            finalize_staged_application(opts.app, opts.platform, version, installed)
            return
        # CinterForgeOS consumes its completed Xcode product instead of the
        # normal application ZIP and owns its specialized runtime preparation.
        if opts.platform == 'mac' and opts.app.lower() in ('cinterforgeos', 'cinterforge-os'):
            stage_cinterforge_os(staged, installed, version, opts.app, opts.platform)
        else:
            stage_standard_application(archive, staged, installed,
                                       opts.app, opts.platform, version)
            export_staged_zip(staged, builds, opts.app, opts.platform, version)
    else:
        installed = installation_directory(opts.app, opts.platform) / version
        require_payload(installed, opts.platform)
        # Presence alone is not a functional smoke. Explicitly refuse to label it a pass.
        raise RuntimeError(f'Functional installed-app smoke adapter required for: {installed}')

if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile) as e:
        # Include the exact failing operation when an APFS stage/rename fails.
        # A bare Errno 66 message omits the source and destination context.
        import traceback
        traceback.print_exc()
        print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(1)
