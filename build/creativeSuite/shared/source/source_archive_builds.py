"""Reusable verified release-archive dependency backend for Creative Suite.

All downloaded and extracted assets are visible under the product's DependencySources;
all installations go to its private Dependencies prefix. Nothing is cached in Cinter.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import urllib.request


def _run(args, *, cwd: Path, env: dict[str, str]) -> None:
    print(f"[archive-source] RUN {' '.join(map(str, args))}", flush=True)
    subprocess.run([str(a) for a in args], cwd=cwd, env=env, check=True)


def _download(url: str, destination: Path, expected_sha256: str) -> None:
    if destination.is_file() and hashlib.sha256(destination.read_bytes()).hexdigest() == expected_sha256:
        return
    temp = destination.with_name(destination.name + '.download')
    temp.unlink(missing_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=120) as source, temp.open('wb') as output:
            shutil.copyfileobj(source, output)
        actual = hashlib.sha256(temp.read_bytes()).hexdigest()
        if actual != expected_sha256:
            raise RuntimeError(f"Checksum mismatch for {url}: expected {expected_sha256}, got {actual}")
        temp.replace(destination)
    finally:
        temp.unlink(missing_ok=True)


def _extract(archive: Path, destination: Path) -> Path:
    if destination.is_dir():
        return destination
    scratch = destination.with_name(destination.name + '-extracting')
    if scratch.exists():
        shutil.rmtree(scratch)
    scratch.mkdir(parents=True)
    try:
        with tarfile.open(archive, 'r:*') as tar:
            for entry in tar.getmembers():
                name = Path(entry.name)
                if name.is_absolute() or '..' in name.parts or entry.issym() or entry.islnk() or entry.isdev():
                    raise RuntimeError(f"Unsafe archive entry in {archive}: {entry.name}")
            # Large upstream distributions (notably Boost) contain repeated
            # directory headers. Apple's Python tarfile extraction may fail
            # with ENOTEMPTY while restoring such entries. After validating
            # every member, use the platform's bsdtar on macOS; it correctly
            # handles repeated directory records without dropping content.
            if sys.platform != 'darwin':
                tar.extractall(scratch)
        if sys.platform == 'darwin':
            _run(['tar', '-xf', archive, '-C', scratch], cwd=scratch, env=os.environ.copy())
        # macOS tar archives can include Finder/AppleDouble metadata beside
        # the actual release root (for example .DS_Store). It is not a
        # second source tree and must not invalidate a verified extraction.
        metadata_names = {'.DS_Store', '__MACOSX'}
        roots = [entry for entry in scratch.iterdir()
                 if entry.name not in metadata_names and not entry.name.startswith('._')]
        if len(roots) != 1 or not roots[0].is_dir():
            raise RuntimeError(f"Expected one source root in {archive}, found {roots}")
        roots[0].rename(destination)
    finally:
        if scratch.exists():
            shutil.rmtree(scratch)
    return destination



def _prepare_legacy_waf_python(source: Path) -> None:
    """Normalize Waf's obsolete universal-newline file modes for Python 3.11.

    Patch the extracted source only, and tolerate formatting differences among
    bundled Waf releases. ``readf`` is the call site which receives ``rU``;
    normalize its mode before Waf passes it to Python's ``open``.
    """
    import re

    utils = source / 'waflib' / 'Utils.py'
    if not utils.is_file():
        raise RuntimeError(f"Waf compatibility source missing: {utils}")
    original = utils.read_text(encoding='utf-8')
    # Older fixes may already have normalized the mode directly at open().
    if re.search(r"open\(fname\s*,\s*m\.replace\(['\"]U['\"]\s*,\s*['\"]['\"]\)\s*\)", original):
        print(f"[archive-source] Waf Python 3 compatibility already prepared: {utils}", flush=True)
        return

    # Match the actual readf definition instead of assuming one particular
    # whitespace/minification layout for the open() expression.
    header = re.compile(r"(?m)^(?P<indent>[ \t]*)def[ \t]+readf\s*\([^\n]*\)\s*:\s*\n")
    matches = list(header.finditer(original))
    if len(matches) != 1:
        raise RuntimeError(f"Unexpected Waf readf definition in {utils}: found {len(matches)}")
    match = matches[0]
    body_start = match.end()
    rest = original[body_start:]
    # Only accept a standard indented Python function body with an open() call.
    block = rest.split('\ndef ', 1)[0]
    if not re.search(r"\bopen\s*\(", block):
        raise RuntimeError(f"Unexpected Waf readf body in {utils}: no open() call")
    marker = "m = m.replace('U', '')"
    if marker in block:
        print(f"[archive-source] Waf Python 3 compatibility already prepared: {utils}", flush=True)
        return
    body_indent = re.search(r'(?m)^([ \t]+)\S', block)
    if not body_indent or len(body_indent.group(1)) <= len(match.group('indent')):
        raise RuntimeError(f"Unexpected Waf readf indentation in {utils}")
    indent = body_indent.group(1)
    prepared = original[:body_start] + indent + marker + '\n' + original[body_start:]
    utils.write_text(prepared, encoding='utf-8')
    print(f"[archive-source] Patched extracted Waf readf file modes: {utils}", flush=True)


def _install_macos_boost(prefix: Path, source_cache: Path) -> None:
    """Install Homebrew's binary Boost instead of compiling Boost with B2.

    Keep a complete matching header/library set inside the app's versioned
    dependency prefix so Ardour's existing private-stack Waf and upstream
    packaging flows do not depend on a live Homebrew keg at runtime.
    """
    brew = shutil.which('brew')
    if not brew:
        raise RuntimeError('Boost needs Homebrew on macOS; brew was not found')
    result = subprocess.run([brew, '--prefix', 'boost'], capture_output=True, text=True)
    brew_prefix = Path(result.stdout.strip()) if result.returncode == 0 else None
    if not brew_prefix or not (brew_prefix / 'include/boost/version.hpp').is_file():
        _run([brew, 'install', 'boost'], cwd=source_cache, env=os.environ.copy())
        result = subprocess.run([brew, '--prefix', 'boost'], capture_output=True, text=True, check=True)
        brew_prefix = Path(result.stdout.strip())
    header = brew_prefix / 'include/boost/version.hpp'
    libraries = sorted((brew_prefix / 'lib').glob('libboost_*'))
    if not header.is_file() or not any(f.name.endswith('.dylib') for f in libraries):
        raise RuntimeError(f'Homebrew Boost is incomplete at {brew_prefix}: missing headers/dylibs')
    # An interrupted B2 build may have installed a subset of a *different*
    # Boost version. Never mix those binaries with Homebrew's header release.
    import re
    source_version_text = header.read_text(encoding='utf-8')
    match = re.search(r'(?m)^#define BOOST_LIB_VERSION "([^"]+)"', source_version_text)
    if not match:
        raise RuntimeError(f'Cannot determine Boost version from {header}')
    source_version = match.group(1)
    version_stamp = source_cache / 'boost-homebrew-installed-version.txt'
    target_header = prefix / 'include/boost/version.hpp'
    target_version = None
    if target_header.is_file():
        old = re.search(r'(?m)^#define BOOST_LIB_VERSION "([^"]+)"', target_header.read_text(encoding='utf-8'))
        target_version = old.group(1) if old else None
    if (version_stamp.is_file() and version_stamp.read_text().strip() == source_version
            and target_version == source_version
            and any((prefix / 'lib').glob('libboost_*.dylib'))):
        print(f'[boost] REUSE installed Homebrew Boost {source_version}', flush=True)
        return
    installed_headers = prefix / 'include/boost'
    if installed_headers.exists():
        shutil.rmtree(installed_headers)
    shutil.copytree(brew_prefix / 'include/boost', installed_headers)
    target_lib = prefix / 'lib'
    target_lib.mkdir(parents=True, exist_ok=True)
    for old in target_lib.glob('libboost_*'):
        if old.is_file() or old.is_symlink():
            old.unlink()
    for library in libraries:
        if library.is_file():
            # Dereference Homebrew's keg-internal symlinks; standalone staged
            # dependencies must not refer back to /opt/homebrew.
            shutil.copy2(library, target_lib / library.name, follow_symlinks=True)
    version_stamp.write_text(source_version + '\n', encoding='utf-8')
    print(f'[boost] INSTALLED Homebrew binary Boost {source_version} into {prefix}', flush=True)


def build_release_archives(records: tuple[dict, ...], *, prefix: Path,
                           source_cache: Path, environment: dict[str, str]) -> None:
    """Build SHA256-pinned release archives; reuse completed private pkg-config installs."""
    source_cache.mkdir(parents=True, exist_ok=True)
    prefix.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(environment)
    pkgpaths = [prefix / 'lib' / 'pkgconfig', prefix / 'lib64' / 'pkgconfig', prefix / 'share' / 'pkgconfig']
    env['PKG_CONFIG_PATH'] = os.pathsep.join(map(str, pkgpaths))
    env['PKG_CONFIG_LIBDIR'] = env['PKG_CONFIG_PATH']
    env['PATH'] = str(prefix / 'bin') + os.pathsep + env.get('PATH', '')
    env['CPPFLAGS'] = f"-I{prefix / 'include'} " + env.get('CPPFLAGS', '')
    env['LDFLAGS'] = f"-L{prefix / 'lib'} " + env.get('LDFLAGS', '')
    # Waf's `build` also executes its freshly linked test programs.  macOS
    # resolves their @rpath references to private libraries only if the
    # executables themselves carry the installation prefix as an LC_RPATH.
    # This must be supplied during Waf configure, not after tests have run.
    # Waf consumes LINKFLAGS for its link tasks; other archive build systems
    # consume LDFLAGS.  Preserve both rather than disabling upstream tests.
    if sys.platform == 'darwin':
        private_rpath = f"-Wl,-rpath,{prefix / 'lib'}"
        for key in ('LDFLAGS', 'LINKFLAGS'):
            flags = env.get(key, '').split()
            if private_rpath not in flags:
                flags.append(private_rpath)
            env[key] = ' '.join(flags)
    for row in records:
        name = row['name']
        modules = row['pkgconfig_modules']
        if row['build_system'] == 'boost' and sys.platform == 'darwin':
            _install_macos_boost(prefix, source_cache)
            continue
        if row['build_system'] == 'boost':
            # Other platforms retain their existing source-build behavior.
            boost_headers = prefix / 'include' / 'boost' / 'version.hpp'
            boost_libraries = tuple((prefix / 'lib').glob('libboost_*.so*')) + tuple((prefix / 'lib').glob('libboost_*.dylib'))
            if boost_headers.is_file() and boost_libraries:
                print(f"[{name}] REUSE private dependency", flush=True)
                continue
        elif all(subprocess.run(['pkg-config', '--exists', mod], env=env).returncode == 0 for mod in modules):
            print(f"[{name}] REUSE private dependency", flush=True)
            continue
        filename = row['url'].rsplit('/', 1)[-1]
        if not filename.endswith(('.tar.gz', '.tar.bz2', '.tar.xz')):
            raise RuntimeError(f"{name}: unsupported release archive filename: {filename}")
        archive = source_cache / filename
        print(f"[{name}] DOWNLOAD / VERIFY {row['url']}", flush=True)
        _download(row['url'], archive, row['sha256'])
        source = _extract(archive, source_cache / f"{name}-release-source")
        if row['build_system'] == 'boost':
            # Install the complete upstream Boost distribution (headers and
            # compiled libraries) into Ardour's private, versioned prefix.
            # Run the release's own bootstrap and B2, not a host Boost keg.
            bootstrap = source / 'bootstrap.sh'
            if not bootstrap.is_file():
                raise RuntimeError(f"{name}: official release lacks bootstrap.sh: {bootstrap}")
            _run(['bash', bootstrap, '--prefix=' + str(prefix), *row['configure_args']], cwd=source, env=env)
            b2 = source / 'b2'
            if not b2.is_file():
                raise RuntimeError(f"{name}: bootstrap did not produce b2: {b2}")
            _run([b2, '-j' + str(os.cpu_count() or 4),
                  '--build-dir=' + str(source_cache / 'boost-build'),
                  'toolset=clang', 'link=shared,static', 'variant=release',
                  '--prefix=' + str(prefix), *row['build_args'],
                  'install', *row['install_args']], cwd=source, env=env)
            if not (prefix / 'include/boost/version.hpp').is_file() or not tuple((prefix / 'lib').glob('libboost_*.dylib')):
                raise RuntimeError(f"{name}: bootstrap/b2 completed without Boost headers and dylibs in {prefix}")
        elif row['build_system'] == 'autotools':
            _run([source / 'configure', '--prefix=' + str(prefix), *row['configure_args']], cwd=source, env=env)
            _run(['make', '-j', str(os.cpu_count() or 4), *row['build_args']], cwd=source, env=env)
            _run(['make', 'install', *row['install_args']], cwd=source, env=env)
        elif row['build_system'] == 'waf':
            waf = source / 'waf'
            if not waf.is_file():
                raise RuntimeError(f"{name}: official release does not include waf executable: {waf}")
            _prepare_legacy_waf_python(source)
            _run([sys.executable, waf, 'configure', '--prefix=' + str(prefix), *row['configure_args']], cwd=source, env=env)
            _run([sys.executable, waf, 'build', *row['build_args']], cwd=source, env=env)
            _run([sys.executable, waf, 'install', *row['install_args']], cwd=source, env=env)
        else:
            raise RuntimeError(f"{name}: unsupported archive build_system {row['build_system']}")
        for module in modules:
            _run(['pkg-config', '--modversion', module], cwd=source, env=env)
