#!/usr/bin/env python3
"""Bundle non-platform ELF dependencies from the SAME AMD64 image used to build.

Run inside the dependency image with the installed application mounted at /payload.
This does not install anything into CFOS or modify the FEX root filesystem.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path('/payload')
DEST = ROOT / 'lib/runtime'
# These belong to the target execution environment (FEX/KDE/Venus), not to
# the application. In particular, bundling Ubuntu libGL/libvulkan or glibc
# would override the accelerated graphics implementation inside CinterForgeOS.
PLATFORM = re.compile(
    r'^(?:'
    r'ld-linux[^/]*'
    r'|lib(?:c|m|pthread|dl|rt|resolv|anl|util|crypt|nss_[^.]+)\.so(?:\..*)?'
    r'|lib(?:GL|GLX|EGL|GLESv[0-9]|OpenGL|vulkan|drm|gbm|wayland-client|X11|xcb)\.so(?:\..*)?'
    r')$'
)

RESOLVED = re.compile(r'^\s*(\S+)\s+=>\s+(/\S+)\s+\(0x[0-9a-fA-F]+\)')
MISSING = re.compile(r'^\s*(\S+)\s+=>\s+not found\s*$')


def elf(path: Path) -> bool:
    try:
        with path.open('rb') as stream:
            return stream.read(4) == b'\x7fELF'
    except OSError:
        return False


def main() -> None:
    if not ROOT.is_dir() or not (ROOT / 'lib').is_dir():
        raise RuntimeError(f'Invalid installed Linux app at {ROOT}')
    binaries = sorted(p for parent in (ROOT / 'bin', ROOT / 'lib') if parent.exists()
                      for p in parent.rglob('*') if p.is_file() and elf(p))
    if not binaries:
        raise RuntimeError('Installed application has no ELF files to analyze')
    DEST.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env['LD_LIBRARY_PATH'] = ':'.join(str(p) for p in (
        ROOT / 'lib', ROOT / 'lib/ardour9', ROOT / 'lib/ardour9/LV2', DEST) if p.exists())
    copied = {}
    unresolved = set()
    for binary in binaries:
        result = subprocess.run(['ldd', str(binary)], text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                env=env, check=False)
        for line in result.stdout.splitlines():
            missing = MISSING.match(line)
            if missing:
                unresolved.add((str(binary.relative_to(ROOT)), missing.group(1)))
                continue
            match = RESOLVED.match(line)
            if not match:
                continue
            soname, location = match.groups()
            if PLATFORM.match(soname):
                continue
            source = Path(location)
            if not source.is_file():
                unresolved.add((str(binary.relative_to(ROOT)), soname))
                continue
            if source.is_relative_to(ROOT):
                continue
            old = copied.get(soname)
            if old and old != source.resolve():
                raise RuntimeError(f'Conflicting runtime library resolution: {soname}: {old} vs {source}')
            target = DEST / soname
            if not target.exists():
                shutil.copy2(source.resolve(), target)
            copied[soname] = source.resolve()
    if unresolved:
        detail = '\n'.join(f'  {binary}: {soname}' for binary, soname in sorted(unresolved))
        raise RuntimeError('Unresolved installed ELF dependencies in build image:\n' + detail)
    print(f'[Linux runtime packaging] {len(binaries)} ELF files checked; '
          f'{len(copied)} external runtime SONAMEs bundled in lib/runtime', flush=True)


if __name__ == '__main__':
    main()
