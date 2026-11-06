#!/usr/bin/env python3
"""Build macOS, Linux AMD64 and Windows x64 Ardour from source using one Mac command.

Run from CFOS lifecycle:
    ./CFOS build cfaudio mac Release

Windows is cross-compiled in the existing LinuxKit Debian guest. Both targets are mandatory by default.
"""
from __future__ import annotations

import argparse
import atexit
import fcntl
import datetime
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

# Resolve our Creative Suite package before importing the host IO layer.  The
# host may also import a top-level package called "shared"; loading it first
# can shadow creativeSuite/shared even if we later update sys.path.
_CREATIVE_SUITE_ROOT = next(parent for parent in Path(__file__).resolve().parents
                            if parent.name == "creativeSuite")
_SHARED_RUNTIME = _CREATIVE_SUITE_ROOT / "shared" / "build" / "build_runtime.py"
if not _SHARED_RUNTIME.is_file():
    raise RuntimeError(
        f"Creative Suite shared runtime is missing: {_SHARED_RUNTIME}\n"
        "Apply the shared-build infrastructure changes to the Cinter checkout "
        "before building Ardour."
    )
if str(_CREATIVE_SUITE_ROOT) in sys.path:
    sys.path.remove(str(_CREATIVE_SUITE_ROOT))
sys.path.insert(0, str(_CREATIVE_SUITE_ROOT))

# Resolve the developer Builds directory without importing the application
# runtime. CinterFileIO imports torch, which is not a build prerequisite.
def cinterforgeBuildsRootPath(repo_root: Path) -> Path:
    source_root = Path(repo_root).resolve()
    if not (source_root / "CinterForgeServer").is_dir() or not (source_root / "CinterForgeOS").is_dir():
        raise ValueError(f"Expected sibling-repository workspace root: {source_root}")
    return source_root / "Builds"

from shared.source.source import resolve_maintained_source
from shared.source.source_dependency_recipes import load_source_dependencies, load_archive_dependencies
from shared.source.source_archive_builds import build_release_archives
# CinterForgeOS host-tool checks are application-owned and loaded from their source file.
import importlib.util as _importlib_util
_host_tools_path = _CREATIVE_SUITE_ROOT / 'apps/cinterforge-os/host_tools.py'
_host_tools_spec = _importlib_util.spec_from_file_location('cinterforge_os_host_tools', _host_tools_path)
if _host_tools_spec is None or _host_tools_spec.loader is None:
    raise RuntimeError(f'Missing host tool validation: {_host_tools_path}')
_host_tools_module = _importlib_util.module_from_spec(_host_tools_spec)
_host_tools_spec.loader.exec_module(_host_tools_module)
ensure_host_tools = _host_tools_module.ensure_host_tools
from shared.build.binary_dependencies import install_requirements
from shared.build.upstream_patches import sync_patch_catalog
from shared.build.build_runtime import (build_lock, product_build_directory, run_visible,
                                  verify_pe_amd64)
from shared.source.source import prepare_build_checkout
from dependencies import Dependency, private_pkgconfig_environment, resolve_tagged_dependency
from shared.mac.dependencies import build_private_dependencies
from build import configure_if_changed
from shared.mac.packaging import package_application, validate_application_bundle
from shared.mac.dependency_closure import close_application_dependencies




# Ardour's bundled GTK2 Waf targets use GNU ELF alias attributes unless their
# upstream DISABLE_VISIBILITY switch is set. Apple Clang cannot emit these
# aliases on Mach-O. Scope the switch to the two affected GTK targets, not
# Ardour or the other libraries. Never modify the maintained source checkout.
_GTK_VISIBILITY_MARKER = "        obj.defines += ['DISABLE_VISIBILITY']  # CinterForge macOS GTK2 Mach-O compatibility\n"


def macos_gtk_visibility(source: Path, *, enable: bool) -> None:
    for relative in ("libs/tk/ydk/wscript", "libs/tk/ytk/wscript"):
        script = source / relative
        if not script.is_file():
            if enable:
                raise RuntimeError(f"Ardour bundled GTK Waf script missing: {script}")
            continue
        original = script.read_text()
        if not enable:
            if _GTK_VISIBILITY_MARKER in original:
                if original.count(_GTK_VISIBILITY_MARKER) != 1:
                    raise RuntimeError(f"Ambiguous GTK compatibility marker: {script}")
                script.write_text(original.replace(_GTK_VISIBILITY_MARKER, "", 1))
            continue
        if _GTK_VISIBILITY_MARKER in original:
            if original.count(_GTK_VISIBILITY_MARKER) != 1:
                raise RuntimeError(f"Ambiguous GTK compatibility marker: {script}")
            continue
        lines = original.splitlines(keepends=True)
        candidates = [index for index, line in enumerate(lines)
                      if "obj.uselib" in line and "' OSX'" in line]
        if len(candidates) != 1 or not lines[candidates[0]].startswith("        "):
            raise RuntimeError(f"Expected exactly one macOS GTK target in {script}; found {len(candidates)}")
        lines.insert(candidates[0] + 1, _GTK_VISIBILITY_MARKER)
        script.write_text("".join(lines))
        print(f"[cfaudio][mac][gtk] Enabled upstream DISABLE_VISIBILITY for {relative}", flush=True)


