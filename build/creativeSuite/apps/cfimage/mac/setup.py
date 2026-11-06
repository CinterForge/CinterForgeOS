#!/usr/bin/env python3
"""Prepare upstream GIMP macOS dependencies before attempting Meson configure."""
from __future__ import annotations

import hashlib
import re
import os
import shutil
import tarfile
import urllib.request
import subprocess
import sys
from pathlib import Path


PIPELINE = Path(__file__).resolve().parents[3] / "shared/mac/source_setup.py"
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parents[6]))
from cf.io.CinterIO import cinterforgeSiblingRepoPath, cinterforgeBuildsRootPath


def run(*args: str, env: dict[str, str] | None = None) -> None:
    print("[cfimage][mac] " + " ".join(args), flush=True)
    subprocess.run(args, check=True, env=env)


def pkg_environment(prefix: Path | None = None) -> dict[str, str]:
    environment = os.environ.copy()
    if prefix is not None:
        package_dirs = (prefix / "lib/pkgconfig", prefix / "share/pkgconfig")
        environment["PKG_CONFIG_PATH"] = os.pathsep.join(
            [*(str(item) for item in package_dirs),
             environment.get("PKG_CONFIG_PATH", "")]
        )
    return environment


def gexiv2_prefix() -> Path:
    built = Path(os.environ["CINTER_BUILT_ROOT"]).expanduser().resolve()
    return built / "Dependencies" / "gexiv2-0.14.6" / "Install"



