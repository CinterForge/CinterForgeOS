#!/usr/bin/env python3
"""Prepare Kdenlive macOS toolchain and resume its private KDE Craft dependency prefix."""
from __future__ import annotations
import shutil
import json
import re
import os
import hashlib
import urllib.request
import tarfile
import subprocess
import sys
from pathlib import Path

SUITE_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SUITE_ROOT))
from shared.mac.dependencies import run, provision_brew as _shared_provision_brew, provision_ecm, provision_pkg_config

def validate_toolchain(app: str) -> None:
    from shared.mac.source_setup import validate
    validate(app)

provision_brew = _shared_provision_brew

def clean_craft_finder_metadata(craft_root: Path) -> None:
    """Remove Finder metadata which KDE Craft cannot merge as package files.

    Finder can leave .DS_Store within the installation prefix. During Craft's
    staged gettext install, a stray file conflicts with a directory of the
    same name and aborts mergeTree. Only remove the exact macOS metadata file
    inside this application's private Craft prefix; preserve all real outputs.
    """
    if not craft_root.is_dir():
        return
    count = 0
    for directory, dirs, files in __import__('os').walk(craft_root):
        # A .DS_Store directory is unexpected; don't follow or destroy it.
        if '.DS_Store' in dirs:
            raise RuntimeError(f'Unexpected directory named .DS_Store: {Path(directory) / ".DS_Store"}')
        if '.DS_Store' in files:
            metadata = Path(directory) / '.DS_Store'
            # Finder metadata may itself be a symlink in a Craft build tree.
            # Unlink the directory entry only; never follow its target.
            if not (metadata.is_symlink() or metadata.is_file()):
                raise RuntimeError(f'Unexpected Finder metadata type: {metadata}')
            metadata.unlink()
            count += 1
    if count:
        print(f'[Creative Suite mac][dependencies] Removed {count} Finder .DS_Store '
              f'files from private KDE Craft prefix: {craft_root}', flush=True)

def repair_boost_numpy2_source(craft_root: Path) -> bool:
    """Apply the upstream NumPy 2 descriptor API correction to private Boost 1.86.

    Craft 26.10 builds Boost.Python with NumPy 2, where PyArray_Descr's
    elsize member is no longer public. Preserve Boost.NumPy rather than
    disabling the library or changing the Python/NumPy dependency graph.
    Only patch the exact known upstream source expression.
    """
    source = (craft_root / 'build' / 'libs' / 'boost' / 'work' /
              'boost-1.86.0' / 'libs' / 'python' / 'src' / 'numpy' / 'dtype.cpp')
    if not source.is_file():
        return False
    original = 'reinterpret_cast<PyArray_Descr*>(ptr())->elsize'
    corrected = 'PyDataType_ELSIZE(reinterpret_cast<PyArray_Descr*>(ptr()))'
    contents = source.read_text(encoding='utf-8')
    if original not in contents:
        if corrected in contents:
            return False
        raise RuntimeError(f'Unexpected Boost.NumPy dtype implementation: {source}')
    if contents.count(original) != 1:
        raise RuntimeError(f'Expected exactly one Boost.NumPy descriptor access: {source}')
    source.write_text(contents.replace(original, corrected), encoding='utf-8')
    print(f'[cfvideo][mac] Applied upstream NumPy 2 descriptor API fix: {source}', flush=True)
    return True


def repair_and_resume_boost_numpy2(craft_root: Path, environment: Path) -> None:
    """Resume Craft's failed Boost target without restarting its dependencies.

    Use Craft's own actions to register the rebuilt package and then allow
    the ordinary install-deps command to resume its dependency graph.
    """
    if not repair_boost_numpy2_source(craft_root):
        raise RuntimeError('KDE Craft dependency preparation failed, but the known '
                           'Boost 1.86 NumPy 2 source incompatibility was not present')
    command = 'set -e; source "$1"; shift; exec craft "$@"'
    run('/bin/bash', '-c', command, 'craft-boost-repair', str(environment),
        '--compile', '--install', '--qmerge', 'libs/boost')


