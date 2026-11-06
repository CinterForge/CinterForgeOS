"""Assemble upstream Inkscape's Unix-style CMake install as a real macOS bundle.

The complete installed prefix remains under Built/Output for inspection. Build
assembles a second, self-contained macOS application without signing it; Stage
owns signing and persistent installation.
"""
from __future__ import annotations

from pathlib import Path
import os
import json
import subprocess
import plistlib
import shutil
import re
import sys


_CREATIVE_SUITE = next(parent for parent in Path(__file__).resolve().parents
                       if parent.name == "creativeSuite")
sys.path.insert(0, str(_CREATIVE_SUITE))
from shared.mac.dependency_closure import close_application_dependencies



def _cairo_compatibility(binary: Path, cairo_name: str) -> tuple[int, ...] | None:
    """Return the minimum Cairo Mach-O version encoded in a dependent."""
    from shared.mac.dependency_closure import _dependencies

    matches = [reference for reference in _dependencies(binary)
               if Path(reference).name == cairo_name]
    if not matches:
        return None
    if len(matches) != 1:
        raise RuntimeError(f"Ambiguous Cairo dependency in {binary}: {matches}")
    output = subprocess.check_output(['/usr/bin/otool', '-L', str(binary)], text=True)
    for line in output.splitlines()[1:]:
        if line.strip().startswith(matches[0] + ' ('):
            match = re.search(r'compatibility version ([0-9.]+)', line)
            if match:
                return tuple(int(component) for component in match.group(1).split('.'))
    raise RuntimeError(f"Cannot inspect Cairo compatibility requirement in {binary}")


def _prepare_private_cairomm(output: Path) -> Path | None:
    """Rebuild the installed cairomm release against the selected Cairo ABI.

    Never modify the Homebrew installation. Preserve the upstream source
    release and the private build under this application's dependency tree.
    """
    from shared.mac.dependency_closure import _dylib_versions

    brew = shutil.which('brew')
    meson = shutil.which('meson')
    if not brew or not meson:
        raise RuntimeError('Inkscape Cairo closure requires brew and meson')
    cairo_prefix = Path(subprocess.check_output([brew, '--prefix', 'cairo'], text=True).strip())
    wrapper_prefix = Path(subprocess.check_output([brew, '--prefix', 'cairomm'], text=True).strip())
    cairo = cairo_prefix / 'lib/libcairo.2.dylib'
    cairomm = wrapper_prefix / 'lib/libcairomm-1.16.1.dylib'
    if not cairo.is_file() or not cairomm.is_file():
        raise RuntimeError(f'Expected installed Cairo and cairomm libraries: {cairo}, {cairomm}')
    cairo_versions = _dylib_versions(cairo, cairo.name)
    if cairo_versions is None:
        raise RuntimeError(f'Cannot inspect installed Cairo Mach-O identity: {cairo}')
    requested = _cairo_compatibility(cairomm, cairo.name)
    if requested is None:
        raise RuntimeError(f'Installed cairomm does not reference Cairo: {cairomm}')
    if requested <= cairo_versions[0]:
        return None

    root = output.parent / 'Dependencies/CairommCairo'
    sources = root / 'DependencySources'
    build = root / 'Build'
    install = root / 'Install'
    rebuilt = install / 'lib' / cairomm.name
    sources.mkdir(parents=True, exist_ok=True)
    candidates = sorted(sources.glob('cairomm-*/meson.build'))
    if not candidates:
        print('[cfvector][mac] Unpack installed cairomm recipe for private Cairo ABI rebuild', flush=True)
        subprocess.run([brew, 'unpack', '--patch', '--destdir', str(sources), 'cairomm'], check=True)
        candidates = sorted(sources.glob('cairomm-*/meson.build'))
    if len(candidates) != 1:
        raise RuntimeError(f'Cannot identify pinned cairomm Meson source: {candidates}')
    environment = os.environ.copy()
    environment['PKG_CONFIG_PATH'] = os.pathsep.join(filter(None, [
        str(cairo_prefix / 'lib/pkgconfig'),
        str(wrapper_prefix / 'lib/pkgconfig'),
        environment.get('PKG_CONFIG_PATH', ''),
    ]))
    if not (build / 'build.ninja').is_file():
        subprocess.run([meson, 'setup', str(build), str(candidates[0].parent),
                        '--prefix', str(install), '--buildtype', 'release'],
                       check=True, env=environment)
    subprocess.run([meson, 'compile', '-C', str(build)], check=True, env=environment)
    subprocess.run([meson, 'install', '-C', str(build)], check=True, env=environment)
    if not rebuilt.is_file():
        raise RuntimeError(f'Private cairomm dylib missing after successful install: {rebuilt}')
    actual = _cairo_compatibility(rebuilt, cairo.name)
    if actual is None or actual > cairo_versions[0]:
        raise RuntimeError(f'Private cairomm still requires incompatible Cairo: '
                           f'{rebuilt} needs {actual}, Cairo provides {cairo_versions[0]}')
    print(f'[cfvector][mac] READY private cairomm ABI: {actual}', flush=True)
    return install


