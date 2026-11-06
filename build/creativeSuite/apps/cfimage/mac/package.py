"""Package the complete native-built GIMP install prefix as a macOS app.

Meson installs GIMP's Unix prefix, not a .app. Keep all installed GIMP
components and resolve every non-system Mach-O dependency before declaring
Build successful. Signing belongs to Stage.
"""
from __future__ import annotations

import os
from pathlib import Path
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

_CREATIVE_SUITE = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_CREATIVE_SUITE))
from shared.mac.dependency_closure import close_application_dependencies



def _install_private_pango(bundle: Path, install: Path) -> None:
    """Install the Setup-prepared Pango ABI consistently in both bundle copies.

    The original Meson prefix is retained under Resources. The dependency
    closer also keeps a copy under Contents/lib. Updating only one copy
    leaves stale Pango consumers requesting Cairo compatibility 2.0.0.
    """
    from shared.mac.dependency_closure import _dependencies, _dylib_versions

    from apps.cfimage.mac.setup import _exports_symbol
    if not _exports_symbol(bundle / 'Contents/lib/libcairo.2.dylib',
                           'cairo_ft_font_face_create_for_ft_face'):
        raise RuntimeError('GIMP bundled Cairo is missing its FreeType font backend')
    if not _exports_symbol(install / 'lib/libpangoft2-1.0.0.dylib',
                           'pango_fc_font_map_get_type'):
        raise RuntimeError('GIMP private Pango was built without Fontconfig/FreeType')
    libraries = sorted((install / 'lib').glob('libpango*.dylib'))
    if not libraries:
        raise RuntimeError(f'GIMP Setup did not prepare private Pango: {install}')
    # The dependency closer relocates Cairo into Contents/lib. Cached builds
    # can therefore have no Cairo in the original Meson Resources/lib prefix.
    cairo = bundle / 'Contents/lib/libcairo.2.dylib'
    if not cairo.is_file():
        raise RuntimeError(f'GIMP bundled Cairo missing: {cairo}')
    versions = _dylib_versions(cairo, cairo.name)
    if versions is None:
        raise RuntimeError(f'Cannot inspect bundled Cairo ABI: {cairo}')

    from shared.mac.dependency_closure import _command
    import re as _re
    for source in libraries:
        for dependency in _dependencies(source):
            if Path(dependency).name != cairo.name:
                continue
            rows = _command('/usr/bin/otool', '-L', str(source)).splitlines()
            row = next((line for line in rows if line.strip().startswith(dependency + ' (')), None)
            match = _re.search(r'compatibility version ([0-9.]+)', row or '')
            requested = tuple(map(int, match.group(1).split('.'))) if match else None
            if requested is None or requested > versions[0]:
                raise RuntimeError(
                    f'GIMP private Pango {source} requires Cairo compatibility '
                    f'{requested}; installed Cairo supplies {versions[0]}. '
                    'Run ./Cinter setup cfimage mac Release to rebuild matching dependencies.'
                )
    for folder in (bundle / 'Contents/lib', bundle / 'Contents/Resources/lib'):
        folder.mkdir(parents=True, exist_ok=True)
        for source in libraries:
            destination = folder / source.name
            if destination.exists() or destination.is_symlink():
                destination.unlink()
            shutil.copy2(source.resolve(), destination)
    print(f'[cfimage][mac] Installed Setup-prepared Pango into both bundled library roots: {install}', flush=True)


def _bundle_babl_extensions(bundle: Path) -> bool:
    """Package babl's dynamically loaded formats; otool cannot discover them.

    Babl installs loadable format modules in its own keg rather than GIMP's
    Meson prefix. A standalone GIMP must carry them with the binary.
    """
    destination = bundle / 'Contents/Resources/lib/babl-0.1'
    if destination.is_dir() and any(
        path.is_file() and path.suffix in ('.so', '.dylib')
        for path in destination.iterdir()
    ):
        return False
    brew = shutil.which('brew')
    if not brew:
        raise RuntimeError('GIMP babl extensions are missing and Homebrew is unavailable')
    prefix = Path(subprocess.check_output(
        [brew, '--prefix', 'babl'], text=True
    ).strip())
    source = prefix / 'lib/babl-0.1'
    modules = sorted(path for path in source.iterdir()
                     if path.is_file() and path.suffix in ('.so', '.dylib')) if source.is_dir() else []
    if not modules:
        raise RuntimeError(f'Installed babl extension modules are missing: {source}')
    destination.mkdir(parents=True, exist_ok=True)
    for module in modules:
        shutil.copy2(module.resolve(), destination / module.name)
    print(f'[cfimage][mac] Bundled {len(modules)} babl runtime extensions: {destination}', flush=True)
    return True


