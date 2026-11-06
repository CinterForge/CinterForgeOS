"""Strict relocation of a completed macOS application dependency closure.

All non-Apple runtime libraries are copied into Contents/lib and every Mach-O
load command is rewritten to resolve inside the bundle. Run BEFORE signing.
"""
from __future__ import annotations

from collections import deque
from pathlib import Path
import os
import shutil
import subprocess
import re


_MAGIC = {b'\xfe\xed\xfa\xce', b'\xce\xfa\xed\xfe',
          b'\xfe\xed\xfa\xcf', b'\xcf\xfa\xed\xfe',
          b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca',
          b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca'}


def _macho(path: Path) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    with path.open('rb') as handle:
        return handle.read(4) in _MAGIC


def _command(*args: str) -> str:
    result = subprocess.run(args, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(f"{' '.join(args)} failed:\n{result.stderr or result.stdout}")
    return result.stdout


def _dependencies(binary: Path) -> list[str]:
    """Parse otool -L, including its per-architecture headers for fat binaries.

    On newer Xcode versions, otool may print ``(architecture arm64):``
    after the filename. That is a section header, not a load command.
    """
    dependencies: list[str] = []
    for raw in _command('/usr/bin/otool', '-L', str(binary)).splitlines():
        line = raw.strip()
        if not line or not raw[:1].isspace():
            # The initial filename and any per-architecture filename headers.
            continue
        if re.fullmatch(r'\(architecture [^)]+\):', line):
            continue
        reference = line.split(' (compatibility version ', 1)[0].split(
            ' (current version ', 1)[0]
        if reference:
            dependencies.append(reference)
    return dependencies


def _dylib_versions(binary: Path, name: str) -> tuple[tuple[int, ...], tuple[int, ...]] | None:
    """Read Mach-O dylib current/compatibility versions from its own load ID."""
    for line in _command('/usr/bin/otool', '-L', str(binary)).splitlines()[1:]:
        line = line.strip()
        if Path(line.split(' (', 1)[0]).name != name:
            continue
        match = re.search(r'\(compatibility version ([0-9.]+), current version ([0-9.]+)\)', line)
        if match:
            return (tuple(map(int, match.group(1).split('.'))),
                    tuple(map(int, match.group(2).split('.'))))
    return None


def _accept_bundled_dylib(bundled: Path, source: Path, reference: str,
                          dependent: Path) -> None:
    """Keep upstream's packaged copy when a compatible system copy has the same name.

    The app bundle is the authority for its load closure. Two nonidentical
    builds of the same dylib are expected, but a SONAME collision is only
    acceptable if the bundled dylib meets the dependent's compatibility
    version requirement. Fail explicitly for unknown/non-Mach-O collisions.
    """
    name = bundled.name
    # Upstream's macOS bundle often retains versioned dylib symlinks. A
    # symlink is not itself a Mach-O file, but its in-bundle target is.
    # Validate the actual Mach-O payload rather than rejecting a valid alias.
    if not (_macho(bundled.resolve()) and _macho(source.resolve())):
        raise RuntimeError(f'Conflicting non-Mach-O dependency {name}: {source} vs {bundled}')
    bundled_versions = _dylib_versions(bundled, name)
    source_versions = _dylib_versions(source, name)
    if bundled_versions is None or source_versions is None:
        raise RuntimeError(f'Unverifiable dependency collision {name}: {source} vs {bundled}')
    requested = None
    for line in _command('/usr/bin/otool', '-L', str(dependent)).splitlines()[1:]:
        line = line.strip()
        if line.split(' (', 1)[0] == reference:
            match = re.search(r'\(compatibility version ([0-9.]+), current version [0-9.]+\)', line)
            if match:
                requested = tuple(map(int, match.group(1).split('.')))
            break
    # Mach-O's compatibility and current fields are different version axes.
    # Cairo, for example, advertises compatibility 2.0.0 while reporting
    # current 1.18.6. Comparing those numbers directly incorrectly rejects
    # the *same* upstream library already used by the dependent. Retain an
    # existing bundled copy only when it is no older than the source on
    # either version axis and meets the reference's compatibility contract.
    bundled_compatibility, bundled_current = bundled_versions
    source_compatibility, source_current = source_versions
    if (requested is None
            or bundled_compatibility < requested
            or bundled_compatibility < source_compatibility
            or bundled_current < source_current):
        raise RuntimeError(
            f'Incompatible bundled dependency {name}: {bundled} '
            f'(compatibility={bundled_compatibility}, current={bundled_current}) '
            f'cannot replace {source} '
            f'(compatibility={source_compatibility}, current={source_current}) '
            f'for {dependent}: {reference} (compatibility={requested})'
        )
    print(f'[mac][dependencies] Reuse upstream bundled {name} '
          f'(bundled current={bundled_versions[1]}, required={requested}; '
          f'external={source})', flush=True)


def _install_id(binary: Path) -> str | None:
    result = subprocess.run(['/usr/bin/otool', '-D', str(binary)],
                            text=True, capture_output=True)
    if result.returncode:
        return None
    entries = result.stdout.splitlines()[1:]
    return entries[0].strip() if entries else None


def _system(path: str) -> bool:
    return (path.startswith('/System/Library/') or
            path.startswith('/usr/lib/') or
            path.startswith('/System/iOSSupport/'))


def _search_dirs(bundle: Path, prefix: Path, extra_dirs: tuple[Path, ...]) -> list[Path]:
    """Generic relocation roots; the application supplies its private/keg paths."""
    roots = [bundle / 'Contents/lib', prefix / 'lib', prefix / 'lib64',
             *extra_dirs, Path('/opt/homebrew/lib'), Path('/usr/local/lib')]
    return [path for path in dict.fromkeys(roots) if path.is_dir()]


def close_application_dependencies(bundle: Path, prefix: Path, *,
                                   extra_search_dirs: tuple[Path, ...] = (),
                                   framework_search_dirs: tuple[Path, ...] = ()) -> None:
    if not bundle.is_dir() or not (bundle / 'Contents/MacOS').is_dir():
        raise RuntimeError(f'Invalid macOS bundle: {bundle}')
    library_dir = bundle / 'Contents/lib'
    library_dir.mkdir(parents=True, exist_ok=True)
    paths = sorted(p for p in bundle.rglob('*') if _macho(p) and '.dSYM' not in p.parts)
    if not paths:
        raise RuntimeError(f'No Mach-O binaries in {bundle}')
    queue = deque(paths)
    processed: set[Path] = set()
    copied = 0
    rewritten = 0
    roots = _search_dirs(bundle, prefix, extra_search_dirs)
    while queue:
        binary = queue.popleft()
        if binary in processed:
            continue
        processed.add(binary)
        install_id = _install_id(binary)
        for reference in _dependencies(binary):
            if reference == install_id:
                continue
            if _system(reference):
                continue
            # Frameworks are directory bundles, not dylibs in Contents/lib.
            # Dependencies copied into Contents/lib later in this closure may
            # reference Qt frameworks that the app packager already installed.
            # Resolve these against the app's private Frameworks directory
            # before the generic basename search (which cannot locate them).
            if '.framework/' in reference:
                components = reference.split('/')
                framework = next((part for part in components
                                  if part.endswith('.framework')), None)
                if framework is None:
                    raise RuntimeError(f'Malformed framework dependency: {binary}: {reference}')
                relative = reference.split(framework + '/', 1)[1]
                if not relative or '..' in Path(relative).parts:
                    raise RuntimeError(f'Invalid framework dependency: {binary}: {reference}')
                framework_binary = bundle / 'Contents/Frameworks' / framework / relative
                if not framework_binary.is_file():
                    # A library copied by this closure may introduce a new Qt
                    # framework dependency that the packager's initial scan
                    # could not see. Resolve it from the app's pinned Setup
                    # prefix, never from an unrelated global Qt installation.
                    candidates = [root / framework for root in framework_search_dirs
                                  if (root / framework).is_dir()]
                    if len(candidates) != 1:
                        raise RuntimeError(
                            f'Unresolved private macOS framework: {binary}\n  {reference}\n'
                            f'Expected exactly one Setup-owned framework from '
                            f'{framework_search_dirs}; found {candidates}'
                        )
                    shutil.copytree(candidates[0], bundle / 'Contents/Frameworks' / framework,
                                    symlinks=True)
                    print(f'[mac][dependencies] Bundled transitive framework {framework}',
                          flush=True)
                if not framework_binary.is_file() or not _macho(framework_binary.resolve()):
                    raise RuntimeError(
                        f'Invalid private macOS framework binary: {binary}\n  {reference}\n'
                        f'Expected Setup-owned Mach-O in {framework_binary}'
                    )
                if not framework_binary.resolve().is_relative_to(bundle.resolve()):
                    raise RuntimeError(f'Framework escapes app bundle: {framework_binary}')
                target = '@loader_path/' + os.path.relpath(framework_binary, binary.parent)
                if reference != target:
                    _command('/usr/bin/install_name_tool', '-change', reference, target, str(binary))
                    rewritten += 1
                queue.append(framework_binary.resolve())
                continue
            if reference.startswith('@loader_path/'):
                original = binary.parent / reference[len('@loader_path/'):]
            elif reference.startswith('@executable_path/'):
                original = bundle / 'Contents/MacOS' / reference[len('@executable_path/'):]
            elif reference.startswith('/'):
                original = Path(reference)
            else:
                original = None
            candidate = original if original is not None and original.is_file() else None
            if candidate is None:
                name = Path(reference).name
                # App-owned Setup dependencies take precedence over unrelated
                # Homebrew installations. Mixing ABI copies based on whatever
                # is installed globally makes the staged app non-reproducible.
                private_roots = [root for root in roots
                                 if root not in (Path('/opt/homebrew/lib'),
                                                 Path('/usr/local/lib'))]
                matches = [root / name for root in private_roots
                           if (root / name).is_file()]
                if not matches:
                    matches = [root / name for root in roots
                               if root not in private_roots and (root / name).is_file()]
                if not matches:
                    # Some upstream projects install libraries below lib/<project>.
                    # Search only this app's already-built prefix.
                    for lib_dir in (prefix / 'lib', prefix / 'lib64'):
                        if lib_dir.is_dir():
                            matches.extend(path for path in lib_dir.glob('*/' + name)
                                           if path.is_file())
                if not matches:
                    # Resolve targets from this app's build tree only when
                    # no installed artifact exists; require an unambiguous Mach-O.
                    build_tree = prefix.parent / 'Build'
                    if build_tree.is_dir():
                        matches = [path for path in build_tree.rglob(name)
                                   if path.is_file() and _macho(path.resolve())
                                   and '.app' not in str(path)]
                if not matches:
                    raise RuntimeError(f'Unresolved macOS dependency: {binary}\n  {reference}\n'
                                       f'Checked developer dependency prefix {prefix}, '
                                       'bundle Contents/lib, and system Homebrew prefixes')
                # Never accidentally use a different ABI when two installed
                # libraries share a basename. Resolve symlink aliases, but
                # fail on distinct copies rather than guessing.
                unique = {match.resolve() for match in matches}
                if len(unique) > 1:
                    # The first existing in-bundle copy is authoritative;
                    # external candidates are handled by collision validation.
                    if matches[0].resolve().is_relative_to(bundle.resolve()):
                        candidate = matches[0]
                    else:
                        raise RuntimeError(f'Ambiguous macOS dependency {reference} '
                                           f'for {binary}: {sorted(map(str, unique))}')
                else:
                    candidate = matches[0]
            if _system(str(candidate)):
                # A bare install name referencing an Apple dylib is still invalid;
                # the actual absolute SDK/runtime install name is required.
                target = str(candidate)
            elif candidate.resolve().is_relative_to(bundle.resolve()):
                target = '@loader_path/' + os.path.relpath(candidate, binary.parent)
                if _macho(candidate):
                    queue.append(candidate)
            else:
                destination = library_dir / candidate.name
                # A dangling symlink left by upstream packaging is not a
                # usable library. Materialize the existing resolved source
                # into the bundle, never follow the dangling link on copy.
                if destination.is_symlink() and not destination.exists():
                    destination.unlink()
                if destination.exists():
                    if destination.is_symlink() and not destination.resolve().is_relative_to(bundle.resolve()):
                        raise RuntimeError(f'Packaged dependency escapes app bundle: {destination} -> {destination.resolve()}')
                    if not destination.samefile(candidate):
                        # Same basename from two different dependency sources is
                        # ambiguous and must not silently change the ABI.
                        if destination.stat().st_size != candidate.stat().st_size or \
                                destination.read_bytes() != candidate.read_bytes():
                            _accept_bundled_dylib(destination, candidate, reference, binary)
                else:
                    shutil.copy2(candidate.resolve(), destination)
                    copied += 1
                if not _macho(destination.resolve()):
                    raise RuntimeError(f'Not a Mach-O runtime dependency: {destination}')
                if not destination.resolve().is_relative_to(bundle.resolve()):
                    raise RuntimeError(f'Packaged dependency escapes app bundle: {destination} -> {destination.resolve()}')
                queue.append(destination.resolve() if destination.is_symlink() else destination)
                target = '@loader_path/' + os.path.relpath(destination, binary.parent)
            if reference != target:
                _command('/usr/bin/install_name_tool', '-change', reference, target, str(binary))
                rewritten += 1
    # Check all load commands again, including transitive dependencies. No
    # bare, @rpath, or developer-machine install names may remain.
    for binary in sorted(processed):
        install_id = _install_id(binary)
        for reference in _dependencies(binary):
            if reference == install_id:
                continue
            if _system(reference):
                continue
            if not reference.startswith('@loader_path/'):
                raise RuntimeError(f'Non-relocatable dependency: {binary}: {reference}')
            resolved = (binary.parent / reference[len('@loader_path/'):]).resolve()
            if not resolved.is_file() or not resolved.is_relative_to(bundle.resolve()):
                raise RuntimeError(f'Unresolved packaged dependency: {binary}: {reference}')
    print(f'[mac][dependencies] Complete: {len(processed)} Mach-O files, '
          f'{copied} libraries copied, {rewritten} load commands relocated', flush=True)


def _packaged_macho_files(bundle: Path) -> list[Path]:
    """Return actual Mach-O files, not symlink aliases or debug symbols."""
    contents = bundle / 'Contents'
    return sorted(path for path in contents.rglob('*')
                  if '.dSYM' not in path.parts and _macho(path))


def _inside_bundle(path: Path, bundle: Path) -> bool:
    return path.is_file() and path.resolve().is_relative_to(bundle.resolve())


def _packaged_candidates(bundle: Path, name: str) -> list[Path]:
    """Find runtime libraries; prefer the shared lib directory over helper copies."""
    contents = bundle / 'Contents'
    lib = contents / 'lib'
    candidates = [lib / name]
    if lib.is_dir():
        candidates.extend(lib.rglob(name))
    frameworks = contents / 'Frameworks'
    if frameworks.is_dir():
        candidates.extend(frameworks.rglob(name))
    return list(dict.fromkeys(path.resolve() for path in candidates
                              if _inside_bundle(path, bundle) and _macho(path.resolve())))


def _copy_missing_absolute_dependency(bundle: Path, reference: str) -> Path | None:
    """Complete an older Built archive using the exact library named by its loader.

    Only an existing absolute Mach-O may be imported. Never guess a library
    version from another keg. Subsequent passes inspect its dependencies too.
    The resulting staged application contains its own copy.
    """
    if not reference.startswith('/') or _system(reference):
        return None
    source = Path(reference)
    if not source.is_file() or not _macho(source.resolve()):
        return None
    destination = bundle / 'Contents/lib' / source.name
    if destination.is_symlink() and not destination.exists():
        destination.unlink()
    if destination.exists():
        if not _inside_bundle(destination, bundle) or not _macho(destination.resolve()):
            raise RuntimeError(f'Invalid bundled dependency: {destination}')
        # Keep the packaged copy; the app bundle is the runtime authority.
    else:
        shutil.copy2(source.resolve(), destination)
        print(f'[mac][dependencies] Bundled missing {source.name} from {source}',
              flush=True)
    return destination.resolve()


def _build_dependency_roots(bundle: Path) -> list[Path]:
    """Locate the matching version's private build inputs, never global kegs.

    Stage receives a ZIP from Built/<app>/mac/<version>. Its private
    dependencies remain in that version's Dependencies directory even when
    the ZIP omitted a required dylib. No source repository is scanned.
    """
    builds = next((parent for parent in bundle.resolve().parents
                   if parent.name == 'Builds'), None)
    if builds is None:
        return []
    staged = builds / 'CreativeSuite' / 'Staged' / 'mac'
    try:
        relative = bundle.resolve().relative_to(staged)
    except ValueError:
        return []
    if len(relative.parts) < 2:
        return []
    app, version = relative.parts[:2]
    # Restage temporary directories are named .cinter-restage-<id>; the
    # installed version is the sibling numeric directory (usually 2026).
    if version.startswith('.cinter-restage-'):
        versions = sorted((builds / 'CreativeSuite' / 'Built' / app / 'mac').glob('[0-9]*'))
        if len(versions) != 1:
            raise RuntimeError(f'Cannot identify unique Built version for {app}: {versions}')
        version = versions[0].name
    root = builds / 'CreativeSuite' / 'Built' / app / 'mac' / version
    return [path for path in (root / 'Dependencies', root / 'Output') if path.is_dir()]


def _copy_missing_private_dependency(bundle: Path, reference: str) -> Path | None:
    """Recover an omitted @rpath library from this app's pinned build only."""
    if not reference.startswith('@rpath/'):
        return None
    name = Path(reference).name
    matches: list[Path] = []
    for root in _build_dependency_roots(bundle):
        for directory, folders, files in os.walk(root):
            folders[:] = [item for item in folders if item not in
                          {'.git', 'CMakeFiles', 'include', 'share', 'Sources', 'src', 'build'}]
            if name in files:
                path = Path(directory) / name
                if path.is_file() and _macho(path.resolve()):
                    matches.append(path.resolve())
    # A single real library may have several aliases inside private sysroots.
    unique = list(dict.fromkeys(matches))
    if len(unique) > 1:
        import hashlib
        hashes = {hashlib.sha256(path.read_bytes()).hexdigest() for path in unique}
        if len(hashes) != 1:
            raise RuntimeError(f'Ambiguous private build dependency {reference}: {unique}')
    if not unique:
        return None
    destination = bundle / 'Contents' / 'lib' / name
    if destination.is_symlink() and not destination.exists():
        destination.unlink()
    if not destination.exists():
        shutil.copy2(unique[0], destination)
        print(f'[mac][dependencies] Bundled private {name} from {unique[0]}', flush=True)
    return destination.resolve()


def _resolve_packaged_reference(bundle: Path, binary: Path, reference: str) -> Path:
    """Resolve one dependency to a verified Mach-O inside the app bundle."""
    contents = bundle / 'Contents'
    if reference.startswith('@loader_path/'):
        direct = binary.parent / reference[len('@loader_path/'):]
        if _inside_bundle(direct, bundle) and _macho(direct.resolve()):
            return direct.resolve()
    if reference.startswith('@executable_path/'):
        direct = contents / 'MacOS' / reference[len('@executable_path/'):]
        if _inside_bundle(direct, bundle) and _macho(direct.resolve()):
            return direct.resolve()
    candidates = _packaged_candidates(bundle, Path(reference).name)
    if not candidates:
        imported = (_copy_missing_absolute_dependency(bundle, reference)
                    or _copy_missing_private_dependency(bundle, reference))
        if imported is not None:
            return imported
        raise RuntimeError(f'Unresolved macOS dependency: {binary}: {reference}; '
                           'no packaged library and no exact available source')
    # A shared copy is canonical; private copies are valid for their own tools.
    canonical = contents / 'lib' / Path(reference).name
    if _inside_bundle(canonical, bundle) and _macho(canonical.resolve()):
        return canonical.resolve()
    if len(candidates) == 1:
        return candidates[0]
    raise RuntimeError(f'Ambiguous macOS dependency: {binary}: {reference}; '
                       f'candidates: {candidates}')


def _relocate_binary(bundle: Path, binary: Path) -> tuple[int, list[Path]]:
    """Rewrite load commands and return dependencies to inspect next."""
    changed = 0
    discovered: list[Path] = []
    own_id = _install_id(binary)
    for reference in _dependencies(binary):
        if reference == own_id or _system(reference):
            continue
        destination = _resolve_packaged_reference(bundle, binary, reference)
        discovered.append(destination)
        target = '@loader_path/' + os.path.relpath(destination, binary.parent)
        if reference != target:
            _command('/usr/bin/install_name_tool', '-change', reference, target,
                     str(binary))
            changed += 1
    return changed, discovered


def _validate_packaged_bundle(bundle: Path) -> None:
    """Check the entire final dependency graph, not only rewritten references."""
    for binary in _packaged_macho_files(bundle):
        own_id = _install_id(binary)
        for reference in _dependencies(binary):
            if reference == own_id or _system(reference):
                continue
            if not reference.startswith('@loader_path/'):
                raise RuntimeError(f'Non-portable dependency: {binary}: {reference}')
            target = binary.parent / reference[len('@loader_path/'):]
            if not _inside_bundle(target, bundle) or not _macho(target.resolve()):
                raise RuntimeError(f'Broken bundled dependency: {binary}: {reference}')


def relocate_packaged_bundle(bundle: Path) -> None:
    """Complete, relocate, and validate the macOS app before signing.

    The queue grows when a missing library is copied into Contents/lib.
    This handles transitive dependencies rather than fixing one crash at a time.
    """
    if not (bundle / 'Contents/MacOS').is_dir():
        raise RuntimeError(f'Invalid macOS application bundle: {bundle}')
    binaries = _packaged_macho_files(bundle)
    if not binaries:
        raise RuntimeError(f'No Mach-O binaries in {bundle}')
    pending = deque(binaries)
    inspected: set[Path] = set()
    rewritten = 0
    while pending:
        binary = pending.popleft()
        if binary in inspected:
            continue
        inspected.add(binary)
        count, dependencies = _relocate_binary(bundle, binary)
        rewritten += count
        pending.extend(path for path in dependencies if path not in inspected)
    _validate_packaged_bundle(bundle)
    print(f'[mac][dependencies] Validated {len(inspected)} Mach-O files; '
          f'relocated {rewritten} references before signing', flush=True)
