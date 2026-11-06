#!/usr/bin/env python3
"""GIMP-owned Meson, pinned GExiv2 and Vala configuration for macOS.

The shared pipeline invokes these hooks but never interprets GIMP's
upstream dependency layout or regenerates GIMP-specific build files.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import signal


def run(arguments: list[str], **kwargs) -> None:
    print("[Creative Suite mac] " + " ".join(arguments), flush=True)
    subprocess.run(arguments, check=True, **kwargs)


def add_pinned_gexiv2_typelib(environment: dict[str, str], built: Path) -> None:
    """Invoke the application's GExiv2 typelib preparation."""
    setup_file = Path(__file__).with_name("setup.py")
    spec = importlib.util.spec_from_file_location("cinterforge_gimp_mac_setup", setup_file)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load GIMP macOS setup: {setup_file}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.add_pinned_gexiv2_typelib(environment, built)


def configure(source: Path, built: Path) -> None:
    work = built / "Build"
    output = built / "Output"
    work.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    command = ["meson", "setup", str(work), str(source),
               "--prefix", str(output), "--buildtype=release"]
    # GIMP 3.3 requires the 0.14 GExiv2 API; Homebrew ships incompatible 0.16.
    compatible = built / "Dependencies" / "gexiv2-0.14.6" / "Install"
    module_file = compatible / "lib/pkgconfig/gexiv2.pc"
    if not module_file.is_file():
        raise RuntimeError(f"Missing upstream-compatible GExiv2: {module_file}; run GIMP Setup")
    environment = os.environ.copy()
    add_pinned_gexiv2_typelib(environment, built)
    environment["PKG_CONFIG_PATH"] = os.pathsep.join(
        [str(compatible / "lib/pkgconfig"),
         str(compatible / "share/pkgconfig"),
         environment.get("PKG_CONFIG_PATH", "")]
    )
    # Homebrew libarchive is keg-only on macOS; its libarchive.pc is not
    # under the default pkg-config search directories.
    archive_prefix = Path(subprocess.check_output(
        ["brew", "--prefix", "libarchive"], text=True
    ).strip())
    archive_pc = archive_prefix / "lib/pkgconfig/libarchive.pc"
    if not archive_pc.is_file():
        raise RuntimeError(f"Missing libarchive pkg-config metadata: {archive_pc}; run GIMP Setup")
    environment["PKG_CONFIG_PATH"] = os.pathsep.join(filter(None, (
        str(archive_pc.parent), environment["PKG_CONFIG_PATH"]
    )))
    # Homebrew libwmf's configure/pkg-config discovery can succeed while
    # GIMP's file-wmf target receives no libwmf header search path.
    # Pass the actual dependency's include prefix to Meson so it is
    # retained across reconfiguration (environment CFLAGS is not).
    wmf_prefix = Path(subprocess.check_output(
        ["brew", "--prefix", "libwmf"], text=True
    ).strip())
    wmf_header = wmf_prefix / "include/libwmf/api.h"
    if not wmf_header.is_file():
        raise RuntimeError(f"GIMP WMF development header missing: {wmf_header}; run GIMP Setup")
    wmf_lib = wmf_prefix / "lib"
    # libwmf-config contributes -lwmf/-lwmflite but does not supply its
    # keg-only Homebrew library directory to the generated linker line.
    # Verify the actual libraries and retain -L across Meson reconfigure.
    for library in ("libwmf", "libwmflite"):
        if not any((wmf_lib / (library + extension)).is_file()
                   for extension in (".dylib", ".a")):
            raise RuntimeError(
                f"GIMP WMF library {library} missing under {wmf_lib}; run GIMP Setup"
            )
    # libwmf-config also injects -ljpeg (and -lexpat/-lz) into its link
    # flags. Homebrew jpeg-turbo is keg-only, so -L for libwmf alone is
    # insufficient even though the GIMP JPEG target itself resolves.
    jpeg_prefix = Path(subprocess.check_output(
        ["brew", "--prefix", "jpeg-turbo"], text=True
    ).strip())
    jpeg_lib = jpeg_prefix / "lib"
    if not any((jpeg_lib / ("libjpeg" + extension)).is_file()
               for extension in (".dylib", ".a")):
        raise RuntimeError(
            f"GIMP WMF transitive JPEG library missing under {jpeg_lib}; run GIMP Setup"
        )
    command.append("-Dc_args=-I" + str(wmf_prefix / "include"))
    command.append("-Dc_link_args=-L" + str(wmf_lib) + " -L" + str(jpeg_lib))
    # Meson resolves vapigen to an ABSOLUTE executable during setup.
    # Changing PATH only during Ninja compilation cannot affect that
    # configured command. Supply the private GEGL-aware vapigen through a
    # per-app Meson native file instead (no upstream/Homebrew mutation).
    vapi = built / "Dependencies/gegl-vala/share/vala/vapi/gegl-0.4.vapi"
    if not vapi.is_file() or "namespace Gegl" not in vapi.read_text():
        raise RuntimeError(f"GEGL VAPI missing: {vapi}; run GIMP Setup")
    import shutil as _shutil
    import shlex
    vapigen = _shutil.which("vapigen")
    if not vapigen:
        raise RuntimeError("vapigen missing; run GIMP Setup")
    tools_dir = built / "Dependencies/gegl-vala/Tools/bin"
    tools_dir.mkdir(parents=True, exist_ok=True)
    wrapper = tools_dir / "vapigen"
    wrapper.write_text("#!/bin/sh\nexec " + shlex.quote(vapigen) +
                       " --vapidir=" + shlex.quote(str(vapi.parent)) + ' "$@"\n')
    wrapper.chmod(0o755)
    # vapigen and valac are different Meson binaries. The generated
    # cfimage-3.0.vapi references Babl and Gegl, so valac also needs the
    # private VAPI search path. A vapigen-only wrapper cannot fix that.
    vala = _shutil.which("valac")
    if not vala:
        raise RuntimeError("valac missing; run GIMP Setup")
    vala_wrapper = tools_dir / "valac"
    # GEGL's generated VAPI references Babl.Object.  The upstream Vala
    # compile command supplies --pkg=gegl-0.4 but omits --pkg=babl-0.1.
    # A vapidir makes a package *discoverable*, not *loaded*: explicitly
    # load Babl for every Vala compile without editing GIMP sources.
    vala_wrapper.write_text("#!/bin/sh\nexec " + shlex.quote(vala) +
                            " --vapidir=" + shlex.quote(str(vapi.parent)) +
                            ' --pkg=babl-0.1 "$@"\n')
    vala_wrapper.chmod(0o755)
    native = tools_dir / "cfimage-vala.ini"
    native.write_text("[binaries]\nvapigen = '" + str(wrapper).replace("'", "\\'") +
                      "'\nvala = '" + str(vala_wrapper).replace("'", "\\'") + "'\n")
    command.extend(("--native-file", str(native)))
    environment["PATH"] = str(tools_dir) + os.pathsep + environment.get("PATH", "")
    # Reconfigure an existing build without deleting persistent objects.
    if (work / "build.ninja").is_file():
        command.append("--reconfigure")
    run(command, env=environment)
    validate_mesons_vapigen(work, built, command, environment)