def resume_kio_dirlister_autotest(craft_root: Path, environment: Path, *, resume: bool = True) -> bool:
    """Repair KIO 6.31's DBus-dependent autotest without cleaning Craft objects.

    The upstream test emits KDirNotify DBus events, but its target does not
    explicitly link Qt6::DBus. Consequently QT_DBUS_LIB is not defined, the
    interface header omits org::kde::KDirNotify, and compilation fails.
    Fix the test target rather than weakening or disabling its assertions.
    """
    work = craft_root / 'build' / 'kde' / 'frameworks' / 'tier3' / 'kio' / 'work'
    source_tree = work / 'kio-6.31.0'
    source = source_tree / 'autotests' / 'kdirlistertest.cpp'
    cmake = source_tree / 'autotests' / 'CMakeLists.txt'
    header = source_tree / 'src' / 'core' / 'kdirnotify.h'
    implementation = source_tree / 'src' / 'core' / 'kdirnotify.cpp'
    build = work / 'build'
    if not all(path.is_file() for path in (source, cmake, header, implementation, build / 'build.ninja')):
        return False

    contents = source.read_text(encoding='utf-8')
    if ('org::kde::KDirNotify::emitFilesRemoved' not in contents
            or 'org::kde::KDirNotify::emitFileRenamed' not in contents):
        return False

    # KIO's source header is the authoritative interface. Qt6::DBus adds the
    # QT_DBUS_LIB compile definition and library required by that header.
    include = '#include "kdirnotify.h"'
    if include not in contents:
        source.write_text(include + '\n' + contents, encoding='utf-8')
        print(f'[cfvideo][mac] Include KIO DBus interface in test: {source}', flush=True)

    cmake_text = cmake.read_text(encoding='utf-8')
    # ECMAddTests already links kdirlistertest with the plain CMake signature.
    # Mixing PRIVATE with that signature makes regeneration fail. Correct a
    # previously injected keyword link in place, preserving Ninja objects.
    invalid_link = 'target_link_libraries(kdirlistertest PRIVATE Qt6::DBus)'
    link = 'target_link_libraries(kdirlistertest Qt6::DBus)'
    if invalid_link in cmake_text:
        if cmake_text.count(invalid_link) != 1:
            raise RuntimeError(f'Ambiguous KIO DBus link repair: {cmake}')
        cmake_text = cmake_text.replace(invalid_link, link)
        cmake.write_text(cmake_text, encoding='utf-8')
        print(f'[cfvideo][mac] Corrected KIO DBus link to plain signature: {cmake}', flush=True)
    elif link not in cmake_text:
        marker = 'set_tests_properties(kiofilewidgets-kfileplacesmodeltest PROPERTIES RUN_SERIAL TRUE)'
        if cmake_text.count(marker) != 1:
            raise RuntimeError(f'KIO 6.31 autotest CMake target layout changed: {cmake}')
        cmake.write_text(cmake_text.replace(marker, link + '\n  ' + marker), encoding='utf-8')
        print(f'[cfvideo][mac] Link KIO kdirlistertest to Qt6 DBus: {cmake}', flush=True)

    # On Craft builds without HAVE_QTDBUS, KF6KIOCore does not compile
    # kdirnotify.cpp, although this autotest directly calls its methods.
    # Compiling that existing implementation into this test resolves the
    # missing methods without rebuilding KIOCore or dropping test coverage.
    implementation_entry = 'target_sources(kdirlistertest PRIVATE ../src/core/kdirnotify.cpp)'
    cmake_text = cmake.read_text(encoding='utf-8')
    if implementation_entry not in cmake_text:
        if cmake_text.count(link) != 1:
            raise RuntimeError(f'Cannot locate KIO test DBus linkage: {cmake}')
        cmake.write_text(cmake_text.replace(link, link + '\n' + implementation_entry), encoding='utf-8')
        print(f'[cfvideo][mac] Compile missing KDirNotify implementation into test: {cmake}', flush=True)

    if not resume:
        return True
    print('[cfvideo][mac] Resume existing KIO Ninja objects (no clean rebuild)', flush=True)
    run(str(craft_root / 'dev-utils' / 'bin' / 'ninja'), '-C', str(build))
    script = 'set -e; source "$1"; shift; exec craft "$@"'
    run('/bin/bash', '-c', script, 'craft-kio-resume', str(environment),
        '--install', '--qmerge', 'kde/frameworks/tier3/kio')
    return True


