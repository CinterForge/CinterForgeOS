"""Package the native Krita install as CinterForge Paint (never Kdenlive)."""
from __future__ import annotations

from pathlib import Path
import plistlib
import os
import shutil
import subprocess
import sys

SUITE_ROOT = next(p for p in Path(__file__).resolve().parents if p.name == 'creativeSuite')
sys.path.insert(0, str(SUITE_ROOT))
from shared.mac.dependency_closure import close_application_dependencies, _macho


def _krita_framework_roots() -> tuple[Path, ...]:
    """Resolve the Qt5 framework stack selected by Krita Setup, not Qt6."""
    brew = shutil.which('brew')
    if not brew:
        raise RuntimeError('Krita packaging requires the Setup-selected Homebrew qt@5')
    prefix = Path(subprocess.check_output(
        [brew, '--prefix', 'qt@5'], text=True).strip()).resolve()
    library_root = prefix / 'lib'
    network = library_root / 'QtNetwork.framework/Versions/5/QtNetwork'
    if not network.is_file() or not _macho(network.resolve()):
        raise RuntimeError(f'Krita Setup Qt5 framework missing: {network}')
    return (library_root,)


def _krita_private_library_roots(output: Path) -> tuple[Path, ...]:
    """Locate the pinned Qt5 QuaZip built by cfpaint Setup in this build only."""
    install = output.parent / 'Dependencies/QuaZipQt5-1.7.2/Install'
    if not install.is_dir():
        raise RuntimeError(f'Krita private Qt5 QuaZip installation missing: {install}; run ./Cinter setup cfpaint mac')
    candidates = sorted(path for path in install.rglob('libquazip1-qt5*.dylib')
                        if path.is_file() and _macho(path.resolve()))
    if not candidates:
        raise RuntimeError(f'Krita private Qt5 QuaZip Mach-O library missing under {install}; run ./Cinter setup cfpaint mac')
    # Versioned dylib aliases may be symlinks; all candidates must resolve
    # to the same actual installed library, not competing ABI versions.
    resolved = {path.resolve() for path in candidates}
    if len(resolved) != 1:
        raise RuntimeError(f'Ambiguous private Qt5 QuaZip libraries: {candidates}')
    return tuple(dict.fromkeys(path.parent for path in candidates))




def _install_genuine_sdl2(bundle: Path, output: Path) -> None:
    """Replace SDL2-compat with private upstream SDL2, including cached bundles."""
    install = output.parent / 'Dependencies/SDL2-2.30.12/Install'
    source = install / 'lib/libSDL2-2.0.0.dylib'
    if not source.is_file() or not _macho(source):
        # A completed Krita build may predate the pinned SDL2 dependency.
        # Repair that dependency in this app's own Builds tree during cached
        # packaging, without rebuilding Krita or requiring a separate command.
        from setup import provision_genuine_sdl2
        print('[cfpaint][mac] Provisioning missing private SDL2 for cached build', flush=True)
        provision_genuine_sdl2(output.parent)
    if not source.is_file() or not _macho(source):
        raise RuntimeError(f'Genuine SDL2 missing or invalid after provisioning: {source}')
    lib = bundle / 'Contents/lib/libSDL2-2.0.0.dylib'
    lib.parent.mkdir(parents=True, exist_ok=True)
    # Atomically replace the compatibility implementation, even in a cached
    # bundle whose SDL2 library was previously code-signed/read-only.
    temporary = lib.with_name(lib.name + '-genuine-new')
    try:
        shutil.copy2(source, temporary)
        temporary.chmod(0o755)
        os.replace(temporary, lib)
    finally:
        temporary.unlink(missing_ok=True)
    # Restore the original Krita entry point from our previous wrapper.
    macos = bundle / 'Contents/MacOS'
    original = macos / 'krita'
    preserved = macos / 'krita-real'
    if preserved.is_file():
        if not _macho(preserved):
            raise RuntimeError(f'Invalid original Krita executable: {preserved}')
        replacement = macos / 'krita-genuine-new'
        try:
            shutil.copy2(preserved, replacement)
            replacement.chmod(0o755)
            os.replace(replacement, original)
        finally:
            replacement.unlink(missing_ok=True)
        preserved.unlink()
    if not _macho(original):
        raise RuntimeError(f'Krita executable missing: {original}')
    (macos / 'cfpaint-launcher').unlink(missing_ok=True)
    (bundle / 'Contents/lib/libSDL3.0.dylib').unlink(missing_ok=True)
    info_path = bundle / 'Contents/Info.plist'
    with info_path.open('rb') as f:
        info = plistlib.load(f)
    info['CFBundleExecutable'] = 'krita'
    with info_path.open('wb') as f:
        plistlib.dump(info, f)
    print(f'[cfpaint][mac] Installed private genuine SDL2: {lib}', flush=True)