def run(*arguments: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    run_visible(arguments, cwd=cwd, env=env, label="cfaudio")


# Exact release selections published by Ardour's nightly build stack.
# These are the initial source-build recipes, ordered by dependency relationships.
# Missing libraries are NOT silently resolved from Homebrew. Further required
# libraries must be provisioned explicitly as source-build recipes.
# Upstream Git tags are resolved to immutable commits by shared code.
# Upstream dependencies are compiled from their published release tags.
# Generate their documentation using the real Texinfo tools; do not bypass
# documentation targets with MAKEINFO=true.
# GLib 2.56.4 predates Apple Clang 17. These edits apply ONLY to the
# disposable prepared copy, with an exact-match check. The GNULib diagnostic
# suppression is confined to the two legacy printf implementation files;
# the project-wide warning/error configuration remains unchanged.
_GLIB_LEGACY_COMPAT = tuple(
    # The legacy macro checks that VAL supports integer XOR and passes it to
    # __sync_fetch_and_*. The atomic storage is pointer-sized, but the *value*
    # must remain an integer; casting VAL to gpointer breaks the macro.
    ("glib/gatomic.c", f"g_atomic_pointer_{operator} ((volatile gpointer *) atomic, val)",
     f"g_atomic_pointer_{operator} ((volatile {integer_type} *) atomic, val)")
    for operator, integer_type in (("add", "gssize"), ("and", "gsize"),
                                   ("or", "gsize"), ("xor", "gsize"))
) + (
    # gdataset stores low-bit flags in a pointer-sized word. Modern Clang
    # rejects passing integer flags to the obsolete pointer-typed atomic
    # primitive. Cast the storage to its actual integer representation,
    # retaining the integer flag operand and the original atomic operation.
    ("glib/gdataset.c", "g_atomic_pointer_or (datalist, (gsize)flags)",
     "g_atomic_pointer_or ((volatile gsize *) datalist, (gsize)flags)"),
    ("glib/gdataset.c", "g_atomic_pointer_and (datalist, ~(gsize)flags)",
     "g_atomic_pointer_and ((volatile gsize *) datalist, ~(gsize)flags)"),
    # GParamSpec floating flag is encoded in the low bit of qdata.
    # The atomic AND mask is integral, so use pointer-sized integral storage.
    ("gobject/gparam.c", "g_atomic_pointer_and (&pspec->qdata, ~(gsize)PARAM_FLOATING_FLAG)",
     "g_atomic_pointer_and ((volatile gsize *) &pspec->qdata, ~(gsize)PARAM_FLOATING_FLAG)", 2),
    # The pinned GLib 2.56.4 kqueue-thread.c has C99-style declarations.
    # Do not reorder them: the scoped Clang compatibility diagnostic below
    # handles -Wdeclaration-after-statement without fragile source edits.
    # Retain the separate kqueue-utils correctness rewrites.
    # dep-list.c also uses C99-style declarations; preserve upstream logic
    # and assertions rather than rearranging its statements. The scoped
    # diagnostic below addresses the legacy compiler warning.
    ('gio/kqueue/kqueue-helper.c', 'get_kqueue_descriptor()', 'get_kqueue_descriptor(void)'),
    ('gio/kqueue/kqueue-utils.c', '  g_assert (kv != NULL);\n  gsize candidate_sz;', '  gsize candidate_sz;\n  g_assert (kv != NULL);'),
    ('gio/kqueue/kqueue-utils.c', '      data += retval;', '      data = (const char *) data + retval;'),
    ('gio/kqueue/kqueue-utils.c', '  g_assert (fd != -1);\n\n  struct stat st;', '  struct stat st;\n  g_assert (fd != -1);'),
    # Clang 17 enforces C89-era warnings in this legacy kqueue code.
    # Keep upstream dep-list declarations and assertions unmodified. Restrict the diagnostic
    # override to these four translation units, never the rest of GLib.
    # The source is always regenerated from the pinned tag and verified.
    # Anchor after the first header: previously prepared GLib sources may
    # already have a diagnostic inserted after kqueue-thread.h. The next
    # upstream header is unchanged in both the pinned release and that state.
    ('gio/kqueue/kqueue-thread.c', '#include "kqueue-sub.h"',
     '#include "kqueue-sub.h"\n#if defined(__clang__)\n'
     '#pragma clang diagnostic ignored "-Wdeclaration-after-statement"\n#endif'),
    ('gio/kqueue/kqueue-utils.c', '#include "kqueue-utils.h"',
     '#include "kqueue-utils.h"\n#if defined(__clang__)\n'
     '#pragma clang diagnostic ignored "-Wdeclaration-after-statement"\n'
     '#pragma clang diagnostic ignored "-Wgnu-pointer-arith"\n#endif'),
    # The previous partially patched dependency cache can already contain an
    # edit at dep-list.h.  Anchor at an earlier unchanged upstream header.
    # Keep the pragma local to dep-list.c rather than weakening global warnings.
    ('gio/kqueue/dep-list.c', '#include <assert.h>',
     '#include <assert.h>\n#if defined(__clang__)\n'
     '#pragma clang diagnostic ignored "-Wdeclaration-after-statement"\n#endif'),
    ('gio/kqueue/kqueue-helper.c', '#include "kqueue-helper.h"',
     '#include "kqueue-helper.h"\n#if defined(__clang__)\n'
     '#pragma clang diagnostic ignored "-Wmissing-prototypes"\n#endif'),
    # GLib 2.56 uses the GNU driver flag -export-dynamic when linking its
    # resourceplugin test module. Apple Clang does not accept that flag;
    # Darwin's linker exports external symbols from bundles by default.
    # Keep the test module and apply the GNU flag on other platforms only.
    ("gio/tests/meson.build",
     "link_args : ['-export-dynamic'] + resource_plugin_platform_link_args,",
     "link_args : (host_system == 'darwin' ? [] : ['-export-dynamic']) "
     "+ resource_plugin_platform_link_args,"),
    # GLib 2.56 predates Python 3.12 removing distutils. Its GDBus
    # code generator uses LooseVersion solely to sort Since annotations.
    # Preserve numeric component ordering with stdlib-only comparison keys.
    ("gio/gdbus-2.0/codegen/utils.py", "import distutils.version",
     "import re"),
    ("gio/gdbus-2.0/codegen/utils.py", "return (distutils.version.LooseVersion(v), key[1])",
     "return (tuple((0, int(part)) if part.isdigit() else (1, part.lower()) "
     "for part in re.findall(r'[0-9]+|[A-Za-z]+', v)), key[1])"),
) + tuple(
    (f"glib/gnulib/{filename}", needle,
     needle + '\n#if defined(__clang__)\n'
     '#pragma clang diagnostic ignored "-Wformat-nonliteral"\n#endif')
    for filename, needle in (("printf.c", '#include <config.h>'),
                             ("vasnprintf.c", '# include <config.h>'))
)

# Ardour's published liblo patch concatenates the mutually incompatible
# integer suffixes LL and ULU, producing LLULU.  Keep the variadic marker
# feature and its 64-bit sentinel values, but use the portable C99 ULL suffix.
# Apply the correction only to the pinned liblo prepared source copy.
_LIBLO_MARKER_COMPAT = (
    ("lo/lo_macros.h", "0xdeadbeefdeadbeefLLULU", "0xdeadbeefdeadbeefULL"),
    ("lo/lo_macros.h", "0xf00baa23f00baa23LLULU", "0xf00baa23f00baa23ULL"),
)



def liblo_marker_rewrites(source_cache: Path) -> tuple[tuple[str, str, str], ...]:
    """Accept upstream, patched and corrected liblo markers on incremental builds.

    liblo 0.32 ships these constants with the nonstandard LLU suffix.  The
    published Ardour patch appends LU, yielding invalid LLULU.  The prepared
    checkout may be in any of those states on a retry.  Validate each marker
    independently and normalize to the valid C99 ULL suffix, without changing
    the sentinel value or disabling liblo functionality.
    """
    header = source_cache / "liblo-prepared" / "lo" / "lo_macros.h"
    content = header.read_text(encoding="utf-8") if header.is_file() else None
    result = []
    for relpath, invalid, corrected in _LIBLO_MARKER_COMPAT:
        upstream = invalid.replace("LLULU", "LLU")
        candidates = (invalid, upstream, corrected)
        if content is None:
            needle = invalid
        else:
            present = [candidate for candidate in candidates if content.count(candidate) == 1]
            if len(present) != 1:
                raise RuntimeError(
                    f"liblo: expected exactly one original, patched, or corrected "
                    f"marker in {header}: {candidates}; found {present}"
                )
            needle = present[0]
        # The shared dependency validator requires a real source edit.  A
        # rewrite from a string to itself is invalid and unnecessary on a
        # repeat build after this header has already been normalized.
        if needle != corrected:
            result.append((relpath, needle, corrected))
    if content is not None:
        state = "already corrected" if not result else "needs correction"
        print(f"[cfaudio][mac][liblo] Marker compatibility: {state}", flush=True)
    return tuple(result)



def ensure_macos_texinfo() -> None:
    """Provide the real GNU makeinfo for complete upstream documentation builds.

    `brew --prefix texinfo` reports a prospective prefix even if the formula
    has not been installed.  Check the executable, not just the prefix command.
    """
    makeinfo = shutil.which("makeinfo")
    if makeinfo:
        print(f"[cfaudio][mac][tools] makeinfo: {makeinfo}", flush=True)
        run_visible([makeinfo, "--version"], label="cfaudio-tools")
        return

    brew = shutil.which("brew")
    if not brew:
        raise RuntimeError(
            "GNU Texinfo (makeinfo) is required to build dependency documentation, "
            "but Homebrew is unavailable. Install Homebrew and Texinfo to continue."
        )

    def installed_makeinfo() -> Path | None:
        result = subprocess.run(
            [brew, "--prefix", "texinfo"], text=True, capture_output=True,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        path = Path(result.stdout.strip()) / "bin" / "makeinfo"
        return path if path.is_file() and os.access(path, os.X_OK) else None

    makeinfo_path = installed_makeinfo()
    if makeinfo_path is None:
        print("[cfaudio][mac][tools] GNU makeinfo absent; installing Texinfo", flush=True)
        run_visible([brew, "install", "texinfo"], label="cfaudio-tools")
        makeinfo_path = installed_makeinfo()
        if makeinfo_path is None:
            raise RuntimeError(
                "Homebrew finished installing texinfo, but its executable is not "
                "present at $(brew --prefix texinfo)/bin/makeinfo. Check the "
                "Homebrew installation output above."
            )

    os.environ["PATH"] = f"{makeinfo_path.parent}{os.pathsep}{os.environ.get('PATH', '')}"
    print(f"[cfaudio][mac][tools] makeinfo: {makeinfo_path}", flush=True)
    run_visible([str(makeinfo_path), "--version"], label="cfaudio-tools")



def ensure_macos_mm_common() -> None:
    """Provision GNOME's *build tool* required by libsigc++/glibmm Autotools.

    mm-common-prepare supplies MM_PREREQ and the Automake include files
    consumed by upstream autogen.sh.  It is a host build utility, not an
    Ardour runtime library; compiled libraries still come from our private
    source-build dependency stack under Builds.
    """
    tool = shutil.which("mm-common-prepare")
    brew = shutil.which("brew")
    if not tool:
        if not brew:
            raise RuntimeError(
                "libsigc++ requires mm-common-prepare (mm-common). "
                "Homebrew is unavailable; install the mm-common build tool first."
            )
        print("[cfaudio][mac][tools] mm-common-prepare absent; installing mm-common", flush=True)
        run_visible([brew, "install", "mm-common"], label="cfaudio-tools")
        prefix = subprocess.run(
            [brew, "--prefix", "mm-common"], capture_output=True, text=True,
            check=False,
        )
        if prefix.returncode != 0 or not prefix.stdout.strip():
            raise RuntimeError("Homebrew did not report an mm-common prefix after installation")
        path = Path(prefix.stdout.strip()) / "bin" / "mm-common-prepare"
        if not path.is_file() or not os.access(path, os.X_OK):
            raise RuntimeError(f"mm-common installed but mm-common-prepare is missing: {path}")
        tool = str(path)

    tool_dir = str(Path(tool).resolve().parent)
    existing_path = os.environ.get("PATH", "")
    if tool_dir not in existing_path.split(os.pathsep):
        os.environ["PATH"] = tool_dir + os.pathsep + existing_path

    # Older GNOME Autotools consumers need MM_PREREQ via aclocal.  Homebrew
    # mm-common installs the macro in share/aclocal; preserve any existing
    # custom locations rather than overriding them.
    share_aclocal = Path(tool).resolve().parent.parent / "share" / "aclocal"
    if not share_aclocal.is_dir() and brew:
        brew_prefix = subprocess.run(
            [brew, "--prefix", "mm-common"], capture_output=True, text=True,
            check=False,
        )
        if brew_prefix.returncode == 0 and brew_prefix.stdout.strip():
            share_aclocal = Path(brew_prefix.stdout.strip()) / "share" / "aclocal"
    if not share_aclocal.is_dir():
        raise RuntimeError(
            "mm-common-prepare is available, but its Autotools macros are missing "
            f"(expected share/aclocal next to {tool})."
        )
    aclocal_path = os.environ.get("ACLOCAL_PATH", "")
    if str(share_aclocal) not in aclocal_path.split(os.pathsep):
        os.environ["ACLOCAL_PATH"] = str(share_aclocal) + (os.pathsep + aclocal_path if aclocal_path else "")
    print(f"[cfaudio][mac][tools] mm-common-prepare: {tool}", flush=True)
    print(f"[cfaudio][mac][tools] ACLOCAL_PATH: {os.environ['ACLOCAL_PATH']}", flush=True)
    run_visible([tool, "--help"], label="cfaudio-tools")


def ensure_macos_doxygen() -> None:
    """Provide the real upstream documentation generator for GNOME bindings.

    Doxygen may be absent even when Graphviz is installed.  Refuse to accept
    an incomplete documentation build just because configure only warns.
    """
    doxygen = shutil.which("doxygen")
    if not doxygen:
        brew = shutil.which("brew")
        if not brew:
            raise RuntimeError("Doxygen is required to generate libsigc++ "
                               "documentation, but Homebrew is unavailable")
        print("[cfaudio][mac][tools] doxygen absent; installing doxygen", flush=True)
        run_visible([brew, "install", "doxygen"], label="cfaudio-tools")
        result = subprocess.run([brew, "--prefix", "doxygen"], capture_output=True,
                                text=True, check=False)
        if result.returncode == 0 and result.stdout.strip():
            candidate = Path(result.stdout.strip()) / "bin" / "doxygen"
            if candidate.is_file() and os.access(candidate, os.X_OK):
                os.environ["PATH"] = str(candidate.parent) + os.pathsep + os.environ.get("PATH", "")
                doxygen = str(candidate)
        if not doxygen:
            doxygen = shutil.which("doxygen")
    if not doxygen:
        raise RuntimeError("Doxygen installation completed but executable is missing")
    print(f"[cfaudio][mac][tools] doxygen: {doxygen}", flush=True)
    run_visible([doxygen, "--version"], label="cfaudio-tools")



def ensure_macos_docbook_stylesheets(product_build: Path) -> None:
    """Resolve upstream DocBook stylesheet URLs without network access.

    libsigc++'s documentation imports the canonical DocBook XSL URL. macOS
    does not ship that stylesheet; provide the full upstream stylesheets and
    an XML catalog in our visible per-version dependency build directory.
    """
    brew = shutil.which("brew")
    if not brew:
        raise RuntimeError("DocBook XSL stylesheets require Homebrew on macOS")

    def stylesheet_root() -> Path | None:
        result = subprocess.run([brew, "--prefix", "docbook-xsl"], text=True,
                                capture_output=True, check=False)
        if result.returncode != 0 or not result.stdout.strip():
            return None
        formula = Path(result.stdout.strip())
        # Search only the installed formula, not the host filesystem.
        # Homebrew layouts differ across formula revisions.
        for chunk in formula.rglob("chunk.xsl"):
            if chunk.parent.name == "html" and (chunk.parent.parent / "common").is_dir():
                return chunk.parent.parent.resolve()
        return None

    root = stylesheet_root()
    if root is None:
        print("[cfaudio][mac][tools] DocBook XSL missing; installing docbook-xsl", flush=True)
        run_visible([brew, "install", "docbook-xsl"], label="cfaudio-tools")
        root = stylesheet_root()
    if root is None:
        raise RuntimeError("docbook-xsl installed but html/chunk.xsl was not found")

    catalog_dir = product_build / "Dependencies" / "DocBook"
    catalog_dir.mkdir(parents=True, exist_ok=True)
    catalog = catalog_dir / "catalog.xml"
    from xml.sax.saxutils import escape
    from xml.etree import ElementTree as ET
    # Both URI and system lookups are used by different libxml2 clients.
    namespace = "urn:oasis:names:tc:entity:xmlns:xml:catalog"
    ET.register_namespace("", namespace)
    top = ET.Element(f"{{{namespace}}}catalog")
    for rule in ("rewriteURI", "rewriteSystem"):
        key = "uriStartString" if rule == "rewriteURI" else "systemIdStartString"
        value = "rewritePrefix"
        for scheme in ("http", "https"):
            ET.SubElement(top, f"{{{namespace}}}{rule}", {
                key: f"{scheme}://docbook.sourceforge.net/release/xsl/current/",
                value: root.as_uri().rstrip("/") + "/",
            })
    ET.ElementTree(top).write(catalog, encoding="utf-8", xml_declaration=True)
    existing = os.environ.get("XML_CATALOG_FILES", "")
    os.environ["XML_CATALOG_FILES"] = str(catalog) + (os.pathsep + existing if existing else "")
    print(f"[cfaudio][mac][tools] DocBook XSL: {root}", flush=True)
    print(f"[cfaudio][mac][tools] XML_CATALOG_FILES: {catalog}", flush=True)

    # Verify the actual processor resolves the upstream URL offline before
    # starting the lengthy dependency build. No source tree is modified.
    xsltproc = shutil.which("xsltproc")
    if not xsltproc:
        raise RuntimeError("xsltproc required by upstream DocBook documentation build")
    probe_input = catalog_dir / "probe.xml"
    probe_output = catalog_dir / "probe.html"
    probe_input.write_text(
        '<?xml version="1.0"?><article xmlns="http://docbook.org/ns/docbook" '
        'version="5.0"><title>DocBook probe</title><para>Ready</para></article>',
        encoding="utf-8")
    run_visible([xsltproc, "--nonet", "--output", str(probe_output),
                 "http://docbook.sourceforge.net/release/xsl/current/html/docbook.xsl",
                 str(probe_input)], env=os.environ.copy(), label="cfaudio-tools")
    if not probe_output.is_file() or not probe_output.stat().st_size:
        raise RuntimeError("DocBook offline stylesheet test produced no HTML")


def ensure_macos_libiconv() -> dict[str, str]:
    """Provide GLib 2.56's required iconv headers and linker search path.

    On Apple Silicon, system iconv exists but this older GLib/Meson combination
    may fail to detect it. Homebrew's libiconv is keg-only: locate the actual
    files and explicitly expose them to Meson without modifying the host's
    global compiler configuration or any maintained source checkout.
    """
    brew = shutil.which("brew")
    if not brew:
        raise RuntimeError("GLib requires libiconv, but Homebrew is unavailable")

    def installed() -> tuple[Path, Path] | None:
        result = subprocess.run([brew, "--prefix", "libiconv"],
                                capture_output=True, text=True, check=False)
        if result.returncode != 0 or not result.stdout.strip():
            return None
        root = Path(result.stdout.strip())
        header, library = root / "include" / "iconv.h", root / "lib" / "libiconv.dylib"
        if header.is_file() and library.is_file():
            return root / "include", root / "lib"
        return None

    paths = installed()
    if paths is None:
        print("[cfaudio][mac][tools] libiconv missing; installing libiconv", flush=True)
        run_visible([brew, "install", "libiconv"], label="cfaudio-tools")
        paths = installed()
    if paths is None:
        raise RuntimeError("libiconv installation lacks include/iconv.h or lib/libiconv.dylib")

    include, library = paths
    print(f"[cfaudio][mac][tools] iconv headers: {include}", flush=True)
    print(f"[cfaudio][mac][tools] iconv library: {library}", flush=True)
    # Meson must see the keg-only library when checking iconv_open(), not
    # merely during the eventual compilation. Never set -Diconv=disabled.
    env = os.environ.copy()
    # Meson reads CFLAGS (not CPPFLAGS) for the compiler's include search.
    # GLib 2.56 defaults to probing libc iconv, which does not locate GNU
    # libiconv on macOS. Its upstream -Diconv=gnu option selects the actual
    # installed implementation; it does NOT disable iconv functionality.
    for key, value in (("CPPFLAGS", f"-I{include}"),
                       ("CFLAGS", f"-I{include}"),
                       ("LDFLAGS", f"-L{library}"),
                       ("CPATH", str(include)),
                       ("LIBRARY_PATH", str(library))):
        old = env.get(key, "")
        sep = os.pathsep if key in ("CPATH", "LIBRARY_PATH") else " "
        env[key] = value + (sep + old if old else "")
    return env


def ensure_macos_gettext(*, provision: bool = True) -> dict[str, str]:
    """Expose keg-only GNU gettext headers and libintl to GLib's Meson checks."""
    brew = shutil.which("brew")
    if not brew:
        raise RuntimeError("GLib needs GNU gettext/libintl and Homebrew is unavailable")

    def installed() -> tuple[Path, Path] | None:
        result = subprocess.run([brew, "--prefix", "gettext"], capture_output=True,
                                text=True, check=False)
        if result.returncode:
            return None
        root = Path(result.stdout.strip())
        if (root / "include/libintl.h").is_file() and (root / "lib/libintl.dylib").is_file():
            return root / "include", root / "lib"
        return None

    locations = installed()
    if locations is None and provision:
        print("[cfaudio][mac][tools] gettext/libintl missing; installing gettext", flush=True)
        run_visible([brew, "install", "gettext"], label="cfaudio-tools")
        locations = installed()
    if locations is None:
        raise RuntimeError("GNU gettext installed without libintl.h / libintl.dylib")
    include, library = locations
    print(f"[cfaudio][mac][tools] gettext headers: {include}; library: {library}", flush=True)
    return {"CFLAGS": f"-I{include}", "CPPFLAGS": f"-I{include}",
            "LDFLAGS": f"-L{library}", "CPATH": str(include),
            "LIBRARY_PATH": str(library)}


def release_patch_catalog(catalog: Path, patch_directory: Path,
                          recipe_path: Path) -> Path:
    """Drop only obsolete edits to generated/missing files in pinned Git tags.

    Ardour's published patch list covers a separate release/build-stack.  The
    glibmm 2.56.1 source has no threads.h, and liblo 0.32 Git does not ship
    the generated Autoconf configure script (its autogen.sh creates that later).
    TagLib 1.13.1 does not need the legacy global StringPrivate rename,
    which otherwise sweeps binary fixture files with macOS BSD sed.
    The libsndfile 1.2.2 Git checkout does not ship generated configure;
    libsamplerate 0.2.2 uses CMake, not the legacy generated Makefile.in.
    Keep the real source patches and do not disable build components.
    """
    obsolete = {
        "glibmm threads.h": lambda value: "glib/glibmm/threads.h" in value,
        "liblo generated configure": lambda value: (
            " -stdlib=libc++" in value and "configure" in value
        ),
        "taglib legacy global rename": lambda value: (
            "find . -type f -exec sed" in value and "StringPrivate/MyPrivateString" in value
        ),
        "libsndfile generated configure": lambda value: (
            "-Wvla" in value and "configure" in value
        ),
        "libsamplerate generated Makefile.in": lambda value: (
            " examples tests" in value and "Makefile.in" in value
        ),
    }
    data = json.loads(catalog.read_text(encoding="utf-8"))
    removed = {name: 0 for name in obsolete}

    def obsolete_name(value: object) -> str | None:
        if isinstance(value, str):
            for name, predicate in obsolete.items():
                if predicate(value):
                    return name
        return None

    def contains_obsolete(value: object) -> bool:
        if obsolete_name(value) is not None:
            return True
        if isinstance(value, list):
            return any(contains_obsolete(item) for item in value)
        if isinstance(value, dict):
            return any(contains_obsolete(item) for item in value.values())
        return False

    def filter_instructions(value: object) -> object:
        if isinstance(value, list):
            filtered = []
            for item in value:
                if isinstance(item, dict) and contains_obsolete(item):
                    if any(isinstance(child, (dict, list)) for child in item.values()):
                        child_filtered = filter_instructions(item)
                        if contains_obsolete(child_filtered):
                            raise RuntimeError("Cannot isolate obsolete dependency patch record")
                        filtered.append(child_filtered)
                    else:
                        matches = [name for name in obsolete if any(
                            obsolete[name](field) for field in item.values()
                            if isinstance(field, str)
                        )]
                        if len(matches) != 1:
                            raise RuntimeError("Ambiguous obsolete dependency patch record")
                        removed[matches[0]] += 1
                    continue
                name = obsolete_name(item)
                if name is not None:
                    removed[name] += 1
                    continue
                filtered.append(filter_instructions(item))
            return filtered
        if isinstance(value, dict):
            filtered = {}
            for key, item in value.items():
                if obsolete_name(item) is not None:
                    raise RuntimeError(
                        f"Obsolete dependency patch embedded in field {key!r}; "
                        "cannot safely remove without the patch schema"
                    )
                filtered[key] = filter_instructions(item)
            return filtered
        return value

    filtered = filter_instructions(data)

    # Pinned source releases may omit files generated by autoreconf. Express
    # such differences in the versioned application recipe rather than adding
    # more source-specific predicates to the build driver.
    recipe_data = json.loads(recipe_path.read_text(encoding="utf-8"))
    exclusion_rules = recipe_data.get("published_patch_exclusions", [])
    for rule in exclusion_rules:
        dependency = rule["dependency"]
        kind = rule["kind"]
        target = rule["target"]
        expected = rule["expected_count"]
        matches = []
        for index, instruction in enumerate(filtered["instructions"]):
            if (instruction["dependency"].casefold() != dependency.casefold()
                    or instruction["kind"] != kind):
                continue
            argv = __import__("shlex").split(instruction["command"])
            if argv and argv[-1] == target:
                matches.append(index)
        if len(matches) != expected:
            raise RuntimeError(
                f"Published patch exclusion {dependency}/{target}: expected "
                f"{expected} matching instructions, found {len(matches)} in {catalog}"
            )
        for index in reversed(matches):
            del filtered["instructions"][index]
        print(f"[cfaudio][mac][patches] Skipped {len(matches)} generated-file "
              f"instruction(s) for {dependency}/{target}", flush=True)
    # Upstream patch-info can repeat an instruction. Require at least one
    # matching generated-file instruction, while keeping earlier exact-match
    # checks for the three legacy edits already audited against the catalog.
    expected_once = ("glibmm threads.h", "liblo generated configure",
                     "taglib legacy global rename")
    if (any(removed[name] != 1 for name in expected_once)
            or any(count < 1 for count in removed.values())
            or contains_obsolete(filtered)):
        raise RuntimeError(
            "Missing, ambiguous, or unfiltered obsolete source patch "
            f"instruction in {catalog}; found {removed}"
        )
    output = patch_directory / "patch-info-pinned-releases.json"
    output.write_text(json.dumps(filtered, indent=2) + "\n", encoding="utf-8")
    print("[cfaudio][mac][patches] Excluded obsolete generated-file edits "
          "(glibmm, liblo, libsndfile, libsamplerate) and TagLib global "
          "rename; kept remaining upstream patches: "
          f"{output}", flush=True)
    return output


def prepare_macos_dependencies(product_build: Path, *, catalog: Path) -> dict[str, str]:
    """Run the REAL shared private dependency builder before Waf configuration.

    This stage deliberately fails if upstream dependency recipes are missing;
    previously it silently skipped compilation and only checked for .pc files.
    """
    ensure_host_tools(Path(__file__).with_name("recipe.json"), platform="mac")
    ensure_macos_texinfo()
    ensure_macos_mm_common()
    ensure_macos_doxygen()
    iconv_env = ensure_macos_libiconv()
    dependency_env = iconv_env.copy()
    gettext_env = ensure_macos_gettext()
    for key, value in gettext_env.items():
        sep = os.pathsep if key in ("CPATH", "LIBRARY_PATH") else " "
        dependency_env[key] = value + sep + dependency_env.get(key, "")
    # libsigc++ DocBook HTML manual generation is explicitly disabled; no XSL catalog needed.
    private_stack = product_build / "Dependencies"
    source_cache = product_build / "DependencySources"
    print(f"[cfaudio][mac][dependencies] PREFIX {private_stack}", flush=True)
    print(f"[cfaudio][mac][dependencies] SOURCE CACHE {source_cache}", flush=True)
    print(f"[cfaudio][mac][dependencies] PATCH CATALOG {catalog}", flush=True)
    required_modules, release_recipes = load_source_dependencies(
        Path(__file__).with_name("recipe.json"))
    # Build archive-only prerequisites before Git packages that depend on them.
    # Archive recipes requiring Git packages run after the Git dependency pass.
    archive_recipes = load_archive_dependencies(Path(__file__).with_name("recipe.json"))
    archive_prerequisites = tuple(row for row in archive_recipes if not row["depends_on"])
    archive_dependents = tuple(row for row in archive_recipes if row["depends_on"])
    if archive_prerequisites:
        build_release_archives(archive_prerequisites, prefix=private_stack,
                               source_cache=source_cache, environment=dependency_env)
    # libcurl's Homebrew metadata requires zlib.pc even on macOS, whose SDK
    # supplies libz but not that pkg-config module. Prepare our recipe-pinned
    # zlib first, before importing the binary libcurl dependency closure.
    # The full dependency pass below reuses this same versioned source build.
    zlib_rows = [row for row in release_recipes if row[0] == "zlib"]
    if len(zlib_rows) != 1:
        raise RuntimeError("Ardour macOS requires exactly one pinned zlib source recipe")
    (name, url, tag, system, options, modules, build_options,
     install_options, build_in_source) = zlib_rows[0]
    private_zlib = resolve_tagged_dependency(
        name=name, source_url=url, tag=tag, build_system=system,
        configure_args=options, pkgconfig_modules=modules,
        build_args=build_options, install_args=install_options,
        build_in_source=build_in_source, source_cache=source_cache,
    )
    build_private_dependencies(
        dependencies=(private_zlib,), prefix=private_stack,
        source_cache=source_cache, environment=dependency_env,
    )
    if not (private_stack / "lib/pkgconfig/zlib.pc").is_file():
        raise RuntimeError("Pinned zlib source build did not install zlib.pc")
    # Source dependencies (including pinned Pango) need their binary prerequisites
    # in the private prefix BEFORE Meson/CMake configuration, not afterward.
    install_requirements(Path(__file__).with_name("recipe.json"),
                         prefix=private_stack, cache=source_cache)
    print(f"[cfaudio][mac][dependencies] PREFLIGHT {len(required_modules)} required "
          f"pkg-config modules covered by {len(release_recipes)} recipes", flush=True)
    # Prebuilt-first: prefer Homebrew bottles for declared binary packages,
    # even when a historical source fallback recipe also exists. Reuse the
    # already staged private source dependencies; never rebuild them merely
    # because a newly required module is present in the source recipe list.
    binary_modules = {
        item["module"] for item in json.loads(
            Path(__file__).with_name("recipe.json").read_text(encoding="utf-8")
        ).get("binary_dependencies", []) if item.get("provider") == "homebrew"
    }
    source_fallback = tuple(row for row in release_recipes
                            if not set(row[5]).issubset(binary_modules))
    skipped = [row[0] for row in release_recipes if row not in source_fallback]
    if skipped:
        print(f"[cfaudio][mac][dependencies] PREBUILT rather than source: "
              f"{', '.join(skipped)}", flush=True)
    release_recipes = source_fallback
    dependencies = tuple(
        resolve_tagged_dependency(name=name, source_url=url, tag=tag,
                                  build_system=system, configure_args=options,
                                  pkgconfig_modules=modules, build_args=build_options,
                                  install_args=install_options, build_in_source=build_in_source,
                                  source_cache=source_cache)
        for name, url, tag, system, options, modules, build_options, install_options, build_in_source in release_recipes
    )
    # CMake's FindIconv may prefer the macOS SDK stub even when the compiler
    # has selected Homebrew's GNU iconv.h. Those two implementations export
    # different symbols (_iconv vs _libiconv). Pin FLAC's FindIconv inputs to
    # the very same header/library pair used by the private build environment.
    # Keep this scoped to FLAC: other dependencies can use their own iconv
    # detection and Linux/Windows builds keep their native toolchains.
    # Gettext is prepended to the merged search paths, so their first entry
    # is not necessarily libiconv. Use the independently resolved iconv paths.
    iconv_include = Path(iconv_env["CPATH"].split(os.pathsep)[0])
    iconv_library = Path(iconv_env["LIBRARY_PATH"].split(os.pathsep)[0]) / "libiconv.dylib"
    if not (iconv_include / "iconv.h").is_file() or not iconv_library.is_file():
        raise RuntimeError("FLAC requires the matching GNU iconv header and library")
    # The macOS SDK provides libz but no zlib.pc. Build upstream zlib 1.3.2
    # in our private prefix, then bind libpng's headers and linker input to
    # that exact version; do not mix SDK zlib with the private pkg-config graph.
    private_zlib_include = private_stack / "include"
    private_zlib_library = private_stack / "lib" / "libz.dylib"
    libpng_zlib_options = (
        f"-DZLIB_INCLUDE_DIR:PATH={private_zlib_include}",
        f"-DZLIB_LIBRARY:FILEPATH={private_zlib_library}",
        # Do not add the install prefix to CMAKE_C_FLAGS: libpng's
        # genout.cmake.in passes CMAKE_C_FLAGS *before* its generated-header
        # directory. A prefix-wide -I would pick an older installed
        # pnglibconf.h instead of the new build-tree pnglibconf.h, yielding
        # PNG_ZLIB_VERNUM != ZLIB_VERNUM even with the correct zlib library.
        # FindZLIB already supplies ZLIB_INCLUDE_DIRS to libpng's generator
        # and the ZLIB::ZLIB target supplies the header path to its libraries.
    )
    flac_iconv_options = (
        f"-DIconv_INCLUDE_DIR:PATH={iconv_include}",
        f"-DIconv_LIBRARY:FILEPATH={iconv_library}",
    )
    # FLAC 1.4.3 builds Doxygen into doc/doxytmp/html in the out-of-tree
    # CMake build directory, but its upstream install rule points at the
    # nonexistent source-tree doc/api directory. Install the *generated* API
    # docs from their actual build location instead; retain BUILD_DOXYGEN.
    flac_doxygen_install_fix = (
        ("doc/CMakeLists.txt",
         'install(DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/api/"',
         'install(DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/doxytmp/html/"'),
    )
    # Apply audited, declarative source compatibility edits from the app recipe.
    # The shared build engine owns applying them to disposable prepared sources.
    recipe_rows = json.loads(Path(__file__).with_name("recipe.json").read_text(
        encoding="utf-8"))["source_dependencies"]["recipes"]
    recipe_rewrites = {}
    for row in recipe_rows:
        edits = row.get("source_rewrites", [])
        if not isinstance(edits, list):
            raise ValueError(f"{row['name']}: source_rewrites must be a list")
        recipe_rewrites[row["name"]] = tuple(
            (edit["path"], edit["old"], edit["new"], edit.get("expected_count", 1))
            for edit in edits
        )
    dependencies = tuple(
        replace(dep, source_rewrites=(*dep.source_rewrites, *recipe_rewrites[dep.name]))
        for dep in dependencies
    )
    dependencies = tuple(
        replace(dep, source_rewrites=_GLIB_LEGACY_COMPAT) if dep.name == "glib"
        else replace(dep, source_rewrites=liblo_marker_rewrites(source_cache)) if dep.name == "liblo"
        else replace(dep, configure_args=(*dep.configure_args, *flac_iconv_options),
                     source_rewrites=(*dep.source_rewrites, *flac_doxygen_install_fix)) if dep.name == "flac"
        else replace(dep, configure_args=(*dep.configure_args, *libpng_zlib_options)) if dep.name == "libpng"
        else dep
        for dep in dependencies
    )
    print(f"[cfaudio][mac][dependencies] PINNED RECIPES {len(dependencies)}", flush=True)
    build_private_dependencies(
        dependencies=dependencies,
        prefix=private_stack,
        source_cache=source_cache,
        patch_catalog=release_patch_catalog(
            catalog, private_stack / "Patches",
            Path(__file__).with_name("recipe.json")), 
        environment=dependency_env,
    )
    if archive_dependents:
        build_release_archives(archive_dependents, prefix=private_stack,
                               source_cache=source_cache, environment=dependency_env)
    print("[cfaudio][mac][dependencies] BUILD DONE — checking private pkg-config", flush=True)
    env = private_pkgconfig_environment(private_stack,
                                        modules=required_modules,
                                        label="cfaudio")
    # Waf's exact curl invocation is stronger than a version-only or
    # --exists check. Fail at the dependency boundary with pkg-config's
    # actual diagnostic instead of the unhelpful Waf "not found".
    command = ["pkg-config", "--print-errors", "libcurl >= 7.55.0",
               "--cflags", "--libs"]
    check = subprocess.run(command, env=env, text=True, capture_output=True)
    if check.returncode:
        raise RuntimeError(
            "Private libcurl exists but Ardour's exact pkg-config query failed:\n"
            + " ".join(command) + "\n"
            + (check.stderr.strip() or check.stdout.strip() or "no pkg-config diagnostic")
        )
    print(f"[cfaudio][mac][dependencies] VERIFIED Waf libcurl flags: "
          f"{check.stdout.strip()}", flush=True)
    return env



def ready_macos_dependencies(product_build: Path) -> dict[str, str]:
    """Build-only: verify previously prepared dependencies; never provision."""
    recipe = Path(__file__).with_name('recipe.json')
    required, _ = load_source_dependencies(recipe)
    prefix = product_build / 'Dependencies'
    env = private_pkgconfig_environment(prefix, modules=required, label='cfaudio')
    # Homebrew gettext is keg-only on macOS. Its include/lib paths are
    # provisioned during Setup, but Waf runs in a NEW process during Build.
    # Preserve that compiler environment for Ardour itself, not just GLib.
    # Build must only locate the existing bottle; never install anything.
    for key, value in ensure_macos_gettext(provision=False).items():
        sep = os.pathsep if key in ('CPATH', 'LIBRARY_PATH') else ' '
        previous = env.get(key, '')
        env[key] = value + (sep + previous if previous else '')
    binary = json.loads(recipe.read_text(encoding='utf-8')).get('binary_dependencies', [])
    # Public headers of packaged libraries may reside in a versioned include
    # subdirectory (e.g. raptor2/raptor.h included by lrdf.h). Read the
    # exact required paths from the recipe and make them visible to Clang.
    extra_includes = []
    for item in binary:
        for subdir in item.get('compiler_include_dirs', []):
            relative = Path(subdir)
            if relative.is_absolute() or '..' in relative.parts:
                raise RuntimeError(f"Invalid compiler include directory for {item['name']}: {subdir}")
            directory = prefix / 'include' / relative
            if not directory.is_dir():
                raise RuntimeError(f"Setup incomplete: {item['name']} requires {directory}")
            extra_includes.append(str(directory))
    if extra_includes:
        existing = env.get('CPATH', '')
        env['CPATH'] = os.pathsep.join(extra_includes + ([existing] if existing else []))
    for item in binary:
        # SDK-backed headers and dylibs belong to Xcode, not the private
        # Dependencies prefix. Setup verifies their compiler/linker usability
        # and creates the private pkg-config metadata; Build only checks that
        # metadata, without provisioning or modifying the SDK.
        if item.get('provider') != 'macos-sdk':
            for header in item.get('header_files', []):
                if not (prefix / 'include' / header).is_file():
                    raise RuntimeError(
                        f"Setup incomplete: {item['name']} header {header} missing from "
                        f"{prefix}. Run ./Cinter setup cfaudio mac first.")
        if item.get('module'):
            minimum = item.get('minimum_version', item.get('version'))
            if not minimum:
                raise RuntimeError(f"{item['name']}: missing pkg-config version in recipe")
            query = ['pkg-config', '--print-errors',
                     f"{item['module']} >= {minimum}", '--cflags', '--libs']
            check = subprocess.run(query, env=env, text=True, capture_output=True)
            if check.returncode:
                raise RuntimeError(
                    f"Setup is incomplete: {item['name']} is not usable from {prefix}. "
                    "Run ./Cinter setup cfaudio mac first.\n" +
                    (check.stderr.strip() or check.stdout.strip()))
    print(f'[cfaudio][mac] READY private dependency closure {prefix}', flush=True)
    return env


def write_manifest(stage: Path, commit: str, target: str, note: str) -> None:
    manifest = {
        "application": "Ardour", "built_by": "CinterForge, Inc.",
        "official_ardour_release": False,
        "upstream_repository": "https://github.com/Ardour/cfaudio",
        "build_source_repository": str(fork),
        "upstream_commit": commit, "target": target,
        "built_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "note": note,
    }
    (stage / "build_provenance.json").write_text(json.dumps(manifest, indent=2) + "\n")


def verify_windows_binaries(output: Path) -> None:
    executables, libraries = verify_pe_amd64(output, label="cfaudio")
    print(f"[cfaudio] Windows binary verification passed: {executables} EXE(s), {libraries} DLL(s)", flush=True)


def target_ready(stage: Path, target: str) -> bool:
    """A provenance record alone is not evidence of a completed installation."""
    manifest = stage / "build_provenance.json"
    install = stage / "Install"
    if not manifest.is_file() or not install.is_dir():
        return False
    try:
        record = json.loads(manifest.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return False
    if record.get("application") != "Ardour" or record.get("target") != target:
        return False
    if not record.get("upstream_commit"):
        return False
    if target == "macOS":
        return any(
            validate_application_bundle(bundle)
            for bundle in install.glob("*.app")
        )
    return any(path.is_file() for path in install.rglob("*.exe"))


def sign_macos_payload(bundle: Path) -> None:
    """Apply the same complete signature closure used after Steam staging."""
    import importlib.util
    signer = Path(__file__).with_name('repair_signatures.py')
    spec = importlib.util.spec_from_file_location('ardour_mac_signatures', signer)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'Cannot load Ardour signature implementation: {signer}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.sign_bundle(bundle)


def publish_mac_payload(product_build: Path) -> None:
    """Bridge Ardour upstream Install bundle into shared lifecycle Output."""
    installed_bundle_dir = product_build / "macOS" / "Install"
    bundles = sorted(installed_bundle_dir.glob("*.app"))
    if len(bundles) != 1 or not validate_application_bundle(bundles[0]):
        raise RuntimeError(f"Expected one complete Ardour .app in {installed_bundle_dir}; found {bundles}")
    output = product_build / "Output"
    output.mkdir(parents=True, exist_ok=True)
    destination = output / bundles[0].name
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(bundles[0], destination, symlinks=True)
    # Ardour's upstream osx_build edits Mach-O install names after copying
    # pre-signed Homebrew libraries.  Those edits invalidate their signatures,
    # and macOS ARM64 kills the process in dyld with CODESIGNING/Invalid Page.
    # Sign the *completed* bundle, after all upstream binary modifications and
    # before it enters the shared stage/install lifecycle.  Ad-hoc is sufficient
    # for local developer launch; distribution signing is a separate concern.
    if sys.platform == "darwin":
        close_application_dependencies(destination, product_build / "Dependencies")
        sign_macos_payload(destination)
    if not validate_application_bundle(destination):
        raise RuntimeError(f"Incomplete Ardour Build payload: {destination}")
    print(f"[cfaudio][mac] READY shared lifecycle payload: {destination}", flush=True)


def ensure_windows_guest_ready(cinterforge: Path) -> None:
    """Prepare the AMD64 disk before a Windows cross-build, even when invoked directly.

    This is intentionally independent of the Update Data stage ordering: a
    standalone Ardour build must never reach the guest with a missing disk.
    The shared Linux builder is incremental and preserves an existing disk.
    """
    linux_builder = next(parent for parent in Path(__file__).resolve().parents if parent.name == "build_cinterforge") / "creativeSuite" / "apps" / "cinterforge-os" / "linux" / "build_linux.py"
    if not linux_builder.is_file():
        raise RuntimeError(f"Missing shared Linux builder: {linux_builder}")
    disk = cinterforgeBuildsRootPath(cinterforge) / "Linux" / "DebianAMD64.ext4"
    print(f"[cfaudio] preparing Windows cross-build AMD64 guest: {disk}", flush=True)
    run(sys.executable, str(linux_builder), "--repo-root", str(cinterforge), "--prepare-debian")
    if not disk.is_file() or disk.stat().st_size < 1024 ** 3:
        raise RuntimeError(f"AMD64 Debian preparation did not create a valid disk: {disk}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("all", "mac", "windows"), default="all")
    parser.add_argument("--revision", default="HEAD", help="Local maintained source Git ref (default: HEAD)")
    parser.add_argument("--prepare-dependencies", action="store_true",
                        help="Setup-only: prepare the complete private dependency stack")
    parser.add_argument("--skip-ready", action="store_true",
                        help="Reuse existing verified staged installations instead of rebuilding")
    args = parser.parse_args()

    script = Path(__file__).resolve()
    cinterforge = next((parent for parent in script.parents if parent.name == "CinterForgeOS"), None)
    if cinterforge is None or not (cinterforge.parent / "CinterForgeServer").is_dir():
        parser.error("Cannot resolve sibling-repository workspace from CFOS source")
    cinterforge = cinterforge.parent
    # Each platform builds into its own versioned product directory. The Windows
    # adapter calls this entry point with --target windows; do not place its
    # checkout or build objects in the macOS or legacy shared Ardour directory.
    if args.target == "all":
        raise RuntimeError("Build macOS and Windows separately so their caches cannot collide")
    product_platform = "mac" if args.target == "mac" else "win"
    # Keep each product version's disposable Waf checkout and object cache in
    # the canonical product-owned output, never in CreativeSuite/Ardour/Source.
    version = (os.environ.get("CINTER_INSTALL_VERSION") or
               os.environ.get("CINTER_APP_VERSION") or "2026").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", version):
        raise RuntimeError(f"Invalid Ardour build version: {version!r}")
    product_build = product_build_directory(cinterforgeBuildsRootPath(cinterforge), "cfaudio", product_platform, version)
    builds = product_build
    product_build.mkdir(parents=True, exist_ok=True)
    source = product_build / "Source"
    # Fail explicitly if another invocation owns this build cache. Never remove
    # or reinitialize an active Waf build directory to 'fix' a collision.
    lock_file = (product_build / ".build.lock").open("a+")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock_file.close()
        raise RuntimeError(f"Another Ardour {product_platform} build is using {product_build}") from exc
    atexit.register(lock_file.close)
    print(f"[cfaudio] {product_platform} source and object cache: {source / 'build'}", flush=True)
    # Use only the maintained source already present on disk.  The recipe's
    # checkout_name is "ardour"; cfaudio is the product name, not a remote URL.
    # An explicit path can select a differently named local fork.
    fork = resolve_maintained_source(cinterforge, Path(__file__).with_name("recipe.json"))
    print(f"[cfaudio] Using local maintained source: {fork}", flush=True)
    requested = (["macOS", "Windows-x64"] if args.target == "all" else
                 ["macOS"] if args.target == "mac" else ["Windows-x64"])
    if args.skip_ready and all(target_ready(
        builds / ("macOS" if target == "macOS" else "Windows"), target
    ) for target in requested):
        print("[cfaudio] Existing staged installations ready: " + ", ".join(requested), flush=True)
        return 0
    builds.mkdir(parents=True, exist_ok=True)
    if platform.system() != "Darwin":
        parser.error("This build entry point must run on the Mac")
    if not shutil.which("git"):
        parser.error("git is required")
    # Recover completed installations before any source checkout, dependency
    # provisioning or VM invocation. A staging failure is not a build failure.
    shared_ardour = cinterforgeBuildsRootPath(cinterforge) / "CreativeSuite" / "Linux" / "Ardour"
    recovered_windows = False
    recovered_mac = False
    if "macOS" in requested:
        mac_stage = builds / "macOS" / "Install"
        if mac_stage.is_dir() and any(p.is_dir() for p in mac_stage.glob("*.app")):
            print(f"[cfaudio] Reusing existing macOS installation: {mac_stage}", flush=True)
            recovered_mac = True
    if "Windows-x64" in requested:
        stage_install = builds / "Windows" / "Install"
        for candidate in (stage_install, shared_ardour / "Windows" / "Install"):
            if not candidate.is_dir() or not any(p.is_file() and p.suffix.lower() == ".exe" and p.name.lower().startswith("cfaudio") for p in candidate.rglob("*")):
                continue
            verify_windows_binaries(candidate)
            if candidate.resolve() != stage_install.resolve():
                stage_install.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(candidate, stage_install, dirs_exist_ok=True)
            print(f"[cfaudio] Reusing Windows installation: {candidate}", flush=True)
            recovered_windows = True
            break
    # The Linux guest output lives under CreativeSuite/Linux/Ardour, not the
    # canonical host staging directory CreativeSuite/Ardour.
    linux_stage = builds / "Linux-x64" / "Install"
    linux_guest = shared_ardour / "Linux-x64"
    linux_candidate = linux_guest / "Install" if (linux_guest / "Install").is_dir() else linux_guest
    recovered_linux = linux_stage.is_dir() and any(p.is_file() and p.name.startswith("cfaudio") for p in linux_stage.rglob("*"))
    if not recovered_linux and linux_candidate.is_dir() and any(p.is_file() and p.name.startswith("cfaudio") for p in linux_candidate.rglob("*")):
        linux_stage.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(linux_candidate, linux_stage, dirs_exist_ok=True)
        recovered_linux = True
        print(f"[cfaudio] Recovered Linux installation: {linux_candidate} -> {linux_stage}", flush=True)
    # Windows and Linux are independent outputs. A missing Linux installation
    # must never cause a successfully built Windows binary to be recompiled.
    # Setup must only prepare dependencies. Re-publishing an already-built
    # application performs dependency relocation and re-signs hundreds of
    # Mach-O files; that belongs to Build/Stage, never Setup.
    # Prepare before source acquisition, host compilation or the guest job.
    # This also covers older Update Data callers that invoke Ardour directly.
    if "Windows-x64" in requested and not recovered_windows:
        sync_patch_catalog(url="https://ardour.org/files/patch-info.html",
                           destination=product_build / "Dependencies" / "Patches")
        ensure_windows_guest_ready(cinterforge)

    if args.prepare_dependencies:
        if args.target != 'mac':
            raise RuntimeError('This dependency setup executor supports macOS only')
        private_stack = product_build / 'Dependencies'
        catalog = sync_patch_catalog(
            url='https://ardour.org/files/patch-info.html',
            destination=private_stack / 'Patches',
        )
        prepare_macos_dependencies(product_build, catalog=catalog)
        print('[cfaudio][mac] SETUP COMPLETE: dependency closure prepared', flush=True)
        return 0

    if all((recovered_mac if t == "macOS" else recovered_windows) for t in requested):
        if args.target == "mac":
            publish_mac_payload(product_build)
        print("[cfaudio] All requested outputs already built; staging recovery complete (no compilation)", flush=True)
        return 0

    # Upstream osx_build emits DMGs inside its source checkout.  These are
    # generated artifacts, not source edits: retain them under the versioned
    # build output before strict git cleanliness checks (including retry runs).
    mac_packages = product_build / "macOS" / "Packages"
    if args.target in ("mac", "all") and source.is_dir():
        upstream_packages = source / "tools" / "osx_packaging"
        for image in sorted(upstream_packages.glob("Ardour-*-arm64.dmg")):
            mac_packages.mkdir(parents=True, exist_ok=True)
            target = mac_packages / image.name
            if target.exists():
                # A new packaging attempt can reuse the same upstream name.
                target.unlink()
            shutil.move(str(image), str(target))
            print(f"[cfaudio][mac] Retained upstream DMG outside source: {target}", flush=True)

    # Restore only our own known Waf compatibility edits before the shared
    # checkout's strict clean-tree validation. The Waf object cache survives.
    if args.target in ("mac", "all") and source.is_dir():
        macos_gtk_visibility(source, enable=False)
    source = prepare_build_checkout(fork, source, revision=args.revision, label="cfaudio", local_only=True)
    if args.target in ("mac", "all"):
        macos_gtk_visibility(source, enable=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    if args.target in ("mac", "all") and not recovered_mac:
        stage = builds / "macOS"
        stage.mkdir(parents=True, exist_ok=True)
        if not shutil.which("clang++"):
            raise RuntimeError("Xcode command-line tools / clang++ are required")
        private_stack = product_build / "Dependencies"
        mac_environment = ready_macos_dependencies(product_build)
        # Waf retains compiled objects in source/build. Avoid repeating configure
        # for the same revision and install prefix; new commits reconfigure.
        config_stamp = stage / 'waf-configured.commit'
        config_value = f'{commit}\n{(stage / "Install").resolve()}\n{private_stack.resolve()}\n'
        configure_if_changed(
            source=source, stamp=config_stamp, fingerprint=config_value,
            configured_cache=source / "build/c4che/_cache.py",
            command=("./waf", "configure", f"--prefix={stage / 'Install'}"),
            env=mac_environment, label="cfaudio",
        )
        run("./waf", "-j", str(os.cpu_count() or 4), cwd=source, env=mac_environment)
        # Ardour owns its macOS bundle layout and dependency closure. Do not
        # reproduce it with a Cinter-owned Waf installation / handwritten .app.
        packaging_dir = source / "tools" / "osx_packaging"
        packaging_script = packaging_dir / "osx_build"
        if not packaging_script.is_file():
            raise RuntimeError(f"Upstream Ardour macOS packager missing: {packaging_script}")
        print(f"[cfaudio] Upstream macOS packaging: {packaging_script} --public --stack {private_stack}", flush=True)
        # The private libarchive bsdtar was built without an LC_RPATH for its
        # private libz.1.dylib; it aborts while osx_build extracts bundled LV2
        # plugins/media.  macOS ships libarchive's tar: use that host binary
        # through a deterministic, build-local PATH overlay.  No upstream
        # flags/features are changed and no sources are patched.
        host_tools = product_build / "Source" / "build" / "HostTools"
        host_tools.mkdir(parents=True, exist_ok=True)
        system_tar = Path("/usr/bin/tar")
        if not system_tar.is_file():
            raise RuntimeError(f"Required macOS libarchive tar missing: {system_tar}")
        bsdtar = host_tools / "bsdtar"
        # HostTools is a generated, disposable build-local PATH overlay. A
        # previous attempt may have left an old bsdtar executable or a link
        # to the private libarchive build here. Reconcile that single owned
        # entry on every build instead of treating it as a fatal conflict.
        if bsdtar.is_symlink():
            if bsdtar.resolve() != system_tar:
                bsdtar.unlink()
        elif bsdtar.is_file():
            bsdtar.unlink()
        elif bsdtar.exists():
            raise RuntimeError(f"Packaging tool path is not a file: {bsdtar}")
        if not bsdtar.is_symlink():
            bsdtar.symlink_to(system_tar)
        if not os.access(bsdtar, os.X_OK):
            raise RuntimeError(f"Required packaging tool is not executable: {bsdtar}")
        print(f"[cfaudio][mac][tools] bsdtar: {bsdtar} -> {system_tar}", flush=True)
        package_env = dict(mac_environment)
        package_env["PATH"] = f"{host_tools}:{package_env.get('PATH', os.environ.get('PATH', ''))}"
        run("/bin/bash", "./osx_build", "--public", "--stack", str(private_stack),
            cwd=packaging_dir, env=package_env)
        # The upstream osx_build deletes Ardour/Ardour9.app AFTER generating
        # its DMG.  Recover the exact completed bundle from that DMG, not the
        # intermediate directory (or the raw Waf output).
        images = sorted(packaging_dir.glob("Ardour-*-arm64.dmg"),
                        key=lambda image: image.stat().st_mtime_ns, reverse=True)
        if not images:
            raise RuntimeError(f"Upstream Ardour packaging produced no arm64 DMG in {packaging_dir}")
        disk_image = images[0]
        mac_packages.mkdir(parents=True, exist_ok=True)
        archived_image = mac_packages / disk_image.name
        if archived_image.exists():
            archived_image.unlink()
        shutil.move(str(disk_image), str(archived_image))
        disk_image = archived_image
        print(f"[cfaudio][mac] Retained completed DMG: {disk_image}", flush=True)
        installation = stage / "Install"
        # DiskImages may reject a mount point on an external development
        # volume (e.g. /Volumes/CIIData) even though the DMG itself is valid.
        # Mount on the macOS system temporary filesystem instead. The built
        # DMG and copied application remain in the external Builds hierarchy;
        # only this ephemeral mount point is outside it.
        with tempfile.TemporaryDirectory(prefix="cfaudio-dmg-") as tmp:
            mount = Path(tmp) / "Mounted"
            mount.mkdir()
            print(f"[cfaudio] Mount upstream packaged DMG: {disk_image} at {mount}", flush=True)
            subprocess.run(["/usr/bin/hdiutil", "attach", "-readonly", "-nobrowse",
                            "-mountpoint", str(mount), str(disk_image)], check=True)
            try:
                bundles = sorted(mount.glob("Ardour*.app"))
                if len(bundles) != 1:
                    raise RuntimeError(f"Expected one Ardour .app inside {disk_image}; found {bundles}")
                contents = bundles[0] / "Contents"
                if not (contents / "Resources" / "media").is_dir():
                    raise RuntimeError("Packaged Ardour is missing bundled media (check bsdtar extraction)")
                package_application(installation=bundles[0], output=installation)
            finally:
                subprocess.run(["/usr/bin/hdiutil", "detach", str(mount)], check=True)
        write_manifest(stage, commit, "macOS", "Upstream tools/osx_packaging/osx_build --public DMG")

    if args.target in ("windows", "all") and not recovered_windows:
        # The existing VirtioFS share is ONLY Builds/CreativeSuite/Linux.
        # Stage a source snapshot there; the guest copies it onto ext4 before
        # compiling so compilation does not write into the host source tree.
        import tarfile
        shared = cinterforgeBuildsRootPath(cinterforge) / "CreativeSuite" / "Linux"
        shared.mkdir(parents=True, exist_ok=True)
        archive = shared / "Ardour" / "cfaudio-source.tar.gz"
        archive.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive, "w:gz") as bundle:
            for file in source.rglob("*"):
                relative_parts = file.relative_to(source).parts
                if (
                    ".git" in relative_parts
                    or relative_parts[0] == "build"
                    or not file.is_file()
                    or file.is_symlink()
                ):
                    continue
                bundle.add(file, arcname="cfaudio/" + file.relative_to(source).as_posix())
        guest_runner = next(parent for parent in script.parents if parent.name == "creativeSuite") / "shared" / "linux" / "linux_guest.py"
        run(sys.executable, str(guest_runner), "--repo-root", str(cinterforge),
            "--build-cfaudio", "--application", "Ardour")
        linux_output = shared / "Ardour" / "Linux-x64"
        # This guest job builds Windows. Linux is a separate product and may
        # legitimately be absent; recover it only if a real installation exists.
        linux_source = linux_output / "Install" if (linux_output / "Install").is_dir() else linux_output
        if linux_source.is_dir() and any(
            item.is_file() and item.name.startswith("cfaudio") for item in linux_source.rglob("*")
        ):
            linux_stage = builds / "Linux-x64"
            linux_stage.mkdir(parents=True, exist_ok=True)
            shutil.copytree(linux_source, linux_stage / "Install", dirs_exist_ok=True)
            write_manifest(linux_stage, commit, "Linux-x64", "Recovered existing Debian AMD64 installation; runtime smoke test not verified")
        else:
            print(f"[cfaudio] Linux installation not present (not required for Windows staging): {linux_output}", flush=True)
        output = shared / "Ardour" / "Windows"
        if not output.is_dir():
            raise RuntimeError(f"Windows guest returned without an output directory: {output}")
        install_output = output / "Install"
        if not install_output.is_dir():
            raise RuntimeError(f"Windows guest did not create Install: {install_output}")
        verify_windows_binaries(install_output)
        stage = builds / "Windows"
        stage.mkdir(parents=True, exist_ok=True)
        shutil.copytree(install_output, stage / "Install", dirs_exist_ok=True)
        write_manifest(stage, commit, "Windows-x64", "MinGW cross-build; installer packaging not verified")
    if args.target == "mac":
        publish_mac_payload(product_build)
    print("[cfaudio] Requested build targets completed; inspect staged artifacts and manifests", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, subprocess.CalledProcessError, RuntimeError) as error:
        print(f"[cfaudio] ERROR: {error}", file=sys.stderr)
        sys.exit(1)
