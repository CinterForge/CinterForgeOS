#!/usr/bin/env python3
"""Native macOS Creative Suite source builds from the supplied upstream trees.

This adapter never downloads a prebuilt application. It deliberately fails when
upstream dependencies or complete relocatable .app packaging are unavailable.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys

SUITE_ROOT = Path(__file__).resolve().parents[2]


def recipe_for(app: str) -> dict:
    """Load the maintained application identity, checkout and build contract."""
    if not app or Path(app).name != app or app in (".", ".."):
        raise RuntimeError(f"Invalid Creative Suite application: {app!r}")
    path = SUITE_ROOT / "apps" / app / "mac" / "recipe.json"
    if not path.is_file():
        raise RuntimeError(f"Missing macOS application recipe: {path}")
    with path.open(encoding="utf-8") as stream:
        recipe = json.load(stream)
    identity = recipe.get("identity", {})
    if identity.get("application") != app or identity.get("platform") != "mac":
        raise RuntimeError(f"Invalid macOS recipe identity: {path}")
    return recipe


def app_module(app: str, module: str):
    """Load application-owned build hooks without legacy upstream names."""
    if str(SUITE_ROOT) not in sys.path:
        sys.path.insert(0, str(SUITE_ROOT))
    return importlib.import_module(f"apps.{app}.mac.{module}")


def product_name_for(app: str) -> str:
    recipe = recipe_for(app)
    for section, key in (("branding", "product_name"), ("identity", "product_name")):
        name = recipe.get(section, {}).get(key)
        if isinstance(name, str) and name.strip():
            return name.strip()
    # Existing products are defined by the Creative Suite catalog, not this runner.
    # Avoid guessing a distributable app name when no contract is present.
    raise RuntimeError(f"Missing product_name in macOS recipe for {app}")


def run(arguments: list[str], **kwargs) -> None:
    print("[Creative Suite mac] " + " ".join(arguments), flush=True)
    subprocess.run(arguments, check=True, **kwargs)


def inputs(app: str) -> tuple[Path, Path, Path]:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError("Native macOS source builds require Apple Silicon macOS")
    recipe = recipe_for(app)
    source_spec = recipe.get("source", {})
    checkout = source_spec.get("checkout_name")
    if not isinstance(checkout, str) or not checkout or Path(checkout).name != checkout or checkout in (".", ".."):
        raise RuntimeError(f"Invalid source checkout in recipe for {app}: {checkout!r}")
    # Central workspace resolver; repositories are siblings of CinterForge.
    cinter_root = SUITE_ROOT.parents[2] / "CinterForgeServer"
    if str(cinter_root) not in sys.path:
        sys.path.insert(0, str(cinter_root))
    from cf.io import CinterIO
    source = Path(CinterIO.cinterforgeSiblingRepoPath(checkout))
    built = (Path(os.environ["CINTER_BUILT_ROOT"]).expanduser().resolve()
             if os.environ.get("CINTER_BUILT_ROOT") else
             Path(CinterIO.cinterforgeBuildsRootPath()) / "CreativeSuite/Built" / app / "mac" /
             os.environ.get("CINTER_INSTALL_VERSION", "2026"))
    build_definition = source_spec.get("build_definition")
    if not isinstance(build_definition, str) or not build_definition or Path(build_definition).is_absolute() or ".." in Path(build_definition).parts:
        raise RuntimeError(f"Invalid build definition in recipe for {app}: {build_definition!r}")
    if not (source / build_definition).is_file():
        raise RuntimeError(f"Missing supplied source: {source / build_definition}")
    cinter = Path(os.environ.get("CINTER_HOPE_ROOT", SUITE_ROOT)).expanduser().resolve()
    return cinter, source, built


def toolchain(app: str) -> None:
    for name in recipe_for(app).get("preparation", {}).get("toolchain", []):
        if not shutil.which(name):
            raise RuntimeError(f"Missing build prerequisite: {name}. Prepare it during Setup.")
    run(["xcodebuild", "-version"])
    run(["xcrun", "--sdk", "macosx", "--show-sdk-path"])


def optional_hook(app: str, module: str, name: str):
    """Return an application-owned hook if the module/function is present."""
    module_path = SUITE_ROOT / "apps" / app / "mac" / (module + ".py")
    if not module_path.is_file():
        return None
    return getattr(app_module(app, module), name, None)


def application_environment(app: str, built: Path) -> dict[str, str]:
    """Resolve a complete application-owned environment for every CMake phase."""
    hook = optional_hook(app, "configure", "build_environment")
    environment = hook(built) if hook else os.environ.copy()
    if not isinstance(environment, dict):
        raise RuntimeError(f"{app} build_environment must return an environment dictionary")
    return environment


def configure(app: str, source: Path, built: Path) -> dict[str, str] | None:
    work, output = built / "Build", built / "Output"
    work.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    system = recipe_for(app).get("configure", {}).get("system")
    if system == "meson":
        hook = optional_hook(app, "configure", "configure")
        if hook is None:
            raise RuntimeError(f"Missing Meson configuration hook for {app}")
        hook(source, built)
        return None
    if system != "cmake":
        raise RuntimeError(f"Unsupported macOS configure system for {app}: {system}")
    command = ["cmake", "-S", str(source), "-B", str(work), "-G", "Ninja",
               "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_OSX_ARCHITECTURES=arm64",
               "-DCMAKE_INSTALL_PREFIX=" + str(output)]
    prepare = optional_hook(app, "configure", "prepare_source")
    if prepare:
        prepare(source)
    options = optional_hook(app, "configure", "append_cmake_options")
    options_environment = options(command, built) if options else None
    environment = application_environment(app, built)
    if options_environment is not None:
        environment.update(options_environment)
    preflight = optional_hook(app, "configure", "preflight")
    if preflight:
        preflight(source, built, environment)
    run(command, env=environment)
    postconfigure = optional_hook(app, "configure", "postconfigure")
    if postconfigure:
        postconfigure(built)
    return environment


def validate_bundle(bundle: Path, output: Path, product_name: str) -> None:
    expected = output / (product_name + ".app")
    if bundle != expected or not expected.is_dir():
        raise RuntimeError(f"Invalid packaged bundle: {bundle}; expected {expected}")
    info_file = bundle / "Contents/Info.plist"
    if not info_file.is_file():
        raise RuntimeError(f"Upstream bundle lacks Info.plist: {bundle}")
    with info_file.open("rb") as handle:
        info = plistlib.load(handle)
    executable = bundle / "Contents/MacOS" / str(info.get("CFBundleExecutable", ""))
    if not executable.is_file():
        raise RuntimeError(f"Upstream bundle executable missing: {executable}")


def build_application(
    app: str, source: Path, built: Path, product_name: str,
    environment: dict[str, str] | None = None,
) -> None:
    work, output = built / "Build", built / "Output"
    system = recipe_for(app).get("configure", {}).get("system")
    if system == "meson":
        hook = optional_hook(app, "configure", "build")
        if hook is None:
            raise RuntimeError(f"Missing Meson build hook for {app}")
        hook(built)
    elif system == "cmake":
        if environment is None:
            raise RuntimeError(f"{app} CMake environment missing after Configure")
        run(["cmake", "--build", str(work), "--parallel",
             str(max(1, (os.cpu_count() or 2) // 2))], env=environment)
        run(["cmake", "--install", str(work)], env=environment)
    else:
        raise RuntimeError(f"Unsupported macOS build system for {app}: {system}")

    package = optional_hook(app, "package", "package")
    if package is None:
        package = optional_hook(app, "stage", "package")
    if package:
        bundle = package(source, output, product_name)
        validate_bundle(bundle, output, product_name)
    else:
        apps = sorted(output.rglob("*.app"))
        apps = [item for item in apps if not any(parent.suffix == ".app" for parent in item.parents)]
        if len(apps) != 1:
            raise RuntimeError(
                f"{app} installed {len(apps)} app bundles into {output}; expected exactly one complete upstream macOS .app"
            )
        bundle = apps[0]
        target = output / (product_name + ".app")
        if bundle != target:
            if target.exists():
                raise RuntimeError(f"Refusing to replace previously built application: {target}")
            bundle.rename(target)
        validate_bundle(target, output, product_name)
    print(f"[Creative Suite mac] BUILT {app}: {output / (product_name + '.app')}", flush=True)
    print("[Creative Suite mac] Package dependency closure/signatures must be verified at Stage", flush=True)



def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("setup", "build"))
    parser.add_argument("app", help="Creative Suite application ID with a macOS recipe")
    args = parser.parse_args()
    cinter, source, built = inputs(args.app)
    toolchain(args.app)
    if args.mode == "setup":
        print(f"[Creative Suite mac] SOURCE {source}", flush=True)
        print(f"[Creative Suite mac] BUILT {built}", flush=True)
        print("[Creative Suite mac] Toolchain validated. Third-party dependencies "
              "must pass upstream configuration; no build-ready claim yet.", flush=True)
        return
    if recipe_for(args.app).get("configure", {}).get("system") == "tauri-v2-rust":
        build = app_module(args.app, "build").build
        build(source, built, product_name_for(args.app))
        return
    environment = configure(args.app, source, built)
    build_application(args.app, source, built, product_name_for(args.app), environment)


if __name__ == "__main__":
    try:
        main()
    except (KeyError, OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"[Creative Suite mac] ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
