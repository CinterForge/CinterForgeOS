#!/usr/bin/env python3
"""Provision Blender Windows source-build inputs in the existing Linux backend."""
from __future__ import annotations
import os
from pathlib import Path
import sys

SUITE = next(p for p in Path(__file__).resolve().parents if p.name == 'creativeSuite')
sys.path.insert(0, str(SUITE))
from container import build_cached_image, run_mounted_image

IMAGE = 'cinterforge-cf3d-msvc-cross:debian13-amd64'
LIB_REF = 'cf3d-v5.0-release'
LIB_SHA = '854341cfd7'
LIB_URL = 'https://projects.cf3d.org/cf3d/lib-windows_x64.git'


def backend():
    cinter = Path(os.environ['CINTER_HOPE_ROOT']).resolve()
    source = cinter.parent / 'CinterForge3D'
    if not (source / 'CMakeLists.txt').is_file():
        raise RuntimeError(f'Blender source missing: {source}')
    sys.path.insert(0, str(SUITE / 'apps/cinterforge-os/linux'))
    from managed_backend import ensure_backend
    docker, env = ensure_backend(cinter.parent / 'Builds/CreativeSuite/Built/cinterforge-os/linux/2026/Ubuntu')
    return docker, env, source


def main():
    docker, env, source = backend()
    version = os.environ.get('CINTER_INSTALL_VERSION', '2026')
    built = Path(os.environ.get('CINTER_BUILT_ROOT',
        source.parent / 'Builds/CreativeSuite/Built/cf3d/win' / version)).resolve()
    dependencies = built / 'Dependencies'
    dependencies.mkdir(parents=True, exist_ok=True)
    dockerfile = dependencies / 'Dockerfile.cf3d-windows'
    content = r'''FROM debian:13-slim
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
  ca-certificates git git-lfs build-essential cmake ninja-build \
  clang-19 clang-tools-19 lld-19 llvm-19 \
  python3 python-is-python3 pkgconf file curl \
  && rm -rf /var/lib/apt/lists/*
# Rust crates with native build scripts require the C linker supplied by
# build-essential. Debian 13 supplies Rust 1.85.1. xwin 0.10.0's locked dependency graph
# requires Rust >= 1.88; pin a newer toolchain instead of downgrading crates.
ENV RUSTUP_HOME=/opt/rustup CARGO_HOME=/opt/cargo
ENV PATH="/opt/cargo/bin:/opt/xwin/bin:${PATH}"
RUN curl --fail --location --silent --show-error https://sh.rustup.rs | \
    sh -s -- -y --profile minimal --default-toolchain 1.90.0 \
    && rustc --version \
    && cargo install xwin --version 0.10.0 --locked --root /opt/xwin \
    && /opt/xwin/bin/xwin --version
# clang-cl is shipped by clang-tools-19, not Debian's clang metapackage.
# Resolve and check the LLVM 19 tools required by the Windows toolchain.
RUN set -eu; \
    for name in clang-cl lld-link llvm-rc llvm-mt; do \
      found=""; \
      for candidate in \
        "/usr/bin/$name" \
        "/usr/bin/${name}-19" \
        "/usr/lib/llvm-19/bin/$name"; do \
        if [ -x "$candidate" ]; then found="$candidate"; break; fi; \
      done; \
      if [ -z "$found" ]; then \
        echo "[cf3d][win] ERROR: required LLVM tool $name is absent" >&2; \
        exit 7; \
      fi; \
      ln -sf "$found" "/usr/local/bin/$name"; \
      printf '[cf3d][win] LLVM tool %s -> %s\n' "$name" "$found"; \
    done
'''
    if not dockerfile.is_file() or dockerfile.read_text() != content:
        dockerfile.write_text(content)
    build_cached_image(engine=docker, image=IMAGE, dockerfile=dockerfile,
                       context=dependencies, platform='linux/amd64', architecture='amd64',
                       env=env, label='Blender Windows clang-cl build tools')
    script = r'''set -euo pipefail
[ "$(uname -m)" = x86_64 ] || { echo 'Linux AMD64 host required' >&2; exit 2; }
command -v clang-cl
command -v lld-link
command -v xwin
printf '[cf3d][win] Compiler/linker tools verified\n'
if [ ! -d /deps/lib-windows_x64/.git ]; then
  if [ -e /deps/lib-windows_x64 ] && [ -n "$(ls -A /deps/lib-windows_x64)" ]; then
    echo 'Existing Windows Blender dependency folder is not a Git checkout' >&2; exit 3
  fi
  git clone --branch "$BLENDER_LIB_REF" --single-branch "$BLENDER_LIB_URL" /deps/lib-windows_x64
fi
cd /deps/lib-windows_x64
git fetch origin "$BLENDER_LIB_REF"
git checkout --detach "$BLENDER_LIB_SHA"
git lfs pull
[ -d /deps/lib-windows_x64/.git ] || exit 3
# xwin downloads Microsoft SDK/CRT development inputs, not Blender binaries.
if [ ! -d /deps/Microsoft/sdk/include ] || [ ! -d /deps/Microsoft/crt/include ]; then
  mkdir -p /deps/Microsoft
  xwin --accept-license --arch x86_64 --cache-dir /deps/Microsoft/Cache \
    splat --output /deps/Microsoft
fi
[ -d /deps/Microsoft/sdk/include ] || { echo 'Windows SDK missing' >&2; exit 4; }
[ -d /deps/Microsoft/crt/include ] || { echo 'MSVC CRT headers missing' >&2; exit 4; }
[ -d /deps/Microsoft/sdk/lib/um/x86_64 ] || { echo 'Windows SDK libraries missing' >&2; exit 4; }
[ -d /deps/Microsoft/crt/lib/x86_64 ] || { echo 'MSVC CRT libraries missing' >&2; exit 4; }
printf '[cf3d][win] Upstream Windows dependencies: %s\n' "$(git rev-parse HEAD)"
printf '[cf3d][win] SDK and libraries present; only a successful source Build establishes READY\n'
'''
    run_mounted_image(engine=docker, image=IMAGE, platform='linux/amd64',
                      mounts=((dependencies, '/deps', False),),
                      variables={'BLENDER_LIB_REF': LIB_REF, 'BLENDER_LIB_SHA': LIB_SHA,
                                 'BLENDER_LIB_URL': LIB_URL},
                      command=('/bin/bash', '-ec', script), env=env,
                      label='Blender Windows SDK and pinned upstream dependencies')


if __name__ == '__main__':
    main()
