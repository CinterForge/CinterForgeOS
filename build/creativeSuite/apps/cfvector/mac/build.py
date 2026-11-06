#!/usr/bin/env python3
"""cfphoto-owned pinned Rust/Node Tauri build and application assembly."""
from __future__ import annotations

import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tomllib

def run(arguments: list[str], **kwargs) -> None:
    print("[Creative Suite mac] " + " ".join(arguments), flush=True)
    subprocess.run(arguments, check=True, **kwargs)

def build(source: Path, built: Path, product_name: str) -> None:
    """Compile supplied Tauri v2/Rust source, retaining its upstream lockfiles."""
    working = built / "Build" / "Source"
    output = built / "Output"
    output.mkdir(parents=True, exist_ok=True)
    if not working.exists():
        shutil.copytree(source, working, ignore=shutil.ignore_patterns(
            "node_modules", "target", "dist", ".git", "__pycache__", ".DS_Store"))
    # Respect cfphoto's committed rust-toolchain.toml rather than whichever
    # rustc version the user's shell happens to select.
    with (working / "src-tauri/rust-toolchain.toml").open("rb") as handle:
        rust_channel = tomllib.load(handle)["toolchain"]["channel"]
    node_prefix = subprocess.check_output(
        ["brew", "--prefix", "node@24"], text=True
    ).strip()
    node_bin = Path(node_prefix) / "bin"
    if not (node_bin / "node").is_file():
        raise RuntimeError("Node 24 LTS not installed; run cfphoto Setup first")
    # Resolve *actual* binaries for the upstream pinned toolchain. A globally
    # installed rustc/cargo (e.g. Homebrew rustc 1.86) can precede rustup's
    # proxies on PATH: RUSTUP_TOOLCHAIN alone cannot override those binaries.
    # Tauri invokes cargo as a child of npm; it must inherit this same PATH.
    rustup = shutil.which("rustup")
    if not rustup:
        raise RuntimeError("rustup missing; run cfphoto Setup first")
    toolchain_binaries = {}
    for name in ("rustc", "cargo"):
        resolved = subprocess.check_output(
            [rustup, "which", "--toolchain", rust_channel, name],
            text=True,
        ).strip()
        binary = Path(resolved)
        if not binary.is_file():
            raise RuntimeError(f"Pinned Rust toolchain missing {name}: {binary}")
        toolchain_binaries[name] = binary
    rust_bin = toolchain_binaries["rustc"].parent
    if toolchain_binaries["cargo"].parent != rust_bin:
        raise RuntimeError("Pinned rustc and cargo are in different toolchain directories")
    environment = os.environ.copy()
    environment["PATH"] = os.pathsep.join((
        str(rust_bin), str(node_bin), environment.get("PATH", "")))
    environment["RUSTUP_TOOLCHAIN"] = rust_channel
    environment["RUSTC"] = str(toolchain_binaries["rustc"])
    environment["CARGO"] = str(toolchain_binaries["cargo"])
    # Fail before npm installs anything if the effective compiler is wrong.
    version = subprocess.check_output(
        ["rustc", "--version"], cwd=working, env=environment, text=True
    ).strip()
    if not version.startswith(f"rustc {rust_channel}.") and not version.startswith(f"rustc {rust_channel} "):
        raise RuntimeError(f"cfphoto requires Rust {rust_channel}; selected {version}")
    print(f"[Creative Suite mac] Pinned cfphoto Rust: {version} ({rust_bin})", flush=True)
    run(["cargo", "--version"], cwd=working, env=environment)
    run(["npm", "ci", "--no-audit", "--no-fund"], cwd=working, env=environment)
    environment["CARGO_TARGET_DIR"] = str(built / "Build" / "CargoTarget")
    run(["npm", "exec", "--", "tauri", "build", "--bundles", "app"],
        cwd=working, env=environment)
    bundles = list((built / "Build" / "CargoTarget" / "release" / "bundle" / "macos").glob("*.app"))
    if len(bundles) != 1:
        raise RuntimeError(f"Expected one Tauri macOS app bundle, found {len(bundles)}: {bundles}")
    source_bundle = bundles[0]
    info_file = source_bundle / "Contents/Info.plist"
    if not info_file.is_file():
        raise RuntimeError(f"Tauri bundle lacks Info.plist: {source_bundle}")
    with info_file.open("rb") as handle:
        info = plistlib.load(handle)
    executable = source_bundle / "Contents/MacOS" / str(info.get("CFBundleExecutable", ""))
    if not executable.is_file():
        raise RuntimeError(f"Missing bundle executable: {executable}")
    destination = output / f"{product_name}.app"
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source_bundle, destination, symlinks=True)
    print(f"[Creative Suite mac] BUILT rapidraw: {destination}", flush=True)
    print("[Creative Suite mac] Verify dependency closure and notarization before distribution.", flush=True)
