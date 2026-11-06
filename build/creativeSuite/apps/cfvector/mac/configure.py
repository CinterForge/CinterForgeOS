"""Inkscape-specific CMake configuration using Setup's pinned ICU metadata."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess


def append_cmake_options(command: list[str], built: Path) -> dict[str, str]:
    """Expose the versioned Homebrew ICU to CMake's pkg-config checks."""
    brew = shutil.which('brew')
    if not brew:
        raise RuntimeError('cfvector requires Homebrew ICU 79 prepared by Setup')
    prefix = Path(subprocess.check_output(
        [brew, '--prefix', 'icu4c@79'], text=True).strip()).resolve()
    pc_dir = prefix / 'lib/pkgconfig'
    if not (pc_dir / 'icu-uc.pc').is_file():
        raise RuntimeError(f'cfvector ICU pkg-config metadata missing: {pc_dir / "icu-uc.pc"}; run ./Cinter setup cfvector mac')
    # pkg-config inserts CommandLineTools SDK paths as -isystem ahead of
    # CMAKE_CXX_FLAGS.  A libc++ path also specified with -isystem is therefore
    # too late: <cmath> finds the CLT math.h rather than libc++ math.h.
    # Use -I (which Clang searches before all -isystem paths) for the matching
    # Xcode libc++ forwarding headers and C SDK headers.  Keep the SDK
    # selection coherent rather than mixing Xcode libc++ with CLT C headers.
    sdk = Path(subprocess.check_output(
        ['xcrun', '--sdk', 'macosx', '--show-sdk-path'], text=True).strip()).resolve()
    cxx_headers = sdk / 'usr/include/c++/v1'
    c_headers = sdk / 'usr/include'
    if not (cxx_headers / 'cmath').is_file() or not (c_headers / 'math.h').is_file():
        raise RuntimeError(f'Incomplete Xcode SDK headers: {sdk}')
    command.extend((
        '-DCMAKE_OSX_SYSROOT=' + str(sdk),
        '-DCMAKE_CXX_FLAGS=-nostdinc++ -I' + str(cxx_headers) + ' -I' + str(c_headers),
    ))
    print(f'[Creative Suite mac][cfvector] Unified Xcode SDK: {sdk}', flush=True)
    env = os.environ.copy()
    env['PKG_CONFIG_PATH'] = os.pathsep.join(filter(None, (
        str(pc_dir), env.get('PKG_CONFIG_PATH', '')
    )))
    subprocess.run(['pkg-config', '--atleast-version=79', 'icu-uc'],
                   env=env, check=True)
    print(f'[Creative Suite mac][cfvector] CMake ICU metadata: {pc_dir}', flush=True)
    return env


def build_environment(built: Path) -> dict[str, str]:
    """Reconstruct application-owned pkg-config environment for Build/Install."""
    command: list[str] = []
    return append_cmake_options(command, built)