def _verify_native_sdl2(bundle: Path) -> None:
    """Fail packaging if the final dependency closure reintroduces SDL3/SDL2-compat."""
    lib = bundle / 'Contents/lib/libSDL2-2.0.0.dylib'
    if not _macho(lib):
        raise RuntimeError(f'Paint genuine SDL2 missing from finished bundle: {lib}')
    for candidate in (bundle / 'Contents').rglob('*'):
        if not candidate.is_file() or candidate.is_symlink():
            continue
        if candidate.name.startswith(('libSDL3', 'libSDL2-compat')):
            raise RuntimeError(f'Paint bundle contains obsolete SDL compatibility runtime: {candidate}')
        if not _macho(candidate):
            continue
        result = subprocess.run(['/usr/bin/otool', '-L', str(candidate)],
                                capture_output=True, text=True, check=True)
        for line in result.stdout.splitlines()[1:]:
            library = line.strip().split(' (compatibility version', 1)[0]
            if 'libSDL3' in library or 'libSDL2-compat' in library:
                raise RuntimeError(f'Paint Mach-O still depends on SDL3 compatibility: {candidate}: {library}')
    print('[cfpaint][mac] VERIFIED native SDL2-only bundle', flush=True)


def _install_bundle_resources(bundle: Path, output: Path) -> None:
    """Use Krita's actual macOS resource layout, also for cached builds.

    KoResourcePaths::getInstallationPrefix() requires
    Contents/Resources/kritaplugins. Contents/Resources/share/kritaplugins
    does NOT satisfy that check and causes qFatal before the GUI appears.
    """
    resources = bundle / 'Contents/Resources'
    resources.mkdir(parents=True, exist_ok=True)
    share = output / 'share'
    legacy = resources / 'share'
    if share.is_dir():
        shutil.copytree(share, resources, dirs_exist_ok=True, symlinks=True)
    elif legacy.is_dir():
        # Repair already-packaged/cached Krita installations.
        shutil.copytree(legacy, resources, dirs_exist_ok=True, symlinks=True)
    marker = resources / 'kritaplugins'
    if not marker.is_dir():
        raise RuntimeError(
            f'Krita installation resources missing: {marker}; '
            f'expected share/kritaplugins from the native CMake install')
    # Keep the app's payload entirely within Contents; the legacy nested
    # share copy is redundant once its contents are installed in Resources.
    if legacy.is_dir() and not legacy.is_symlink():
        shutil.rmtree(legacy)
    print(f'[cfpaint][mac] VERIFIED Krita bundle resources: {marker}', flush=True)


