"""Package the native Krita install as CinterForge Paint (never Kdenlive)."""
from __future__ import annotations

from pathlib import Path
import plistlib
import shutil
import sys

SUITE_ROOT = next(p for p in Path(__file__).resolve().parents if p.name == 'creativeSuite')
sys.path.insert(0, str(SUITE_ROOT))
from shared.mac.dependency_closure import close_application_dependencies, _macho


def package_krita(output: Path, product_name: str) -> Path:
    if product_name != 'CinterForge Paint':
        raise RuntimeError(f'Unexpected Krita product identity: {product_name}')
    upstream = output / 'bin/krita.app'
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
                or cached_executable.name != 'krita'
                or not _macho(cached_executable)):
            raise RuntimeError(f'Unexpected cached Krita application: {target}')
        close_application_dependencies(target, output)
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
    if executable.name != 'krita' or not _macho(executable):
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
            destination = target / 'Contents' / ('Resources/share' if folder == 'share' else folder)
            shutil.copytree(source, destination, dirs_exist_ok=True, symlinks=True)
    close_application_dependencies(target, output)
    print(f'[cfpaint][mac] Packaged native Krita application: {target}', flush=True)
    return target