def validate_mesons_vapigen(work: Path, built: Path, command: list[str], environment: dict[str, str]) -> None:
    # Existing Meson build directories can retain a previously discovered
    # absolute /opt/homebrew/bin/vapigen after --reconfigure. Verify the
    # generated Ninja command actually invokes the private wrapper.
    ninja = work / "build.ninja"
    wrapper = built / "Dependencies/gegl-vala/Tools/bin/vapigen"
    if not ninja.is_file():
        raise RuntimeError(f"GIMP Meson build file missing: {ninja}")
    vala_wrapper = built / "Dependencies/gegl-vala/Tools/bin/valac"
    if str(wrapper) not in ninja.read_text() or str(vala_wrapper) not in ninja.read_text():
        # Wipe is necessary only when Meson preserves an old absolute
        # program lookup. In that case rebuilding is preferable to another
        # guaranteed 2000-target failure; dependencies remain untouched.
        wipe = [arg for arg in command if arg != "--reconfigure"] + ["--wipe"]
        print("[Creative Suite mac] Meson cached old vapigen; regenerating build definitions", flush=True)
        run(wipe, env=environment)
        if str(wrapper) not in ninja.read_text() or str(vala_wrapper) not in ninja.read_text():
            raise RuntimeError(f"Meson did not select private GEGL-aware Vala tools: {wrapper}, {vala_wrapper}")