def install_craft_certificate_bundle(craft_root: Path) -> None:
    """Install an existing trusted CA bundle where Craft's curl expects it."""
    import ssl

    bundle = craft_root / 'etc' / 'cacert.pem'
    if bundle.is_file() and bundle.stat().st_size > 0:
        return
    candidates = [Path('/etc/ssl/cert.pem')]
    defaults = ssl.get_default_verify_paths()
    if defaults.cafile:
        candidates.append(Path(defaults.cafile))
    try:
        import certifi
        candidates.append(Path(certifi.where()))
    except ImportError:
        pass
    for candidate in candidates:
        if candidate.is_file() and candidate.stat().st_size > 0:
            bundle.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(candidate, bundle)
            print(f'[cfvideo][mac] Installed system trust bundle for Craft: {bundle}', flush=True)
            return
    raise RuntimeError('No trusted CA bundle available; cannot resume Craft securely')

def resume_gettext_image_merge(craft_root: Path, craft_script: Path) -> bool:
    """Merge a completed gettext image without rebuilding its compiled contents.

    A failed Craft merge can leave a fully installed gettext image in the
    private build prefix. Remove macOS Finder metadata from both sides of
    that merge, then ask Craft to register the image in its own database.
    Never report success unless Craft's qmerge command completes.
    """
    image_root = craft_root / 'build' / 'libs' / 'gettext' / 'image-RelWithDebInfo-1.0'
    staged_root = image_root.joinpath(*craft_root.parts[1:])
    # A gettext install produces the staged documentation; do not treat a
    # partially created image as a completed installation.
    if not (staged_root / 'share' / 'doc' / 'gettext').is_dir():
        return False
    clean_craft_finder_metadata(craft_root)
    print('[cfvideo][mac] Retrying Craft gettext qmerge from completed image '
          '(no gettext rebuild)', flush=True)
    run(sys.executable, str(craft_script), '--qmerge', 'libs/gettext')
    return True



def resume_python_image_merge(craft_root: Path, craft_script: Path) -> bool:
    """Finish Craft Python's compiled installation after a Finder metadata collision.

    Craft completed Python's build and image installation, but mergeTree
    rejected a Finder .DS_Store file in the image. Retry its native qmerge
    rather than calling `craft` again (which runs cleanimage/reinstalls).
    """
    python_build = craft_root / 'build' / 'libs' / 'python'
    for image_root in sorted(python_build.glob('image-RelWithDebInfo-*')):
        if not image_root.is_dir() or image_root.is_symlink():
            continue
        staged_root = image_root.joinpath(*craft_root.parts[1:])
        framework = staged_root / 'lib' / 'Python.framework' / 'Versions' / '3.12' / 'Python'
        if not framework.is_file():
            continue
        clean_craft_finder_metadata(craft_root)
        print('[cfvideo][mac] Retrying Craft Python qmerge from completed image '
              '(preserving compiled Python)', flush=True)
        run(sys.executable, str(craft_script), '--qmerge', 'libs/python')
        restore_craft_python_prefix_layout(craft_root)
        validate_craft_python_launcher(craft_root)
        return True
    return False