def add_pinned_gexiv2_typelib(environment: dict[str, str], built: Path) -> None:
    """Expose GExiv2's compiled typelib to GIMP's in-build Python plug-ins.

    GIR search paths only affect binding generation; gi.repository imports
    resolve compiled .typelib files through GI_TYPELIB_PATH at runtime.
    """
    install = built / "Dependencies/gexiv2-0.14.6/Install"
    matches = sorted(install.rglob("GExiv2-0.10.typelib"))
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one pinned GExiv2-0.10.typelib under {install}; "
            f"found {len(matches)}. Run GIMP Setup."
        )
    # GIRepository opens the library recorded *inside* the typelib, not the
    # directory that contains the typelib. On macOS, DYLD_* is not a reliable
    # lookup mechanism across the GIMP -> plugin interpreter process chain.
    # Recompile the upstream GIR with an absolute build-time load name, as
    # GIMP's own macOS bundler does for its relocatable release typelibs.
    library = install / "lib/libgexiv2.2.dylib"
    if not library.is_file():
        raise RuntimeError(f"Pinned GExiv2 shared library missing: {library}; run GIMP Setup")
    gir = install / "share/gir-1.0/GExiv2-0.10.gir"
    if not gir.is_file():
        raise RuntimeError(f"Pinned GExiv2 GIR missing: {gir}; run GIMP Setup")
    import xml.etree.ElementTree as ET
    ET.register_namespace("", "http://www.gtk.org/introspection/core/1.0")
    ET.register_namespace("c", "http://www.gtk.org/introspection/c/1.0")
    ET.register_namespace("glib", "http://www.gtk.org/introspection/glib/1.0")
    import tempfile
    compiler = shutil.which("g-ir-compiler")
    if compiler is None:
        raise RuntimeError("g-ir-compiler missing; run GIMP Setup")
    tree = ET.parse(gir)
    namespace = next((node for node in tree.getroot()
                      if node.tag.endswith('}namespace') or node.tag == 'namespace'), None)
    if namespace is None:
        raise RuntimeError(f"GExiv2 GIR has no namespace: {gir}")
    declared = namespace.get('shared-library', '')
    libraries = [entry.strip() for entry in declared.split(',') if entry.strip()]
    if library.name not in libraries:
        raise RuntimeError(f"Unexpected GExiv2 GIR library declaration {declared!r}: {gir}")
    namespace.set('shared-library', ','.join(
        str(library) if entry == library.name else entry for entry in libraries))
    generated = built / 'Dependencies/gexiv2-0.14.6/BuildTypelibs'
    generated.mkdir(parents=True, exist_ok=True)
    target = generated / 'GExiv2-0.10.typelib'
    with tempfile.TemporaryDirectory(dir=generated) as scratch:
        adjusted_gir = Path(scratch) / gir.name
        tree.write(adjusted_gir, encoding='utf-8', xml_declaration=True)
        compiled = Path(scratch) / target.name
        run(compiler, str(adjusted_gir), '--output', str(compiled),
            '--includedir', str(gir.parent))
        compiled.replace(target)
    environment['GI_TYPELIB_PATH'] = os.pathsep.join(filter(None, (
        str(generated), str(matches[0].parent), environment.get('GI_TYPELIB_PATH', '')
    )))
    # Verify the actual Python GI import BEFORE another large Ninja run.
    # The Cinter pyenv Python does not contain Homebrew's PyGObject module.
    # Use the ABI-matched Homebrew interpreter already prepared during Setup,
    # without changing the caller's Python or dropping the typelib environment.
    binding_environment = gobject_python_environment()
    interpreter = shutil.which('python3', path=binding_environment['PATH'])
    if interpreter is None:
        raise RuntimeError('GIMP Homebrew Python 3 missing for GI import')
    check = subprocess.run([interpreter, '-c',
        "import gi; gi.require_version('GExiv2', '0.10'); "
        "from gi.repository import GExiv2; print(GExiv2.__file__ if hasattr(GExiv2, '__file__') else 'GExiv2 loaded')"],
        env={**environment, 'PATH': binding_environment['PATH']}, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check.returncode:
        raise RuntimeError('GIMP pinned GExiv2 failed the actual Python GI import '
                           f'(interpreter={interpreter}):\n{check.stdout}{check.stderr}')
    print(f"[Creative Suite mac] Validated private GExiv2 typelib: {target}", flush=True)

def gobject_python_environment() -> dict[str, str]:
    """Use the Homebrew Python ABI that Homebrew PyGObject was built for.

    The Cinter pyenv interpreter is intentionally not changed. GExiv2's Meson
    test invokes ``python3 -c 'import gi'``, so its configure environment must
    select a Python with the matching system-packaged bindings.
    """
    if not shutil.which("brew"):
        raise RuntimeError("Homebrew is required to provision Python gi bindings")
    run("brew", "install", "pygobject3")
    python_prefix = Path(subprocess.check_output(
        ["brew", "--prefix", "python@3.15"], text=True).strip())
    # Homebrew versioned Python formulae guarantee bin/python3.15, not
    # libexec/bin/python3.  Use the versioned executable, then create a local
    # python3 entry point for GExiv2's Meson find_program('python3', ...).
    python3 = python_prefix / "bin" / "python3.15"
    if not python3.is_file():
        raise RuntimeError(f"Homebrew Python executable not found: {python3}")
    built = Path(os.environ["CINTER_BUILT_ROOT"]).expanduser().resolve()
    python_bin = built / "Dependencies" / "gexiv2-0.14.6" / "Tools" / "bin"
    python_bin.mkdir(parents=True, exist_ok=True)
    python_link = python_bin / "python3"
    if python_link.is_symlink():
        if python_link.resolve() != python3.resolve():
            python_link.unlink()
    elif python_link.exists():
        raise RuntimeError(f"Refusing to replace existing non-symlink: {python_link}")
    if not python_link.exists():
        python_link.symlink_to(python3)
    environment = os.environ.copy()
    environment["PATH"] = str(python_bin) + os.pathsep + environment.get("PATH", "")
    print(f"[cfimage][mac] Validate Homebrew Python GObject bindings: {python_link}", flush=True)
    run(str(python_link), "-c", "import gi; print('gi:', gi.__file__)", env=environment)
    return environment


def prepare_gexiv2() -> Path:
    """Build the compatible upstream API; Homebrew's 0.16 is not GIMP-compatible."""
    built = Path(os.environ["CINTER_BUILT_ROOT"]).expanduser().resolve()
    dependency = built / "Dependencies" / "gexiv2-0.14.6"
    source_dir = dependency / "Source" / "gexiv2-0.14.6"
    archive = dependency / "DependencySources" / "gexiv2-0.14.6.tar.xz"
    install = gexiv2_prefix()
    if has_dependency("gexiv2", "0.14.0", pkg_environment(install)):
        version = subprocess.check_output(
            ["pkg-config", "--modversion", "gexiv2"],
            text=True, env=pkg_environment(install)
        ).strip()
        if version.startswith("0.14.") and (install / "share/gir-1.0/GExiv2-0.10.gir").is_file():
            print(f"[cfimage][mac] Reusing pinned gexiv2 {version}: {install}", flush=True)
            return install
        print("[cfimage][mac] Rebuilding pinned gexiv2: introspection GIR not installed", flush=True)
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.is_file():
        url = "https://download.gnome.org/sources/gexiv2/0.14/gexiv2-0.14.6.tar.xz"
        print(f"[cfimage][mac] Download {url} -> {archive}", flush=True)
        urllib.request.urlretrieve(url, archive)
    expected_md5 = "4139dfeca8e30288969233568c72e06e"
    checksum = hashlib.md5(archive.read_bytes()).hexdigest()
    if checksum != expected_md5:
        raise RuntimeError(f"gexiv2 source checksum mismatch: {checksum}; expected {expected_md5}")
    if not source_dir.is_dir():
        source_dir.parent.mkdir(parents=True, exist_ok=True)
        run("tar", "-xf", str(archive), "-C", str(source_dir.parent))
    if not (source_dir / "meson.build").is_file():
        raise RuntimeError(f"Invalid upstream gexiv2 source: {source_dir}")
    work = dependency / "Build"
    command = ["meson", "setup", str(work), str(source_dir),
               "--prefix", str(install), "--buildtype=release"]
    if (work / "build.ninja").is_file():
        command.append("--reconfigure")
    meson_env = gobject_python_environment()
    run(*command, env=meson_env)
    run("meson", "compile", "-C", str(work), env=meson_env)
    run("meson", "install", "-C", str(work), env=meson_env)
    if not has_dependency("gexiv2", "0.14.0", pkg_environment(install)):
        raise RuntimeError(f"Pinned gexiv2 not visible in {install}")
    gir = install / "share/gir-1.0/GExiv2-0.10.gir"
    if not gir.is_file():
        raise RuntimeError(f"Pinned gexiv2 introspection GIR missing after build: {gir}")
    return install


def has_dependency(module: str, minimum: str, environment: dict[str, str] | None = None) -> bool:
    result = subprocess.run(
        ["pkg-config", f"--atleast-version={minimum}", module],
        check=False,
        env=environment,
    )
    return result.returncode == 0


def prepare_gegl_vala() -> None:
    """Supply a real GEGL VAPI from the installed GEGL GIR for GIMP's vapigen.

    A Homebrew GEGL bottle can provide Gegl-0.4.gir and headers without
    installing its optional gegl-0.4.vapi.  GIMP's generated Gimp-3.0.vapi
    references the Gegl namespace; pkg-config alone does not provide it.
    Keep generated bindings private to this application's external Built tree.
    """
    if not shutil.which('vapigen'):
        run('brew', 'install', 'vala')
    prefix = Path(subprocess.check_output(
        ['brew', '--prefix', 'gegl'], text=True).strip())
    installed = prefix / 'share/vala/vapi/gegl-0.4.vapi'
    built = Path(os.environ['CINTER_BUILT_ROOT']).expanduser().resolve()
    vapi_dir = built / 'Dependencies' / 'gegl-vala' / 'share/vala/vapi'
    vapi_dir.mkdir(parents=True, exist_ok=True)
    target = vapi_dir / 'gegl-0.4.vapi'
    if installed.is_file():
        # Leave the Homebrew dependency alone; the build can use its VAPI.
        shutil.copy2(installed, target)
    else:
        gir = prefix / 'share/gir-1.0/Gegl-0.4.gir'
        if not gir.is_file():
            raise RuntimeError(f'GEGL Vala binding missing and no GIR to generate it: {gir}')
        run('vapigen', '--library=gegl-0.4', '--directory=' + str(vapi_dir),
            '--pkg=babl-0.1', '--pkg=gio-2.0', '--pkg=gdk-pixbuf-2.0',
            '--pkg=cairo-1.0', str(gir))
    if not target.is_file() or 'namespace Gegl' not in target.read_text():
        raise RuntimeError(f'GEGL Vala binding is incomplete: {target}')
    # GEGL's GObject GIR cannot represent its C varargs functions, including
    # gegl_render_op(), which GIMP's upstream goat Vala extension uses.
    # Match the actual GEGL Vala declarations, scoped to this app's private
    # generated binding (never mutate Homebrew or GIMP upstream sources).
    binding = target.read_text()
    init_pattern = r'(?m)^(\s*public\s+(?:static\s+)?void\s+init\s*\()([^;]*)(\);)'
    matches = list(re.finditer(init_pattern, binding))
    if len(matches) != 1:
        raise RuntimeError(f'Expected one Gegl.init Vala declaration, found {len(matches)}: {target}')
    binding = re.sub(init_pattern,
                     lambda match: match.group(1) + 'ref unowned string[]? argv' + match.group(3),
                     binding, count=1)
    if not re.search(r'\bvoid\s+render_op\s*\(', binding):
        anchor = 'namespace Gegl {'
        if binding.count(anchor) != 1:
            raise RuntimeError(f'Unexpected Gegl namespace in generated VAPI: {target}')
        declaration = ('\n\t[CCode (cname = "gegl_render_op", cheader_filename = "gegl.h")]'
                       '\n\tpublic void render_op (Buffer source_buffer, Buffer target_buffer, '
                       'string operation_name, ...);\n')
        binding = binding.replace(anchor, anchor + declaration, 1)
    target.write_text(binding)
    if 'ref unowned string[]? argv' not in binding or not re.search(r'\bvoid\s+render_op\s*\(', binding):
        raise RuntimeError(f'GEGL Vala declarations incomplete: {target}')
    print(f'[cfimage][mac] READY GEGL Vala init/render_op bindings: {target}', flush=True)


def prepare_upstream_submodules() -> None:
    """Populate GIMP's exact gitlinked data revision; do not substitute system data."""
    # CINTER_SOURCE_ROOT is optional. Path("") resolves to the process CWD
    # (normally Cinter), which is NOT the upstream GIMP checkout.
    specified = os.environ.get("CINTER_SOURCE_ROOT")
    if specified:
        source = Path(specified).expanduser().resolve()
    else:
        source = cinterforgeSiblingRepoPath("CinterForgeImage")
    source = source.resolve()
    if not (source / "meson.build").is_file():
        raise RuntimeError(f"GIMP upstream source is missing: {source}")
    if not (source / ".git").exists():
        raise RuntimeError(f"GIMP upstream source is not a Git checkout: {source}; "
                           "the version-matched gimp-data submodule cannot be verified")
    # A populated submodule must still match the superproject's pinned commit.
    # git submodule update is idempotent and repairs both missing and stale data.
    print(f"[cfimage][mac] Initialize pinned upstream submodules: {source}", flush=True)
    subprocess.run(["git", "-C", str(source), "submodule", "update", "--init"], check=True)
    data = source / "gimp-data" / "meson.build"
    if not data.is_file():
        raise RuntimeError(f"Required upstream gimp-data submodule absent after Git update: {data}")
    print(f"[cfimage][mac] READY pinned gimp-data: {data.parent}", flush=True)


def _exports_symbol(library: Path, name: str) -> bool:
    """Inspect the native Mach-O export table, not its ABI version alone."""
    if not library.is_file():
        return False
    result = subprocess.run(['/usr/bin/nm', '-gU', str(library)],
                            capture_output=True, text=True, check=True)
    return any(line.split()[-1] == '_' + name for line in result.stdout.splitlines()
               if line.split())


def _reset_meson_build_directory(build: Path) -> None:
    """Start a native dependency rebuild from its pinned source, not stale Meson state.

    A directory containing build.ninja/coredata can still reference a deleted or
    moved source tree. Meson --reconfigure then fails before compiling anything.
    Only the disposable Build directory is removed; sources and Install survive.
    """
    if build.is_symlink() or (build.exists() and not build.is_dir()):
        raise RuntimeError(f'Unexpected Meson build path: {build}')
    if build.exists():
        print(f'[cfimage][mac] RESET generated Meson build directory: {build}', flush=True)
        shutil.rmtree(build)


def prepare_matching_pango(built: Path) -> Path:
    """Prepare a private Pango with the FreeType/Fontconfig backend GIMP requires.

    On macOS upstream Pango's automatic Meson feature detection disables the
    Fontconfig backend even if Homebrew installed it. An ABI-compatible Pango
    can consequently load cleanly but return NULL for CAIRO_FONT_TYPE_FT.
    """
    brew, meson, ninja = (shutil.which(x) for x in ('brew', 'meson', 'ninja'))
    if not all((brew, meson, ninja)):
        raise RuntimeError('GIMP font runtime requires brew, meson and ninja')
    pango_prefix = Path(subprocess.check_output(
        [brew, '--prefix', 'pango'], text=True).strip())
    cairo_prefix = Path(subprocess.check_output(
        [brew, '--prefix', 'cairo'], text=True).strip())
    cairo = cairo_prefix / 'lib/libcairo.2.dylib'
    # macOS Cairo Meson defaults also disable the optional FT backend. Build
    # the exact installed formula source privately only when that is necessary.
    if not _exports_symbol(cairo, 'cairo_ft_font_face_create_for_ft_face'):
        cairo_dep = built / 'Dependencies/CairoFT'
        cairo_src = cairo_dep / 'DependencySources'
        cairo_build = cairo_dep / 'Build'
        cairo_install = cairo_dep / 'Install'
        cairo_src.mkdir(parents=True, exist_ok=True)
        if not list(cairo_src.glob('cairo-*/meson.build')):
            subprocess.run([brew, 'unpack', '--patch', '--destdir', str(cairo_src), 'cairo'], check=True)
        candidates = sorted(cairo_src.glob('cairo-*/meson.build'))
        if len(candidates) != 1:
            raise RuntimeError(f'Expected one Cairo source tree: {candidates}')
        flags = ['-Dfontconfig=enabled', '-Dfreetype=enabled']
        if _exports_symbol(cairo_install / 'lib/libcairo.2.dylib', 'cairo_ft_font_face_create_for_ft_face'):
            print(f'[cfimage][mac] REUSE private Cairo FreeType build: {cairo_install}', flush=True)
        else:
            _reset_meson_build_directory(cairo_build)
            command = [meson, 'setup', str(cairo_build), str(candidates[0].parent),
                       '--prefix', str(cairo_install), '--buildtype', 'release', *flags]
            subprocess.run(command, check=True)
            subprocess.run([meson, 'compile', '-C', str(cairo_build)], check=True)
            subprocess.run([meson, 'install', '-C', str(cairo_build)], check=True)
        cairo_prefix = cairo_install
        cairo = cairo_prefix / 'lib/libcairo.2.dylib'
        if not _exports_symbol(cairo, 'cairo_ft_font_face_create_for_ft_face'):
            raise RuntimeError(f'Privately rebuilt Cairo still lacks FreeType: {cairo}')
    dependency = built / 'Dependencies/PangoCairo'
    sources = dependency / 'DependencySources'
    build = dependency / 'Build'
    installed = dependency / 'Install'
    library = installed / 'lib/libpangocairo-1.0.0.dylib'
    ft_library = installed / 'lib/libpangoft2-1.0.0.dylib'
    sources.mkdir(parents=True, exist_ok=True)
    if not list(sources.glob('pango-*/meson.build')):
        subprocess.run([brew, 'unpack', '--patch', '--destdir', str(sources), 'pango'], check=True)
    candidates = sorted(sources.glob('pango-*/meson.build'))
    if len(candidates) != 1:
        raise RuntimeError(f'Expected one unpacked Pango source: {candidates}')
    environment = os.environ.copy()
    paths = [cairo_prefix / 'lib/pkgconfig', pango_prefix / 'lib/pkgconfig']
    environment['PKG_CONFIG_PATH'] = os.pathsep.join(
        [*(str(path) for path in paths), environment.get('PKG_CONFIG_PATH', '')]
    )
    options = ['-Dfontconfig=enabled', '-Dfreetype=enabled', '-Dcairo=enabled']
    # A completed private installation is immutable for this pinned dependency.
    # Validate its required backend and Cairo ABI before deciding to rebuild.
    if library.is_file() and _exports_symbol(ft_library, 'pango_fc_font_map_get_type'):
        from shared.mac.dependency_closure import _dylib_versions
        cairo_versions = _dylib_versions(cairo, cairo.name)
        rows = subprocess.check_output(['/usr/bin/otool', '-L', str(library)], text=True)
        match = next((re.search(r'compatibility version ([0-9.]+)', line)
                      for line in rows.splitlines()
                      if Path(line.strip().split(' (', 1)[0]).name == cairo.name), None)
        if (cairo_versions is not None and match is not None and
                tuple(map(int, match.group(1).split('.'))) <= cairo_versions[0]):
            print(f'[cfimage][mac] REUSE verified private Pango: {installed}', flush=True)
            return installed
    _reset_meson_build_directory(build)
    command = [meson, 'setup', str(build), str(candidates[0].parent),
               '--prefix', str(installed), '--buildtype', 'release', *options]
    subprocess.run(command, check=True, env=environment)
    subprocess.run([meson, 'compile', '-C', str(build)], check=True, env=environment)
    subprocess.run([meson, 'install', '-C', str(build)], check=True, env=environment)
    if not library.is_file() or not _exports_symbol(ft_library, 'pango_fc_font_map_get_type'):
        raise RuntimeError(
            f'GIMP private Pango lacks Fontconfig/FreeType despite enabled build: {installed}'
        )
    from shared.mac.dependency_closure import _dependencies, _dylib_versions
    cairo_versions = _dylib_versions(cairo, cairo.name)
    if cairo_versions is None:
        raise RuntimeError(f'Cannot inspect Cairo compatibility version: {cairo}')
    rows = subprocess.check_output(['/usr/bin/otool', '-L', str(library)], text=True)
    match = next((re.search(r'compatibility version ([0-9.]+)', line)
                  for line in rows.splitlines()
                  if Path(line.strip().split(' (', 1)[0]).name == cairo.name), None)
    if match is None or tuple(map(int, match.group(1).split('.'))) > cairo_versions[0]:
        raise RuntimeError(f'GIMP private Pango still requires incompatible Cairo: {library}')
    print('[cfimage][mac] READY private Pango with required Fontconfig/FreeType backend', flush=True)
    return installed


def prepare_matching_gegl_operations() -> None:
    """Require every installed GEGL operation to link against installed Cairo.

    GEGL's loadable operations are not covered by pkg-config or the GEGL
    dylib's ordinary dependency closure. A Homebrew bottle built against an
    older Cairo ABI can be incompatible with the Cairo currently installed.
    Rebuild the original Homebrew formula when necessary, then verify *all*
    operation modules, rather than patching Mach-O compatibility claims.
    """
    from shared.mac.dependency_closure import _dylib_versions
    import re as _re

    brew = shutil.which('brew')
    if not brew:
        raise RuntimeError('Homebrew is required for GEGL operation ABI validation')
    cairo_prefix = Path(subprocess.check_output(
        [brew, '--prefix', 'cairo'], text=True).strip())
    gegl_prefix = Path(subprocess.check_output(
        [brew, '--prefix', 'gegl'], text=True).strip())
    cairo = cairo_prefix / 'lib/libcairo.2.dylib'
    versions = _dylib_versions(cairo, cairo.name)
    if versions is None:
        raise RuntimeError(f'Cannot inspect GEGL dependency Cairo ABI: {cairo}')
    module_root = gegl_prefix / 'lib/gegl-0.4'

    def incompatible_operations() -> list[str]:
        modules = sorted(path for path in module_root.rglob('*')
                         if path.is_file() and path.suffix in ('.dylib', '.so'))
        if not modules:
            raise RuntimeError(f'GEGL operation modules missing: {module_root}')
        conflicts = []
        for module in modules:
            output = subprocess.check_output(['/usr/bin/otool', '-L', str(module)], text=True)
            for line in output.splitlines()[1:]:
                dependency = line.strip().split(' (', 1)[0]
                if Path(dependency).name != cairo.name:
                    continue
                match = _re.search(r'compatibility version ([0-9.]+)', line)
                if match is None:
                    raise RuntimeError(f'Cannot inspect Cairo requirement in {module}')
                required = tuple(int(part) for part in match.group(1).split('.'))
                if required > versions[0]:
                    conflicts.append(f'{module.name}: needs {required}, Cairo supplies {versions[0]}')
        return conflicts

    conflicts = incompatible_operations()
    if conflicts:
        print('[cfimage][mac] Installed GEGL operations use a newer Cairo ABI: '
              + '; '.join(conflicts), flush=True)
        print('[cfimage][mac] Rebuild the Homebrew GEGL formula against installed Cairo', flush=True)
        subprocess.run([brew, 'reinstall', '--build-from-source', 'gegl'], check=True)
        conflicts = incompatible_operations()
        if conflicts:
            raise RuntimeError('GEGL remains incompatible with installed Cairo after '
                               'source rebuild: ' + '; '.join(conflicts))
    print('[cfimage][mac] READY GEGL operation modules against Cairo ABI', flush=True)



def prepare_adwaita_legacy() -> Path:
    """Stage GNOME's real GTK3 fallback theme, not an empty placeholder.

    Homebrew adwaita-icon-theme does not supply the separate AdwaitaLegacy
    dependency declared by its index.theme. The upstream assets are static,
    so install the pinned theme directly under this app's visible Build tree.
    """
    built = Path(os.environ["CINTER_BUILT_ROOT"]).expanduser().resolve()
    dependency = built / "Dependencies/adwaita-icon-theme-legacy-46.2"
    archive = dependency / "DependencySources/adwaita-icon-theme-legacy-46.2.tar.xz"
    install = dependency / "Install/share/icons/AdwaitaLegacy"
    expected_sha256 = "548480f58589a54b72d18833b755b15ffbd567e3187249d74e2e1f8f99f22fb4"
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.is_file():
        url = ("https://download.gnome.org/sources/adwaita-icon-theme-legacy/46/"
               "adwaita-icon-theme-legacy-46.2.tar.xz")
        print(f"[cfimage][mac] Download {url} -> {archive}", flush=True)
        urllib.request.urlretrieve(url, archive)
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    if checksum != expected_sha256:
        raise RuntimeError(f"AdwaitaLegacy SHA256 mismatch: {checksum}; expected {expected_sha256}")
    if not (install / "index.theme").is_file() or not any(install.rglob("*.png")):
        source_prefix = "adwaita-icon-theme-legacy-46.2/"
        install.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive, "r:xz") as source:
            for member in source.getmembers():
                if member.name == source_prefix + "index.theme":
                    target = install / "index.theme"
                elif member.name.startswith(source_prefix + "AdwaitaLegacy/"):
                    relative = member.name[len(source_prefix + "AdwaitaLegacy/"):]
                    target = install / relative
                else:
                    continue
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile() or member.issym() or member.islnk():
                    raise RuntimeError(f"Unexpected AdwaitaLegacy source member: {member.name}")
                if not target.resolve().is_relative_to(install.resolve()):
                    raise RuntimeError(f"Unsafe AdwaitaLegacy archive path: {member.name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                stream = source.extractfile(member)
                if stream is None:
                    raise RuntimeError(f"Unreadable AdwaitaLegacy source member: {member.name}")
                with stream, target.open("wb") as output:
                    shutil.copyfileobj(stream, output)
    if not (install / "index.theme").is_file() or not any(install.rglob("*.png")):
        raise RuntimeError(f"Incomplete upstream AdwaitaLegacy icon theme: {install}")
    print(f"[cfimage][mac] READY pinned AdwaitaLegacy icons: {install}", flush=True)
    return install


def main() -> None:
    # Use GIMP's checked-in meson.build as the version authority.
    prepare_upstream_submodules()
    run(sys.executable, str(PIPELINE), "cfimage")
    # GTK uses these runtime icon themes for standard widgets and error dialogs.
    # They are data assets, so Mach-O closure cannot discover or stage them.
    run("brew", "install", "adwaita-icon-theme", "hicolor-icon-theme")
    prepare_adwaita_legacy()
    # GTK's fallback image-missing icon still requires the dynamic pixbuf
    # decoders; their loadable modules are not ordinary linked dependencies.
    run("brew", "install", "gdk-pixbuf", "librsvg")
    pixbuf_prefix = Path(subprocess.check_output(
        ["brew", "--prefix", "gdk-pixbuf"], text=True).strip())
    pixbuf_loaders = pixbuf_prefix / "lib/gdk-pixbuf-2.0/2.10.0/loaders"
    pixbuf_query = pixbuf_prefix / "bin/gdk-pixbuf-query-loaders"
    if not pixbuf_loaders.is_dir() or not any(
        entry.is_file() and entry.suffix in (".so", ".dylib")
        for entry in pixbuf_loaders.iterdir()
    ) or not pixbuf_query.is_file():
        raise RuntimeError(
            f"GIMP GdkPixbuf runtime is incomplete: {pixbuf_loaders}, {pixbuf_query}"
        )
    # SVG icons in Adwaita are decoded by librsvg's GdkPixbuf module,
    # installed in a separate Homebrew formula from the built-in decoders.
    svg_prefix = Path(subprocess.check_output(
        ["brew", "--prefix", "librsvg"], text=True).strip())
    svg_loaders = svg_prefix / "lib/gdk-pixbuf-2.0/2.10.0/loaders"
    svg_modules = sorted(svg_loaders.glob("*svg*")) if svg_loaders.is_dir() else []
    svg_modules = [module for module in svg_modules
                   if module.is_file() and module.suffix in (".so", ".dylib")]
    if not svg_modules:
        raise RuntimeError(
            f"GIMP SVG icon decoder is missing from librsvg: {svg_loaders}"
        )
    print(f"[cfimage][mac] READY GdkPixbuf loaders: {pixbuf_loaders}", flush=True)
    print(f"[cfimage][mac] READY SVG icon decoders: {svg_modules}", flush=True)
    # Fontconfig loads its configuration at runtime rather than through the
    # Mach-O dependency graph. Validate the files here so Build only bundles
    # an already-prepared dependency, and never discovers a missing setup step.
    run("brew", "install", "fontconfig")
    brew_prefix = Path(subprocess.check_output(
        ["brew", "--prefix"], text=True).strip())
    fontconfig_dir = brew_prefix / "etc" / "fonts"
    if not (fontconfig_dir / "fonts.conf").is_file() or not (fontconfig_dir / "conf.d").is_dir():
        raise RuntimeError(
            f"GIMP Fontconfig configuration missing after Setup: {fontconfig_dir}"
        )
    print(f"[cfimage][mac] READY Fontconfig configuration: {fontconfig_dir}", flush=True)
    required = (
        ("babl-0.1", "0.1.118", "babl"),
        ("gegl-0.4", "0.4.66", "gegl"),
        ("exiv2", "0.27.4", "exiv2"),
        # GIMP 3.x still links GTK 3. Meson requests pkg-config gtk+-3.0;
        # this comes from the Homebrew gtk+3 formula, not gtk4.
        ("gtk+-3.0", "3.24.0", "gtk+3"),
        # GIMP MyPaint brush engine; required by upstream Meson configuration.
        ("libmypaint", "1.6.0", "libmypaint"),
        # Brush data is a distinct upstream package with its own pkg-config file.
        ("mypaint-brushes-2.0", "2.0.0", "mypaint-brushes"),
        # Metadata validation library used by GIMP upstream configuration.
        ("appstream", "1.0.0", "appstream"),
        # macOS ships a library but not Homebrew libarchive pkg-config metadata.
        ("libarchive", "3.0.0", "libarchive"),
        # GIMP's Meson configure requires the MIME database and its pkg-config
        # metadata; the upstream dependency is not satisfied by macOS LaunchServices.
        ("shared-mime-info", "2.0", "shared-mime-info"),
    )
    # Homebrew libarchive is keg-only; pkg-config cannot discover it unless
    # its own package metadata directory is on the search path.
    if shutil.which("brew"):
        archive_prefix = Path(subprocess.check_output(
            ["brew", "--prefix", "libarchive"], text=True).strip())
        archive_pc = archive_prefix / "lib" / "pkgconfig"
        if archive_pc.is_dir():
            os.environ["PKG_CONFIG_PATH"] = os.pathsep.join(filter(None, (
                str(archive_pc), os.environ.get("PKG_CONFIG_PATH", "")
            )))
    missing = [(module, version, formula) for module, version, formula in required
               if not has_dependency(module, version)]
    if missing:
        if not shutil.which("brew"):
            raise RuntimeError("Homebrew is required for GIMP's missing dependencies: "
                               + ", ".join(formula for _, _, formula in missing))
        run("brew", "install", *(formula for _, _, formula in missing))
    # Provision the optional image import/export integrations that this exact
    # upstream Meson configure reported absent.  Keep capabilities enabled:
    # Homebrew binary packages are preferred to source builds when available.
    # Linux-specific probes (ALSA, linux/input.h, xvfb) are not macOS blockers.
    mac_image_integrations = (
        ("libmng", "libmng"),
        ("xpm", "libxpm"),
        ("cfitsio", "cfitsio"),
        ("libraw", "libraw"),
    )
    missing_integrations = [formula for module, formula in mac_image_integrations
                            if subprocess.run(["pkg-config", "--exists", module],
                                              check=False).returncode != 0]
    # Some upstream interfaces use a config script rather than pkg-config.
    if not shutil.which("libwmf-config"):
        missing_integrations.append("libwmf")
    for executable, formula in (("gs", "ghostscript"),
                                ("desktop-file-validate", "desktop-file-utils")):
        if not shutil.which(executable):
            missing_integrations.append(formula)
    if missing_integrations:
        if not shutil.which("brew"):
            raise RuntimeError("Homebrew unavailable for GIMP image integrations: "
                               + ", ".join(missing_integrations))
        run("brew", "install", *dict.fromkeys(missing_integrations))
    for module, _ in mac_image_integrations:
        if subprocess.run(["pkg-config", "--exists", module],
                          check=False).returncode != 0:
            raise RuntimeError(f"GIMP image integration pkg-config module missing: {module}")
    if not shutil.which("libwmf-config"):
        raise RuntimeError("GIMP libwmf-config missing after installation")
    wmf_prefix = Path(subprocess.check_output(
        ["brew", "--prefix", "libwmf"], text=True
    ).strip())
    if not (wmf_prefix / "include/libwmf/api.h").is_file():
        raise RuntimeError(f"GIMP libwmf development headers missing: {wmf_prefix / 'include/libwmf/api.h'}")
    for executable in ("gs", "desktop-file-validate"):
        if not shutil.which(executable):
            raise RuntimeError(f"GIMP tool missing after installation: {executable}")
    # Refresh the libarchive keg-only pkg-config path after installation.
    archive_prefix = Path(subprocess.check_output(
        ["brew", "--prefix", "libarchive"], text=True).strip())
    archive_pc = archive_prefix / "lib" / "pkgconfig"
    if not (archive_pc / "libarchive.pc").is_file():
        raise RuntimeError(f"Homebrew libarchive metadata missing: {archive_pc / 'libarchive.pc'}")
    os.environ["PKG_CONFIG_PATH"] = os.pathsep.join(filter(None, (
        str(archive_pc), os.environ.get("PKG_CONFIG_PATH", "")
    )))
    # GExiv2 0.14 generates GObject introspection data during its Meson build.
    # Prepare the scanner instead of disabling this upstream functionality.
    if not shutil.which("g-ir-scanner"):
        if not shutil.which("brew"):
            raise RuntimeError("g-ir-scanner is required; Homebrew is unavailable")
        run("brew", "install", "gobject-introspection")
    if not shutil.which("g-ir-scanner"):
        raise RuntimeError("g-ir-scanner remains unavailable after provisioning gobject-introspection")
    if not has_dependency("gobject-introspection-1.0", "1.0"):
        raise RuntimeError("gobject-introspection-1.0 is missing from pkg-config after Setup")
    print("[cfimage][mac] READY g-ir-scanner and GObject Introspection", flush=True)
    # GExiv2 0.14.6 generates Vala bindings during Meson configuration.
    # Keep the upstream feature enabled rather than bypassing the compiler.
    if not shutil.which("valac"):
        if not shutil.which("brew"):
            raise RuntimeError("valac is required for GExiv2; Homebrew is unavailable")
        run("brew", "install", "vala")
    if not shutil.which("valac"):
        raise RuntimeError("valac remains unavailable after provisioning Vala")
    run("valac", "--version")
    # Prepare the exact Pango/Cairo ABI combination during Setup, never Build.
    built = (Path(os.environ["CINTER_BUILT_ROOT"]).expanduser().resolve()
             if os.environ.get("CINTER_BUILT_ROOT") else
             cinterforgeBuildsRootPath() /
             "CreativeSuite/Built/cfimage/mac" /
             os.environ.get("CINTER_INSTALL_VERSION", "2026"))
    prepare_matching_pango(built)
    prepare_matching_gegl_operations()
    prepare_gegl_vala()
    pinned_prefix = prepare_gexiv2()
    for module, minimum, _ in (*required, ("gexiv2", "0.14.0", "pinned-gexiv2")):
        environment = pkg_environment(pinned_prefix) if module == "gexiv2" else None
        if not has_dependency(module, minimum, environment):
            raise RuntimeError(f"Required {module} >= {minimum} is not visible to pkg-config; "
                               "check the Homebrew pkg-config search path and versions")
        version = subprocess.check_output(
            ["pkg-config", "--modversion", module], text=True, env=environment).strip()
        if module == "gexiv2" and tuple(int(part) for part in version.split(".")[:2]) >= (0, 15):
            raise RuntimeError(f"GIMP requires gexiv2 < 0.15.0, found {version}")
        print(f"[cfimage][mac] READY {module} {version}", flush=True)
    print("[cfimage][mac] Core image dependencies prepared; full configure/build "
          "still required before recipe READY", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"[cfimage][mac] ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