def _install_private_cairomm(bundle: Path, installed: Path) -> None:
    """Replace cairomm copies in the private application, not Homebrew."""
    libraries = sorted((installed / 'lib').glob('libcairomm-1.16*.dylib'))
    if not libraries:
        raise RuntimeError(f'Private cairomm install contains no dylibs: {installed}')
    for source in libraries:
        for destination in bundle.rglob(source.name):
            if destination.is_symlink():
                destination.unlink()
            elif destination.is_file():
                destination.unlink()
            else:
                raise RuntimeError(f'Unexpected cairomm destination: {destination}')
            shutil.copy2(source.resolve(), destination)
    target = bundle / 'Contents/lib'
    target.mkdir(parents=True, exist_ok=True)
    for source in libraries:
        destination = target / source.name
        if destination.exists() or destination.is_symlink():
            destination.unlink()
        shutil.copy2(source.resolve(), destination)



def _prepare_private_pango(output: Path) -> Path | None:
    """Build an app-owned Pango against Cairo when Homebrew's binaries disagree.

    Cairo's dylib compatibility version is checked against the actual load
    commands rather than assuming the matching file name implies ABI safety.
    """
    from shared.mac.dependency_closure import _dylib_versions

    brew = shutil.which('brew')
    meson = shutil.which('meson')
    if not brew or not meson:
        raise RuntimeError('Inkscape private Pango build requires brew and meson')
    cairo_prefix = Path(subprocess.check_output(
        [brew, '--prefix', 'cairo'], text=True).strip())
    pango_prefix = Path(subprocess.check_output(
        [brew, '--prefix', 'pango'], text=True).strip())
    cairo = cairo_prefix / 'lib/libcairo.2.dylib'
    pangocairo = pango_prefix / 'lib/libpangocairo-1.0.0.dylib'
    if not cairo.is_file() or not pangocairo.is_file():
        raise RuntimeError(f'Inkscape Cairo/Pango dependencies missing: {cairo}, {pangocairo}')
    versions = _dylib_versions(cairo, cairo.name)
    if versions is None:
        raise RuntimeError(f'Cannot inspect Cairo Mach-O identity: {cairo}')
    required = _cairo_compatibility(pangocairo, cairo.name)
    if required is None:
        raise RuntimeError(f'Installed Pango does not reference Cairo: {pangocairo}')
    if required <= versions[0]:
        return None

    root = output.parent / 'Dependencies/PangoCairo'
    sources = root / 'DependencySources'
    build = root / 'Build'
    install = root / 'Install'
    sources.mkdir(parents=True, exist_ok=True)
    candidates = sorted(sources.glob('pango-*/meson.build'))
    if not candidates:
        print('[cfvector][mac] Unpack installed Pango recipe for private Cairo ABI build', flush=True)
        subprocess.run([brew, 'unpack', '--patch', '--destdir', str(sources), 'pango'], check=True)
        candidates = sorted(sources.glob('pango-*/meson.build'))
    if len(candidates) != 1:
        raise RuntimeError(f'Cannot identify pinned Pango Meson source: {candidates}')
    environment = os.environ.copy()
    environment['PKG_CONFIG_PATH'] = os.pathsep.join(filter(None, [
        str(cairo_prefix / 'lib/pkgconfig'),
        str(pango_prefix / 'lib/pkgconfig'),
        environment.get('PKG_CONFIG_PATH', ''),
    ]))
    if not (build / 'build.ninja').is_file():
        subprocess.run([meson, 'setup', str(build), str(candidates[0].parent),
                        '--prefix', str(install), '--buildtype', 'release'],
                       check=True, env=environment)
    subprocess.run([meson, 'compile', '-C', str(build)], check=True, env=environment)
    subprocess.run([meson, 'install', '-C', str(build)], check=True, env=environment)
    rebuilt = install / 'lib' / pangocairo.name
    if not rebuilt.is_file():
        raise RuntimeError(f'Private Pango dylib missing after installation: {rebuilt}')
    actual = _cairo_compatibility(rebuilt, cairo.name)
    if actual is None or actual > versions[0]:
        raise RuntimeError(f'Private Pango still requires incompatible Cairo: '
                           f'{rebuilt} needs {actual}, Cairo provides {versions[0]}')
    print(f'[cfvector][mac] READY private Pango/Cairo ABI: {actual}', flush=True)
    return install


