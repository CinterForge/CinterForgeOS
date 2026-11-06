#!/usr/bin/env python3
"""Application-owned native macOS dependency preparation."""
from __future__ import annotations
import shutil
import re
import os
import hashlib
import json
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

def provision_brew(formulas: tuple[str, ...], *, private_prefix=None) -> None:
    def installer(formula):
        if formula.startswith('kde-mac/kde/'):
            install_kde_formula(formula, private_prefix=private_prefix)
        else:
            run('brew', 'install', formula)
    _shared_provision_brew(formulas, install_formula=installer)

def install_kde_formula(formula: str, *, private_prefix: Path | tuple[Path, ...] | None = None,
                        _dependency_stack: tuple[str, ...] = ()) -> None:
    """Install an exact KDE formula, with isolated private KF5 prerequisites.

    Homebrew removed `brew install --env=std`; the supported environment
    selector is declared inside a formula.  Preserve the upstream formula
    intact except for a private, generated copy with a standard build
    environment and explicit pinned CMake dependency path.
    """
    if formula in _dependency_stack:
        raise RuntimeError('Circular KDE Homebrew dependency: ' +
                           ' -> '.join((*_dependency_stack, formula)))
    trusted: set[str] = set()
    install_command = ['brew', 'install', formula]
    if private_prefix is not None:
        prefixes = (private_prefix,) if isinstance(private_prefix, Path) else private_prefix
        expected = ('KF5I18n', 'KF5Archive', 'Qca-qt5')
        if not 1 <= len(prefixes) <= len(expected):
            raise RuntimeError(f'Unsupported private KDE dependency prefix count: {len(prefixes)}')
        for prefix, component in zip(prefixes, expected):
            config = prefix / f'lib/cmake/{component}/{component}Config.cmake'
            if not config.is_file():
                raise RuntimeError(f'Pinned {component} config missing: {config}')
        # `brew cat` is the formula's authoritative upstream recipe. Keep
        # the modified copy visible in this app's private dependency area.
        original = subprocess.check_output(['brew', 'cat', formula], text=True)
        import json
        # Homebrew configures each dependency in a separate, sanitized build
        # environment. The parent's CMAKE_PREFIX_PATH does not reach those
        # builds. Prepare missing KDE dependencies through the same private
        # pinned-prefix formula mechanism, starting at the leaves of the graph.
        # Rewrite their dependency declarations to the private tap so Homebrew
        # does not build the unmodified upstream formula a second time.
        private_dependencies: dict[str, str] = {}
        dependency_pattern = re.compile(
            r"""(?m)^(\s*depends_on\s+)(["'])(kde-mac/kde/[A-Za-z0-9_+.-]+|kf5-[A-Za-z0-9_+.-]+)(["'])"""
        )
        for match in dependency_pattern.finditer(original):
            dependency = match.group(3)
            if dependency.startswith('kf5-'):
                dependency = 'kde-mac/kde/' + dependency
            if subprocess.run(['brew', 'list', '--versions', dependency],
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, check=False).returncode == 0:
                continue
            if dependency == formula:
                raise RuntimeError(f'KDE formula depends on itself: {formula}')
            if dependency not in private_dependencies:
                install_kde_formula(dependency, private_prefix=private_prefix,
                                    _dependency_stack=(*_dependency_stack, formula))
                private_dependencies[match.group(3)] = (
                    'cinterforge/kf5-private/' + dependency.rsplit('/', 1)[-1]
                )
        if private_dependencies:
            original = dependency_pattern.sub(
                lambda match: (match.group(1) + match.group(2) +
                               private_dependencies.get(match.group(3), match.group(3)) +
                               match.group(4)), original)
        class_match = re.search(r'(?m)^class\s+\w+\s*<\s*Formula\s*$', original)
        install_match = re.search(r'(?m)^(\s*)def install\s*$', original)
        if not class_match or not install_match:
            raise RuntimeError(f'Cannot safely adapt KDE formula structure: {formula}')
        if re.search(r'(?m)^\s*env\s+:', original):
            raise RuntimeError(f'KDE formula already declares an environment: {formula}')
        # The upstream recipe may specify a different prefix explicitly;
        # refuse rather than layering competing build environments.
        if 'CMAKE_PREFIX_PATH' in original:
            raise RuntimeError(f'KDE formula already configures CMAKE_PREFIX_PATH: {formula}')
        copied = original[:class_match.end()] + '\n  env :std' + original[class_match.end():]
        install_match = re.search(r'(?m)^(\s*)def install\s*$', copied)
        assert install_match is not None
        indent = install_match.group(1)
        private_path = json.dumps(os.pathsep.join(str(p) for p in prefixes))
        # In standard Homebrew formula builds ENV.prepend_path is supported.
        copied = (copied[:install_match.end()] + '\n' + indent +
                  '  ENV.prepend_path "CMAKE_PREFIX_PATH", ' + private_path +
                  copied[install_match.end():])
        # Qt 5 framework includes can be shadowed by Homebrew's unversioned
        # /opt/homebrew/include/QtGui forwarding headers. On macOS these
        # headers may recursively include themselves (qvectornd.h in
        # particular). Prepend a private overlay of the *actual* Qt 5
        # framework Headers to the formula's C++ header search path. This
        # keeps all Qt 5 modules consistent without editing any installed
        # Homebrew files, altering upstream sources, or disabling features.
        qt5 = Path(subprocess.check_output(
            ['brew', '--prefix', 'qt@5'], text=True).strip()).resolve()
        frameworks = sorted((qt5 / 'lib').glob('Qt*.framework'))
        if not frameworks:
            raise RuntimeError(f'Qt 5 frameworks unavailable: {qt5 / "lib"}')
        built_root = Path(os.environ['CINTER_BUILT_ROOT']).expanduser().resolve()
        overlay = built_root / 'Dependencies' / 'KF5Qt5HeaderOverlay'
        overlay.mkdir(parents=True, exist_ok=True)
        for framework in frameworks:
            headers = framework / 'Headers'
            if not headers.is_dir():
                raise RuntimeError(f'Qt 5 framework headers missing: {headers}')
            link = overlay / framework.name.removesuffix('.framework')
            if link.is_symlink():
                if link.resolve() != headers.resolve():
                    link.unlink()
                else:
                    continue
            if link.exists():
                raise RuntimeError(f'Unexpected non-symlink Qt header overlay: {link}')
            link.symlink_to(headers, target_is_directory=True)
        # CMake does not reliably propagate Homebrew's ENV CXXFLAGS into
        # generated Ninja compile rules (the failing KF5Wallet command had no
        # overlay -I at all). Clang reads CPATH at compile time, before the
        # implicit /opt/homebrew/include search directory. Keep Qt 5 headers
        # ahead of the Qt 6 forwarding headers even for generated sources.
        # KIO's HTTP autotests inherit an explicit -isystem <SDK>/usr/include
        # from a dependency. That C header directory precedes Clang's
        # implicit libc++ headers, which breaks libc++ include_next wrappers.
        # CPATH cannot correct an explicit -isystem ordering. For KIO alone,
        # declare the SDK's matching libc++ directory as the first C++
        # include directory, and disable only Clang's *implicit duplicate*
        # C++ include directory. The same libc++ is still used, with all
        # upstream code, documentation and tests enabled.
        # Assemble the install-method preamble in execution order. The KIO
        # compiler flags depend on qt5_sdk_libcxx, so that variable must be
        # defined before the ENV.append statement in the generated Ruby.
        header_setup = (
            '  qt5_sdk_libcxx = MacOS.sdk_path/"usr/include/c++/v1"\n' +
            '  raise "Matching macOS SDK libc++ headers missing: #{qt5_sdk_libcxx}" unless qt5_sdk_libcxx.directory?\n' +
            '  ENV.prepend_path "CPATH", ' + json.dumps(str(overlay)) + '\n'
        )
        if formula.rsplit('/', 1)[-1] == 'kf5-kio':
            # CMake captures CXXFLAGS at configure time. A user -I path is
            # searched before CMake's explicit -isystem SDK C header path.
            header_setup += '  ENV.append "CXXFLAGS", "-nostdinc++ -I#{qt5_sdk_libcxx}"\n'
        copied = (copied[:install_match.end()] + '\n' + indent +
                  header_setup.replace('\n', '\n' + indent) +
                  copied[install_match.end():])
        # Phonon 4.11.1 predates Clang 16's strict enum constant checks.
        # Apply the upstream ABI-compatible unsigned enum declaration inside
        # the private Homebrew build, leaving all Phonon features enabled.
        # An unexpected upstream source layout fails rather than silently
        # suppressing the compiler diagnostic.
        if formula.rsplit('/', 1)[-1] == 'phonon':
            phonon_patch = (
                '  header = buildpath/"phonon/objectdescription.h"\n'
                '  source = header.read\n'
                '  declaration = "enum ObjectDescriptionType"\n'
                '  raise "Unsupported Phonon ObjectDescriptionType declaration" unless source.include?(declaration)\n'
                '  patched = source.sub(/enum ObjectDescriptionType[[:space:]]*[{]/, "enum ObjectDescriptionType : unsigned {")\n'
                '  raise "Phonon enum patch failed" if patched == source\n'
                '  inreplace header.to_s, /enum ObjectDescriptionType[[:space:]]*[{]/, "enum ObjectDescriptionType : unsigned {"\n'
            )
            # Insert after `def install` and before any upstream CMake work.
            copied = (copied[:install_match.end()] + '\n' + indent +
                      phonon_patch.replace('\n', '\n' + indent) +
                      copied[install_match.end():])
        built_root = Path(os.environ['CINTER_BUILT_ROOT']).expanduser().resolve()
        local_formulas = built_root / 'Dependencies' / 'KF5FormulaSources'
        local_formulas.mkdir(parents=True, exist_ok=True)
        recipe = local_formulas / (formula.rsplit('/', 1)[-1] + '.rb')
        recipe.write_text(copied)
        print(f'[Creative Suite mac][dependencies] Private pinned KF5 recipe: {recipe}', flush=True)
        # Modern Homebrew refuses direct local formula paths. Maintain a
        # private git-backed tap in this application's visible Built tree;
        # Homebrew registers it without copying the recipe into source.
        tap_root = built_root / 'Dependencies' / 'KF5PrivateTap'
        tap_root.mkdir(parents=True, exist_ok=True)
        formula_dir = tap_root / 'Formula'
        formula_dir.mkdir(exist_ok=True)
        tap_recipe = formula_dir / recipe.name
        tap_recipe.write_text(copied)
        # KDE formulae reference a sibling ../lib/cmake helper. `brew cat`
        # returns the formula alone, so transplant its exact companion from
        # the already-registered upstream KDE tap into the private tap.
        # Without it, Homebrew fails before the private formula can configure.
        helper_refs = re.findall(r'(?m)^\s*require_relative\s+["\']\.\./lib/([^"\']+)["\']', copied)
        if helper_refs:
            upstream_tap = Path(subprocess.check_output(
                ['brew', '--repo', 'kde-mac/kde'], text=True).strip()).resolve()
            lib_dir = tap_root / 'lib'
            lib_dir.mkdir(exist_ok=True)
            for helper in sorted(set(helper_refs)):
                if not re.fullmatch(r'[A-Za-z0-9_-]+', helper):
                    raise RuntimeError(f'Unexpected KDE formula helper: {helper!r}')
                upstream_helper = upstream_tap / 'lib' / (helper + '.rb')
                if not upstream_helper.is_file():
                    raise RuntimeError(f'Missing upstream KDE formula helper: {upstream_helper}')
                shutil.copyfile(upstream_helper, lib_dir / upstream_helper.name)
        if not (tap_root / '.git').is_dir():
            run('git', '-C', str(tap_root), 'init')
        run('git', '-C', str(tap_root), 'add', 'Formula/' + recipe.name)
        for helper in sorted(set(helper_refs)):
            run('git', '-C', str(tap_root), 'add', 'lib/' + helper + '.rb')
        # `brew tap` works with a local git remote; it does not require
        # publishing or modifying the original kde-mac/kde tap.
        if subprocess.run(['git', '-C', str(tap_root), 'diff', '--cached', '--quiet'],
                          check=False).returncode != 0:
            run('git', '-C', str(tap_root), '-c', 'user.name=CinterForge',
                '-c', 'user.email=build@localhost', 'commit', '-m',
                'Pin private KF5 Homebrew formula')
        tap_name = 'cinterforge/kf5-private'
        registered = subprocess.run(['brew', 'tap'], capture_output=True, text=True, check=True)
        if tap_name not in registered.stdout.splitlines():
            run('brew', 'tap', tap_name, str(tap_root))
        else:
            # `brew tap` clones the local Git source once. Existing cloned
            # taps must incorporate subsequent pinned changes on reruns.
            installed_tap = Path(subprocess.check_output(
                ['brew', '--repo', tap_name], text=True).strip())
            run('git', '-C', str(installed_tap), 'pull', '--ff-only', str(tap_root), 'HEAD')
        install_command = ['brew', 'install', tap_name + '/' + recipe.stem]
    for _ in range(64):
        print('[Creative Suite mac][dependencies] ' + ' '.join(install_command), flush=True)
        result = subprocess.run(install_command, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if result.stdout:
            print(result.stdout, end='' if result.stdout.endswith('\n') else '\n', flush=True)
        if result.returncode == 0:
            return
        match = re.search(
            r'Refusing to load formula (kde-mac/kde/[A-Za-z0-9_+.-]+) '
            r'from untrusted tap kde-mac/kde', result.stdout or ''
        )
        if not match:
            # Homebrew often prints only the last 15 build-log lines, which
            # can contain warnings rather than the compiler error itself.
            # Surface the actual build diagnostics before reporting failure.
            name = install_command[-1].rsplit('/', 1)[-1]
            log_dir = Path.home() / 'Library' / 'Logs' / 'Homebrew' / name
            logs = sorted(log_dir.glob('*.log')) if log_dir.is_dir() else []
            for log in logs[-4:]:
                lines = log.read_text(errors='replace').splitlines()
                errors = [i for i, line in enumerate(lines)
                          if re.search(r'(?i)(?:^|\s)(?:fatal error:|error:|undefined symbols|ld: error:|ninja: error:|FAILED:)', line)]
                if errors:
                    print(f'[Creative Suite mac][dependencies] Build failures in {log}:', flush=True)
                    for i in errors[:20]:
                        start, end = max(0, i - 2), min(len(lines), i + 4)
                        print('\n'.join(f'  {n + 1}: {lines[n]}' for n in range(start, end)), flush=True)
            raise RuntimeError(f'Homebrew failed to install {formula} (exit {result.returncode}); '
                               f'full compiler logs: {log_dir}')
        dependency = match.group(1)
        if dependency in trusted:
            raise RuntimeError(f'Homebrew still rejects trusted formula {dependency}')
        print(f'[Creative Suite mac][dependencies] Trust exact KDE formula: {dependency}', flush=True)
        run('brew', 'trust', '--formula', dependency)
        trusted.add(dependency)
    raise RuntimeError(f'Too many transitive trust dependencies for {formula}')

def provision_pinned_kf5_i18n() -> Path:
    """Build KF5 I18n 5.111 into Krita's private dependencies.

    The KDE Homebrew tap no longer contains kf5-ki18n. The KDE 6 `ki18n`
    bottle is incompatible with the Qt 5 / KF5 API required by this checkout.
    """
    built = Path(os.environ['CINTER_BUILT_ROOT']).expanduser().resolve()
    root = built / 'Dependencies' / 'KF5I18n-5.111.0'
    install = root / 'Install'
    config = next(install.rglob('KF5I18nConfig.cmake'), None) if install.is_dir() else None
    if config and config.is_file():
        print(f'[Creative Suite mac][dependencies] Reuse pinned KF5I18n: {config}', flush=True)
        return install
    archive = root / 'DependencySources' / 'ki18n-5.111.0.tar.xz'
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        url = 'https://download.kde.org/Attic/frameworks/5.111/ki18n-5.111.0.tar.xz'
        print(f'[Creative Suite mac][dependencies] Fetch pinned {url}', flush=True)
        urllib.request.urlretrieve(url, archive)
    digest = hashlib.sha1(archive.read_bytes()).hexdigest()
    if digest != '74ba5353907f8ee95fcb27ea7e1d7e26db03813c':
        raise RuntimeError(f'KF5 I18n source SHA1 mismatch: {digest}')
    source = root / 'Source' / 'ki18n-5.111.0'
    if not (source / 'CMakeLists.txt').is_file():
        source.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive) as tf:
            # Trusted archive is checksum-verified, but still validate paths.
            for member in tf.getmembers():
                dest = (source.parent / member.name).resolve()
                if not dest.is_relative_to(source.parent.resolve()):
                    raise RuntimeError(f'Unsafe KF5 archive member: {member.name}')
            tf.extractall(source.parent)  # SHA1-verified archive; compatible with Python 3.11.0
    if not (source / 'CMakeLists.txt').is_file():
        raise RuntimeError(f'Invalid pinned KF5 I18n source: {source}')
    qt5 = Path(subprocess.check_output(['brew', '--prefix', 'qt@5'], text=True).strip())
    ecm = Path(subprocess.check_output(['brew', '--prefix', 'extra-cmake-modules'], text=True).strip())
    work = root / 'Build'
    run('cmake', '-S', str(source), '-B', str(work), '-G', 'Ninja',
        '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_OSX_ARCHITECTURES=arm64',
        '-DCMAKE_INSTALL_PREFIX=' + str(install),
        '-DCMAKE_PREFIX_PATH=' + ';'.join(map(str, (qt5, ecm))),
        '-DQt5_DIR=' + str(qt5 / 'lib/cmake/Qt5'),
        '-DECM_DIR=' + str(ecm / 'share/ECM/cmake'))
    run('cmake', '--build', str(work), '--parallel', str(max(1, (os.cpu_count() or 2) // 2)))
    run('cmake', '--install', str(work))
    if not list(install.rglob('KF5I18nConfig.cmake')):
        raise RuntimeError(f'Pinned KF5 I18n installed without CMake config: {install}')
    return install

def provision_pinned_kf5_archive() -> Path:
    """Install the missing KF5Archive 5.111.0 from upstream KDE source.

    The kde-mac/kde tap no longer supplies a kf5-karchive formula. Build it
    into Krita's private dependency tree; never substitute KF6 Archive.
    """
    built = Path(os.environ['CINTER_BUILT_ROOT']).expanduser().resolve()
    root = built / 'Dependencies' / 'KF5Archive-5.111.0'
    install = root / 'Install'
    config = install / 'lib/cmake/KF5Archive/KF5ArchiveConfig.cmake'
    if config.is_file():
        print(f'[Creative Suite mac][dependencies] Reuse pinned KF5Archive: {config}', flush=True)
        return install
    source_dir = root / 'Source'
    source = source_dir / 'karchive-5.111.0'
    archive = root / 'DependencySources' / 'karchive-5.111.0.tar.xz'
    archive.parent.mkdir(parents=True, exist_ok=True)
    url = 'https://download.kde.org/Attic/frameworks/5.111/karchive-5.111.0.tar.xz'
    if not archive.is_file():
        print(f'[Creative Suite mac][dependencies] Fetch pinned {url}', flush=True)
        urllib.request.urlretrieve(url, archive)
    # Verify against the upstream release checksum, rather than assuming that
    # a partial/interrupted download is a usable source distribution.
    checksum_path = archive.with_suffix(archive.suffix + '.sha256')
    if not checksum_path.is_file():
        urllib.request.urlretrieve(url + '.sha256', checksum_path)
    match = re.search(r'\b([0-9a-fA-F]{64})\b', checksum_path.read_text())
    if not match or hashlib.sha256(archive.read_bytes()).hexdigest().lower() != match.group(1).lower():
        raise RuntimeError(f'KF5Archive upstream SHA256 mismatch: {archive}')
    if not (source / 'CMakeLists.txt').is_file():
        source_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive) as tf:
            for member in tf.getmembers():
                dest = (source_dir / member.name).resolve()
                if not dest.is_relative_to(source_dir.resolve()):
                    raise RuntimeError(f'Unsafe KF5Archive source member: {member.name}')
            tf.extractall(source_dir)
    if not (source / 'CMakeLists.txt').is_file():
        raise RuntimeError(f'Pinned KF5Archive source missing: {source}')
    qt5 = Path(subprocess.check_output(['brew', '--prefix', 'qt@5'], text=True).strip())
    ecm = Path(subprocess.check_output(['brew', '--prefix', 'extra-cmake-modules'], text=True).strip())
    kcore = Path(subprocess.check_output(['brew', '--prefix', 'kde-mac/kde/kf5-kcoreaddons'], text=True).strip())
    build = root / 'Build'
    run('cmake', '-S', str(source), '-B', str(build), '-G', 'Ninja',
        '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_OSX_ARCHITECTURES=arm64',
        '-DCMAKE_INSTALL_PREFIX=' + str(install),
        '-DCMAKE_PREFIX_PATH=' + ';'.join(map(str, (qt5, ecm, kcore))),
        '-DQt5_DIR=' + str(qt5 / 'lib/cmake/Qt5'),
        '-DECM_DIR=' + str(ecm / 'share/ECM/cmake'))
    run('cmake', '--build', str(build), '--parallel', str(max(1, (os.cpu_count() or 2) // 2)))
    run('cmake', '--install', str(build))
    if not config.is_file():
        raise RuntimeError(f'KF5Archive built without CMake package: {config}')
    return install

def provision_pinned_qca_qt5() -> Path:
    """Build QCA 2.3.12 against Qt 5 for KF5Wallet's Qca-qt5 API."""
    config_spec = json.loads((Path(__file__).with_name('recipe.json')).read_text())['qca_qt5']
    version = config_spec['version']
    built = Path(os.environ['CINTER_BUILT_ROOT']).expanduser().resolve()
    root = built / 'Dependencies' / f'QcaQt5-{version}'
    install = root / 'Install'
    config = install / 'lib/cmake/Qca-qt5/Qca-qt5Config.cmake'
    if config.is_file():
        print(f'[Creative Suite mac][dependencies] Reuse pinned Qca-qt5: {config}', flush=True)
        return install
    archive = root / 'DependencySources' / f'qca-{version}.tar.xz'
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.is_file():
        print(f'[Creative Suite mac][dependencies] Fetch pinned Qca-qt5 source: {config_spec["url"]}', flush=True)
        urllib.request.urlretrieve(config_spec['url'], archive)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != config_spec['sha256']:
        raise RuntimeError(f'Qca-qt5 pinned source SHA256 mismatch: {digest}')
    source_root = root / 'Source'
    source = source_root / f'qca-{version}'
    if not (source / 'CMakeLists.txt').is_file():
        source_root.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive) as tf:
            for member in tf.getmembers():
                destination = (source_root / member.name).resolve()
                if not destination.is_relative_to(source_root.resolve()):
                    raise RuntimeError(f'Unsafe QCA source member: {member.name}')
                if member.issym() or member.islnk():
                    raise RuntimeError(f'Unexpected QCA archive link: {member.name}')
            tf.extractall(source_root)
    if not (source / 'CMakeLists.txt').is_file():
        raise RuntimeError(f'Pinned QCA source missing CMakeLists.txt: {source}')
    qt5 = Path(subprocess.check_output(['brew', '--prefix', 'qt@5'], text=True).strip())
    build = root / 'Build'
    run('cmake', '-S', str(source), '-B', str(build), '-G', 'Ninja',
        '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_OSX_ARCHITECTURES=arm64',
        '-DCMAKE_INSTALL_PREFIX=' + str(install),
        '-DCMAKE_PREFIX_PATH=' + str(qt5),
        '-DQt5_DIR=' + str(qt5 / 'lib/cmake/Qt5'),
        '-DBUILD_WITH_QT6=OFF', '-DQT6=OFF',
        '-DBUILD_TESTS=ON', '-DBUILD_TOOLS=ON', '-DBUILD_PLUGINS=auto')
    run('cmake', '--build', str(build), '--parallel', str(max(1, (os.cpu_count() or 2) // 2)))
    run('cmake', '--install', str(build))
    if not config.is_file():
        raise RuntimeError(f'QCA built without Qt 5 CMake package: {config}')
    return install

def provision_quazip_qt5() -> Path:
    """Build QuaZip for Krita's Qt 5 ABI (Homebrew's quazip is Qt 6).

    Keep the pinned upstream checkout, build and install in this application's
    existing external Built/Dependencies hierarchy. Do not install Qt 6 QuaZip
    into the Qt 5 build or change Homebrew's global library selection.
    """
    built = Path(os.environ['CINTER_BUILT_ROOT']).expanduser().resolve()
    root = built / 'Dependencies' / 'QuaZipQt5-1.7.2'
    source = root / 'Source'
    build = root / 'Build'
    install = root / 'Install'
    configs = sorted(install.rglob('QuaZip-Qt5Config.cmake'))
    if len(configs) == 1:
        print(f'[Creative Suite mac][dependencies] Reuse private Qt5 QuaZip: {configs[0]}', flush=True)
        return configs[0].parent
    if configs:
        raise RuntimeError(f'Unexpected multiple private Qt 5 QuaZip configs: {configs}')
    root.mkdir(parents=True, exist_ok=True)
    if not source.exists():
        run('git', 'clone', '--branch', 'v1.7.2', '--depth', '1',
            'https://github.com/stachenov/quazip.git', str(source))
    if not (source / 'CMakeLists.txt').is_file():
        raise RuntimeError(f'Pinned QuaZip source missing: {source}')
    tag = subprocess.check_output(['git', '-C', str(source), 'describe', '--tags', '--exact-match'], text=True).strip()
    if tag != 'v1.7.2':
        raise RuntimeError(f'Private QuaZip checkout is not pinned at v1.7.2: {source} ({tag})')
    qt5 = Path(subprocess.check_output(['brew', '--prefix', 'qt@5'], text=True).strip())
    run('cmake', '-S', str(source), '-B', str(build), '-G', 'Ninja',
        '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_OSX_ARCHITECTURES=arm64',
        '-DCMAKE_INSTALL_PREFIX=' + str(install),
        '-DCMAKE_PREFIX_PATH=' + str(qt5),
        '-DQt5_DIR=' + str(qt5 / 'lib/cmake/Qt5'),
        '-DQUAZIP_QT_MAJOR_VERSION=5')
    run('cmake', '--build', str(build), '--parallel', str(max(1, (os.cpu_count() or 2) // 2)))
    run('cmake', '--install', str(build))
    configs = sorted(install.rglob('QuaZip-Qt5Config.cmake'))
    if len(configs) != 1:
        raise RuntimeError(f'Expected one installed Qt 5 QuaZip config in {install}, found {configs}')
    print(f'[Creative Suite mac][dependencies] READY private Qt5 QuaZip: {configs[0]}', flush=True)
    return configs[0].parent

def provision_krita_kf5() -> None:
    """Install the KF5 ABI required by this Krita checkout, not KF6.

    The upstream KDE macOS Homebrew tap publishes KF5 formulae separately.
    Do not silently substitute the newer KF6 libraries from homebrew/core:
    KF5 CMake package names and Qt 5 ABI are required by Krita 5.x.
    """
    provision_ecm()
    # This Krita 5.x checkout needs Qt5, not Homebrew's Qt6 defaults.
    provision_brew(('qt@5', 'appstream', 'immer', 'zug', 'lager',
                    'eigen@3', 'opencolorio', 'mlt', 'xsimd'))
    immer = Path(subprocess.check_output(['brew', '--prefix', 'immer'], text=True).strip())
    immer_configs = sorted(immer.rglob('ImmerConfig.cmake'))
    if len(immer_configs) != 1:
        raise RuntimeError(f'Expected one installed Immer CMake config under {immer}, found: {immer_configs}')
    print(f'[Creative Suite mac][dependencies] READY Immer: {immer_configs[0]}', flush=True)
    zug = Path(subprocess.check_output(['brew', '--prefix', 'zug'], text=True).strip())
    zug_configs = sorted(zug.rglob('ZugConfig.cmake'))
    if len(zug_configs) != 1:
        raise RuntimeError(f'Expected one installed Zug CMake config under {zug}, found: {zug_configs}')
    print(f'[Creative Suite mac][dependencies] READY Zug: {zug_configs[0]}', flush=True)
    lager = Path(subprocess.check_output(['brew', '--prefix', 'lager'], text=True).strip())
    lager_configs = sorted(lager.rglob('LagerConfig.cmake'))
    if len(lager_configs) != 1:
        raise RuntimeError(f'Expected one installed Lager CMake config under {lager}, found: {lager_configs}')
    print(f'[Creative Suite mac][dependencies] READY Lager: {lager_configs[0]}', flush=True)
    # Krita requires Eigen 3.x; Homebrew's unversioned eigen is now 5.x.
    eigen = Path(subprocess.check_output(['brew', '--prefix', 'eigen@3'], text=True).strip())
    eigen_configs = sorted(eigen.rglob('Eigen3Config.cmake'))
    if len(eigen_configs) != 1:
        raise RuntimeError(f'Expected one Eigen 3 CMake config under {eigen}, found: {eigen_configs}')
    print(f'[Creative Suite mac][dependencies] READY Eigen3: {eigen_configs[0]}', flush=True)
    # Retain upstream color-management and video-import support.
    ocio = Path(subprocess.check_output(['brew', '--prefix', 'opencolorio'], text=True).strip())
    if not (ocio / 'include/OpenColorIO/OpenColorIO.h').is_file():
        raise RuntimeError(f'OpenColorIO headers missing under {ocio}')
    mlt = Path(subprocess.check_output(['brew', '--prefix', 'mlt'], text=True).strip())
    if not list(mlt.rglob('Mlt7Config.cmake')):
        raise RuntimeError(f'MLT 7 CMake config missing under {mlt}')
    # Both are required by this Krita checkout. QuaZip from brew is linked
    # against Qt 6; use our pinned, private Qt 5 build instead.
    xsimd = Path(subprocess.check_output(['brew', '--prefix', 'xsimd'], text=True).strip())
    xsimd_configs = sorted(xsimd.rglob('xsimdConfig.cmake'))
    if len(xsimd_configs) != 1:
        raise RuntimeError(f'Expected one xsimd CMake config under {xsimd}, found {xsimd_configs}')
    print(f'[Creative Suite mac][dependencies] READY xsimd: {xsimd_configs[0]}', flush=True)
    provision_quazip_qt5()
    tap = 'kde-mac/kde'
    source = 'https://invent.kde.org/packaging/homebrew-kde.git'
    if subprocess.run(['brew', 'tap'], check=True, capture_output=True,
                      text=True).stdout.splitlines().count(tap) == 0:
        run('brew', 'tap', tap, source)
    required = {
        'Config': 'kf5-kconfig',
        'WidgetsAddons': 'kf5-kwidgetsaddons',
        'Completion': 'kf5-kcompletion',
        'CoreAddons': 'kf5-kcoreaddons',
        'GuiAddons': 'kf5-kguiaddons',
        'ItemViews': 'kf5-kitemviews',
    }
    # Include the project's other commonly required KF5 integration modules;
    # CMake is still the authority for any additional dependencies.
    # kiconthemes requires KF5Archive during CMake configure.  Homebrew's
    # upstream formula omits that build dependency, so prepare the exact
    # KF5Archive 5.111 package before configuring KIconThemes.
    extras = ('kf5-kconfigwidgets', 'kf5-kiconthemes',
              'kf5-kio', 'kf5-kitemmodels', 'kf5-kwindowsystem',
              'kf5-kcrash', 'kf5-kservice', 'kf5-sonnet', 'kf5-kxmlgui')
    private_i18n = provision_pinned_kf5_i18n()
    # Archive depends on already-installed KF5CoreAddons. Prepare the latter
    # before compiling pinned Archive; the remaining KDE formulas come after.
    provision_brew(tuple(f'{tap}/{name}' for name in required.values()),
                   private_prefix=private_i18n)
    private_archive = provision_pinned_kf5_archive()
    private_qca = provision_pinned_qca_qt5()
    provision_brew(tuple(f'{tap}/{name}' for name in extras),
                   private_prefix=(private_i18n, private_archive, private_qca))
    missing = []
    for cmake_component, formula in required.items():
        prefix = Path(subprocess.check_output(
            ['brew', '--prefix', f'{tap}/{formula}'], text=True).strip())
        matches = list(prefix.glob(f'**/KF5{cmake_component}Config.cmake'))
        if not matches:
            missing.append(f'KF5{cmake_component}: {prefix}')
        else:
            print(f'[Creative Suite mac][dependencies] READY KF5{cmake_component}: '
                  f'{matches[0]}', flush=True)
    if missing:
        raise RuntimeError('Installed KF5 formulas have no expected CMake configs: '
                           + '; '.join(missing))

def provision_genuine_sdl2(built_root: Path | None = None) -> Path:
    """Build upstream SDL2, not Homebrew's SDL2-compat alias, in Paint's Builds tree."""
    built = (built_root if built_root is not None else Path(os.environ['CINTER_BUILT_ROOT'])).expanduser().resolve()
    root = built / 'Dependencies' / 'SDL2-2.30.12'
    source, build, install = (root / name for name in ('Source', 'Build', 'Install'))
    library = install / 'lib/libSDL2-2.0.0.dylib'
    if library.is_file():
        print(f'[cfpaint][mac] Reuse genuine SDL2: {library}', flush=True)
        return install
    if not (source / 'CMakeLists.txt').is_file():
        source.parent.mkdir(parents=True, exist_ok=True)
        run('git', 'clone', '--depth', '1', '--branch', 'release-2.30.12',
            'https://github.com/libsdl-org/SDL.git', str(source))
    run('cmake', '-S', str(source), '-B', str(build), '-G', 'Ninja',
        '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_OSX_ARCHITECTURES=arm64',
        '-DCMAKE_INSTALL_PREFIX=' + str(install),
        '-DSDL_SHARED=ON', '-DSDL_STATIC=OFF', '-DSDL_TEST=OFF')
    run('cmake', '--build', str(build), '--parallel', str(max(1, (os.cpu_count() or 2) // 2)))
    run('cmake', '--install', str(build))
    if not library.is_file():
        raise RuntimeError(f'Genuine SDL2 build missing {library}')
    print(f'[cfpaint][mac] READY genuine SDL2: {library}', flush=True)
    return install


def main() -> None:
    validate_toolchain("cfpaint")
    print("[cfpaint][mac] Preparing pinned genuine SDL2 before KDE dependencies", flush=True)
    provision_genuine_sdl2()
    provision_krita_kf5()

if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, KeyError, ValueError, subprocess.CalledProcessError) as exc:
        print(f'[Creative Suite mac][setup] ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
