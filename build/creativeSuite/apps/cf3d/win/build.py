#!/usr/bin/env python3
"""Attempt Blender Windows native source build via Linux-hosted clang-cl."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys

SUITE = next(p for p in Path(__file__).resolve().parents if p.name == 'creativeSuite')
sys.path.insert(0, str(SUITE))
from container import run_mounted_image
from setup import IMAGE, backend


def main():
    docker, env, source = backend()
    built = Path(os.environ['CINTER_BUILT_ROOT']).resolve()
    deps = built / 'Dependencies'
    if not (deps / 'lib-windows_x64/.git').is_dir():
        raise RuntimeError('Blender Windows libraries missing: run ./Cinter setup cf3d win')
    if not (deps / 'Microsoft/sdk/include').is_dir():
        raise RuntimeError('Microsoft SDK missing: run ./Cinter setup cf3d win')
    build, output = built / 'Build', built / 'Output'
    build.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    toolchain = deps / 'clang-msvc.cmake'
    toolchain.write_text('''set(CMAKE_SYSTEM_NAME Windows)
set(CMAKE_SYSTEM_PROCESSOR AMD64)
set(CMAKE_C_COMPILER clang-cl)
set(CMAKE_CXX_COMPILER clang-cl)
set(CMAKE_LINKER lld-link)
# clang-cl on Linux otherwise picks the ELF GNU linker for its link phase.
# Force the COFF/MSVC linker for executables, libraries and CMake probes.
set(CMAKE_EXE_LINKER_FLAGS_INIT "-fuse-ld=lld")
set(CMAKE_SHARED_LINKER_FLAGS_INIT "-fuse-ld=lld")
set(CMAKE_MODULE_LINKER_FLAGS_INIT "-fuse-ld=lld")
set(CMAKE_C_COMPILER_TARGET x86_64-pc-windows-msvc)
set(CMAKE_CXX_COMPILER_TARGET x86_64-pc-windows-msvc)
set(CMAKE_RC_COMPILER llvm-rc)
set(CMAKE_MT llvm-mt)
''')
    script = r'''set -euo pipefail
[ "$(uname -m)" = x86_64 ] || exit 2
# xwin's output is a real Microsoft SDK/VC CRT, not MinGW headers.
export INCLUDE="/deps/Microsoft/crt/include;/deps/Microsoft/sdk/include/ucrt;/deps/Microsoft/sdk/include/shared;/deps/Microsoft/sdk/include/um"
export LIB="/deps/Microsoft/crt/lib/x86_64;/deps/Microsoft/sdk/lib/ucrt/x86_64;/deps/Microsoft/sdk/lib/um/x86_64"
printf 'int main(void) { return 0; }\n' > /build/sdk-smoke.c
# Even in clang-cl mode, Linux-hosted Clang otherwise calls GNU ld.
# Force lld-link and verify the resulting executable is PE/COFF.
clang-cl --target=x86_64-pc-windows-msvc -fuse-ld=lld /build/sdk-smoke.c \
  /Fe:/build/sdk-smoke.exe /link /machine:x64
file /build/sdk-smoke.exe | grep -q PE32+
printf '[cf3d][win] SDK/MSVC ABI cross-link verified\n'
cmake -S /source -B /build -G Ninja \
  -DCMAKE_TOOLCHAIN_FILE=/deps/clang-msvc.cmake \
  -DCMAKE_BUILD_TYPE=Release -DLIBDIR=/deps/lib-windows_x64 \
  -DCMAKE_INSTALL_PREFIX=/output \
  -C /source/build_files/cmake/config/blender_release.cmake
printf '[cf3d][win] Compiling Windows Blender source\n'
cmake --build /build --parallel "$(nproc)"
cmake --install /build
find /output -name cf3d.exe -type f -size +0c -print | grep -q . || {
  echo '[cf3d][win] No installed Windows cf3d.exe found' >&2; exit 8;
}
'''
    run_mounted_image(engine=docker, image=IMAGE, platform='linux/amd64',
                      mounts=((source, '/source', True), (deps, '/deps', False),
                              (build, '/build', False), (output, '/output', False)),
                      variables={}, command=('/bin/bash', '-ec', script), env=env,
                      label='Blender Windows clang-cl CMake source build')
    recipe_path = Path(__file__).with_name('recipe.json')
    recipe = json.loads(recipe_path.read_text())
    recipe.setdefault('readiness', {}).setdefault('setup_result', {})['configured'] = True
    recipe['readiness']['configured'] = True
    recipe.setdefault('configure', {})['observed_result'] = {'configured': True}
    recipe_path.write_text(json.dumps(recipe, indent=2) + '\n')
    print(f'[cf3d][win] SOURCE BUILD DONE {output}', flush=True)


if __name__ == '__main__':
    main()