def build(built: Path) -> None:
    work = built / "Build"
    # Meson/GObject Introspection discovers .gir files from XDG_DATA_DIRS,
    # NOT PKG_CONFIG_PATH.  The pinned GExiv2 is installed into the app's
    # private dependency prefix, outside the Homebrew GIR search path.
    gexiv2 = built / "Dependencies" / "gexiv2-0.14.6" / "Install"
    gir = gexiv2 / "share/gir-1.0/GExiv2-0.10.gir"
    if not gir.is_file():
        raise RuntimeError(f"GIMP pinned GExiv2 introspection data missing: {gir}; run GIMP Setup")
    environment = os.environ.copy()
    add_pinned_gexiv2_typelib(environment, built)
    environment["XDG_DATA_DIRS"] = os.pathsep.join(filter(None, (
        str(gexiv2 / "share"),
        environment.get("XDG_DATA_DIRS", "/opt/homebrew/share:/usr/local/share:/usr/share"),
    )))
    # GI_GIR_PATH is recognized by versions of g-ir-scanner supporting
    # explicit extra GIR directories. XDG_DATA_DIRS also works with older
    # versions and is required for standard desktop data discovery.
    environment["GI_GIR_PATH"] = os.pathsep.join(filter(None, (
        str(gir.parent), environment.get("GI_GIR_PATH", "")
    )))
    print(f"[Creative Suite mac] GIMP GIR dependency: {gir}", flush=True)
    # A moved Meson build tree retains absolute source/build paths in build.ninja.
    # Fail before launching a build-tree GIMP against stale paths rather than
    # hanging in the splash generator. Preserve existing binaries and deps.
    ninja_file = work / "build.ninja"
    if not ninja_file.is_file():
        raise RuntimeError(f"GIMP Meson build definition missing: {ninja_file}; run Cinter configure cfimage")
    coredata = work / "meson-private" / "coredata.dat"
    if not coredata.is_file():
        raise RuntimeError(f"GIMP Meson configuration missing: {coredata}; reconfigure cfimage")
    # The compiled generator must remain alongside its build-tree libraries.
    # The original GIMP in-build wrapper is responsible for their runtime paths.
    if not (work / "app").is_dir() or not (work / "libgimpbase").is_dir():
        raise RuntimeError(f"Incomplete relocated GIMP build tree: {work}; reconfigure cfimage")
    # The build-tree gimp-console must keep Meson's paths, not apply the
    # installed .app bundle relocation from app/main.c.
    environment["GIMP_NO_WRAPPER"] = "1"
    # GIMP generates its splash by launching gimp-console from Ninja. On macOS
    # that child can open a GUI or block indefinitely; a hung generator must
    # never leave the entire Creative Suite lifecycle waiting forever.
    # GIMP's macOS in-build wrapper mutates LC_RPATH on shared build-tree
    # binaries under a global flock. Parallel Ninja jobs can contend with
    # splash generation and Python plug-in initialization. Serialize this
    # macOS compile to avoid the build-time executable/rpath race.
    compile_command = ["meson", "compile", "-j", "1", "-C", str(work)]
    print("[Creative Suite mac] " + " ".join(compile_command), flush=True)
    process = subprocess.Popen(compile_command, env=environment,
                               start_new_session=True)
    timeout = int(os.environ.get("CINTER_GIMP_COMPILE_TIMEOUT", "1800"))
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        diagnostic = built / "Logs" / "gimp-compile-timeout.txt"
        diagnostic.parent.mkdir(parents=True, exist_ok=True)
        ps = subprocess.run(["ps", "-axo", "pid,ppid,etime,state,command"],
                            capture_output=True, text=True, check=False)
        matching = "\n".join(line for line in ps.stdout.splitlines()
                             if any(token in line for token in
                                    ("gimp-console", "gimp-splash", "python-fu", "ninja")))
        diagnostic.write_text(f"Timed out after {timeout}s: {' '.join(compile_command)}\n"
                              f"Relevant processes:\n{matching}\n")
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        raise RuntimeError(f"GIMP build stalled; process diagnostics: {diagnostic}") from exc
    if process.returncode:
        raise subprocess.CalledProcessError(process.returncode, compile_command)
    run(["meson", "install", "-C", str(work)], env=environment)