def restore_craft_python_prefix_layout(craft_root: Path) -> bool:
    """Correct Craft's nested DESTDIR layout after its Python image qmerge.

    Python's macOS framework recipe stages a complete absolute install prefix
    under DESTDIR. Craft's qmerge can preserve that prefix *inside* the Craft
    root rather than place it at the root. Its reported success then leaves
    PipBuildSystem unable to find <root>/bin/python3. Move only the installed
    Python image's top-level entries to their intended prefix location.
    The compiled build tree and Craft package database remain untouched.
    """
    nested_prefix = craft_root.joinpath(*craft_root.parts[1:])
    # Framework binaries in the staged DESTDIR can be symlinks whose absolute
    # targets do not exist until the prefix is relocated. Checking is_file()
    # here incorrectly skips a perfectly valid staged framework.
    nested_versions = nested_prefix / 'lib' / 'Python.framework' / 'Versions' / '3.12'
    if not nested_versions.is_dir():
        return False
    if not any(nested_versions.iterdir()):
        raise RuntimeError(f'Empty staged Python framework: {nested_versions}')
    if nested_prefix == craft_root or craft_root not in nested_prefix.parents:
        raise RuntimeError(f'Invalid nested Craft Python prefix: {nested_prefix}')
    # CPython's macOS framework installation also stages public development
    # headers under include/ and auxiliary executables under libexec/. Keep
    # these actual upstream payloads rather than discarding them or treating
    # a successful qmerge as a failure. Reject other unexpected entries.
    allowed = {'bin', 'include', 'lib', 'libexec', 'share', 'Python Launcher.app', 'IDLE.app'}
    entries = [entry for entry in nested_prefix.iterdir() if entry.name != '.DS_Store']
    unexpected = sorted(entry.name for entry in entries if entry.name not in allowed)
    if unexpected:
        raise RuntimeError(f'Unexpected entries in staged Craft Python prefix: {unexpected}')
    def merge_staged_entry(source: Path, destination: Path) -> None:
        """Merge a package entry, accepting preexisting identical symlinks.

        Python's DESTDIR image and an interrupted Craft qmerge can contain
        the same absolute framework/bin links. shutil.copytree(...,
        symlinks=True, dirs_exist_ok=True) raises FileExistsError for those
        links even when they are identical. Reconcile links explicitly while
        preserving unrelated installed packages in the shared Craft prefix.
        """
        if source.is_symlink():
            target = os.readlink(source)
            if destination.is_symlink():
                if os.readlink(destination) != target:
                    raise RuntimeError(
                        f'Conflicting Craft Python symlink: {destination}: '
                        f'{os.readlink(destination)!r} != {target!r}'
                    )
                return
            if os.path.lexists(destination):
                raise RuntimeError(f'Craft Python symlink conflicts with existing entry: {destination}')
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.symlink_to(target)
        elif source.is_dir():
            if os.path.lexists(destination) and (destination.is_symlink() or not destination.is_dir()):
                raise RuntimeError(f'Craft Python directory conflicts with existing entry: {destination}')
            destination.mkdir(parents=True, exist_ok=True)
            for child in source.iterdir():
                if child.name == '.DS_Store':
                    continue
                merge_staged_entry(child, destination / child.name)
        elif source.is_file():
            if os.path.lexists(destination) and (destination.is_symlink() or not destination.is_file()):
                raise RuntimeError(f'Craft Python file conflicts with existing entry: {destination}')
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        else:
            raise RuntimeError(f'Unsupported staged Python file: {source}')

    for source in entries:
        merge_staged_entry(source, craft_root / source.name)
    print(f'[cfvideo][mac] Restored Python DESTDIR prefix layout: '
          f'{nested_prefix} -> {craft_root}', flush=True)
    return True


def validate_craft_python_launcher(craft_root: Path) -> None:
    """Verify Craft's expected Python launcher after merging its macOS framework.

    KDE Craft's PipBuildSystem looks specifically for <craft-root>/bin/python3.
    Python's framework installation can supply the interpreter without that
    root-level launcher after recovery from an interrupted image merge.
    """
    launcher = craft_root / 'bin' / 'python3'
    if launcher.is_file():
        return
    if os.path.lexists(launcher) and not launcher.is_symlink():
        raise RuntimeError(f'Craft Python launcher is not a regular file or symlink: {launcher}')

    framework_versions = craft_root / 'lib' / 'Python.framework' / 'Versions'
    interpreters = sorted(framework_versions.glob('3.*/bin/python3*'))
    interpreters = [path for path in interpreters
                    if path.name.count('.') == 1 and path.is_file()]
    if len(interpreters) != 1:
        raise RuntimeError(
            f'Craft Python qmerge completed without {launcher}; expected one '
            f'installed framework interpreter, found: {interpreters}'
        )
    interpreter = interpreters[0]
    if not os.access(interpreter, os.X_OK):
        raise RuntimeError(f'Craft framework Python is not executable: {interpreter}')
    # Use a relative link so all runtime references remain inside Craft's
    # dedicated Kdenlive dependency prefix.
    launcher.parent.mkdir(parents=True, exist_ok=True)
    if launcher.is_symlink():
        launcher.unlink()
    launcher.symlink_to(os.path.relpath(interpreter, launcher.parent))
    print(f'[cfvideo][mac] Restored Craft Python launcher: {launcher} -> '
          f'{os.readlink(launcher)}', flush=True)


