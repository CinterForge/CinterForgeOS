"""Package Kdenlive's upstream macOS install as a standalone native app.

CMake installs Kdenlive in Output/bin/cfvideo.app with its data in
Output/share. Build consolidates these into Output/CinterForge Video.app;
Stage remains responsible for signing and installing the final bundle.
"""
from __future__ import annotations

from collections import deque
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
from functools import lru_cache


SUITE_ROOT = next(parent for parent in Path(__file__).resolve().parents
                  if parent.name == 'creativeSuite')
sys.path.insert(0, str(SUITE_ROOT))
from shared.mac.dependency_closure import (
    _command, _dependencies, _macho, close_application_dependencies,
)


# Resolve Apple's own frameworks against the selected macOS SDK instead of
# maintaining an incomplete allowlist (Network, DiskArbitration, etc.). This
# cannot mask missing Qt/KDE frameworks: only actual Apple SDK framework
# directories count as system frameworks.
@lru_cache(maxsize=1)
def _macos_sdk_framework_roots() -> tuple[Path, Path]:
    try:
        result = subprocess.run(
            ('/usr/bin/xcrun', '--sdk', 'macosx', '--show-sdk-path'),
            capture_output=True, text=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError(
            'Kdenlive framework relocation requires the macOS SDK; '
            'run ./Cinter setup cfvideo mac Release.'
        ) from error
    sdk = Path(result.stdout.strip())
    if not sdk.is_dir():
        raise RuntimeError(f'macOS SDK path is unavailable: {sdk}')
    return (
        sdk / 'System/Library/Frameworks',
        sdk / 'System/Library/PrivateFrameworks',
    )


def _apple_framework_install_name(framework_name: str, relative: str) -> str | None:
    for sdk_root in _macos_sdk_framework_roots():
        if (sdk_root / framework_name).is_dir():
            system_root = Path('/System/Library') / sdk_root.name
            return str(system_root / framework_name / relative)
    return None


def preflight_craft_framework_inventory(bundle: Path, craft_root: Path) -> None:
    """Discover ALL missing private frameworks before touching any load commands.

    Craft is the dependency authority. Inspect the app, its QML/plugins, and
    Craft's Qt/KF runtime images, including frameworks which will be reached
    only after a transitive dylib is copied into the app. A missing Craft
    framework is a Setup failure, not an invitation to use global Homebrew Qt.
    """
    roots = tuple(root for root in (craft_root / 'lib', craft_root / 'lib64',
                                    craft_root / 'Frameworks') if root.is_dir())
    if not roots:
        raise RuntimeError(f'KDE Craft installed framework roots missing: {craft_root}')
    binaries = {path for path in bundle.rglob('*') if _macho(path)}
    # Scan the already installed Qt/KF dependency images as well: examining
    # just the executable misses private libraries which closure copies later.
    for root in roots:
        for path in root.rglob('*'):
            if not path.is_file() or path.is_symlink():
                continue
            if ('.framework' in str(path) and '/Qt' in str(path)
                    or path.name.startswith(('libKF6', 'libQt6'))):
                if _macho(path):
                    binaries.add(path)
    missing: dict[str, set[str]] = {}
    ambiguous: dict[str, set[str]] = {}
    for binary in sorted(binaries):
        for reference in _dependencies(binary):
            if '.framework/' not in reference:
                continue
            framework = next((part for part in reference.split('/')
                              if part.endswith('.framework')), None)
            if framework is None:
                raise RuntimeError(f'Malformed framework reference: {binary}: {reference}')
            relative = reference.split(framework + '/', 1)[1]
            if not relative or '..' in Path(relative).parts:
                raise RuntimeError(f'Unsafe framework reference: {binary}: {reference}')
            if _apple_framework_install_name(framework, relative) is not None:
                continue
            if (bundle / 'Contents/Frameworks' / framework / relative).is_file():
                continue
            candidates = [root / framework for root in roots
                          if (root / framework / relative).is_file()]
            if not candidates:
                missing.setdefault(framework, set()).add(f'{binary.name}: {reference}')
            elif len({c.resolve() for c in candidates}) != 1:
                ambiguous.setdefault(framework, set()).add(f'{binary.name}: {reference}')
    if missing or ambiguous:
        details = []
        for label, inventory in (('MISSING from KDE Craft Setup', missing),
                                 ('AMBIGUOUS in KDE Craft Setup', ambiguous)):
            for framework, dependents in sorted(inventory.items()):
                details.append(f'  {label}: {framework}')
                details.extend(f'    {item}' for item in sorted(dependents)[:5])
        raise RuntimeError(
            'Kdenlive private framework dependency inventory incomplete; '
            'all discovered issues follow (not one at a time):\n'
            + '\n'.join(details) + '\n'
            + f'Craft prefix: {craft_root}\n'
            + 'Repair the exact Qt/KDE Craft dependency recipe through Setup; '
            'never substitute a global Qt installation.'
        )
    print(f'[cfvideo][mac] Preflight: scanned {len(binaries)} application '
          'and Craft Qt/KF Mach-O images; all referenced private frameworks '
          'exist in Setup-owned KDE Craft', flush=True)


def install_craft_qt_frameworks(bundle: Path, craft_root: Path) -> None:
    """Bundle and relocate the complete Qt frameworks used by KDE Craft.

    The generic dylib closure handles ordinary libraries, not framework
    containers. Preserve Qt's actual framework structure and resources here,
    before running the generic closure. Only the Kdenlive Setup-owned private
    Craft prefix may supply missing frameworks.
    """
    destination_root = bundle / 'Contents/Frameworks'
    search_roots = (craft_root / 'lib', craft_root / 'lib64',
                    craft_root / 'Frameworks')
    queue = deque(path for path in bundle.rglob('*') if _macho(path))
    visited: set[Path] = set()
    installed: set[str] = set()

    while queue:
        binary = queue.popleft()
        if binary in visited:
            continue
        visited.add(binary)
        for reference in _dependencies(binary):
            if '.framework/' not in reference:
                continue
            framework_name = next((part for part in reference.split('/')
                                   if part.endswith('.framework')), None)
            if framework_name is None:
                raise RuntimeError(f'Malformed framework load: {binary}: {reference}')
            relative = reference.split(framework_name + '/', 1)[1]
            if not relative or relative.startswith('../'):
                raise RuntimeError(f'Invalid framework executable path: {reference}')
            # The SDK identifies Apple-owned frameworks even when the dylib
            # resides only in macOS's dyld shared cache at runtime.
            system_install_name = _apple_framework_install_name(
                framework_name, relative
            )
            if system_install_name is not None:
                if reference != system_install_name:
                    _command('/usr/bin/install_name_tool', '-change', reference,
                             system_install_name, str(binary))
                continue
            destination = destination_root / framework_name
            if not destination.is_dir():
                sources = [root / framework_name for root in search_roots
                           if (root / framework_name).is_dir()]
                if len(sources) != 1:
                    raise RuntimeError(
                        f'Kdenlive Qt framework {framework_name} is not uniquely '
                        f'installed by Setup in KDE Craft: {sources}. '
                        'Run ./Cinter setup cfvideo mac Release.'
                    )
                destination_root.mkdir(parents=True, exist_ok=True)
                shutil.copytree(sources[0], destination, symlinks=True)
                installed.add(framework_name)
            framework_binary = destination / relative
            if not framework_binary.is_file() or not _macho(framework_binary.resolve()):
                raise RuntimeError(
                    f'Kdenlive framework has no Mach-O executable: '
                    f'{framework_binary} (from {reference})'
                )
            target = '@loader_path/' + os.path.relpath(framework_binary, binary.parent)
            if reference != target:
                _command('/usr/bin/install_name_tool', '-change', reference,
                         target, str(binary))
            # Framework dependencies must also be examined and relocated.
            queue.append(framework_binary.resolve())

    if installed:
        print(f'[cfvideo][mac] Bundled private KDE Craft Qt frameworks: '
              f'{", ".join(sorted(installed))}', flush=True)


def validate_splash_qml_runtime(bundle: Path) -> None:
    """Reject staged QML imports that would make Splash.rootObjects() empty.

    Kdenlive dereferences rootObjects().at(0) after loading its splash QML.
    Missing QtQuick or KI18n imports therefore produce a null-access crash,
    rather than a useful missing-module error.
    """
    import configparser
    conf_path = bundle / 'Contents/Resources/qt.conf'
    conf = configparser.ConfigParser(interpolation=None)
    conf.read(conf_path, encoding='utf-8')
    configured = conf.get('Paths', 'QmlImports', fallback=None)
    if configured != 'Resources/qml':
        raise RuntimeError(f'Kdenlive Qt QML path is not bundle-local: {conf_path}: {configured!r}')
    root = bundle / 'Contents/Resources/qml'
    for name in ('QtQuick', 'QtQuick/Controls', 'QtQuick/Layouts',
                 'QtQuick/Window', 'org/kde/ki18n'):
        module = root / name
        if not (module / 'qmldir').is_file():
            raise RuntimeError(f'Kdenlive splash QML import not packaged: {module}/qmldir')
        for line in (module / 'qmldir').read_text(encoding='utf-8').splitlines():
            words = line.split()
            if len(words) >= 2 and words[0] == 'plugin':
                suffixes = ('.dylib', '.so')
                if not any((module / (prefix + words[1] + suffix)).is_file()
                           for prefix in ('', 'lib') for suffix in suffixes):
                    raise RuntimeError(f'Kdenlive splash QML plugin missing: {module}/{words[1]}')
    for entry in root.rglob('*'):
        if entry.is_symlink():
            resolved = entry.resolve(strict=False)
            if not resolved.exists() or not resolved.is_relative_to(bundle.resolve()):
                raise RuntimeError(f'Kdenlive packaged QML link escapes bundle or is broken: {entry} -> {resolved}')
    print('[cfvideo][mac] Validated splash QML modules and bundle-local plugins', flush=True)


def install_craft_qt_plugins(bundle: Path, craft_root: Path) -> None:
    """Install the actual Qt runtime plugins from this app's pinned Craft SDK.

    Qt loads the Cocoa QPA plugin with QFactoryLoader, not via an executable
    Mach-O load command, so dylib closure alone cannot discover it. A valid
    framework closure without platforms/libqcocoa.dylib cannot start Qt GUI.
    """
    plugin_roots = (
        craft_root / 'plugins',
        craft_root / 'lib/plugins',
        craft_root / 'lib64/plugins',
        craft_root / 'lib/qt6/plugins',
        craft_root / 'lib64/qt6/plugins',
    )
    candidates = [root for root in plugin_roots
                  if (root / 'platforms/libqcocoa.dylib').is_file()]
    if len(candidates) != 1:
        raise RuntimeError(
            'Kdenlive requires exactly one Setup-owned Qt Cocoa platform '
            f'plugin tree (platforms/libqcocoa.dylib); found {candidates}. '
            f'Checked {plugin_roots}. Run ./Cinter setup cfvideo mac Release.'
        )
    source = candidates[0]
    destination = bundle / 'Contents/PlugIns'
    destination.mkdir(parents=True, exist_ok=True)
    installed = 0
    for category in sorted(source.iterdir()):
        if not category.is_dir() or category.is_symlink():
            continue
        # Only install Qt plugin categories with real dylibs. Craft also
        # contains developer/build-only metadata and potentially other assets.
        plugins = [p for p in category.glob('*.dylib') if _macho(p)]
        if not plugins:
            continue
        target = destination / category.name
        target.mkdir(parents=True, exist_ok=True)
        for plugin in plugins:
            copied = target / plugin.name
            shutil.copy2(plugin, copied)
            installed += 1
    cocoa = destination / 'platforms/libqcocoa.dylib'
    if not _macho(cocoa):
        raise RuntimeError(f'Kdenlive packaged Qt Cocoa plugin is invalid: {cocoa}')
    print(f'[cfvideo][mac] Bundled {installed} pinned Craft Qt plugins '
          f'(including Cocoa QPA) from {source}', flush=True)


def bundle_kio_workers(bundle: Path, craft_root: Path) -> None:
    """Bundle the KIO workers Craft actually installed, when available.

    Some macOS Craft builds of KF6 KIO do not build protocol workers. KIO
    still ships its linked libraries, and an absent *optional* file worker
    must not prevent packaging or starting the video editor. Report missing
    protocol functionality explicitly; never borrow Homebrew/host plugins.
    """
    roots = (
        craft_root / 'plugins', craft_root / 'lib/plugins',
        craft_root / 'lib64/plugins', craft_root / 'lib/qt6/plugins',
        craft_root / 'lib64/qt6/plugins',
    )
    matches = [root / 'kf6/kio' for root in roots
               if (root / 'kf6/kio').is_dir()]
    if len(matches) > 1:
        raise RuntimeError(f'Ambiguous private Craft KIO plugin trees: {matches}')
    if not matches:
        print('[cfvideo][mac] WARNING: private Craft KF6 KIO provides no '
              'protocol-worker directory; continuing without external KIO '
              'workers. KIO-based file/URL operations may be unavailable. '
              f'Checked: {roots}', file=sys.stderr, flush=True)
        return
    source = matches[0]
    plugins = sorted(p for p in source.rglob('*')
                     if p.suffix in ('.so', '.dylib') and _macho(p))
    if not plugins:
        print(f'[cfvideo][mac] WARNING: private Craft KIO worker directory '
              f'is empty: {source}; KIO-based file/URL operations may be '
              'unavailable.', file=sys.stderr, flush=True)
        return
    dest = bundle / 'Contents/PlugIns/kf6/kio'
    for plugin in plugins:
        target = dest / plugin.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(plugin, target)
    if not all(_macho(dest / p.relative_to(source)) for p in plugins):
        raise RuntimeError('KIO worker copy failed integrity check')
    if not any('file' in p.stem.lower() for p in plugins):
        print(f'[cfvideo][mac] WARNING: {source} lacks a local file-protocol '
              'worker; some KIO operations may fail.', file=sys.stderr,
              flush=True)
    print(f'[cfvideo][mac] Bundled {len(plugins)} private KF6 KIO '
          f'protocol workers: {dest}', flush=True)


def bundle_mlt_runtime(bundle: Path, output: Path, craft_root: Path) -> None:
    """Ship MLT's load-on-demand producers and profiles with its dylib.

    Dyld closure alone cannot discover MLT producers (notably `color`): MLT
    loads them via its own repository at runtime. Without the color producer,
    creating the very first blank Kdenlive document enters invalid MLT track
    state and can crash in mlt_multitrack_refresh().
    """
    module_candidates = (
        craft_root / 'plugins/mlt',  # Craft macOS moves lib/mlt here.
        craft_root / 'lib/mlt-7', craft_root / 'lib/mlt',
        output / 'plugins/mlt', output / 'lib/mlt-7', output / 'lib/mlt',
    )
    modules = [root for root in module_candidates if root.is_dir()
               and (any(root.glob('libmlt*.so')) or any(root.glob('libmlt*.dylib')))]
    if len(modules) != 1:
        raise RuntimeError(
            'Kdenlive MLT loadable producer repository must be installed by '
            f'Setup in exactly one location: {modules}; checked {module_candidates}. '
            'A plain libmlt-7 dylib is not a functional MLT installation. '
            'Run ./Cinter setup cfvideo mac Release.'
        )
    profiles_candidates = (
        craft_root / 'share/mlt-7', craft_root / 'share/mlt',
        output / 'share/mlt-7', output / 'share/mlt',
    )
    profiles = [root for root in profiles_candidates
                if (root / 'profiles').is_dir()
                and any((root / 'profiles').iterdir())]
    if len(profiles) != 1:
        raise RuntimeError(
            'Kdenlive MLT data/profiles must be installed by Setup in exactly '
            f'one location: {profiles}; checked {profiles_candidates}. '
            'Run ./Cinter setup cfvideo mac Release.'
        )
    # Upstream Kdenlive's macOS MLT discovery looks for Resources/mlt-7/profiles
    # (the compiled libName), then derives MacOS/melt from that profile path.
    # Do not rename the data directory to 'mlt': that breaks both lookups.
    # Keep Craft's macOS plug-in layout: MLT discovers modules in
    # Contents/PlugIns/mlt independently of Qt's separate plugin tree.
    module_dest = (bundle / 'Contents/PlugIns/mlt' if modules[0].parent.name == 'plugins'
                   else bundle / 'Contents/lib' / modules[0].name)
    data_dest = bundle / 'Contents/Resources' / profiles[0].name
    shutil.copytree(modules[0], module_dest, dirs_exist_ok=True, symlinks=True)
    shutil.copytree(profiles[0], data_dest, dirs_exist_ok=True, symlinks=True)
    if not (any(module_dest.glob('libmlt*.so')) or any(module_dest.glob('libmlt*.dylib'))):
        raise RuntimeError(f'MLT repository has no loadable modules: {module_dest}')
    if not (data_dest / 'profiles').is_dir():
        raise RuntimeError(f'MLT profile runtime missing: {data_dest / "profiles"}')
    # melt is a separate executable, not a dylib and therefore invisible to
    # Mach-O dependency closure. Require the pinned, Setup-owned renderer.
    melt_candidates = (craft_root / 'bin/melt', craft_root / 'bin/melt-7',
                       output / 'bin/melt', output / 'bin/melt-7')
    melt_sources = [source for source in melt_candidates if source.is_file()
                    and _macho(source)]
    if len({source.resolve() for source in melt_sources}) != 1:
        raise RuntimeError('Kdenlive requires exactly one Setup-owned MLT melt '
                           f'executable; found {melt_sources}. '
                           'Run ./Cinter setup cfvideo mac Release.')
    melt_dest = bundle / 'Contents/MacOS/melt'
    shutil.copy2(melt_sources[0], melt_dest)
    if not _macho(melt_dest) or not os.access(melt_dest, os.X_OK):
        raise RuntimeError(f'Kdenlive bundled melt is not executable: {melt_dest}')
    print(f'[cfvideo][mac] Bundled MLT renderer {melt_dest} and '
          f'private MLT repository {module_dest} and profiles {data_dest}', flush=True)



def bundle_frei0r_effects(bundle: Path, craft_root: Path) -> None:
    """Ship actual Frei0r plugins, not just MLT's frei0r loading module."""
    candidates = (craft_root / 'plugins/frei0r-1',
                  craft_root / 'lib/frei0r-1', craft_root / 'lib64/frei0r-1')
    sources = [root for root in candidates if root.is_dir()
               and any(p.suffix in ('.so', '.dylib') and _macho(p)
                       for p in root.rglob('*') if p.is_file())]
    if len(sources) != 1:
        raise RuntimeError('Exactly one private Frei0r plugin tree required; '
                           f'found {sources}; checked {candidates}. '
                           'Run ./Cinter setup cfvideo mac Release.')
    source = sources[0]
    # Standard Kdenlive macOS bundle layout; preserve vendor subdirectories.
    target = bundle / 'Contents/PlugIns/frei0r-1'
    shutil.copytree(source, target, dirs_exist_ok=True, symlinks=True)
    count = sum(1 for p in target.rglob('*') if p.is_file()
                and p.suffix in ('.so', '.dylib') and _macho(p))
    if count == 0:
        raise RuntimeError(f'Frei0r plugin packaging failed: {target}')
    print(f'[cfvideo][mac] Bundled {count} private Frei0r effects: {target}', flush=True)


def bundle_kdenlive_render(bundle: Path, output: Path) -> None:
    """Install the upstream rendering executable where Kdenlive expects it.

    CMake installs kdenlive_render beside cfvideo.app in Output/bin, rather
    than inside the application. Kdenlive locates this helper relative to its
    running executable; the standalone bundle must own it before dependency
    closure processes Mach-O load commands.
    """
    source = output / 'bin/kdenlive_render'
    destination = bundle / 'Contents/MacOS/kdenlive_render'
    if not source.is_file() or not _macho(source):
        raise RuntimeError(
            f'Kdenlive upstream rendering helper missing or invalid: {source}. '
            'Build must install the full upstream kdenlive_render target.'
        )
    shutil.copy2(source, destination)
    if not _macho(destination) or not os.access(destination, os.X_OK):
        raise RuntimeError(f'Kdenlive rendering helper is not executable: {destination}')
    print(f'[cfvideo][mac] Bundled native render helper: {destination}', flush=True)


def package_kdenlive(output: Path, product_name: str) -> Path:
    target = output / (product_name + '.app')

    # Upstream CMake installs bin/kdenlive.app. Branding happens here, after
    # installation, rather than assuming the upstream CMake target was renamed.
    upstream = output / 'bin/kdenlive.app'
    legacy = output / 'bin/cfvideo.app'
    if not upstream.is_dir() and legacy.is_dir():
        upstream = legacy
    if not upstream.is_dir():
        raise RuntimeError(f'Kdenlive native app is missing: {output / "bin/kdenlive.app"}')

    def installed_executable(bundle: Path) -> Path:
        info_file = bundle / 'Contents/Info.plist'
        if not info_file.is_file():
            raise RuntimeError(f'Kdenlive installed bundle lacks Info.plist: {bundle}')
        with info_file.open('rb') as handle:
            metadata = plistlib.load(handle)
        executable_name = metadata.get('CFBundleExecutable')
        if not isinstance(executable_name, str) or not executable_name:
            raise RuntimeError(f'Kdenlive installed bundle has no executable name: {info_file}')
        executable = bundle / 'Contents/MacOS' / executable_name
        if not executable.is_file():
            raise RuntimeError(f'Kdenlive installed bundle executable missing: {executable}')
        return executable

    executable = installed_executable(upstream)
    if executable.name not in ('kdenlive', 'cfvideo'):
        raise RuntimeError(f'Unexpected Kdenlive upstream executable: {executable}')
    if target.exists():
        if not target.is_dir() or target.is_symlink():
            raise RuntimeError(f'Kdenlive branded destination is not a bundle: {target}')
        shutil.rmtree(target)
    upstream.rename(target)
    executable = installed_executable(target)
    branded_executable = target / 'Contents/MacOS/cfvideo'
    if executable != branded_executable:
        if branded_executable.exists():
            raise RuntimeError(f'Kdenlive branded executable already exists: {branded_executable}')
        executable.rename(branded_executable)
    info_file = target / 'Contents/Info.plist'
    with info_file.open('rb') as handle:
        metadata = plistlib.load(handle)
    metadata['CFBundleExecutable'] = 'cfvideo'
    metadata['CFBundleName'] = product_name
    metadata['CFBundleDisplayName'] = product_name
    with info_file.open('wb') as handle:
        plistlib.dump(metadata, handle)
    print(f'[cfvideo][mac] Branded installed upstream bundle: {target}', flush=True)
    bundle = target

    # CMake may install native helper apps alongside the main executable.
    # Keep them inside the self-contained branded app, never under Output/bin.
    helpers = sorted(
        candidate for candidate in output.rglob('*.app')
        if candidate.is_dir()
        and candidate != bundle
        and not any(parent.suffix == '.app' for parent in candidate.relative_to(output).parents)
    )
    for helper in helpers:
        helper_executable = installed_executable(helper)
        destination = bundle / 'Contents/Helpers' / helper.name
        if destination.exists():
            raise RuntimeError(f'Kdenlive packaged helper already exists: {destination}')
        destination.parent.mkdir(parents=True, exist_ok=True)
        helper.rename(destination)
        print(f'[cfvideo][mac] Bundled native helper {helper_executable.name}: {destination}', flush=True)

    # The upstream install deliberately separates the macOS executable and
    # its KDE/MLT effect assets. Data must travel with the installed app.
    source_data = output / 'share'
    if not source_data.is_dir():
        raise RuntimeError(f'Kdenlive installed resources missing: {source_data}')
    data_target = bundle / 'Contents/Resources/share'
    shutil.copytree(source_data, data_target, dirs_exist_ok=True)
    print(f'[cfvideo][mac] Bundled installed application resources: {data_target}', flush=True)

    craft_root = output.parent / 'Dependencies/KDECraft/Root'
    private_lib = craft_root / 'lib'
    if not private_lib.is_dir():
        raise RuntimeError(f'Kdenlive private runtime dependency prefix missing: {private_lib}')
    # Kdenlive's splash imports QtQuick, Controls, Layouts and org.kde.ki18n.
    # The build-time QML_IMPORT_PATH does not survive a Finder/Steam launch.
    # Ship this app's pinned Craft QML modules and give Qt a bundle-local path.
    from apps.cfvideo.mac.configure import ki18n_qml_import_root
    qml_source = ki18n_qml_import_root(output.parent)
    qml_target = bundle / 'Contents/Resources/qml'
    if qml_target.is_dir():
        shutil.rmtree(qml_target)
    shutil.copytree(qml_source, qml_target, symlinks=True)
    for required in ('org/kde/ki18n/qmldir', 'QtQuick/qmldir'):
        if not (qml_target / required).is_file():
            raise RuntimeError(f'Kdenlive installed QML runtime is incomplete: {qml_target / required}')
    # Qt on macOS resolves the Paths entries relative to Contents. Keep all
    # Qt imports private to the installed, independently portable .app.
    qt_conf = bundle / 'Contents/Resources/qt.conf'
    if qt_conf.exists():
        import configparser
        conf = configparser.ConfigParser(interpolation=None)
        conf.optionxform = str
        conf.read(qt_conf, encoding='utf-8')
        if not conf.has_section('Paths'):
            conf.add_section('Paths')
    else:
        import configparser
        conf = configparser.ConfigParser(interpolation=None)
        conf.optionxform = str
        conf.add_section('Paths')
    conf.set('Paths', 'QmlImports', 'Resources/qml')
    conf.set('Paths', 'Plugins', 'PlugIns')
    with qt_conf.open('w', encoding='utf-8') as handle:
        conf.write(handle)
    print(f'[cfvideo][mac] Bundled private QML modules: {qml_target}', flush=True)
    validate_splash_qml_runtime(bundle)
    bundle_kdenlive_render(bundle, output)
    bundle_mlt_runtime(bundle, output, craft_root)
    bundle_frei0r_effects(bundle, craft_root)
    install_craft_qt_plugins(bundle, craft_root)
    bundle_kio_workers(bundle, craft_root)
    preflight_craft_framework_inventory(bundle, craft_root)
    install_craft_qt_frameworks(bundle, craft_root)
    close_application_dependencies(
        bundle, output, extra_search_dirs=(private_lib,),
        framework_search_dirs=(craft_root / 'lib', craft_root / 'lib64',
                               craft_root / 'Frameworks'),
    )
    # KDE Craft can install CMake intermediate objects alongside real Qt
    # permission plugins. These are build products, not loadable plugins.
    # Do not ship or sign them as part of the installed application.
    plugin_root = bundle / 'Contents/PlugIns'
    linker_metadata = set(bundle.rglob('*.prl'))
    if plugin_root.is_dir():
        for pattern in ('*.a', '*.o', '*.obj', '*.lo', '*.la'):
            linker_metadata.update(plugin_root.rglob(pattern))
    for metadata in sorted(linker_metadata):
        if metadata.is_symlink() or not metadata.is_file():
            raise RuntimeError(f'Unexpected Qt build-only file: {metadata}')
        metadata.unlink()
    if linker_metadata:
        print(f'[cfvideo][mac] Excluded {len(linker_metadata)} Qt '
              f'build-only files from runtime bundle', flush=True)
    # Generated CMake object directories must never be installed under PlugIns.
    if plugin_root.is_dir():
        for directory in sorted(plugin_root.rglob('objects-*'), reverse=True):
            if not directory.is_dir():
                continue
            remaining = [item for item in directory.rglob('*') if item.is_file() or item.is_symlink()]
            if remaining:
                raise RuntimeError(
                    f'Kdenlive Qt plugin build directory contains unexpected '
                    f'files: {directory}: {remaining[:5]}'
                )
            shutil.rmtree(directory)
    print(f'[cfvideo][mac] Validated relocatable native bundle: {bundle}', flush=True)
    return bundle


def package(source: Path, output: Path, product_name: str) -> Path:
    """Application-owned packaging entry point for the shared lifecycle."""
    return package_kdenlive(output, product_name)