def _bundle_gegl_operations(bundle: Path) -> None:
    """Carry GEGL's runtime operation modules, which are loaded by name.

    GEGL operation discovery is not represented in Mach-O load commands:
    bundling libgegl alone leaves GIMP unable to find operations such as
    gegl:alien-map. Copy the installed GEGL module tree without selecting
    individual effects, then let the normal dependency closer relocate its
    dynamic library dependencies.
    """
    brew = shutil.which('brew')
    if brew is None:
        raise RuntimeError('GIMP GEGL operation packaging requires Setup-installed Homebrew')
    result = subprocess.run([brew, '--prefix', 'gegl'], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise RuntimeError(
            f'GIMP GEGL operation runtime was not prepared by Setup: '
            f'{result.stderr.strip()}. Run ./Cinter setup cfimage mac Release.'
        )
    source = Path(result.stdout.strip()) / 'lib/gegl-0.4'
    if not source.is_dir():
        raise RuntimeError(f'GIMP GEGL operation modules missing: {source}')
    modules = sorted(path for path in source.rglob('*')
                     if path.is_file() and path.suffix in ('.so', '.dylib'))
    if not modules:
        raise RuntimeError(f'GIMP GEGL operation directory has no loadable modules: {source}')
    # Fail before copying when a bottle's operation modules demand a Cairo
    # compatibility version not supplied by the app's installed runtime.
    # Setup rebuilds the upstream GEGL formula to correct this mismatch.
    from shared.mac.dependency_closure import _dylib_versions
    cairo = bundle / 'Contents/lib/libcairo.2.dylib'
    versions = _dylib_versions(cairo, cairo.name) if cairo.is_file() else None
    if versions is None:
        raise RuntimeError(f'GIMP bundled Cairo ABI unavailable: {cairo}')
    for module in modules:
        output = subprocess.check_output(['/usr/bin/otool', '-L', str(module)], text=True)
        for line in output.splitlines()[1:]:
            if Path(line.strip().split(' (', 1)[0]).name != cairo.name:
                continue
            match = re.search(r'compatibility version ([0-9.]+)', line)
            if match is None:
                raise RuntimeError(f'Cannot inspect GEGL operation Cairo requirement: {module}')
            required = tuple(int(part) for part in match.group(1).split('.'))
            if required > versions[0]:
                raise RuntimeError(
                    f'GEGL operation {module.name} requires Cairo compatibility {required}, '
                    f'but bundled Cairo supplies {versions[0]}. '
                    'Run ./Cinter setup cfimage mac Release to rebuild incompatible GEGL modules.'
                )
    destination = bundle / 'Contents/Resources/lib/gegl-0.4'
    destination.mkdir(parents=True, exist_ok=True)
    for module in modules:
        target = destination / module.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        # A cached Output may contain read-only, previously signed Mach-O
        # modules. Replace the directory entry atomically instead of opening
        # the old module for writing; never mutate the source or drop modes.
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f'.{target.name}.', suffix='.replacement', dir=target.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            shutil.copy2(module.resolve(), temporary)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    print(f'[cfimage][mac] Bundled {len(modules)} GEGL operation modules: {destination}', flush=True)


def _bundle_gtk_icon_themes(bundle: Path) -> None:
    """Bundle GTK's stock icon themes required even to render fatal dialogs.

    GIMP's own icon directory is not a replacement for the GTK Adwaita and
    hicolor themes. Neither icon assets nor their index.theme files appear in
    Mach-O dependency closure, so copying only linked libraries is insufficient.
    """
    destination = bundle / 'Contents/Resources/share/icons'
    brew = shutil.which('brew')
    if brew is None:
        raise RuntimeError('GIMP GTK icon packaging requires Setup-installed Homebrew')
    # Modern Adwaita inherits its GTK3 full-color icons from AdwaitaLegacy.
    # Copying only Adwaita and hicolor silently drops e.g. image-missing,
    # folder and document-open on some versions of adwaita-icon-theme.
    for formula in ('adwaita-icon-theme', 'hicolor-icon-theme'):
        result = subprocess.run([brew, '--prefix', formula], text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode or not result.stdout.strip():
            raise RuntimeError(
                f'GIMP GTK icon theme was not prepared by Setup ({formula}): '
                f'{result.stderr.strip()}. Run ./Cinter setup cfimage mac Release.'
            )
        icon_root = Path(result.stdout.strip()) / 'share/icons'
        if not icon_root.is_dir():
            raise RuntimeError(f'GIMP GTK icon directory missing: {icon_root}')
        # Copy *all* themes supplied by each pinned formula, not a hardcoded
        # subset. Themes can be split across Adwaita/AdwaitaLegacy versions.
        themes = sorted(path for path in icon_root.iterdir()
                        if path.is_dir() and (path / 'index.theme').is_file())
        if not themes:
            raise RuntimeError(f'GIMP GTK icon theme assets missing: {icon_root}')
        for source in themes:
            target = destination / source.name
            destination.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                if target.is_dir() and not target.is_symlink():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            shutil.copytree(source, target, symlinks=False)
            print(f'[cfimage][mac] Bundled GTK icon theme {source.name}: {target}', flush=True)

    # The separate upstream GTK3 fallback is prepared once by Setup.
    # Do not depend on machine-global icon themes at runtime.
    built = Path(os.environ["CINTER_BUILT_ROOT"]).expanduser().resolve()
    legacy = (built / 'Dependencies/adwaita-icon-theme-legacy-46.2'
              / 'Install/share/icons/AdwaitaLegacy')
    if not (legacy / 'index.theme').is_file() or not any(legacy.rglob('*.png')):
        raise RuntimeError(
            f'GIMP missing pinned AdwaitaLegacy assets: {legacy}. '
            'Run ./Cinter setup cfimage mac Release.'
        )
    target = destination / 'AdwaitaLegacy'
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(legacy, target)
    print(f'[cfimage][mac] Bundled GTK icon theme AdwaitaLegacy: {target}', flush=True)

    for required in ('Adwaita', 'hicolor'):
        if not (destination / required / 'index.theme').is_file():
            raise RuntimeError(f'GIMP required GTK icon theme missing: {required}')
    # Do not claim the bundle is complete if its declared fallback is absent.
    # In particular, AdwaitaLegacy contains GTK3 legacy stock icons.
    adwaita_index = (destination / 'Adwaita/index.theme').read_text(errors='replace')
    for line in adwaita_index.splitlines():
        if line.strip().startswith('Inherits='):
            inherited = [name.strip() for name in line.split('=', 1)[1].split(',')]
            for theme in inherited:
                if theme and not (destination / theme / 'index.theme').is_file():
                    raise RuntimeError(
                        f'GIMP bundled Adwaita inherits missing icon theme {theme}; '
                        'Setup must supply the complete GTK3 icon theme stack'
                    )
            break



def _bundle_gdk_pixbuf_loaders(bundle: Path) -> None:
    """Carry GTK's dynamic PNG/SVG/etc decoders and its cache generator.

    GTK loads these modules at runtime, so Mach-O dependency scanning alone
    cannot find them. The loader cache must be generated for the final installed
    bundle path, not the Setup machine's Homebrew prefix.
    """
    brew = shutil.which('brew')
    if brew is None:
        raise RuntimeError('GIMP GdkPixbuf packaging requires Setup-installed Homebrew')
    result = subprocess.run(
        [brew, '--prefix', 'gdk-pixbuf'],
        capture_output=True, text=True,
    )
    if result.returncode or not result.stdout.strip():
        raise RuntimeError(
            f'GIMP GdkPixbuf was not prepared by Setup: {result.stderr.strip()}'
        )
    prefix = Path(result.stdout.strip())
    source = prefix / 'lib/gdk-pixbuf-2.0/2.10.0/loaders'
    modules = sorted(
        path for path in source.iterdir()
        if path.is_file() and path.suffix in ('.so', '.dylib')
    ) if source.is_dir() else []
    query = prefix / 'bin/gdk-pixbuf-query-loaders'
    if not modules or not query.is_file():
        raise RuntimeError(
            f'GIMP GdkPixbuf loaders or cache generator missing: {source}, {query}; '
            'run ./Cinter setup cfimage mac Release'
        )
    # The base gdk-pixbuf keg does not own the SVG loader. Adwaita's
    # symbolic icons need librsvg's separately installed loader at runtime.
    svg_result = subprocess.run(
        [brew, '--prefix', 'librsvg'], capture_output=True, text=True,
    )
    if svg_result.returncode or not svg_result.stdout.strip():
        raise RuntimeError(
            f'GIMP librsvg was not prepared by Setup: {svg_result.stderr.strip()}'
        )
    svg_source = Path(svg_result.stdout.strip()) / 'lib/gdk-pixbuf-2.0/2.10.0/loaders'
    svg_modules = sorted(
        path for path in svg_source.iterdir()
        if path.is_file() and path.suffix in ('.so', '.dylib') and 'svg' in path.name.lower()
    ) if svg_source.is_dir() else []
    if not svg_modules:
        raise RuntimeError(f'GIMP librsvg icon loader missing: {svg_source}')
    all_modules = modules + svg_modules
    names = [module.name for module in all_modules]
    if len(names) != len(set(names)):
        raise RuntimeError(f'GIMP GdkPixbuf loader name collision: {names}')
    resources = bundle / 'Contents/Resources'
    destination = resources / 'lib/gdk-pixbuf-2.0/2.10.0/loaders'
    destination.mkdir(parents=True, exist_ok=True)
    for module in all_modules:
        target = destination / module.name
        # Replace cached/signed files by inode to avoid mutating signed binaries.
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f'.{module.name}.', suffix='.replacement', dir=destination
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            shutil.copy2(module.resolve(), temporary)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    query_target = resources / 'bin/gdk-pixbuf-query-loaders'
    query_target.parent.mkdir(parents=True, exist_ok=True)
    # A previously staged/cached build can contain signed read-only executables.
    # Copy into a new inode and atomically replace, exactly as for the loaders.
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f'.{query_target.name}.', suffix='.replacement', dir=query_target.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(query.resolve(), temporary)
        os.replace(temporary, query_target)
    finally:
        temporary.unlink(missing_ok=True)
    print(f'[cfimage][mac] Bundled {len(all_modules)} GdkPixbuf loaders (including SVG) and cache generator', flush=True)


def _bundle_gsettings_schemas(bundle: Path) -> None:
    """Compile GTK3 and GIMP schemas into the app-owned runtime prefix.

    GTK's file chooser creates org.gtk.Settings.FileChooser via GSettings,
    including during widget teardown. A missing compiled schema can trigger
    GLib's fatal logging path after the icon probe succeeds. Schemas are data,
    invisible to the Mach-O dependency closer.
    """
    brew = shutil.which('brew')
    if not brew:
        raise RuntimeError('GIMP GSettings packaging requires Setup-installed Homebrew')
    destination = bundle / 'Contents/Resources/share/glib-2.0/schemas'
    destination.mkdir(parents=True, exist_ok=True)
    # The GIMP Meson install was copied into Resources already. Retain its
    # schemas and include the schemas installed by the matching GTK3 formula.
    result = subprocess.run([brew, '--prefix', 'gtk+3'], capture_output=True, text=True)
    if result.returncode or not result.stdout.strip():
        raise RuntimeError('GIMP GTK3 schemas unavailable; run ./Cinter setup cfimage mac Release: '
                           + result.stderr.strip())
    gtk_root = Path(result.stdout.strip()) / 'share/glib-2.0/schemas'
    if not gtk_root.is_dir():
        raise RuntimeError(f'GTK3 schema directory missing from Setup installation: {gtk_root}')
    gtk_schemas = sorted(gtk_root.glob('*.gschema.xml'))
    if not gtk_schemas:
        raise RuntimeError(f'GTK3 installed no GSettings schema XML: {gtk_root}')
    for source in gtk_schemas:
        target = destination / source.name
        if target.is_file() and target.read_bytes() != source.read_bytes():
            raise RuntimeError(f'Conflicting bundled GTK3 GSettings schema: {target} vs {source}')
        if not target.is_file():
            shutil.copy2(source, target)
    # Compile all bundled XML as one directory. Never copy a compiled host
    # schemas cache: the final bundle must own the compilation and inputs.
    glib_result = subprocess.run([brew, '--prefix', 'glib'], capture_output=True, text=True)
    if glib_result.returncode or not glib_result.stdout.strip():
        raise RuntimeError('GIMP GLib schema compiler unavailable; run ./Cinter setup cfimage mac Release')
    compiler = Path(glib_result.stdout.strip()) / 'bin/glib-compile-schemas'
    if not compiler.is_file():
        raise RuntimeError(f'GLib schema compiler not installed: {compiler}')
    compiled = destination / 'gschemas.compiled'
    compiled.unlink(missing_ok=True)
    command = [str(compiler), '--strict', str(destination)]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode or not compiled.is_file():
        raise RuntimeError('GIMP private GSettings compilation failed: '
                           + (result.stderr.strip() or result.stdout.strip()))
    # Verify the GTK file chooser schema exists in the actual bundled cache.
    inspector = Path(glib_result.stdout.strip()) / 'bin/gsettings'
    if inspector.is_file():
        check = subprocess.run([str(inspector), '--schemadir', str(destination),
                                'list-schemas'], capture_output=True, text=True)
        if check.returncode or 'org.gtk.Settings.FileChooser' not in check.stdout.splitlines():
            raise RuntimeError('GIMP bundled GSettings cache lacks org.gtk.Settings.FileChooser')
    print(f'[cfimage][mac] Compiled private GTK3/GIMP GSettings schemas: {destination}', flush=True)


def _build_gtk_asset_probe(bundle: Path) -> None:
    """Link a real GTK icon-rendering test into the standalone application.

    Static icon inventory checks are insufficient: symbolic SVG icon decode
    errors are fatal in GTK's file chooser. The probe runs with the same
    loader cache and resource environment as GIMP *after staging*.
    """
    source = Path(__file__).with_name('gtk_asset_probe.c')
    if not source.is_file():
        raise RuntimeError(f'GIMP GTK resource probe source missing: {source}')
    pkg_config = shutil.which('pkg-config')
    compiler = shutil.which('cc')
    if not pkg_config or not compiler:
        raise RuntimeError('GIMP GTK asset validation requires Setup-installed cc and pkg-config')
    flags = subprocess.run([pkg_config, '--cflags', '--libs', 'gtk+-3.0'],
                           capture_output=True, text=True)
    if flags.returncode:
        raise RuntimeError(f'GIMP GTK probe dependencies unavailable: {flags.stderr.strip()}')
    destination = bundle / 'Contents/Resources/bin/gtk-asset-probe'
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Use the versioned external build Output for intermediate artifacts.
    # Replace rather than overwriting a signed/read-only cached Output inode.
    with tempfile.TemporaryDirectory(prefix='cfimage-gtk-probe-', dir=bundle.parent) as temporary:
        binary = Path(temporary) / 'gtk-asset-probe'
        command = [compiler, str(source), '-o', str(binary),
                   *shlex.split(flags.stdout)]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(f'GIMP GTK resource probe compilation failed:\n{result.stderr}')
        os.replace(binary, destination)
    destination.chmod(0o755)


def _bundle_fontconfig(bundle: Path) -> None:
    """Keep the installed Fontconfig configuration with its bundled dylib.

    Fontconfig discovers fonts.conf and conf.d at runtime, not through Mach-O
    linking. The compile-time Homebrew prefix is not available on a clean Mac.
    GIMP treats an asynchronous font initialization error as fatal.
    """
    brew = shutil.which('brew')
    if brew is None:
        raise RuntimeError('GIMP Fontconfig packaging requires Setup-installed Homebrew')
    # Homebrew's fontconfig formula installs its active configuration into
    # HOMEBREW_PREFIX/etc/fonts, not into the formula's opt/fontconfig keg.
    # Query the prefix itself rather than constructing a machine-specific path.
    result = subprocess.run([brew, '--prefix'], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode or not result.stdout.strip():
        raise RuntimeError(
            f'Cannot locate the Homebrew Fontconfig configuration: {result.stderr.strip()}'
        )
    source = Path(result.stdout.strip()) / 'etc/fonts'
    if not (source / 'fonts.conf').is_file() or not (source / 'conf.d').is_dir():
        raise RuntimeError(f'GIMP Fontconfig installation is incomplete: {source}')
    destination = bundle / 'Contents/Resources/etc/fonts'
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        shutil.rmtree(destination)
    # Materialize Homebrew's conf.d symlinks: links back to a keg are not
    # portable and must not survive Stage onto another computer.
    shutil.copytree(source, destination, symlinks=False)
    if not (destination / 'fonts.conf').is_file() or not (destination / 'conf.d').is_dir():
        raise RuntimeError(f'GIMP bundled Fontconfig is incomplete: {destination}')
    print(f'[cfimage][mac] Bundled Fontconfig configuration: {destination}', flush=True)


def refresh_gimp_launcher(output: Path, product_name: str) -> None:
    """Refresh only the installed app's launcher; never recompile dependencies.

    Cached Output bundles survive source-side packaging fixes. A new ZIP alone
    cannot apply those fixes unless the existing launcher is refreshed first.
    """
    bundle = output / f'{product_name}.app'
    contents = bundle / 'Contents'
    resources = contents / 'Resources'
    macos = bundle / 'Contents/MacOS'
    if not bundle.is_dir() or not resources.is_dir():
        raise RuntimeError(f'GIMP bundle is incomplete: {bundle}')
    executable_candidates = sorted(path for path in (resources / 'bin').iterdir()
                                   if re.fullmatch(r'(?:cfimage|gimp)-\d+\.\d+(?:\.\d+)?', path.name)
                                   and path.is_file() and os.access(path, os.X_OK))
    if len(executable_candidates) != 1:
        raise RuntimeError(f'Expected one bundled GIMP executable: {executable_candidates}')
    executable = executable_candidates[0]
    _bundle_babl_extensions(bundle)
    # Setup only builds private Cairo when Homebrew Cairo lacks FreeType.
    # Use the same selected Cairo here instead of requiring a private build
    # that Setup intentionally did not create.
    from apps.cfimage.mac.setup import _exports_symbol
    private_cairo = output.parent / 'Dependencies/CairoFT/Install/lib/libcairo.2.dylib'
    if private_cairo.is_file():
        cairo_source = private_cairo
    else:
        brew = shutil.which('brew')
        if not brew:
            raise RuntimeError('GIMP Cairo packaging requires Homebrew or Setup-prepared Cairo')
        cairo_prefix = Path(subprocess.check_output(
            [brew, '--prefix', 'cairo'], text=True).strip())
        cairo_source = cairo_prefix / 'lib/libcairo.2.dylib'
    if not cairo_source.is_file() or not _exports_symbol(
            cairo_source, 'cairo_ft_font_face_create_for_ft_face'):
        raise RuntimeError(
            f'GIMP Cairo with FreeType unavailable: {cairo_source}; '
            'run ./Cinter setup cfimage mac Release before Build'
        )
    for root in (bundle / 'Contents/lib', bundle / 'Contents/Resources/lib'):
        root.mkdir(parents=True, exist_ok=True)
        destination = root / cairo_source.name
        if destination.is_file() or destination.is_symlink():
            destination.unlink()
        shutil.copy2(cairo_source.resolve(), destination)
    _bundle_gegl_operations(bundle)
    _bundle_gtk_icon_themes(bundle)
    _bundle_gdk_pixbuf_loaders(bundle)
    _bundle_fontconfig(bundle)
    _bundle_gsettings_schemas(bundle)
    _build_gtk_asset_probe(bundle)
    prepared_pango = output.parent / "Dependencies/PangoCairo/Install"
    if not (prepared_pango / "lib/libpangocairo-1.0.0.dylib").is_file():
        raise RuntimeError(
            f'GIMP matching Pango dependency is not prepared: {prepared_pango}; '
            'run ./Cinter setup cfimage mac Release before Build'
        )
    _install_private_pango(bundle, prepared_pango)
    # babl discovers its format and SIMD extensions via BABL_PATH. Its
    # installed modules live in Resources/lib, not the relocated Mach-O
    # dependency closure in Contents/lib.
    babl_modules = resources / 'lib/babl-0.1'
    if not babl_modules.is_dir() or not any(
        path.is_file() and path.suffix in ('.so', '.dylib')
        for path in babl_modules.iterdir()
    ):
        raise RuntimeError(f'GIMP babl format extensions missing: {babl_modules}')
    # Launch Services and Stage codesigning require CFBundleExecutable to be
    # native Mach-O. Keep the environment setup in a bundled shell resource.
    launcher_script = resources / 'cfimage-launcher.sh'
    launcher_script.write_text(
        '#!/bin/sh\nset -eu\n'
        'CONTENTS="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"\n'
        'PREFIX="$CONTENTS/Resources"\n'
        'export PATH="$PREFIX/bin${PATH:+:$PATH}"\n'
        'export XDG_DATA_DIRS="$PREFIX/share${XDG_DATA_DIRS:+:$XDG_DATA_DIRS}"\n'
        'export XDG_CONFIG_DIRS="$PREFIX/etc/xdg${XDG_CONFIG_DIRS:+:$XDG_CONFIG_DIRS}"\n'
        'export GSETTINGS_SCHEMA_DIR="$PREFIX/share/glib-2.0/schemas"\n'
        'export GI_TYPELIB_PATH="$PREFIX/lib/girepository-1.0${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}"\n'
        'export GDK_PIXBUF_MODULEDIR="$PREFIX/lib/gdk-pixbuf-2.0/2.10.0/loaders"\n'
        '# GTK needs a loader cache with paths for this installed app location.\n'
        'CACHE_ROOT="${XDG_CACHE_HOME:-${HOME}/Library/Caches}/CinterForge/Image"\n'
        'mkdir -p "$CACHE_ROOT"\n'
        'export GDK_PIXBUF_MODULE_FILE="$CACHE_ROOT/gdk-pixbuf-loaders.cache"\n'
        'generate_pixbuf_cache() {\n'
        '  set --\n'
        '  for module in "$GDK_PIXBUF_MODULEDIR"/*.so "$GDK_PIXBUF_MODULEDIR"/*.dylib; do\n'
        '    [ -f "$module" ] || continue\n'
        '    set -- "$@" "$module"\n'
        '  done\n'
        '  [ "$#" -gt 0 ] || { echo "No bundled GdkPixbuf loaders" >&2; return 1; }\n'
        '  "$PREFIX/bin/gdk-pixbuf-query-loaders" "$@"\n'
        '}\n'
        'generate_pixbuf_cache > "$GDK_PIXBUF_MODULE_FILE.tmp.$$" || {\n'
        '  rm -f "$GDK_PIXBUF_MODULE_FILE.tmp.$$"\n'
        '  exit 1\n'
        '}\n'
        '# Symbolic Adwaita icons require the SVG loader, not just PNG.\n'
        'grep -E -q \'\"svg\"\' "$GDK_PIXBUF_MODULE_FILE.tmp.$$" || {\n'
        '  echo "Bundled GdkPixbuf SVG loader is unavailable" >&2\n'
        '  rm -f "$GDK_PIXBUF_MODULE_FILE.tmp.$$"\n'
        '  exit 1\n'
        '}\n'
        'mv -f "$GDK_PIXBUF_MODULE_FILE.tmp.$$" "$GDK_PIXBUF_MODULE_FILE"\n'
        'export FONTCONFIG_PATH="$PREFIX/etc/fonts"\n'
        'export FONTCONFIG_FILE="$PREFIX/etc/fonts/fonts.conf"\n'
        'export GIMP_NO_WRAPPER=1\n'
        'export GIMP3_DATADIR="$PREFIX/share/gimp/3.0"\n'
        'export BABL_PATH="$PREFIX/lib/babl-0.1"\n'
        'export GEGL_PATH="$PREFIX/lib/gegl-0.4"\n'
        'export GIMP3_PLUGINDIR="$PREFIX/lib/gimp/3.0/plug-ins"\n'
        'export DYLD_FALLBACK_LIBRARY_PATH="$CONTENTS/lib:$PREFIX/lib${DYLD_FALLBACK_LIBRARY_PATH:+:$DYLD_FALLBACK_LIBRARY_PATH}"\n'
        '# Capture the exact GLib/GdkPixbuf failure when Launch Services starts the app.\n'
        '# Installed applications may be read-only; diagnostics live in user cache.\n'
        'DIAGNOSTICS="$CACHE_ROOT/Tests/Results"\n'
        'mkdir -p "$DIAGNOSTICS"\n'
        '# Do not inherit a desktop D-Bus address: macOS launchd may expose a\n'
        '# listener that accepts connections but never finishes authentication.\n'
        '# A failed connection makes GApplication run locally instead of hanging.\n'
        'unset DBUS_STARTER_ADDRESS DBUS_STARTER_BUS_TYPE DBUS_SESSION_BUS_PID\n'
        'export DBUS_SESSION_BUS_ADDRESS=unix:path=/dev/null/cfimage-no-session-bus\n'
        'exec 2>> "$DIAGNOSTICS/GimpRuntime.log"\n'
        'echo "[cfimage runtime] $(date)" >&2\n'
        '# Probe the installed GTK icon renderer before GIMP constructs a file chooser.\n'
        'if [ "${1:-}" = "--check-gtk-assets" ]; then\n'
        '  exec "$PREFIX/bin/gtk-asset-probe"\n'
        'fi\n'
        '"$PREFIX/bin/gtk-asset-probe" || {\n'
        '  echo "GIMP cannot render bundled GTK icons; see GimpRuntime.log" >&2\n'
        '  exit 1\n'
        '}\n'
        f'exec "$PREFIX/bin/{executable.name}" "$@"\n'
    )
    launcher_script.chmod(0o755)
    # Compile a minimal native entry point which forwards argv unchanged to
    # the bundled shell script. No build-host paths are embedded in the app.
    launcher_source = output.parent / 'Build' / 'cfimage-launcher.c'
    launcher_source.parent.mkdir(parents=True, exist_ok=True)
    launcher_source.write_text(r'''#include <mach-o/dyld.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
int main(int argc, char **argv) {
    char path[PATH_MAX];
    uint32_t size = sizeof(path);
    if (_NSGetExecutablePath(path, &size) != 0) {
        fputs("Cannot resolve CinterForge Image executable path\n", stderr);
        return 1;
    }
    char *slash = strrchr(path, '/');
    if (!slash) return 1;
    *slash = '\0';
    const char *suffix = "/../Resources/cfimage-launcher.sh";
    if (strlen(path) + strlen(suffix) >= sizeof(path)) return 1;
    strcat(path, suffix);
    char **args = calloc((size_t)argc + 2, sizeof(char *));
    if (!args) return 1;
    args[0] = "/bin/sh";
    args[1] = path;
    for (int i = 1; i < argc; ++i) args[i + 1] = argv[i];
    execv("/bin/sh", args);
    perror("Cannot launch CinterForge Image");
    return 1;
}
''')
    launcher = macos / 'cfimage-launcher'
    compiler = shutil.which('clang') or '/usr/bin/clang'
    subprocess.run([compiler, '-o', str(launcher), str(launcher_source)], check=True)
    close_application_dependencies(bundle, output)

def package_gimp(source: Path, output: Path, product_name: str) -> Path:
    # The executable's minor version follows the pinned upstream GIMP build
    # (for example gimp-3.0), not the plug-in API directory (gimp/3.0).
    # Meson installs the built executable into Output/bin, not the upstream
    # source checkout's bin directory. Validate the actual installed payload.
    installed_bin = output / 'bin'
    executable_candidates = sorted(
        path for path in installed_bin.iterdir()
        if re.fullmatch(r'(?:cfimage|gimp)-\d+\.\d+(?:\.\d+)?', path.name)
        and path.is_file()
        and os.access(path, os.X_OK)
    ) if installed_bin.is_dir() else []
    if len(executable_candidates) != 1:
        raise RuntimeError(
            f'Expected exactly one versioned GIMP GUI executable in {installed_bin}; '
            f'found {executable_candidates}'
        )
    executable = executable_candidates[0]
    required = (output / 'lib/gimp/3.0/plug-ins', output / 'share/gimp/3.0')
    for path in required:
        if not path.is_dir():
            raise RuntimeError(f'Incomplete GIMP install (missing {path})')
    bundle = output / f'{product_name}.app'
    if bundle.is_symlink():
        raise RuntimeError(f'Refusing to replace symlinked application bundle: {bundle}')
    if bundle.exists():
        # Build always reconstructs this generated bundle from the freshly
        # installed Meson prefix. A previous successful Build (or Stage) must
        # not make subsequent Builds fail before packaging begins.
        if not bundle.is_dir() or not (bundle / 'Contents/Info.plist').is_file():
            raise RuntimeError(f'Refusing to replace unrecognized bundle: {bundle}')
        with (bundle / 'Contents/Info.plist').open('rb') as handle:
            previous_info = plistlib.load(handle)
        if previous_info.get('CFBundleIdentifier') != 'io.cinterforge.image':
            raise RuntimeError(f'Refusing to replace foreign application bundle: {bundle}')
        shutil.rmtree(bundle)
        print(f'[cfimage][mac] Replacing previous generated application bundle: {bundle}', flush=True)
    contents = bundle / 'Contents'
    resources = contents / 'Resources'
    macos = contents / 'MacOS'
    macos.mkdir(parents=True)
    resources.mkdir(parents=True)
    try:
        # Retain the *entire* Meson installation: plug-ins, Python/Scheme,
        # introspection, color profiles, translations, and application data.
        for item in sorted(output.iterdir()):
            if item == bundle:
                continue
            dest = resources / item.name
            if item.is_dir():
                shutil.copytree(item, dest, symlinks=False)
            elif item.is_symlink():
                # Resolve installed-prefix links into real bundle-owned files.
                # A symlink into Homebrew or the Meson output is not portable.
                resolved = item.resolve(strict=True)
                if resolved.is_dir():
                    shutil.copytree(resolved, dest, symlinks=False)
                elif resolved.is_file():
                    shutil.copy2(resolved, dest)
                else:
                    raise RuntimeError(f"Unsupported GIMP installed symlink: {item}")
            elif item.is_file():
                shutil.copy2(item, dest)
        info = {
            'CFBundleExecutable': 'cfimage-launcher',
            'CFBundleIdentifier': 'io.cinterforge.image',
            'CFBundleName': product_name,
            'CFBundleDisplayName': product_name,
            'CFBundlePackageType': 'APPL',
            'CFBundleShortVersionString': os.environ.get('CINTER_INSTALL_VERSION', '2026'),
            'CFBundleVersion': os.environ.get('CINTER_INSTALL_VERSION', '2026').replace('-', '.'),
            'NSHighResolutionCapable': True,
        }
        with (contents / 'Info.plist').open('wb') as handle:
            plistlib.dump(info, handle)
        refresh_gimp_launcher(output, product_name)
    except Exception:
        shutil.rmtree(bundle)
        raise
    print(f'[Creative Suite mac] Assembled GIMP from full native install: {bundle}', flush=True)
    return bundle


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Package an existing GIMP Meson installation")
    parser.add_argument("output", type=Path, help="Completed GIMP installation prefix")
    parser.add_argument("product_name", help="CinterForge application bundle name")
    parser.add_argument("--refresh-launcher", action="store_true",
                        help="Update cached app launcher without rebuilding or repackaging dependencies")
    options = parser.parse_args()
    if options.refresh_launcher:
        refresh_gimp_launcher(options.output, options.product_name)
    else:
        package_gimp(options.output, options.output, options.product_name)


if __name__ == "__main__":
    main()


def package(source: Path, output: Path, product_name: str) -> Path:
    """Application-owned packaging entry point for the shared lifecycle."""
    return package_gimp(source, output, product_name)