def _install_private_pango(bundle: Path, installed: Path) -> None:
    """Install the coherent Pango library family together in this bundle."""
    libraries = sorted((installed / 'lib').glob('libpango*.dylib'))
    if not any(source.name.startswith('libpangocairo-') for source in libraries):
        raise RuntimeError(f'Private Pango installation has no pangocairo: {installed}')
    target = bundle / 'Contents/lib'
    target.mkdir(parents=True, exist_ok=True)
    for source in libraries:
        for destination in bundle.rglob(source.name):
            if destination.is_file() or destination.is_symlink():
                destination.unlink()
            else:
                raise RuntimeError(f'Unexpected Pango library destination: {destination}')
            shutil.copy2(source.resolve(), destination)
        destination = target / source.name
        if destination.exists() or destination.is_symlink():
            destination.unlink()
        shutil.copy2(source.resolve(), destination)


def package_inkscape(source: Path, output: Path, product_name: str) -> Path:
    upstream_resources = source / "packaging/macos/res"
    upstream_plist = upstream_resources / "inkscape.plist"
    executable = output / "bin/inkscape"
    inkview = output / "bin/inkview"
    data = output / "share/inkscape"
    if not all(item.is_file() for item in (upstream_plist, executable, inkview)):
        raise RuntimeError(
            f"Inkscape upstream installation incomplete: expected {upstream_plist}, "
            f"{executable} and {inkview}"
        )
    if not data.is_dir():
        raise RuntimeError(f"Inkscape installed assets missing: {data}")

    bundle = output / f"{product_name}.app"
    if bundle.exists():
        raise RuntimeError(f"Inkscape bundle already exists; refusing to overwrite: {bundle}")
    contents = bundle / "Contents"
    resources = contents / "Resources"
    macos = contents / "MacOS"
    macos.mkdir(parents=True)
    resources.mkdir(parents=True)

    try:
        # Preserve all upstream installed plugins, translations, extensions,
        # icons, and helper executables. Do not cherry-pick feature directories.
        for child in sorted(output.iterdir()):
            if child == bundle or child.name.startswith("."):
                continue
            destination = resources / child.name
            if child.is_dir():
                shutil.copytree(child, destination, symlinks=True)
            elif child.is_file():
                shutil.copy2(child, destination)

        # Use upstream's document bindings and application metadata, with the
        # generated product executable and identity replacing placeholders.
        with upstream_plist.open("rb") as handle:
            info = plistlib.load(handle)
        info["CFBundleExecutable"] = "cfvector-launcher"
        info["CFBundleName"] = product_name
        info["CFBundleDisplayName"] = product_name
        info["CFBundleIdentifier"] = "io.cinterforge.vector"
        info["CFBundleShortVersionString"] = os.environ.get("CINTER_INSTALL_VERSION", "2026")
        info["CFBundleVersion"] = os.environ.get("CINTER_INSTALL_VERSION", "2026").replace("-", ".")
        # The distributed icon is provided by upstream's macOS resources.
        icon = upstream_resources / "Inkscape-Generic.icns"
        if not icon.is_file():
            raise RuntimeError(f"Inkscape icon missing: {icon}")
        shutil.copy2(icon, resources / icon.name)
        info["CFBundleIconFile"] = icon.name
        for resource in upstream_resources.glob("*.icns"):
            destination = resources / resource.name
            if not destination.exists():
                shutil.copy2(resource, destination)
        with (contents / "Info.plist").open("wb") as handle:
            plistlib.dump(info, handle)

        # A private session bus is required by GApplication registration. A
        # stale host DBUS_SESSION_BUS_ADDRESS can leave the GUI blocked in
        # g_bus_get_sync() indefinitely (confirmed by macOS thread sampling).
        # Package the session tools and their config with the application.
        dbus_prefix = Path(subprocess.check_output(
            ["brew", "--prefix", "dbus"], text=True).strip())
        dbus_bin = resources / "bin"
        dbus_bin.mkdir(parents=True, exist_ok=True)
        for name in ("dbus-run-session", "dbus-daemon"):
            source_binary = dbus_prefix / "bin" / name
            if not source_binary.is_file():
                raise RuntimeError(f"Private D-Bus executable missing: {source_binary}")
            shutil.copy2(source_binary, dbus_bin / name)
        session_conf = dbus_prefix / "share/dbus-1/session.conf"
        if not session_conf.is_file():
            raise RuntimeError(f"Private D-Bus session configuration missing: {session_conf}")
        private_conf = resources / "share/dbus-1/session.conf"
        private_conf.parent.mkdir(parents=True, exist_ok=True)
        # Homebrew's macOS session.conf normally listens on a launchd socket.
        # dbus-run-session creates its own daemon, not a launchd socket, so
        # that configuration fails with DBUS_LAUNCHD_SESSION_BUS_SOCKET empty.
        # Give the app-owned daemon a private Unix socket while preserving the
        # upstream policy, authentication and service-directory configuration.
        config_text = session_conf.read_text(encoding="utf-8")
        launchd_listener = re.compile(
            r"<listen>\s*launchd:env=DBUS_LAUNCHD_SESSION_BUS_SOCKET\s*</listen>"
        )
        config_text, replacements = launchd_listener.subn(
            "<listen>unix:tmpdir=/tmp</listen>", config_text
        )
        if replacements != 1:
            raise RuntimeError(
                "Unexpected Homebrew D-Bus session configuration: expected "
                "one launchd listener to replace for the private session"
            )
        private_conf.write_text(config_text, encoding="utf-8")

        # Verify the actual private bus before producing an installable app.
        # A daemon that exists on disk but cannot authenticate clients causes
        # GApplication::register_application() to hang before showing a window.
        dbus_send = dbus_prefix / "bin/dbus-send"
        if not dbus_send.is_file():
            raise RuntimeError(f"Private D-Bus client probe missing: {dbus_send}")
        bus_environment = os.environ.copy()
        for variable in ("DBUS_SESSION_BUS_ADDRESS", "DBUS_SESSION_BUS_PID",
                         "DBUS_STARTER_ADDRESS", "DBUS_STARTER_BUS_TYPE"):
            bus_environment.pop(variable, None)
        bus_environment["PATH"] = str(dbus_bin) + os.pathsep + bus_environment.get("PATH", "")
        probe = [str(dbus_bin / "dbus-run-session"),
                 f"--config-file={private_conf}", "--", str(dbus_send),
                 "--session", "--print-reply", "--type=method_call",
                 "--dest=org.freedesktop.DBus", "/org/freedesktop/DBus",
                 "org.freedesktop.DBus.ListNames"]
        try:
            result = subprocess.run(probe, env=bus_environment, text=True,
                                    capture_output=True, timeout=15)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Vector private D-Bus session authentication timed out") from exc
        if result.returncode or 'array [' not in result.stdout:
            raise RuntimeError("Vector private D-Bus session did not answer ListNames: "
                               f"exit={result.returncode} stdout={result.stdout} "
                               f"stderr={result.stderr}")
        print("[cfvector][mac] Private D-Bus authentication preflight passed", flush=True)

        # Stage signs the CFBundleExecutable as Mach-O. A shell script here
        # cannot be codesigned as the primary executable. Compile a tiny native
        # trampoline, preserving the private runtime environment of the old
        # script and forwarding all arguments to the installed Inkscape binary.
        launcher = macos / "cfvector-launcher"
        launcher_source = output / "cfvector-launcher.c"
        launcher_source.write_text(r'''#include <errno.h>
#include <mach-o/dyld.h>
#include <libgen.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int prepend(const char *name, const char *prefix) {
    const char *old = getenv(name);
    size_t length = strlen(prefix) + (old && *old ? strlen(old) + 1 : 0) + 1;
    char *value = malloc(length);
    if (!value) return -1;
    if (old && *old) snprintf(value, length, "%s:%s", prefix, old);
    else snprintf(value, length, "%s", prefix);
    int status = setenv(name, value, 1);
    free(value);
    return status;
}

int main(int argc, char **argv) {
    (void)argc;
    char executable[PATH_MAX];
    uint32_t size = sizeof(executable);
    if (_NSGetExecutablePath(executable, &size) != 0) return 126;
    char resolved[PATH_MAX];
    if (!realpath(executable, resolved)) return 126;
    char *macos = dirname(resolved);
    char contents[PATH_MAX], resources[PATH_MAX], location[PATH_MAX];
    if (snprintf(contents, sizeof(contents), "%s/..", macos) >= sizeof(contents)) return 126;
    if (snprintf(resources, sizeof(resources), "%s/Resources", contents) >= sizeof(resources)) return 126;
    if (snprintf(location, sizeof(location), "%s/bin", resources) >= sizeof(location) || prepend("PATH", location)) return 126;
    if (snprintf(location, sizeof(location), "%s/share", resources) >= sizeof(location) || prepend("XDG_DATA_DIRS", location)) return 126;
    if (snprintf(location, sizeof(location), "%s/etc/xdg", resources) >= sizeof(location) || prepend("XDG_CONFIG_DIRS", location)) return 126;
    if (snprintf(location, sizeof(location), "%s/lib", contents) >= sizeof(location) || prepend("DYLD_FALLBACK_LIBRARY_PATH", location)) return 126;
    // Always create an app-owned session; never connect to a stale host bus.
    unsetenv("DBUS_SESSION_BUS_ADDRESS");
    unsetenv("DBUS_SESSION_BUS_PID");
    unsetenv("DBUS_STARTER_ADDRESS");
    unsetenv("DBUS_STARTER_BUS_TYPE");
    char runner[PATH_MAX], config[PATH_MAX], inkscape[PATH_MAX];
    if (snprintf(runner, sizeof(runner), "%s/bin/dbus-run-session", resources) >= sizeof(runner)) return 126;
    if (snprintf(config, sizeof(config), "--config-file=%s/share/dbus-1/session.conf", resources) >= sizeof(config)) return 126;
    if (snprintf(inkscape, sizeof(inkscape), "%s/bin/inkscape", resources) >= sizeof(inkscape)) return 126;
    char **arguments = calloc((size_t)argc + 5, sizeof(char *));
    if (!arguments) return 126;
    arguments[0] = runner;
    arguments[1] = config;
    arguments[2] = "--";
    arguments[3] = inkscape;
    for (int i = 1; i < argc; ++i) arguments[i + 3] = argv[i];
    execv(runner, arguments);
    fprintf(stderr, "CinterForge Vector launcher: %s: %s\n", location, strerror(errno));
    return 127;
}
''', encoding="utf-8")
        try:
            subprocess.run(["xcrun", "clang", "-arch", "arm64", "-O2",
                            "-mmacosx-version-min=12.0", str(launcher_source),
                            "-o", str(launcher)], check=True)
        finally:
            launcher_source.unlink(missing_ok=True)
        # Reuse the existing strict recursive Mach-O relocation implementation.
        # It fails the Build if even one library cannot be resolved in-bundle.
        # Application-specific locations belong to this recipe, not the
        # shared Mach-O traversal. Resolve brew kegs explicitly and fail loud.
        recipe = json.loads((Path(__file__).parent / "recipe.json").read_text())
        extra_dirs = []
        for entry in recipe["mac_dependency_closure"]["extra_search_dirs"]:
            if entry.startswith("brew:"):
                formula, _, suffix = entry[5:].partition("/")
                if not formula or not suffix:
                    raise RuntimeError(f"Invalid Inkscape closure search path: {entry}")
                brew_prefix = subprocess.check_output(
                    ["brew", "--prefix", formula], text=True
                ).strip()
                path = Path(brew_prefix) / suffix
            else:
                path = output / entry
            if not path.is_dir():
                if entry.startswith("brew:"):
                    raise RuntimeError(f"Inkscape pinned dependency directory missing: {path}")
                continue  # Optional upstream lib64 layout.
            extra_dirs.append(path)
        private_cairomm = _prepare_private_cairomm(output)
        if private_cairomm is not None:
            _install_private_cairomm(bundle, private_cairomm)
            extra_dirs.insert(0, private_cairomm / 'lib')
        private_pango = _prepare_private_pango(output)
        if private_pango is not None:
            _install_private_pango(bundle, private_pango)
            extra_dirs.insert(0, private_pango / 'lib')
        close_application_dependencies(bundle, output,
                                       extra_search_dirs=tuple(extra_dirs))
    except Exception:
        shutil.rmtree(bundle)
        raise
    print(f"[Creative Suite mac] Assembled complete Inkscape bundle: {bundle}", flush=True)
    return bundle


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("usage: package_bundle.py <source-checkout> <built-output> <product-name>")
    package_inkscape(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), sys.argv[3])


def package(source: Path, output: Path, product_name: str) -> Path:
    """Application-owned packaging entry point for the shared lifecycle."""
    return package_inkscape(source, output, product_name)
