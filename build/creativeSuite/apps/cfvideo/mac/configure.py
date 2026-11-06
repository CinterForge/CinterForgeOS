#!/usr/bin/env python3
"""Resolve installed KDE Craft development packages for Kdenlive on macOS."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


def append_cmake_options(command: list[str], built: Path) -> None:
    craft_root = built / "Dependencies" / "KDECraft" / "Root"
    craft_cmake = craft_root / "lib" / "cmake"
    if not craft_cmake.is_dir():
        raise RuntimeError(
            f"Kdenlive KDE Craft CMake packages missing: {craft_cmake}; run Setup"
        )

    # Never select ECM from Craft's build/work tree. Its ECMConfig.cmake can
    # exist before installation and resolves ECM_MODULE_DIR to the build image,
    # which does not contain the installed modules Kdenlive needs.
    installed_ecm = (
        craft_root / "share" / "ECM" / "cmake" / "ECMConfig.cmake",
        craft_cmake / "ECM" / "ECMConfig.cmake",
    )
    config = next((path for path in installed_ecm if path.is_file()), None)
    if config is None:
        raise RuntimeError(
            "KDE Craft Extra CMake Modules is not installed in its prefix. "
            f"Checked: {', '.join(str(path) for path in installed_ecm)}; "
            "run Setup and check the KDE Craft install log"
        )

    # The installed package must contain its modules; merely finding the CMake
    # config is not enough (a partial Craft installation can leave it behind).
    module_candidates = (
        craft_root / "share" / "ECM" / "modules" / "ECMUseFindModules.cmake",
        craft_root / "share" / "ECM" / "modules" / "ECMInstallIcons.cmake",
    )
    if not all(path.is_file() for path in module_candidates):
        raise RuntimeError(
            "KDE Craft ECM installation is incomplete: "
            + ", ".join(str(path) for path in module_candidates if not path.is_file())
        )

    # Craft OpenTimelineIO was compiled against Craft Imath (3.1 here).
    # Resolving Imath headers from Homebrew 3.2 changes the C++ ABI namespace:
    # Kdenlive then requests Imath_3_2 symbols that Craft OTIO does not export.
    # Pin both CMake's Imath package and the compiler's header search order to
    # the same dependency installation used by OTIO. Do not rebuild OTIO or
    # turn off the benchmark/tests to mask this linkage problem.
    imath_header = craft_root / "include" / "Imath" / "ImathNamespace.h"
    imath_configs = sorted((craft_root / "lib" / "cmake").glob("Imath*/ImathConfig.cmake"))
    imath_libraries = sorted((craft_root / "lib").glob("libImath-3_*.dylib"))
    if not imath_header.is_file() or len(imath_configs) != 1 or not imath_libraries:
        raise RuntimeError(
            "Kdenlive's KDE Craft Imath SDK is incomplete or ambiguous; "
            f"header={imath_header} config={imath_configs} libraries={imath_libraries}. "
            "Run Setup; do not mix Homebrew Imath with Craft OpenTimelineIO."
        )
    imath_namespace = re.search(
        r"#\s*define\s+IMATH_INTERNAL_NAMESPACE\s+Imath_(\d+)_(\d+)",
        imath_header.read_text(encoding="utf-8"),
    )
    if imath_namespace is not None:
        version_marker = f"{imath_namespace.group(1)}_{imath_namespace.group(2)}"
        if not any(version_marker in library.name for library in imath_libraries):
            raise RuntimeError(
                f"KDE Craft Imath headers and libraries disagree: "
                f"namespace={imath_namespace.group(0)} libraries={imath_libraries}"
            )
    # -U clears a stale CMakeCache entry left by an earlier Homebrew-configured
    # build before the explicit private package is installed in that cache.
    command.append("-UImath_*")
    command.append("-DImath_DIR=" + str(imath_configs[0].parent))
    # OTIO's public headers include Imath headers both as <Imath/ImathBox.h>
    # and as <ImathBox.h>. The second form otherwise falls through to
    # Homebrew's newer Imath, even with Craft/include first. Both spellings
    # must resolve to the same Imath ABI used when the Craft OTIO dylib was built.
    imath_include = craft_root / "include" / "Imath"
    if not (imath_include / "ImathBox.h").is_file():
        raise RuntimeError(f"KDE Craft ImathBox.h missing: {imath_include}")
    # Force the matching namespace header into every translation unit before
    # transitive Homebrew include paths can load a different Imath version.
    # This preserves the ABI used by KDE Craft's installed OpenTimelineIO.
    command.append(
        "-DCMAKE_CXX_FLAGS=-include " + str(imath_header)
        + " -I" + str(imath_include)
        + " -I" + str(craft_root / "include")
    )
    print(f"[cfvideo][mac] Imath SDK pinned to KDE Craft: {imath_configs[0].parent}", flush=True)

    # CMake's package discovery can otherwise select Homebrew Qt submodules
    # even after locating the top-level Qt6 package in Craft. That produces
    # conflicting Qt frameworks, runtime RPATH cycles and unstable rebuilds.
    # Pin the *whole installed Qt6 SDK* to the private Craft prefix. Do not
    # exclude Homebrew wholesale: unrelated dependencies may still need it.
    qt_configs = sorted(craft_cmake.glob("Qt6*/Qt6*Config.cmake"))
    qt_directories = {}
    for qt_config in qt_configs:
        module = qt_config.parent.name
        if qt_config.name == module + "Config.cmake":
            qt_directories[module] = qt_config.parent
    if "Qt6" not in qt_directories or "Qt6Core" not in qt_directories:
        raise RuntimeError(
            f"Private Craft Qt6 development packages are incomplete: {craft_cmake}; run Setup"
        )
    # -U discards cached Homebrew locations before explicitly pinning each
    # component. The private SDK remains the single Qt provider for this app.
    command.extend(("-UQt6*_DIR", "-UQt6*_ROOT"))
    for module, directory in sorted(qt_directories.items()):
        command.append(f"-D{module}_DIR={directory}")
    command.extend((
        "-DCMAKE_FIND_USE_PACKAGE_REGISTRY=OFF",
        "-DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=OFF",
        "-DCMAKE_FIND_PACKAGE_PREFER_CONFIG=ON",
    ))
    print(f"[cfvideo][mac] Pinned {len(qt_directories)} Qt6 packages to private Craft", flush=True)

    command.append("-DCMAKE_PREFIX_PATH=" + str(craft_root))
    command.append("-DECM_DIR=" + str(config.parent))
    command.append("-DCINTER_KDENLIVE_QML_IMPORT_ROOT=" + str(ki18n_qml_import_root(built)))


def ki18n_qml_import_root(built: Path) -> Path:
    """Return the installed QML import root, not a Craft build-image path."""
    root = built / "Dependencies" / "KDECraft" / "Root"
    candidates = (
        root / "lib" / "qml",
        root / "lib" / "qt6" / "qml",
        root / "lib64" / "qml",
        root / "qml",
        root / "share" / "qml",
    )
    for directory in candidates:
        module = directory / "org" / "kde" / "ki18n"
        if not (module / "qmldir").is_file():
            continue
        metadata = (module / "qmldir").read_text(encoding="utf-8")
        if not re.search(r"(?m)^module\s+org\.kde\.ki18n\s*$", metadata):
            raise RuntimeError(f"Malformed KI18n QML module declaration: {module / 'qmldir'}")
        return directory
    raise RuntimeError(
        "KDE Craft's org.kde.ki18n QML module is missing. "
        "Install the full KF6 KI18n QML package before building Kdenlive. Checked: "
        + ", ".join(str(path) for path in candidates)
    )


def prepare_source(source: Path) -> None:
    """Expose Craft KI18n to Qt-generated qmllint/qmlcachegen commands.

    QML_IMPORT_PATH in the process environment is not used by every Qt build
    tool. ECM forwards ecm_add_qml_module(IMPORT_PATH ...) to Qt 6, emitting -I options, which
    are required for tooling to resolve KI18n and suppress cascade errors.
    Patch the existing upstream CMake declaration idempotently and leave all
    QML files, warning levels and build targets untouched.
    """
    declaration = re.compile(r"\becm_add_qml_module\s*\(\s*kdenliveLib\b")
    matches = []
    for cmake in source.rglob("CMakeLists.txt"):
        original = cmake.read_text(encoding="utf-8")
        if declaration.search(original):
            matches.append((cmake, original))
    if len(matches) != 1:
        raise RuntimeError(
            "Expected exactly one upstream ecm_add_qml_module(kdenliveLib ...) "
            f"declaration; found {len(matches)} in {source}. Refusing an ambiguous patch."
        )
    cmake, original = matches[0]
    match = declaration.search(original)
    assert match is not None
    # Inject immediately after the target, before URI / VERSION / QML_FILES.
    # Passing the root as a CMake option keeps paths outside the source tree.
    injection = '\n    IMPORT_PATH "${CINTER_KDENLIVE_QML_IMPORT_ROOT}"'
    trailing = original[match.end():]
    if trailing.lstrip().startswith('IMPORT_PATH "${CINTER_KDENLIVE_QML_IMPORT_ROOT}"'):
        return
    modified = original[:match.end()] + injection + original[match.end():]
    cmake.write_text(modified, encoding="utf-8")
    print(f"[cfvideo][mac] Added build-tool QML IMPORT_PATH to {cmake}", flush=True)


def build_environment(built: Path) -> dict[str, str]:
    """Expose the installed KI18n module to QML runtime and build tools."""
    import_root = ki18n_qml_import_root(built)
    environment = os.environ.copy()
    craft_root = built / "Dependencies" / "KDECraft" / "Root"
    craft_env = craft_root / "craft" / "craftenv.sh"
    if not craft_env.is_file():
        raise RuntimeError(f"Missing KDE Craft environment: {craft_env}; run Setup")
    # Capture the actual environment used by KDE Craft Setup, including its
    # private executable and library paths. Never mutate the parent shell.
    result = subprocess.run(
        ["/bin/bash", "-c", 'set -e; source "$1"; /usr/bin/env -0',
         "craft-build", str(craft_env)],
        env=environment, capture_output=True, check=True,
    )
    for entry in result.stdout.split(b"\0"):
        if not entry:
            continue
        key, sep, value = entry.partition(b"=")
        if sep:
            environment[os.fsdecode(key)] = os.fsdecode(value)
    existing = environment.get("QML_IMPORT_PATH")
    paths = [str(import_root)]
    if existing:
        paths.append(existing)
    environment["QML_IMPORT_PATH"] = os.pathsep.join(paths)
    # meinproc6 locates DocTools XML catalogs through QStandardPaths, not
    # CMAKE_PREFIX_PATH. Keep Craft's installed data visible during Ninja's
    # translated handbook generation, without depending on host KDE installs.
    craft_root = built / "Dependencies" / "KDECraft" / "Root"
    catalogs = sorted(craft_root.glob("share/**/kdoctools/customization/catalog.xml"))
    if not catalogs:
        catalogs = sorted(craft_root.glob("share/**/kdoctools/catalog.xml"))
    if not catalogs:
        raise RuntimeError(
            f"KDE Craft DocTools XML catalog is missing under {craft_root / 'share'}; "
            "install the KF6 DocTools data package during Setup before building "
            "translated Kdenlive documentation."
        )
    # KDE DocTools resolves its catalog through QStandardPaths. Depending on
    # the KF6 build, the lookup is either "kf6/kdoctools/..." or
    # "kdoctools/..." relative to XDG_DATA_DIRS. Expose BOTH private Craft
    # data roots; the previous code exposed only share/kf6, which makes the
    # first lookup fail even though the catalog is installed.
    data_roots = [str(craft_root / "share")]
    for catalog in catalogs:
        relative = catalog.relative_to(craft_root / "share")
        components = relative.parts
        index = components.index("kdoctools")
        data_root = craft_root / "share" / Path(*components[:index])
        if str(data_root) not in data_roots:
            data_roots.append(str(data_root))
    # QStandardPaths searches XDG_DATA_HOME before XDG_DATA_DIRS. Set both
    # explicitly for the private Craft meinproc6 process (no host KDE tree).
    environment["XDG_DATA_HOME"] = str(craft_root / "share")
    existing_data = environment.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share")
    environment["XDG_DATA_DIRS"] = os.pathsep.join(data_roots + [existing_data])
    # libxml2's catalog resolver is independent of Qt QStandardPaths. Give
    # meinproc6 the exact installed catalog rather than relying on discovery
    # through the host's default /etc/xml/catalog.
    environment["XML_CATALOG_FILES"] = os.pathsep.join(
        [str(catalog) for catalog in catalogs]
        + ([environment["XML_CATALOG_FILES"]] if environment.get("XML_CATALOG_FILES") else [])
    )
    # meinproc6 also relies on the KDE install prefix to resolve its DocTools
    # catalogs; do not let a globally installed KDE tree take precedence.
    print(f"[cfvideo][mac] KDE DocTools catalogs: {catalogs}", flush=True)
    print(f"[cfvideo][mac] Qt QML import path: {environment['QML_IMPORT_PATH']}", flush=True)
    return environment


def invalidate_mismatched_otio_object(built: Path) -> None:
    """Recompile only a stale OTIO export object built with the wrong Imath ABI.

    Incremental Ninja builds can retain an otioexport object from an earlier
    Homebrew-Imath configuration even when the current CMake configuration
    uses KDE Craft Imath. OTIO itself must not be rebuilt or ABI-mixed.
    """
    work = built / "Build"
    objects = sorted(work.glob("src/CMakeFiles/kdenliveLib.dir/**/otioexport.cpp.o"))
    if len(objects) > 1:
        raise RuntimeError(f"Ambiguous Kdenlive OTIO export objects: {objects}")
    for obj in objects:
        result = subprocess.run(
            ["nm", "-u", str(obj)], capture_output=True, text=True, check=True
        )
        if "Imath_3_2" not in result.stdout:
            continue
        obj.unlink()
        print(
            f"[cfvideo][mac] Invalidated stale Homebrew-Imath 3.2 object: {obj}",
            flush=True,
        )
        print(
            "[cfvideo][mac] Ninja will recompile OTIO export using pinned Craft Imath",
            flush=True,
        )


def postconfigure(built: Path) -> None:
    invalidate_mismatched_otio_object(built)


def preflight(source: Path, built: Path, environment: dict[str, str]) -> None:
    """Exercise the real private DocTools binary before starting Ninja."""
    tool = built / "Dependencies/KDECraft/Root/bin/meinproc6"
    docs = sorted((source / "po").glob("*/docs/kdenlive/index.docbook"))
    if not tool.is_file() or not docs:
        raise RuntimeError(f"DocTools preflight prerequisites missing: {tool}, {source / 'po'}")
    # --check validates catalog resolution without producing a cache artifact.
    result = subprocess.run([str(tool), "--check", str(docs[0])], cwd=source,
                            env=environment, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(
            f"KDE DocTools preflight failed ({result.returncode}) with private Craft environment: "
            f"{result.stdout[-1500:]} {result.stderr[-2500:]}"
        )
    print("[cfvideo][mac] KDE DocTools catalog preflight passed", flush=True)