def restore_preserved_python_image(craft_root: Path) -> bool:
    """Restore Craft's interrupted Python install image for its native qmerge.

    An earlier Setup retired the image to PreservedImages after a failed
    cleanimage. The compiled Python and the image remain valid; put the image
    back at Craft's expected exact path before invoking its package manager.
    Do not move aside or replace a newer active image.
    """
    python_build = craft_root / 'build' / 'libs' / 'python'
    active_image = python_build / 'image-RelWithDebInfo-3.12.13'
    if active_image.exists() or active_image.is_symlink():
        if not active_image.is_dir() or active_image.is_symlink():
            raise RuntimeError(f'Invalid Craft Python image: {active_image}')
        return False

    preserved_root = craft_root.parent / 'PreservedImages' / 'libs-python'
    candidates = sorted(
        (item for item in preserved_root.glob('image-RelWithDebInfo-3.12.13*')
         if item.is_dir() and not item.is_symlink()),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return False

    for preserved in candidates:
        staged_root = preserved.joinpath(*craft_root.parts[1:])
        framework = staged_root / 'lib' / 'Python.framework' / 'Versions' / '3.12' / 'Python'
        if framework.is_file():
            python_build.mkdir(parents=True, exist_ok=True)
            preserved.rename(active_image)
            print(f'[cfvideo][mac] Restored compiled Craft Python image '
                  f'{preserved} -> {active_image}', flush=True)
            return True
    raise RuntimeError(
        f'Preserved Craft Python images have no staged 3.12 framework: {candidates}'
    )


def ensure_mlt_runtime(craft_root: Path, craft_environment: Path) -> None:
    """Install the MLT application runtime, not merely its linkable library.

    Craft's `--install-deps cfvideo` installs build dependencies but does not
    guarantee that MLT's loadable service modules, profiles, and `melt` tool
    are present. Use the same private Craft package for the complete runtime.
    """
    # Craft's macOS MLT blueprint relocates lib/mlt to plugins/mlt at install.
    module_roots = (craft_root / 'plugins/mlt', craft_root / 'lib/mlt-7',
                    craft_root / 'lib/mlt')
    profile_roots = (craft_root / 'share/mlt-7', craft_root / 'share/mlt')
    melt_paths = (craft_root / 'bin/melt', craft_root / 'bin/melt-7')

    def ready() -> bool:
        return (any(root.is_dir() and (any(root.glob('libmlt*.so')) or any(root.glob('libmlt*.dylib')))
                    for root in module_roots)
                and any((root / 'profiles').is_dir()
                        and any((root / 'profiles').iterdir()) for root in profile_roots)
                and any(path.is_file() and os.access(path, os.X_OK)
                        for path in melt_paths))

    if ready():
        print('[cfvideo][mac] Complete private MLT runtime already installed', flush=True)
        return
    print('[cfvideo][mac] Installing complete MLT runtime through private KDE Craft',
          flush=True)
    script = 'set -e; source "$1"; shift; exec craft "$@"'
    # A normal `craft libs/mlt` invokes the all action, including Craft core
    # and blueprint updates, even when it reports MLT up to date. When MLT's
    # runtime is missing, repair only that package. Never traverse Qt/KF6 or
    # update Craft repositories merely to diagnose an incomplete MLT image.
    print('[cfvideo][mac] Repairing incomplete MLT package only '
          '(no dependency traversal)', flush=True)
    script = 'set -e; source "$1"; shift; exec craft "$@"'
    run('/bin/bash', '-c', script, 'craft-mlt-runtime-repair',
        str(craft_environment), '--no-cache', '--compile', '--install',
        '--qmerge', 'libs/mlt')
    if not ready():
        raise RuntimeError(
            'Craft libs/mlt finished without its loadable producer modules, '
            'profiles, and melt executable under the private prefix '
            '(including plugins/mlt, the macOS Craft install location): '
            f'{craft_root}. Inspect the upstream Craft MLT recipe; do not '
            'substitute a host-installed MLT runtime.'
        )
    print('[cfvideo][mac] Complete MLT runtime installed in private Craft prefix',
          flush=True)



def ensure_frei0r_effects(craft_root: Path, craft_environment: Path) -> None:
    """Install Frei0r effects into Kdenlive's own Craft prefix, once.

    MLT's libmltfrei0r module is only a loader, not the effects themselves.
    Guard by real installed plugin files so repeated Setup is cheap and an
    incomplete Craft install is never considered ready.
    """
    locations = (craft_root / 'plugins/frei0r-1',
                 craft_root / 'lib/frei0r-1', craft_root / 'lib64/frei0r-1')

    def plugins():
        return [p for root in locations if root.is_dir()
                for p in root.rglob('*')
                if p.is_file() and p.suffix in ('.so', '.dylib')]

    existing = plugins()
    if existing:
        print(f'[cfvideo][mac] Reusing {len(existing)} private Frei0r effects', flush=True)
        return
    print('[cfvideo][mac] Installing missing Frei0r effects via private KDE Craft', flush=True)
    command = 'set -e; source "$1"; shift; exec craft "$@"'
    # Use Craft's package database and install only the missing effects,
    # never rebuild Kdenlive, MLT, Qt, or all KF6 frameworks here.
    run('/bin/bash', '-c', command, 'craft-frei0r', str(craft_environment),
        '--compile', '--install', '--qmerge', 'libs/frei0r')
    installed = plugins()
    if not installed:
        raise RuntimeError('Craft libs/frei0r completed without loadable effects '
                           f'in the private installation: {locations}. '
                           'Inspect the installed Craft Frei0r blueprint; '
                           'do not use host Homebrew plugins.')
    print(f'[cfvideo][mac] Installed {len(installed)} private Frei0r effects', flush=True)


def provision_kdenlive_frameworks() -> None:
    """Prepare Kdenlive's complete upstream KDE/Qt stack using KDE Craft.

    Homebrew has no kbookmarks formula (and many other upstream KF6 modules).
    KDE Craft's cfvideo recipe is the supported macOS integration point.
    All Craft outputs stay under this application's external Built directory.
    """
    import os
    import urllib.request

    built_value = os.environ.get('CINTER_BUILT_ROOT')
    if not built_value:
        raise RuntimeError('CINTER_BUILT_ROOT missing; cannot allocate private Craft dependency stack')
    built = Path(built_value).expanduser().resolve()
    dependency = built / 'Dependencies' / 'KDECraft'
    craft_root = dependency / 'Root'
    source_dir = dependency / 'DependencySources'
    bootstrap = source_dir / 'CraftBootstrap.py'
    craft_executable = craft_root / 'craft' / 'bin' / 'craft.py'
    craft_environment = craft_root / 'craft' / 'craftenv.sh'
    source_dir.mkdir(parents=True, exist_ok=True)
    # Preserve the same private prefix across retries. KDE's bootstrap has
    # already downloaded and installed packages when interrupted; moving Root
    # aside discards the very state that Craft needs to resume incrementally.
    clean_craft_finder_metadata(craft_root)
    temporary_craft = craft_root / 'craft-tmp' / 'bin' / 'craft.py'
    shelf = craft_root / 'etc' / 'bootstrap.shelf'
    if craft_executable.is_file():
        print(f'[cfvideo][mac] Reusing existing KDE Craft: {craft_executable}', flush=True)
    elif temporary_craft.is_file() and shelf.is_file():
        print(f'[cfvideo][mac] Resuming interrupted KDE Craft bootstrap in {craft_root}', flush=True)
        install_craft_certificate_bundle(craft_root)
        resume_gettext_image_merge(craft_root, temporary_craft)
        restore_preserved_python_image(craft_root)
        resume_python_image_merge(craft_root, temporary_craft)
        restore_craft_python_prefix_layout(craft_root)
        # A previous bootstrap may have qmerged Python successfully but
        # stopped at pip because its root-level interpreter link was absent.
        if any((craft_root / 'lib' / 'Python.framework' / 'Versions').glob('3.*/bin/python3.*')):
            validate_craft_python_launcher(craft_root)
        try:
            run(sys.executable, str(temporary_craft), 'craft')
        except subprocess.CalledProcessError:
            # The gettext install can finish before Craft rejects a macOS
            # .DS_Store collision during qmerge. Preserve the compiled image
            # and retry the merge through Craft rather than recompiling it.
            if resume_python_image_merge(craft_root, temporary_craft):
                run(sys.executable, str(temporary_craft), 'craft')
            elif resume_gettext_image_merge(craft_root, temporary_craft):
                run(sys.executable, str(temporary_craft), 'craft')
            else:
                raise
        validate_craft_python_launcher(craft_root)
        if not craft_executable.is_file():
            raise RuntimeError(f'Craft did not install its executable: {craft_executable}')
        run(sys.executable, str(craft_executable), '--unshelve', str(shelf))
        shutil.rmtree(craft_root / 'craft-tmp')
    elif craft_root.exists() and any(craft_root.iterdir()):
        raise RuntimeError(
            f'Incomplete KDE Craft prefix without a resumable bootstrap: {craft_root}. '
            'Preserved all files; investigate rather than downloading into a new prefix.'
        )
    else:
        url = 'https://raw.githubusercontent.com/KDE/craft/master/setup/CraftBootstrap.py'
        print(f'[cfvideo][mac] KDE Craft bootstrap {url} -> {bootstrap}', flush=True)
        urllib.request.urlretrieve(url, bootstrap)
        # A failed initial bootstrap leaves craft-tmp and its installed
        # packages in place; the next Setup resumes rather than restarting.
        run(sys.executable, str(bootstrap), '--prefix', str(craft_root), '--use-defaults')
    if craft_executable.is_file():
        install_craft_certificate_bundle(craft_root)
    if not craft_executable.is_file() or not craft_environment.is_file():
        raise RuntimeError(f'KDE Craft bootstrap incomplete: {craft_root}')

    # Setup installs Kdenlive's dependency graph only; Build compiles the
    # application from the separately managed upstream source checkout.
    # Craft's installation database determines which dependencies are current.
    recipe = json.loads((Path(__file__).parent / 'recipe.json').read_text(encoding='utf-8'))
    craft_args = recipe['preparation']['craft']['setup_args']
    if not isinstance(craft_args, list) or not all(isinstance(arg, str) for arg in craft_args):
        raise ValueError('Kdenlive recipe preparation.craft.setup_args must be a list of strings')
    if '--install-deps' not in craft_args:
        raise ValueError('Kdenlive Setup must install dependencies only (missing --install-deps)')
    # Craft has its own installed-package database; don't invalidate a complete
    # dependency traversal merely because Python recovery/validation logic is
    # edited. Only authored inputs that select the Craft dependency graph are
    # part of this SHA. Build and package code are deliberately excluded.
    setup_proof = dependency / 'SetupComplete.sha256'
    graph_inputs = {
        'schema': 2,
        'craft_args': craft_args,
        'recipe_preparation': recipe['preparation']['craft'],
        'craft_abi': 'macos-clang-arm64',
    }
    setup_fingerprint = hashlib.sha256(
        json.dumps(graph_inputs, sort_keys=True, separators=(',', ':'),
                   ensure_ascii=False).encode('utf-8')
    ).hexdigest()
    # The original SetupComplete.sha256 was only written after a successful
    # traversal, but included setup.py's hash. Migrate that *completed* state
    # once, rather than triggering a full traversal just to change the proof
    # format. After migration the graph SHA detects changes to the recipe.
    legacy_proof = dependency / 'SetupComplete.legacy.sha256'
    force_setup = os.environ.get('CINTER_FORCE_CRAFT_SETUP', '').lower() in ('1', 'true', 'yes')
    cmake_dir = craft_root / 'lib' / 'cmake'
    installed = (craft_executable.is_file() and craft_environment.is_file()
                 and cmake_dir.is_dir())
    current_proof = setup_proof.read_text(encoding='utf-8').strip() if setup_proof.is_file() else ''
    if installed and not force_setup and current_proof == setup_fingerprint:
        print('[cfvideo][mac] Craft dependency graph unchanged (SHA-256); '
              'skipping install-deps traversal', flush=True)
        ensure_mlt_runtime(craft_root, craft_environment)
        ensure_frei0r_effects(craft_root, craft_environment)
        return
    if (installed and not force_setup and not legacy_proof.exists()
            and re.fullmatch(r'[0-9a-f]{64}', current_proof)):
        # Existing proof could only be created after Craft traversal succeeded.
        # Preserve it for audit; subsequent invocations use the graph-only SHA.
        legacy_proof.write_text(current_proof + '\n', encoding='utf-8')
        setup_proof.write_text(setup_fingerprint + '\n', encoding='utf-8')
        print('[cfvideo][mac] Migrated completed Craft dependency proof to '
              'graph-only SHA-256 (no rebuild)', flush=True)
        ensure_mlt_runtime(craft_root, craft_environment)
        ensure_frei0r_effects(craft_root, craft_environment)
        return
    if force_setup:
        print('[cfvideo][mac] Explicit CINTER_FORCE_CRAFT_SETUP requested', flush=True)
    else:
        print('[cfvideo][mac] Dependency graph changed or Setup incomplete; '
              'resuming with Craft installed-package database', flush=True)
    # Incomplete dependency installations are never accepted as ready.
    # Preserve their prefix and Ninja objects for Craft's incremental resume.
    setup_proof.unlink(missing_ok=True)
    # KDE Craft's own craftenv.sh references optional shell variables before
    # assigning them. Nounset makes that supported initialization fail with
    # CRAFT_PYTHON_BIN: unbound variable. Keep errexit, but let Craft's
    # environment script manage its optional values as upstream expects.
    script = 'set -e; source "$1"; shift; exec craft "$@"'
    # Repair an already-unpacked KIO source *before* Craft starts its dependency
    # traversal. Then Craft/Ninja only compile the changed test object once.
    resume_kio_dirlister_autotest(craft_root, craft_environment, resume=False)
    try:
        run('/bin/bash', '-c', script, 'craft-setup', str(craft_environment), *craft_args)
    except subprocess.CalledProcessError:
        # Recover known failures in place. The completed dependency packages and
        # Ninja object directories must not be cleaned just to retry Setup.
        if resume_kio_dirlister_autotest(craft_root, craft_environment):
            print('[cfvideo][mac] KIO resumed; continue remaining Craft dependencies', flush=True)
        elif repair_boost_numpy2_source(craft_root):
            script_boost = 'set -e; source "$1"; shift; exec craft "$@"'
            run('/bin/bash', '-c', script_boost, 'craft-boost-repair', str(craft_environment),
                '--compile', '--install', '--qmerge', 'libs/boost')
        else:
            raise
        run('/bin/bash', '-c', script, 'craft-setup', str(craft_environment), *craft_args)
    if not cmake_dir.is_dir():
        raise RuntimeError(f'KDE Craft did not provide the expected CMake packages: {cmake_dir}')
    ensure_mlt_runtime(craft_root, craft_environment)
    ensure_frei0r_effects(craft_root, craft_environment)
    setup_proof.write_text(setup_fingerprint + '\n', encoding='utf-8')
    print(f'[Creative Suite mac][dependencies] KDE Craft framework prefix: {craft_root}', flush=True)
    print('[Creative Suite mac][dependencies] Upstream Kdenlive CMake validation still required', flush=True)

def main() -> None:
    validate_toolchain("cfvideo")
    provision_kdenlive_frameworks()

if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, KeyError, ValueError, subprocess.CalledProcessError) as exc:
        print(f'[Creative Suite mac][setup] ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
