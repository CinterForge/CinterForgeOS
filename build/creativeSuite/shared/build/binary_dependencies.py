"""Binary package acquisition into a versioned, application-owned Dependencies tree.

Homebrew bottles are not relocatable Homebrew installations: brew manages its
own Cellar, while this module stages selected binary outputs in the app prefix.
APT .deb archives can be unpacked without touching the host's dpkg database.
Never replace an existing private source build implicitly.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess


def _call(command: list[str], *, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(command, check=True, text=True, capture_output=True, env=env)
    return completed.stdout.strip()


def _private_pc_environment(prefix: Path) -> dict[str, str]:
    env = os.environ.copy()
    directories = [prefix / part for part in ("lib/pkgconfig", "lib64/pkgconfig", "share/pkgconfig")]
    env["PKG_CONFIG_LIBDIR"] = os.pathsep.join(str(p) for p in directories)
    env["PKG_CONFIG_PATH"] = env["PKG_CONFIG_LIBDIR"]
    return env


def _local_package_works(prefix: Path, module: str, minimum: str) -> bool:
    pc = prefix / "lib/pkgconfig" / f"{module}.pc"
    if not pc.is_file():
        return False
    # Match Ardour's Waf check_cfg invocation, not only the version probe.
    # A .pc file can advertise a valid version while --cflags/--libs fails
    # because a required transitive .pc file is absent.
    check = subprocess.run(
        ["pkg-config", f"{module} >= {minimum}", "--cflags", "--libs"],
        env=_private_pc_environment(prefix), capture_output=True, text=True,
    )
    return check.returncode == 0


def _copy_keg_dylibs(keg: Path, target: Path, names: tuple[str, ...]) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for pattern in names:
        matches = list((keg / 'lib').glob(pattern))
        if not matches:
            raise RuntimeError(f"No {pattern} dylib in binary Homebrew formula {keg}")
        for item in matches:
            if item.is_file():
                destination = target / item.name
                # A previously staged bottle can leave a symlink pointing
                # back into Homebrew's read-only Cellar. copy2() follows the
                # *destination* symlink and attempts to overwrite the keg.
                # Copy to an app-local temporary file, then replace the path
                # atomically without following an existing destination link.
                temporary = target / (item.name + '.staging')
                try:
                    if temporary.exists() or temporary.is_symlink():
                        temporary.unlink()
                    shutil.copy2(item, temporary, follow_symlinks=True)
                    os.replace(temporary, destination)
                finally:
                    if temporary.exists() or temporary.is_symlink():
                        temporary.unlink()


def _normalize_pkgconfig_version(line: str) -> str:
    """Use the numeric upstream version for pkg-config comparisons.

    Some Homebrew formulae (notably rtmpdump) publish Version: v2.4,
    which pkg-config orders below the required numeric version 2.0.
    Strip only a leading v before a digit; preserve all other metadata.
    """
    return re.sub(r"^(Version:\s*)v(?=\d)", r"\1", line)


def _rewrite_pc(metadata: Path, destination: Path, keg: Path, prefix: Path) -> None:
    """Rebase bottle metadata without touching existing source-built .pc files."""
    raw = metadata.read_text(encoding="utf-8")
    lines = raw.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if re.match(r"^prefix\s*=", line):
            lines[index] = f"prefix={prefix}\n"
        elif re.match(r"^exec_prefix\s*=", line):
            lines[index] = "exec_prefix=${prefix}\n"
        elif re.match(r"^libdir\s*=", line):
            lines[index] = "libdir=${prefix}/lib\n"
        elif re.match(r"^includedir\s*=", line):
            lines[index] = "includedir=${prefix}/include\n"
        else:
            lines[index] = line.replace(str(keg.resolve()), str(prefix)).replace(str(keg), str(prefix))
    if not any(re.match(r"^prefix\s*=", line) for line in lines):
        lines.insert(0, f"prefix={prefix}\n")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("".join(_normalize_pkgconfig_version(line) for line in lines), encoding="utf-8")


def _stage_homebrew_pkgconfig_closure(prefix: Path, brew: str, formula: str,
                                      module: str, minimum_version: str) -> None:
    """Install the actual .pc dependency closure from Homebrew binary bottles.

    Keep all existing private source builds unchanged. Only missing pkg-config
    modules are brought in; copy their matching binary dylibs and headers too.
    This does not substitute host pkg-config paths for the private prefix.
    """
    # `brew deps` is recursive by default. `--recursive` belongs to
    # `brew uses`, not `brew deps`, and makes Homebrew exit with an error.
    # Include optional/build/test dependencies in discovery; only modules
    # actually required by the private pkg-config closure are staged.
    try:
        dependency_names = _call([
            brew, 'deps', '--formula', '--include-build', '--include-test',
            '--include-optional', formula,
        ]).splitlines()
    except subprocess.CalledProcessError as error:
        raise RuntimeError(
            f'Homebrew dependency discovery failed for {formula}: '
            f'{(error.stderr or error.stdout or str(error)).strip()}'
        ) from error
    # Homebrew install brings in the formula's transitive runtime requirements.
    providers: dict[str, tuple[str, Path, Path]] = {}
    for dependency in dependency_names:
        dependency = dependency.strip()
        if not dependency:
            continue
        try:
            keg = Path(_call([brew, '--prefix', dependency]))
        except subprocess.CalledProcessError:
            continue
        for directory in ('lib/pkgconfig', 'share/pkgconfig'):
            for pc in (keg / directory).glob('*.pc'):
                providers.setdefault(pc.stem, (dependency, keg, pc))

    for _ in range(len(providers) + 1):
        check = subprocess.run(
            ['pkg-config', '--print-errors', f'{module} >= {minimum_version}',
             '--cflags', '--libs'], env=_private_pc_environment(prefix),
            capture_output=True, text=True,
        )
        if check.returncode == 0:
            return
        missing = sorted(set(re.findall(
            r"Package ['\"]([^'\"]+)['\"], required by|Package ['\"]([^'\"]+)['\"] was not found",
            check.stderr,
        )))
        modules = sorted(set(name for pair in missing for name in pair))
        if not modules:
            raise RuntimeError(f'Cannot resolve {module} pkg-config closure: {check.stderr.strip()}')
        resolved = 0
        for required in modules:
            provider = providers.get(required)
            if provider is None:
                continue
            dependency, keg, metadata = provider
            destination = prefix / 'lib/pkgconfig' / f'{required}.pc'
            if destination.exists():
                continue  # Never overwrite an existing private package.
            _rewrite_pc(metadata, destination, keg, prefix)
            # Stage binaries and public headers for the binary provider. If a
            # library was already built locally, its files always win.
            source_lib = keg / 'lib'
            dest_lib = prefix / 'lib'
            dest_lib.mkdir(parents=True, exist_ok=True)
            for library in source_lib.glob('*.dylib'):
                dest = dest_lib / library.name
                if not dest.exists() and not dest.is_symlink():
                    shutil.copy2(library, dest, follow_symlinks=True)
            source_include = keg / 'include'
            if source_include.is_dir():
                for entry in source_include.iterdir():
                    target = prefix / 'include' / entry.name
                    if entry.is_dir():
                        shutil.copytree(entry, target, dirs_exist_ok=True,
                                        copy_function=_copy_only_missing)
                    elif not target.exists() and not target.is_symlink():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(entry, target)
            print(f'[binary][mac] STAGED pkg-config {required} from brew {dependency}', flush=True)
            resolved += 1
        if resolved == 0:
            raise RuntimeError(
                f'No installed Homebrew dependency provides {modules} for {module}. '
                f'Existing private modules were preserved.\n{check.stderr.strip()}'
            )
    raise RuntimeError(f'pkg-config closure resolution did not converge for {module}')


def _copy_only_missing(src: str, dst: str) -> str:
    if not Path(dst).exists() and not Path(dst).is_symlink():
        shutil.copy2(src, dst)
    return dst


def _stage_public_headers(keg: Path, prefix: Path) -> None:
    """Stage an entire bottle's public include tree, including generated headers.

    Selecting only the top-level API header loses its quoted includes (for
    example libwebsockets.h -> lws_config.h). Keep existing private headers
    when other dependency providers share the same include prefix.
    """
    source = keg / 'include'
    if not source.is_dir():
        raise RuntimeError(f'Prebuilt {keg} has no public include directory')
    target = prefix / 'include'
    target.mkdir(parents=True, exist_ok=True)
    for entry in source.iterdir():
        destination = target / entry.name
        if entry.is_dir():
            if destination.is_file():
                raise RuntimeError(f'Header directory conflicts with file: {destination}')
            shutil.copytree(entry, destination, dirs_exist_ok=True,
                            copy_function=_copy_only_missing)
        elif entry.is_file() and not destination.exists() and not destination.is_symlink():
            shutil.copy2(entry, destination)


def _stage_quoted_header_closure(keg: Path, prefix: Path, headers: tuple[str, ...]) -> None:
    """Preserve transitive quoted includes for bottle public headers.

    Some bottles install generated configuration headers in a subdirectory
    while their public root header includes the header by basename. Resolve
    from the same bottle only and reject ambiguous or missing candidates.
    """
    import collections
    source_root = keg / 'include'
    destination_root = prefix / 'include'
    pending = collections.deque(destination_root / header for header in headers)
    seen: set[Path] = set()
    while pending:
        header = pending.popleft()
        if header in seen or not header.is_file():
            continue
        seen.add(header)
        body = header.read_text(encoding='utf-8', errors='replace')
        for name in re.findall(r'^\s*#\s*include\s*"([^"\n]+)"', body, re.MULTILINE):
            relative = Path(name)
            if relative.is_absolute() or '..' in relative.parts:
                raise RuntimeError(f'Unsafe quoted include {name} in {header}')
            adjacent = header.parent / relative
            if adjacent.is_file():
                if adjacent.is_relative_to(destination_root):
                    pending.append(adjacent)
                continue
            candidates = sorted(path for path in source_root.rglob(relative.name)
                                if path.is_file() and path.parts[-len(relative.parts):] == relative.parts)
            if not candidates:
                # System and third-party includes may be resolved via -I;
                # the compiler's complete syntax probe remains authoritative.
                continue
            if len(candidates) != 1:
                raise RuntimeError(f'Ambiguous quoted include {name} for {header}: {candidates}')
            adjacent.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidates[0], adjacent)
            pending.append(adjacent)


def _public_headers_compile(prefix: Path, module: str,
                            headers: tuple[str, ...],
                            include_dirs: tuple[str, ...] = ()) -> bool:
    """Check transitive header usability, not merely the presence of .pc."""
    if not headers:
        return True
    env = _private_pc_environment(prefix)
    flags = subprocess.run(['pkg-config', '--cflags', module], env=env,
                           capture_output=True, text=True)
    if flags.returncode:
        return False
    import shlex
    for header in headers:
        compiler = shutil.which('clang++' if header.endswith(('.hpp', '.hh', '.hxx')) else 'clang')
        if not compiler:
            raise RuntimeError('Clang is needed for macOS dependency preflight')
        # A public header may include its sibling by basename with angle
        # brackets (raptor2/raptor.h -> <raptor2.h>). Such headers require
        # their own include directory in addition to the prefix root.
        header_directory = (prefix / 'include' / header).parent
        extra_flags = []
        for subdir in include_dirs:
            relative = Path(subdir)
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError(f'Invalid private compiler include directory: {subdir}')
            directory = prefix / 'include' / relative
            if directory.is_dir():
                extra_flags.append('-I' + str(directory))
        command = [compiler, '-fsyntax-only', '-x',
                   'c++' if header.endswith(('.hpp', '.hh', '.hxx')) else 'c',
                   '-I' + str(prefix / 'include'),
                   '-I' + str(header_directory), *extra_flags, *shlex.split(flags.stdout), '-']
        result = subprocess.run(command, input=(
            '#include <stddef.h>\n#include <stdio.h>\n'
            f'#include <{header}>\n'),
                                capture_output=True, text=True, env=env)
        if result.returncode:
            print(f'[binary][mac] INCOMPLETE {module}/{header}: '
                  f'{result.stderr.strip()}', flush=True)
            return False
    return True


def stage_homebrew_formula(prefix: Path, *, formula: str, module: str,
                           minimum_version: str, library_globs: tuple[str, ...],
                           header_directory: str | None = None,
                           header_files: tuple[str, ...] = (),
                           compiler_include_dirs: tuple[str, ...] = ()) -> None:
    """Stage matched Homebrew headers, libraries and a relocatable .pc.

    Does not change an existing healthy private package or silently use host
    pkg-config results. An incomplete private dependency closure is diagnosed.
    """
    prefix = prefix.resolve()
    required_headers = tuple(prefix / 'include' / h for h in header_files)
    # A valid .pc does not prove that every library requested by the
    # application is present (FFTW's threaded library has no separate .pc).
    # Stage the complete prebuilt library family when any declared member
    # is missing, even if an older private build already supplies the module.
    if (_local_package_works(prefix, module, minimum_version)
            and all(path.is_file() for path in required_headers)
            and _public_headers_compile(prefix, module, header_files, compiler_include_dirs)
            and all(any(path.is_file() for path in (prefix / 'lib').glob(pattern))
                    for pattern in library_globs)):
        print(f"[binary][mac] REUSE private {module}", flush=True)
        return
    brew = shutil.which('brew')
    if not brew:
        raise RuntimeError(f"Homebrew required for binary {formula} on macOS")
    try:
        keg = Path(_call([brew, '--prefix', formula]))
    except subprocess.CalledProcessError:
        keg = Path('/nonexistent')
    if not (keg / 'lib/pkgconfig' / f'{module}.pc').is_file():
        print(f"[binary][mac] INSTALL brew {formula}", flush=True)
        subprocess.run([brew, 'install', formula], check=True)
        keg = Path(_call([brew, '--prefix', formula]))
    metadata = keg / 'lib/pkgconfig' / f'{module}.pc'
    if not metadata.is_file():
        raise RuntimeError(f"Homebrew {formula} does not provide {metadata}")
    target_lib = prefix / 'lib'
    _copy_keg_dylibs(keg, target_lib, library_globs)
    # Import generated/configuration headers as well as the visible API
    # header. These are part of the prebuilt bottle, not source builds.
    _stage_public_headers(keg, prefix)
    if header_directory is not None:
        headers = keg / 'include' / header_directory
        if not headers.is_dir():
            raise RuntimeError(f"Homebrew {formula} missing headers: {headers}")
        output = prefix / 'include' / header_directory
        if output.exists() and not output.is_dir():
            raise RuntimeError(f"Cannot stage {formula} over non-directory {output}")
        shutil.copytree(headers, output, dirs_exist_ok=True)
    # Some libraries (libjpeg, for example) publish headers directly under
    # include/ rather than inside a named include subdirectory. Keep the
    # selection declarative in recipe.json, not hardcoded to any formula.
    for header in header_files:
        relative = Path(header)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError(f'Invalid header path in recipe: {header}')
        source = keg / 'include' / relative
        if not source.is_file():
            raise RuntimeError(f'Homebrew {formula} missing required header {source}')
        target = prefix / 'include' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() and not target.is_symlink():
            shutil.copy2(source, target)
    _stage_quoted_header_closure(keg, prefix, header_files)
    if not _public_headers_compile(prefix, module, header_files, compiler_include_dirs):
        # This check is repeated after pkg-config staging below; at this
        # point the prior metadata can still reference the old closure.
        print(f'[binary][mac] Checking {module} after metadata refresh', flush=True)
    destination = prefix / 'lib/pkgconfig' / f'{module}.pc'
    destination.parent.mkdir(parents=True, exist_ok=True)
    raw = metadata.read_text(encoding='utf-8')
    # The .pc file commonly names the physical Cellar version, not the
    # /opt/homebrew/opt/<formula> symlink returned by brew --prefix.
    # Rebase explicit installation variables, retaining Requires(.private),
    # Libs(.private) and all upstream compilation/linker options verbatim.
    lines = raw.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if re.match(r'^prefix\s*=', line):
            lines[index] = f'prefix={prefix}\n'
        elif re.match(r'^exec_prefix\s*=', line):
            lines[index] = 'exec_prefix=${prefix}\n'
        elif re.match(r'^libdir\s*=', line):
            lines[index] = 'libdir=${prefix}/lib\n'
        elif re.match(r'^includedir\s*=', line):
            lines[index] = 'includedir=${prefix}/include\n'
        else:
            # Formula files may use a physical Cellar path in other flags.
            line = line.replace(str(keg.resolve()), str(prefix)).replace(str(keg), str(prefix))
            lines[index] = line
    if not any(re.match(r'^prefix\s*=', line) for line in lines):
        lines.insert(0, f'prefix={prefix}\n')
    destination.write_text(''.join(_normalize_pkgconfig_version(line) for line in lines), encoding='utf-8')
    _stage_homebrew_pkgconfig_closure(prefix, brew, formula, module, minimum_version)
    if not _public_headers_compile(prefix, module, header_files, compiler_include_dirs):
        raise RuntimeError(f'Prebuilt {formula} public header closure is incomplete in {prefix}')
    env = _private_pc_environment(prefix)
    # Waf's autowaf.check_pkg uses the combined version/cflags/libs query.
    # --exists alone is not enough to detect an incomplete binary import.
    check = subprocess.run(
        ['pkg-config', '--print-errors', f'{module} >= {minimum_version}',
         '--cflags', '--libs'], env=env, text=True, capture_output=True,
    )
    if check.returncode:
        raise RuntimeError(
            f"Staged {formula}/{module} into {prefix}, but its PRIVATE pkg-config "
            f"dependency closure is incomplete: {(check.stderr or check.stdout).strip()}. "
            "Install the missing binary dependencies into this same Dependencies prefix."
        )
    version = _call(['pkg-config', '--modversion', module], env=env)
    if not _local_package_works(prefix, module, minimum_version):
        raise RuntimeError(f"Staged {module} {version}, require >= {minimum_version}")
    print(f"[binary][mac] READY {formula} {version} in {prefix}", flush=True)


def stage_macos_sdk_pkgconfig(prefix: Path, *, module: str, version: str,
                               headers: tuple[str, ...], libraries: tuple[str, ...],
                               probe_symbol: str) -> None:
    """Expose a *verified* macOS SDK library to private pkg-config clients.

    SDK libraries are part of the platform toolchain, not downloadable bottles.
    Verify that the SDK compiler can link the declared symbol before writing
    metadata, rather than fabricating a pc entry for an absent library.
    """
    if not re.fullmatch(r'[A-Za-z0-9_.+-]+', module):
        raise ValueError(f'Invalid SDK module: {module}')
    for token in (*headers, *libraries):
        if not re.fullmatch(r'[A-Za-z0-9_./+-]+', token) or '..' in Path(token).parts:
            raise ValueError(f'Invalid SDK header/library: {token}')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', probe_symbol):
        raise ValueError(f'Invalid SDK link symbol: {probe_symbol}')
    compiler = shutil.which('clang')
    if not compiler:
        raise RuntimeError(f'Cannot verify macOS SDK library {module}: clang missing')
    probe = ''.join(f'#include <{header}>\n' for header in headers)
    probe += f'int main(void) {{ (void)&{probe_symbol}; return 0; }}\n'
    command = [compiler, '-x', 'c', '-', '-o', '/dev/null',
               *(f'-l{library}' for library in libraries)]
    completed = subprocess.run(command, input=probe, text=True, capture_output=True)
    if completed.returncode:
        raise RuntimeError(f'macOS SDK dependency {module} cannot link: {completed.stderr.strip()}')
    metadata = prefix / 'lib/pkgconfig' / f'{module}.pc'
    if metadata.exists():
        if not _local_package_works(prefix, module, version):
            raise RuntimeError(f'Existing private {module}.pc is broken: {metadata}')
        print(f'[binary][mac] REUSE SDK {module}', flush=True)
        return
    metadata.parent.mkdir(parents=True, exist_ok=True)
    contents = (f'prefix={prefix}\n'
                f'Name: {module}\nDescription: macOS SDK library (verified by clang)\n'
                f'Version: {version}\nLibs: ' + ' '.join(f'-l{lib}' for lib in libraries) + '\n')
    metadata.write_text(contents, encoding='utf-8')
    if not _local_package_works(prefix, module, version):
        metadata.unlink()
        raise RuntimeError(f'Generated SDK pkg-config metadata failed for {module}')
    print(f'[binary][mac] READY SDK {module} (link verified)', flush=True)


def extract_apt_binary(prefix: Path, *, package: str, cache: Path) -> None:
    """Extract a Debian binary archive under application-owned paths.

    APT resolves/downloads a distribution-matched package; dpkg-deb unpacks
    without altering the host. Consumers must still validate transitive runtime
    dependencies and relocate pkg-config metadata before enabling the package.
    """
    if not re.fullmatch(r'[a-z0-9][a-z0-9+.-]*', package):
        raise ValueError(f'Invalid Debian package: {package}')
    if not shutil.which('apt-get') or not shutil.which('dpkg-deb'):
        raise RuntimeError('apt-get and dpkg-deb are required for Debian binary provisioning')
    cache = cache.resolve()
    prefix = prefix.resolve()
    cache.mkdir(parents=True, exist_ok=True)
    before = set(cache.glob(f'{package}_*.deb'))
    print(f'[binary][linux] DOWNLOAD apt {package} into {cache}', flush=True)
    subprocess.run(['apt-get', 'download', package], cwd=cache, check=True)
    archives = sorted(set(cache.glob(f'{package}_*.deb')) - before) or sorted(cache.glob(f'{package}_*.deb'))
    if len(archives) != 1:
        raise RuntimeError(f'Ambiguous APT archives for {package}: {archives}')
    # Keep the original /usr sublayout under a visible staging directory;
    # never overwrite an already prepared private prefix with unverified data.
    staging = cache / f'{package}-unpacked'
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    subprocess.run(['dpkg-deb', '-x', str(archives[0]), str(staging)], check=True)
    print(f'[binary][linux] EXTRACTED {package}: {staging}; verify before installing into {prefix}', flush=True)


def install_requirements(requirements_file: Path, *, prefix: Path, cache: Path | None = None) -> None:
    """Provision declared binary requirements for one app/platform.

    The app/platform recipe.json declares binary_dependencies. Transitive
    packages are resolved from package-manager metadata, not hardcoded here.
    """
    import json
    data = json.loads(requirements_file.read_text(encoding='utf-8'))
    if not isinstance(data.get('binary_dependencies'), list):
        raise RuntimeError(f'Missing binary_dependencies array in {requirements_file}')
    platform = requirements_file.parent.name
    if platform not in ('mac', 'linux', 'win'):
        raise RuntimeError(f'Unsupported platform in {requirements_file}: {platform}')
    # The closure of a public header may use include roots advertised by a
    # *different* staged dependency (lrdf.h includes <raptor.h>, located in
    # raptor2/). Apply the recipe's complete declared include graph to each
    # compile probe, rather than mistaking an existing header for a failure.
    declared_include_dirs = tuple(dict.fromkeys(
        subdir for dependency in data['binary_dependencies']
        for subdir in dependency.get('compiler_include_dirs', ())
    ))
    for item in data['binary_dependencies']:
        name = item['name']
        provider = item['provider']
        if provider == 'existing':
            print(f'[requirements][{platform}] PRESERVE existing {name}', flush=True)
        elif provider == 'macos-sdk' and platform == 'mac':
            stage_macos_sdk_pkgconfig(prefix, module=item['module'],
                                       version=item['version'],
                                       headers=tuple(item['header_files']),
                                       libraries=tuple(item['link_libraries']),
                                       probe_symbol=item['probe_symbol'])
        elif provider == 'homebrew' and platform == 'mac':
            stage_homebrew_formula(prefix, formula=item['package'],
                                   module=item['module'],
                                   minimum_version=item['minimum_version'],
                                   library_globs=tuple(item['libraries']),
                                   header_directory=item.get('headers'),
                                   header_files=tuple(item.get('header_files', ())),
                                   compiler_include_dirs=declared_include_dirs)
        elif provider == 'apt' and platform == 'linux':
            if cache is None:
                raise RuntimeError(f'APT requirement {name} needs DependencySources cache')
            extract_apt_binary(prefix, package=item['package'], cache=cache)
        else:
            raise RuntimeError(f'Unsupported {platform} package provider {provider!r} for {name}')
