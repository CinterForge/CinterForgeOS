#!/usr/bin/env python3
"""Krita-specific macOS CMake inputs, kept outside the shared pipeline.

Setup owns preparing dependencies; this module validates and exposes the
exact installed Qt5/KF5 stack to upstream CMake without disabling features.
"""
from __future__ import annotations

from pathlib import Path
import subprocess


def append_cmake_options(command: list[str], built: Path) -> None:
    prefix = subprocess.check_output(
        ["brew", "--prefix", "extra-cmake-modules"], text=True
    ).strip()
    config = Path(prefix) / "share/ECM/cmake/ECMConfig.cmake"
    if not config.is_file():
        raise RuntimeError(f"ECM is missing: {config}; run Setup first")
    command.append("-DECM_DIR=" + str(config.parent))
    # KF5 lives in distinct, often keg-only KDE Homebrew prefixes.
    # Pass exactly the installed KF5 locations; never reuse KF6.
    required = ('kf5-kconfig', 'kf5-kwidgetsaddons',
                'kf5-kcompletion', 'kf5-kcoreaddons',
                'kf5-kguiaddons', 'kf5-kitemviews')
    extras = ('kf5-kconfigwidgets', 'kf5-kiconthemes', 'kf5-kio',
              'kf5-kitemmodels', 'kf5-karchive', 'kf5-kwindowsystem',
              'kf5-kcrash', 'kf5-kservice', 'kf5-sonnet', 'kf5-kxmlgui')
    # Setup installs KIO and ItemModels from the app-owned,
    # trusted private tap. Query that formula explicitly: asking
    # for the upstream tap fails trust checks even when the exact
    # package is already installed and ready.
    private_formulas = {'kf5-kio', 'kf5-kitemmodels'}
    prefixes = []
    for name in (*required, *extras):
        if name == 'kf5-karchive':
            prefix = built / 'Dependencies/KF5Archive-5.111.0/Install'
            config = prefix / 'lib/cmake/KF5Archive/KF5ArchiveConfig.cmake'
            if not config.is_file():
                raise RuntimeError(f'Pinned KF5Archive missing: {config}; run Krita Setup')
        else:
            tap = ('cinterforge/kf5-private/' if name in private_formulas
                   else 'kde-mac/kde/')
            prefix = Path(subprocess.check_output(
                ['brew', '--prefix', tap + name], text=True).strip())
        prefixes.append(str(prefix))
    i18n = built / 'Dependencies/KF5I18n-5.111.0/Install'
    if not list(i18n.rglob('KF5I18nConfig.cmake')):
        raise RuntimeError(f'Pinned KF5 I18n missing: {i18n}; run Krita Setup')
    prefixes.append(str(i18n))
    qt5 = Path(subprocess.check_output(
        ['brew', '--prefix', 'qt@5'], text=True).strip())
    if not (qt5 / 'lib/cmake/Qt5/Qt5Config.cmake').is_file():
        raise RuntimeError(f'Qt5Config.cmake missing from {qt5}; run Krita Setup')
    # Qt5_DIR alone cannot resolve transitive find_dependency(Qt5Core/Widgets/Xml).
    # Keep the Qt5 prefix first so all KF5 package dependencies resolve.
    command.append('-DCMAKE_PREFIX_PATH=' + ';'.join([str(qt5), *prefixes]))
    immer = Path(subprocess.check_output(
        ['brew', '--prefix', 'immer'], text=True).strip())
    configs = sorted(immer.rglob('ImmerConfig.cmake'))
    if len(configs) != 1:
        raise RuntimeError(f'Krita requires Immer CMake package, found {configs}; run Krita Setup')
    command.append('-DImmer_DIR=' + str(configs[0].parent))
    zug = Path(subprocess.check_output(
        ['brew', '--prefix', 'zug'], text=True).strip())
    zug_configs = sorted(zug.rglob('ZugConfig.cmake'))
    if len(zug_configs) != 1:
        raise RuntimeError(f'Krita requires Zug CMake package, found {zug_configs}; run Krita Setup')
    command.append('-DZug_DIR=' + str(zug_configs[0].parent))
    lager = Path(subprocess.check_output(
        ['brew', '--prefix', 'lager'], text=True).strip())
    lager_configs = sorted(lager.rglob('LagerConfig.cmake'))
    if len(lager_configs) != 1:
        raise RuntimeError(f'Krita requires Lager CMake package, found {lager_configs}; run Krita Setup')
    command.append('-DLager_DIR=' + str(lager_configs[0].parent))
    # This Krita checkout requests Eigen >=3.3 and the 3.x API.
    # Homebrew's unversioned Eigen is 5.x; use the pinned major.
    eigen = Path(subprocess.check_output(
        ['brew', '--prefix', 'eigen@3'], text=True).strip())
    eigen_configs = sorted(eigen.rglob('Eigen3Config.cmake'))
    if len(eigen_configs) != 1:
        raise RuntimeError(f'Krita requires Eigen3 CMake package, found {eigen_configs}; run Krita Setup')
    command.append('-DEigen3_DIR=' + str(eigen_configs[0].parent))
    # Expose Homebrew's keg-only dependency prefixes without
    # altering Krita's upstream optional feature flags.
    ocio = Path(subprocess.check_output(
        ['brew', '--prefix', 'opencolorio'], text=True).strip())
    if not (ocio / 'include/OpenColorIO/OpenColorIO.h').is_file():
        raise RuntimeError(f'OpenColorIO headers missing under {ocio}; run Krita Setup')
    command.append('-DOPENCOLORIO_INCLUDE_DIR=' + str(ocio / 'include'))
    ocio_libraries = sorted((ocio / 'lib').glob('libOpenColorIO*.dylib'))
    if not ocio_libraries:
        raise RuntimeError(f'OpenColorIO library missing under {ocio}; run Krita Setup')
    command.append('-DOPENCOLORIO_LIBRARY=' + str(ocio_libraries[0]))
    mlt = Path(subprocess.check_output(
        ['brew', '--prefix', 'mlt'], text=True).strip())
    mlt_configs = sorted(mlt.rglob('Mlt7Config.cmake'))
    if len(mlt_configs) != 1:
        raise RuntimeError(f'Krita requires Mlt7 CMake package, found {mlt_configs}; run Krita Setup')
    command.append('-DMlt7_DIR=' + str(mlt_configs[0].parent))
    xsimd = Path(subprocess.check_output(
        ['brew', '--prefix', 'xsimd'], text=True).strip())
    xsimd_configs = sorted(xsimd.rglob('xsimdConfig.cmake'))
    if len(xsimd_configs) != 1:
        raise RuntimeError(f'Krita requires xsimd CMake package, found {xsimd_configs}; run Krita Setup')
    command.append('-Dxsimd_DIR=' + str(xsimd_configs[0].parent))
    quazip = built / 'Dependencies/QuaZipQt5-1.7.2/Install'
    quazip_configs = sorted(quazip.rglob('QuaZip-Qt5Config.cmake'))
    if len(quazip_configs) != 1:
        raise RuntimeError(f'Krita requires private Qt5 QuaZip, found {quazip_configs}; run Krita Setup')
    command.append('-DQuaZip-Qt5_DIR=' + str(quazip_configs[0].parent))
    command.append('-DQt5_DIR=' + str(qt5 / 'lib/cmake/Qt5'))
    # Krita's MyPaint targets link -ljson-c via libmypaint's upstream
    # pkg-config metadata. Homebrew provides json-c in its own keg; expose
    # that concrete library directory to executable and module linkers.
    json_c = Path(subprocess.check_output(
        ['brew', '--prefix', 'json-c'], text=True).strip())
    json_c_library = json_c / 'lib/libjson-c.dylib'
    if not json_c_library.is_file():
        raise RuntimeError(f'Krita MyPaint dependency missing: {json_c_library}; run Setup first')
    for linker_type in ('EXE', 'MODULE', 'SHARED'):
        command.append(f'-DCMAKE_{linker_type}_LINKER_FLAGS=-L{json_c / "lib"}')
    # Krita's Qt5 compatibility wrapper assigns stable enum values to HDR
    # color spaces that Qt5's QSurfaceFormat does not publicly expose.
    # The corresponding upstream test currently refers to nonexistent Qt5
    # QSurfaceFormat members. Use the same enum values as the wrapper rather
    # than removing the test or changing application color management.
    source_flag = command.index('-S')
    source = Path(command[source_flag + 1])
    test = source / 'libs/ui/tests/KisSurfaceColorSpaceWrapperTest.cpp'
    if not test.is_file():
        raise RuntimeError(f'Krita surface color-space test source missing: {test}')
    body = test.read_text(encoding='utf-8')
    substitutions = {
        'QSurfaceFormat::scRGBColorSpace':
            'static_cast<QSurfaceFormat::ColorSpace>(KisSurfaceColorSpaceWrapper::scRGBColorSpace)',
        'QSurfaceFormat::bt2020PQColorSpace':
            'static_cast<QSurfaceFormat::ColorSpace>(KisSurfaceColorSpaceWrapper::bt2020PQColorSpace)',
    }
    changed = False
    for old, new in substitutions.items():
        if old in body:
            body = body.replace(old, new)
            changed = True
        elif new not in body:
            raise RuntimeError(f'Unexpected Krita color-space test API: {test}: {old}')
    if changed:
        test.write_text(body, encoding='utf-8')
        print(f'[cfpaint][mac] Corrected Qt5 surface color-space test enum references: {test}', flush=True)
    # Qt5 is installed as frameworks, but a different Homebrew Qt release
    # places forwarding headers under /opt/homebrew/include/QtCore. CMake's
    # transitive -isystem /opt/homebrew/include then overrides the Qt5
    # framework paths and causes recursive QtCore headers. Put the actual
    # Qt5 framework headers first through a private, app-owned include root.
    # This also covers generated CMake targets and incremental Ninja builds.
    overlay = built / 'Dependencies/KritaQt5Headers'
    overlay.mkdir(parents=True, exist_ok=True)
    frameworks = sorted((qt5 / 'lib').glob('Qt*.framework'))
    if not frameworks:
        raise RuntimeError(f'No Qt5 frameworks found in {qt5 / "lib"}')
    for framework in frameworks:
        headers = framework / 'Headers'
        if not headers.is_dir():
            raise RuntimeError(f'Qt5 headers missing: {headers}')
        link = overlay / framework.stem
        if link.is_symlink():
            if link.resolve() == headers.resolve():
                continue
            link.unlink()
        elif link.exists():
            raise RuntimeError(f'Conflicting Krita Qt5 header directory: {link}')
        link.symlink_to(headers, target_is_directory=True)
    # CMake adds CMAKE_CXX_FLAGS ahead of target-specific -isystem flags.
    # Never modify global Homebrew headers or disable upstream targets.
    # Krita's LUT sources use <OpenColorIO.h>, while Homebrew installs
    # that header under include/OpenColorIO/. Keep the package's public
    # include root for <OpenColorIO/OpenColorIO.h> users as well.
    command.append('-DCMAKE_CXX_FLAGS=-I' + str(overlay)
                   + ' -I' + str(ocio / 'include/OpenColorIO'))