def package_krita(output: Path, product_name: str) -> Path:
    if product_name != 'CinterForge Paint':
        raise RuntimeError(f'Unexpected Krita product identity: {product_name}')
    upstream_candidates = [output / 'bin/cfpaint.app', output / 'bin/krita.app']
    upstream = next((candidate for candidate in upstream_candidates if candidate.is_dir() and not candidate.is_symlink()), upstream_candidates[0])
    target = output / (product_name + '.app')
    # Packaging moves bin/krita.app into the top-level product bundle.
    # A subsequent Build must validate that already-packaged bundle rather
    # than demand the upstream path that the first packaging consumed.
    if not upstream.exists() and not upstream.is_symlink() and target.is_dir() and not target.is_symlink():
        cached_info = target / 'Contents/Info.plist'
        if not cached_info.is_file():
            raise RuntimeError(f'Cached Krita bundle metadata missing: {cached_info}')
        with cached_info.open('rb') as f:
            cached = plistlib.load(f)
        cached_executable = target / 'Contents/MacOS' / str(cached.get('CFBundleExecutable', ''))
        if (cached.get('CFBundleName') != product_name
                or cached_executable.name not in ('krita', 'cfpaint', 'cfpaint-launcher')
                or not _macho(cached_executable)):
            raise RuntimeError(f'Unexpected cached Krita application: {target}')
        _install_bundle_resources(target, output)
        _install_genuine_sdl2(target, output)
        close_application_dependencies(
            target, output,
            extra_search_dirs=(_krita_private_library_roots(output) + (output.parent / "Dependencies/SDL2-2.30.12/Install/lib",)),
            framework_search_dirs=_krita_framework_roots())
        _verify_native_sdl2(target)
        print(f'[cfpaint][mac] Validated cached Krita application: {target}', flush=True)
        return target
    if not upstream.is_dir() or upstream.is_symlink():
        raise RuntimeError(f'Krita native installation missing: {upstream}')
    info_path = upstream / 'Contents/Info.plist'
    if not info_path.is_file():
        raise RuntimeError(f'Krita bundle metadata missing: {info_path}')
    with info_path.open('rb') as f:
        info = plistlib.load(f)
    executable = upstream / 'Contents/MacOS' / str(info.get('CFBundleExecutable', ''))
    if executable.name not in ('krita', 'cfpaint') or not _macho(executable):
        raise RuntimeError(f'Unexpected Krita Mach-O executable: {executable}')
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_dir():
            raise RuntimeError(f'Refusing to replace unexpected bundle: {target}')
        previous_info = target / 'Contents/Info.plist'
        if not previous_info.is_file():
            raise RuntimeError(f'Refusing to replace unrecognized bundle: {target}')
        with previous_info.open('rb') as f:
            previous = plistlib.load(f)
        if previous.get('CFBundleName') not in ('CinterForge Paint', 'krita', 'Krita'):
            raise RuntimeError(f'Refusing to replace foreign bundle: {target}')
        shutil.rmtree(target)
    upstream.rename(target)
    info['CFBundleName'] = product_name
    info['CFBundleDisplayName'] = product_name
    with (target / 'Contents/Info.plist').open('wb') as f:
        plistlib.dump(info, f)
    # Krita installs its libraries, paint engines, plugins and resource data
    # beside the native .app. Keep those payloads within the app before closure.
    for folder in ('lib', 'lib64', 'share', 'plugins'):
        source = output / folder
        if source.is_dir():
            destination = target / 'Contents' / ('Resources' if folder == 'share' else folder)
            shutil.copytree(source, destination, dirs_exist_ok=True, symlinks=True)
    _install_bundle_resources(target, output)
    _install_genuine_sdl2(target, output)
    close_application_dependencies(
        target, output,
        extra_search_dirs=(_krita_private_library_roots(output) + (output.parent / "Dependencies/SDL2-2.30.12/Install/lib",)),
        framework_search_dirs=_krita_framework_roots())
    _verify_native_sdl2(target)
    print(f'[cfpaint][mac] Packaged native Krita application: {target}', flush=True)
    return target


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: package.py <built Output directory>')
    result = package_krita(Path(sys.argv[1]).resolve(), 'CinterForge Paint')
    print(f'[cfpaint][mac] READY {result}', flush=True)


def package(source: Path, output: Path, product_name: str) -> Path:
    """Application-owned packaging entry point for the shared lifecycle."""
    return package_krita(output, product_name)
