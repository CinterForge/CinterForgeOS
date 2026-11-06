"""Shared dependency planning contract; no implicit Homebrew or network fallback.

Providers for each native build system will be added here incrementally.
"""
from __future__ import annotations
from dataclasses import dataclass
import json
from pathlib import Path
import os
import re
import subprocess
from shared.build.build_runtime import run_visible, build_lock
from shared.build.upstream_patches import apply_dependency_patches, selected_dependency_patches


@dataclass(frozen=True)
class Dependency:
    name: str
    revision: str
    source_url: str
    build_system: str
    configure_args: tuple[str, ...] = ()
    build_args: tuple[str, ...] = ()
    install_args: tuple[str, ...] = ()
    # Required private pkg-config packages produced by this dependency.
    # An existing stamp alone is never sufficient evidence of installation.
    pkgconfig_modules: tuple[str, ...] = ()
    # Exact, verified source compatibility edits in disposable dependency copies.
    source_rewrites: tuple[tuple[str, str, str] | tuple[str, str, str, int], ...] = ()
    # Some upstream Makefiles use source-relative paths and cannot build with VPATH.
    build_in_source: bool = False


def dependency_prefix(build_directory: Path) -> Path:
    """Private dependency installation prefix within a versioned product build."""
    return build_directory / "Dependencies"


def validate_dependency_plan(dependencies: tuple[Dependency, ...]) -> None:
    """Require pinned source identities before any downloading or compilation."""
    for item in dependencies:
        if not all((item.name, item.revision, item.source_url, item.build_system)):
            raise ValueError(f"Incomplete dependency specification: {item!r}")
        if not re.fullmatch(r"[0-9a-fA-F]{40}", item.revision):
            raise ValueError(f"Dependency revision must be a full pinned Git commit: {item.name}")
        if item.build_system not in ("cmake", "meson", "autotools"):
            raise ValueError(f"Unsupported dependency build system: {item.name}: {item.build_system}")
        if type(item.build_in_source) is not bool or (item.build_in_source and item.build_system != "autotools"):
            raise ValueError(f"Invalid in-source build setting: {item.name}")
        for rewrite in item.source_rewrites:
            if len(rewrite) not in (3, 4):
                raise ValueError(f"Invalid source compatibility edit arity: {item.name}: {rewrite!r}")
            path, before, after = rewrite[:3]
            expected = rewrite[3] if len(rewrite) == 4 else 1
            if type(expected) is not int or expected < 1:
                raise ValueError(f"Invalid compatibility match count: {item.name}: {rewrite!r}")
            if not path or Path(path).is_absolute() or ".." in Path(path).parts or not before or before == after:
                raise ValueError(f"Invalid source compatibility edit: {item.name}: {path}")
        for module in item.pkgconfig_modules:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+\-]*", module):
                raise ValueError(f"Invalid pkg-config module for {item.name}: {module!r}")
        for option in (*item.configure_args, *item.build_args, *item.install_args):
            if not isinstance(option, str) or not option or "\x00" in option:
                raise ValueError(f"Invalid build option for dependency {item.name}")


def build_dependencies(dependencies: tuple[Dependency, ...], *, prefix: Path,
                       source_cache: Path | None = None,
                       environment: dict[str, str] | None = None,
                       patch_catalog: Path | None = None,
                       target_platform: str | None = None) -> None:
    """Build pinned upstream dependencies into an isolated private prefix.

    Supports CMake, Meson and Autotools with ordinary upstream build contracts.
    Recipes that need custom flags or patches must use their app operation file;
    never silently substitute host package managers or unpinned source.
    """
    validate_dependency_plan(dependencies)
    if patch_catalog is not None and target_platform not in ("mac", "linux", "win"):
        raise ValueError("Patched dependencies require target_platform mac/linux/win")
    prefix = prefix.expanduser().absolute()
    if not dependencies:
        prefix.mkdir(parents=True, exist_ok=True)
        return
    source_cache = (source_cache or prefix.parent / "DependencySources").expanduser().absolute()
    if source_cache == prefix or source_cache.is_relative_to(prefix):
        raise ValueError("Dependency source checkout cannot be inside installation prefix")
    names = [item.name for item in dependencies]
    if len(names) != len(set(names)):
        raise ValueError("Duplicate dependency names")
    env = os.environ.copy()
    if environment:
        env.update(environment)
    # Require private packages to win over host libraries as each build completes.
    pc_locations = (prefix / "lib/pkgconfig", prefix / "lib64/pkgconfig",
                    prefix / "share/pkgconfig")
    # Do not allow host pkg-config modules to leak into the private stack.
    env["PKG_CONFIG_LIBDIR"] = os.pathsep.join(map(str, pc_locations))
    env["PKG_CONFIG_PATH"] = os.pathsep.join(map(str, pc_locations))
    env["CMAKE_PREFIX_PATH"] = str(prefix)
    env["PATH"] = str(prefix / "bin") + os.pathsep + env.get("PATH", "")
    # Some upstream Autotools Makefiles execute built tools from their default
    # target (Vamp's `make` runs its host as a test).  On macOS the linker
    # records dylib references as @rpath names; these temporary host tools must
    # carry their *own* LC_RPATH load command.  DYLD_LIBRARY_PATH is not
    # reliable here: macOS SIP-protected /usr/bin/make strips DYLD_* variables
    # before spawning those tools.  Supply the private build rpath at link
    # time instead, without modifying upstream tests or installed applications.
    if target_platform == "mac":
        private_lib = prefix / "lib"
        rpath_flag = f"-Wl,-rpath,{private_lib}"
        link_flags = env.get("LDFLAGS", "").split()
        if rpath_flag not in link_flags:
            link_flags.append(rpath_flag)
        env["LDFLAGS"] = " ".join(link_flags)
    # A private prefix is a shared mutable output: lock it as well as the
    # source cache, including when multiple apps share that cache.
    with build_lock(prefix), build_lock(source_cache):
        for dep in dependencies:
            if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._+-]*", dep.name):
                raise ValueError(f"Unsafe dependency name: {dep.name!r}")
            checkout = source_cache / dep.name
            if not checkout.exists():
                run_visible(["git", "clone", "--", dep.source_url, str(checkout)], label=dep.name)
            if not (checkout / ".git").exists():
                raise RuntimeError(f"Invalid cached dependency checkout: {checkout}")
            origin = subprocess.check_output(
                ["git", "remote", "get-url", "origin"], cwd=checkout, text=True).strip()
            if origin != dep.source_url:
                raise RuntimeError(f"Dependency origin mismatch for {dep.name}: {origin}")
            current = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip()
            status = subprocess.check_output(
                ["git", "status", "--porcelain", "--untracked-files=all"],
                cwd=checkout, text=True).strip()
            if status:
                raise RuntimeError(f"Dirty cached dependency source: {checkout}\n{status}")
            if current != dep.revision:
                run_visible(["git", "fetch", "origin", dep.revision], cwd=checkout, label=dep.name)
                run_visible(["git", "checkout", "--detach", dep.revision], cwd=checkout, label=dep.name)
            actual = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip()
            if actual != dep.revision:
                raise RuntimeError(f"Dependency revision mismatch: {dep.name}")
            # Never configure/bootstrap in the pinned Git checkout. Autotools
            # may generate configure, Makefile.in, and helper files in-tree.
            # Keep a separately named, visible, disposable source worktree.
            import hashlib
            import json
            import shutil
            matching = (selected_dependency_patches(catalog=Path(patch_catalog),
                        dependency=dep.name, platform=target_platform)
                        if patch_catalog is not None else [])
            patch_fingerprint = hashlib.sha256(json.dumps(
                matching, sort_keys=True).encode()).hexdigest()
            prepared = source_cache / (dep.name + "-prepared")
            prepare_stamp = source_cache / (dep.name + "-prepared-revision.txt")
            prepare_id = f"{dep.revision}:{patch_fingerprint}:{hashlib.sha256(repr(dep.source_rewrites).encode()).hexdigest()}"
            previous_prepare_id = (prepare_stamp.read_text(encoding="utf-8")
                                   if prepare_stamp.is_file() else None)
            prepared_changed = not prepared.is_dir() or previous_prepare_id != prepare_id
            # The preparation stamp is not proof that the prepared files still
            # contain the recipe's edits (e.g. a previously interrupted/manual
            # repair may have restored a file while keeping the stamp).
            # Verify all expected edit results before trusting the stamp.
            if not prepared_changed and dep.source_rewrites:
                for rewrite in dep.source_rewrites:
                    relative, old, new = rewrite[:3]
                    expected = rewrite[3] if len(rewrite) == 4 else 1
                    candidate = (prepared / relative).resolve()
                    if not candidate.is_relative_to(prepared.resolve()) or not candidate.is_file():
                        prepared_changed = True
                        print(f"[{dep.name}] DIRTY prepared source missing: {relative}", flush=True)
                        break
                    content = candidate.read_text(encoding="utf-8")
                    if (content.count(new) < expected
                            or content.count(old) != expected * new.count(old)):
                        prepared_changed = True
                        print(f"[{dep.name}] DIRTY compatibility edit not present: {relative}", flush=True)
                        break
            # A compatibility edit changes only its named files. Do not throw
            # away the whole prepared source and the Ninja object cache just
            # because one rewrite changed. A source revision or upstream patch
            # change does require a fresh preparation/configuration.
            prior_parts = previous_prepare_id.split(":", 2) if previous_prepare_id else []
            source_reset = (not prepared.is_dir() or len(prior_parts) != 3
                            or prior_parts[0] != dep.revision
                            or prior_parts[1] != patch_fingerprint)
            if prepared_changed:
                if source_reset:
                    if prepared.exists():
                        shutil.rmtree(prepared)
                    shutil.copytree(checkout, prepared, ignore=shutil.ignore_patterns(".git"))
                    if patch_catalog is not None and matching:
                        apply_dependency_patches(catalog=Path(patch_catalog), dependency=dep.name,
                                                 source=prepared, platform=target_platform,
                                                 prefix=prefix, phase="source")
                else:
                    # Restore exactly the rewritten source files from the pinned
                    # checkout before applying the updated recipe. Reject this
                    # fast path if any upstream patch also owns one of them.
                    for rewrite in dep.source_rewrites:
                        relative = rewrite[0]
                        target = (prepared / relative).resolve()
                        original = (checkout / relative).resolve()
                        if not (target.is_relative_to(prepared.resolve())
                                and original.is_relative_to(checkout.resolve())
                                and original.is_file() and target.is_file()):
                            raise RuntimeError(f"{dep.name}: invalid rewrite source: {relative}")
                        if target.read_bytes() != original.read_bytes():
                            shutil.copy2(original, target)
                    print(f"[{dep.name}] UPDATE only changed compatibility sources", flush=True)
                for rewrite in dep.source_rewrites:
                    relative, old, new = rewrite[:3]
                    expected = rewrite[3] if len(rewrite) == 4 else 1
                    target = (prepared / relative).resolve()
                    if not target.is_relative_to(prepared.resolve()) or not target.is_file():
                        raise RuntimeError(f"{dep.name}: invalid compatibility source: {relative}")
                    content = target.read_text(encoding="utf-8")
                    if content.count(old) != expected:
                        raise RuntimeError(f"{dep.name}: expected exactly {expected} match(es) in {relative} "
                                           f"for compatibility edit; found {content.count(old)}")
                    updated = content.replace(old, new)
                    if updated != content:
                        target.write_text(updated, encoding="utf-8")
                        print(f"[{dep.name}] APPLY verified source compatibility: {relative}", flush=True)
                prepare_stamp.write_text(prepare_id, encoding="utf-8")
            # A stamp (and a substring check) cannot prove a prepared file is
            # byte-for-byte the intended result. For recipes without upstream
            # source patches, derive each complete edited file from the pinned
            # checkout, in recipe order, on *every* build. Reconcile only files
            # whose content differs, preserving all other objects and sources.
            # This also repairs old caches created before source verification.
            if dep.source_rewrites and not matching:
                expected_files: dict[str, str] = {}
                for rewrite in dep.source_rewrites:
                    relative, old, new = rewrite[:3]
                    count = rewrite[3] if len(rewrite) == 4 else 1
                    if relative not in expected_files:
                        original = (checkout / relative).resolve()
                        if not original.is_relative_to(checkout.resolve()) or not original.is_file():
                            raise RuntimeError(f"{dep.name}: missing pinned source: {relative}")
                        expected_files[relative] = original.read_text(encoding="utf-8")
                    content = expected_files[relative]
                    if content.count(old) != count:
                        raise RuntimeError(
                            f"{dep.name}: pinned source mismatch in {relative}: "
                            f"expected {count} match(es), got {content.count(old)}")
                    expected_files[relative] = content.replace(old, new)
                for relative, expected_content in expected_files.items():
                    target = (prepared / relative).resolve()
                    if not target.is_relative_to(prepared.resolve()) or not target.is_file():
                        raise RuntimeError(f"{dep.name}: missing prepared source: {relative}")
                    if target.read_text(encoding="utf-8") != expected_content:
                        target.write_text(expected_content, encoding="utf-8")
                        print(f"[{dep.name}] REPAIR prepared source from pinned recipe: {relative}", flush=True)
                print(f"[{dep.name}] VERIFIED {len(expected_files)} prepared compatibility files", flush=True)
            build_source = prepared
            build_dir = source_cache / (dep.name + "-build")
            stamp = source_cache / (dep.name + "-installed")
            fingerprint = repr((target_platform, dep.source_url, dep.revision, dep.build_system,
                                str(prefix), dep.configure_args, dep.build_args,
                                dep.install_args, dep.pkgconfig_modules, dep.source_rewrites, dep.build_in_source, patch_fingerprint)) + "\n"
            if (stamp.exists() and prefix.is_dir() and any(prefix.iterdir())
                    and stamp.read_text(encoding="utf-8") == fingerprint
                    and _modules_present(dep.pkgconfig_modules, prefix, env)):
                if target_platform == "mac" and dep.name == "libsndfile" and                         "-DBUILD_SHARED_LIBS=ON" in dep.configure_args:
                    shared = prefix / "lib" / "libsndfile.dylib"
                    if not shared.is_file():
                        raise RuntimeError(f"Cached libsndfile shared library missing: {shared}")
                    obsolete = prefix / "lib" / "libsndfile.a"
                    if obsolete.is_file():
                        obsolete.unlink()
                        print(f"[libsndfile] REMOVED obsolete static install: {obsolete}", flush=True)
                print(f"[{dep.name}] REUSE private dependency", flush=True)
                continue
            if dep.build_system == "cmake":
                # libsndfile's 1.2.2 CMakeLists forcibly disables BUILD_TESTING
                # for shared builds. Build its complete upstream test binaries
                # in a separate static object tree, while installing the shared
                # library for downstream applications. Do not disable tests to
                # work around a static codec link closure.
                if target_platform == "mac" and dep.name == "libsndfile" and                         "-DBUILD_SHARED_LIBS=ON" in dep.configure_args:
                    test_dir = source_cache / (dep.name + "-tests-build")
                    test_args = [arg for arg in dep.configure_args
                                 if not arg.startswith("-DBUILD_SHARED_LIBS=") and
                                 not arg.startswith("-DBUILD_TESTING=")]
                    run_visible(["cmake", "-S", str(build_source), "-B", str(test_dir),
                                 f"-DCMAKE_INSTALL_PREFIX={prefix}",
                                 f"-DCMAKE_PREFIX_PATH={prefix}", *test_args,
                                 "-DBUILD_SHARED_LIBS=OFF", "-DBUILD_TESTING=ON"],
                                env=env, label=dep.name + "-tests")
                    run_visible(["cmake", "--build", str(test_dir), "--parallel"],
                                env=env, label=dep.name + "-tests")
                    print("[libsndfile] VERIFIED complete static upstream test build; "
                          "shared product build follows", flush=True)
                # A changed CMake recipe can change headers produced by custom
                # commands, not just CMakeCache.txt. Reconfiguring an existing
                # object tree does not necessarily rebuild these generated
                # headers (libpng's pnglibconf.h captures the zlib version,
                # for example). Never mix outputs from two configurations.
                # Record the *configured* recipe, not only a fully installed
                # recipe. A compilation failure never writes the installed
                # stamp, so using it to detect changes leaves a previously
                # configured tree alive forever. libpng then retains a generated
                # pnglibconf.h from an earlier zlib selection.
                configure_stamp = source_cache / (dep.name + "-cmake-config.txt")
                if target_platform == "mac" and dep.name == "libsamplerate" and build_dir.is_dir():
                    cmake_cache = build_dir / "CMakeCache.txt"
                    if cmake_cache.is_file() and "libsndfile.a" in cmake_cache.read_text(errors="replace"):
                        print("[libsamplerate] RESET obsolete static libsndfile CMake linkage", flush=True)
                        shutil.rmtree(build_dir)
                        configure_stamp.unlink(missing_ok=True)
                configure_id = repr((dep.revision, str(build_source), str(prefix),
                                     dep.configure_args, prepare_id)) + "\n"
                if build_dir.is_dir() and (
                    not configure_stamp.is_file()
                    or configure_stamp.read_text(encoding="utf-8") != configure_id
                    or source_reset and prepared_changed
                ):
                    print(f"[{dep.name}] RESET CMake generated build after configuration/source change: "
                          f"{build_dir}", flush=True)
                    shutil.rmtree(build_dir)
                    configure_stamp.unlink(missing_ok=True)
                run_visible(["cmake", "-S", str(build_source), "-B", str(build_dir),
                             f"-DCMAKE_INSTALL_PREFIX={prefix}",
                             f"-DCMAKE_PREFIX_PATH={prefix}", *dep.configure_args], env=env, label=dep.name)
                configure_stamp.write_text(configure_id, encoding="utf-8")
                run_visible(["cmake", "--build", str(build_dir), "--parallel", *dep.build_args], env=env, label=dep.name)
                run_visible(["cmake", "--install", str(build_dir), *dep.install_args], env=env, label=dep.name)
                if target_platform == "mac" and dep.name == "libsndfile" and                         "-DBUILD_SHARED_LIBS=ON" in dep.configure_args:
                    # CMake does not uninstall products from the previous
                    # static configuration when switching to a shared build.
                    # That obsolete libsndfile.a takes precedence in a stale
                    # libsamplerate CMake cache even after the dylib installs.
                    dylib = prefix / "lib" / "libsndfile.dylib"
                    if not dylib.is_file():
                        raise RuntimeError(f"libsndfile shared build did not install {dylib}")
                    old_archive = prefix / "lib" / "libsndfile.a"
                    if old_archive.is_file():
                        old_archive.unlink()
                        print(f"[libsndfile] REMOVED obsolete static install: {old_archive}", flush=True)
            elif dep.build_system == "meson":
                # GLib's build-time resource generator links against the newly
                # built GLib libraries, not the older copies under Dependencies.
                # Do not inject the private installation rpath into GLib's Meson
                # configuration: it precedes Meson's build-tree loader paths.
                meson_env = env.copy()
                if target_platform == "mac" and dep.name == "glib":
                    flags = meson_env.get("LDFLAGS", "").split()
                    meson_env["LDFLAGS"] = " ".join(
                        flag for flag in flags if flag != rpath_flag)
                cmd = ["meson", "setup", str(build_dir), str(build_source), f"--prefix={prefix}", *dep.configure_args]
                if (build_dir / "build.ninja").is_file():
                    # New prepared sources or compiler search paths require a
                    # fresh Meson configuration (reconfigure caches CFLAGS).
                    if source_reset:
                        cmd.append("--wipe")
                    else:
                        cmd.append("--reconfigure")
                # Meson --reconfigure does not relink unchanged targets when
                # their old LC_RPATH remains embedded. Detect that exact stale
                # GLib build configuration and wipe its generated object tree
                # once, retaining the pinned checkout and private dependencies.
                if target_platform == "mac" and dep.name == "glib" and (build_dir / "build.ninja").is_file():
                    generator = build_dir / "gio/glib-compile-resources"
                    if generator.is_file():
                        load_commands = subprocess.run(
                            ["otool", "-l", str(generator)], text=True,
                            capture_output=True, check=True).stdout
                        if f"path {prefix / 'lib'} (offset " in load_commands:
                            print("[glib] RESET stale build-tree generator with private-prefix LC_RPATH", flush=True)
                            cmd = [option for option in cmd if option not in ("--reconfigure", "--wipe")]
                            cmd.append("--wipe")
                run_visible(cmd, env=meson_env, label=dep.name)
                if target_platform == "mac":
                    # Meson does not reliably refresh its cached linker flags
                    # from LDFLAGS during --reconfigure. Its freshly built host
                    # utilities (e.g. glib-compile-resources) must execute
                    # against the private libraries before `meson install`.
                    # Put the prefix rpath into Meson's actual link option,
                    # preserving any linker arguments configured by upstream.
                    result = subprocess.run(
                        ["meson", "introspect", "--buildoptions", str(build_dir)],
                        env=meson_env, text=True, capture_output=True, check=True)
                    options = json.loads(result.stdout)
                    # Meson projects may be C-only, C++-only (cairomm), or
                    # mixed-language. Only configure options that the project
                    # exposes; C-only assumptions break valid C++ projects.
                    for language_option in ("c_link_args", "cpp_link_args"):
                        link_option = next((option for option in options
                                            if option.get("name") == language_option), None)
                        if link_option is None:
                            continue
                        if not isinstance(link_option.get("value"), list):
                            raise RuntimeError(
                                f"{dep.name}: invalid Meson {language_option} value")
                        current_flags = link_option["value"]
                        # GLib build tools must prefer their newly compiled
                        # GLib libraries rather than an older installation.
                        if dep.name == "glib":
                            wanted_flags = [flag for flag in current_flags
                                            if flag != rpath_flag]
                        else:
                            wanted_flags = ([*current_flags, rpath_flag]
                                            if rpath_flag not in current_flags
                                            else current_flags)
                        if wanted_flags != current_flags:
                            run_visible(["meson", "configure", str(build_dir),
                                         f"-D{language_option}=" + json.dumps(wanted_flags)],
                                        env=meson_env, label=dep.name)
                compile_env = meson_env
                if dep.name == "glib" and target_platform == "mac":
                    # GLib's just-built resource compiler must load the matching
                    # build-tree GLib/GObject/GIO dylibs, not older dylibs in the
                    # already populated private install prefix. On Darwin the
                    # latter can satisfy @rpath loads but crash at a NULL PC
                    # when GLib's internal ABI does not match the new binary.
                    # This is build-only; installed rpaths remain unchanged.
                    build_libraries = tuple(
                        build_dir / component for component in
                        ("glib", "gobject", "gmodule", "gthread", "gio")
                    )
                    compile_env = meson_env.copy()
                    compile_env["DYLD_LIBRARY_PATH"] = os.pathsep.join(
                        [*(str(path) for path in build_libraries),
                         str(prefix / "lib")]
                    )
                    print("[glib] BUILD-TREE DYLIB PRIORITY "
                          + compile_env["DYLD_LIBRARY_PATH"], flush=True)
                if dep.name == "glib" and target_platform == "mac":
                    # The executable's LC_RPATH does NOT supply the search path
                    # for libgio's own @rpath/libz.1.dylib dependency. The
                    # loader resolves that reference from libgio's rpath chain.
                    # Compile the dependency first, then give that specific
                    # build-tree dylib a path to the installed private zlib
                    # before any generated-resource tool can execute.
                    gio_dylib = build_dir / "gio/libgio-2.0.0.dylib"
                    private_zlib = prefix / "lib/libz.1.dylib"
                    if not private_zlib.is_file():
                        raise RuntimeError(f"glib: private zlib missing: {private_zlib}")
                    run_visible(["ninja", "-C", str(build_dir),
                                 "gio/libgio-2.0.0.dylib"],
                                env=compile_env, label=dep.name)
                    gio_loads = subprocess.run(
                        ["otool", "-L", str(gio_dylib)], text=True,
                        capture_output=True, check=True).stdout
                    if "@rpath/libz.1.dylib" not in gio_loads:
                        raise RuntimeError("glib: expected private zlib dependency "
                                           "not found in freshly built libgio")
                    gio_commands = subprocess.run(
                        ["otool", "-l", str(gio_dylib)], text=True,
                        capture_output=True, check=True).stdout
                    if f"path {prefix / 'lib'} (offset " not in gio_commands:
                        run_visible(["install_name_tool", "-add_rpath",
                                     str(prefix / "lib"), str(gio_dylib)],
                                    env=compile_env, label=dep.name)
                    print(f"[glib] VERIFIED libgio private-zlib loader path: "
                          f"{prefix / 'lib'}", flush=True)
                try:
                    run_visible(["meson", "compile", "-C", str(build_dir), *dep.build_args],
                                env=compile_env, label=dep.name)
                except subprocess.CalledProcessError:
                    if dep.name == "glib" and target_platform == "mac":
                        # Avoid an interactive LLDB session (which can hang on
                        # developer-tools permissions). Report binary and
                        # loader closure directly during the failed Setup.
                        generator = build_dir / "gio/glib-compile-resources"
                        if generator.is_file():
                            print("[glib] FAILED RESOURCE GENERATOR LOADER DIAGNOSTICS", flush=True)
                            for command in (("otool", "-L", str(generator)),
                                            ("otool", "-l", str(generator)),
                                            ("file", str(generator))):
                                try:
                                    result = subprocess.run(
                                        command, env=compile_env, text=True,
                                        stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, timeout=10,
                                        check=False)
                                    print(f"[glib] {' '.join(command)} "
                                          f"(exit={result.returncode})\n{result.stdout}", flush=True)
                                except (subprocess.TimeoutExpired, OSError) as error:
                                    print(f"[glib] Diagnostic unavailable: {error}", flush=True)
                    raise

                if target_platform == "mac" and dep.name in ("glib", "pango"):
                    # Meson owns the unversioned ABI aliases as symlinks, but
                    # earlier private dependency acquisition may have populated
                    # the same names with regular dylib files. After the full
                    # build succeeds, reconcile every known library in this
                    # Meson project rather than failing one alias at a time.
                    # Require its freshly built versioned target before touching
                    # an existing prefix file; leave unrelated dylibs alone.
                    meson_dylibs = {
                        "glib": (
                            ("glib", "glib-2.0"),
                            ("gobject", "gobject-2.0"),
                            ("gmodule", "gmodule-2.0"),
                            ("gthread", "gthread-2.0"),
                            ("gio", "gio-2.0"),
                        ),
                        "pango": (
                            ("pango", "pango-1.0"),
                            ("pango", "pangoft2-1.0"),
                            ("pango", "pangocairo-1.0"),
                        ),
                    }
                    for component, name in meson_dylibs[dep.name]:
                        versioned = build_dir / component / f"lib{name}.0.dylib"
                        alias = prefix / "lib" / f"lib{name}.dylib"
                        if alias.is_file() and not alias.is_symlink():
                            if not versioned.is_file():
                                raise RuntimeError(
                                    f"{dep.name}: cannot reconcile {alias}: "
                                    f"freshly built target missing: {versioned}")
                            alias.unlink()
                            print(f"[{dep.name}] RECONCILE Meson dylib alias: "
                                  f"{alias}", flush=True)
                run_visible(["meson", "install", "-C", str(build_dir), *dep.install_args], env=env, label=dep.name)
            else:
                build_dir.mkdir(parents=True, exist_ok=True)
                configure = build_source / "configure"
                # A failed autogen.sh can leave behind a *present but invalid*
                # configure (for example, literal MM_PREREQ(...) when mm-common
                # was missing). Checking configure.is_file() alone then traps all
                # subsequent builds on that broken generated script. Record a
                # successful bootstrap separately from source preparation, and
                # retry it after a failure once the required tools are installed.
                bootstrap_stamp = source_cache / (dep.name + "-autotools-bootstrap.txt")
                bootstrap_id = prepare_id + "\n"
                autogen = build_source / "autogen.sh"
                autotools_input = ((build_source / "configure.ac").is_file()
                                   or (build_source / "configure.in").is_file())
                # Older runs of upstream autogen.sh may already have configured
                # the disposable source directory.  Out-of-tree configure will
                # reject that worktree. Recreate ONLY the prepared source from
                # the pinned, pristine checkout, then bootstrap without asking
                # autogen.sh to perform its own in-source configure. Do not
                # touch the pinned checkout, installed libraries or Ardour.
                if not dep.build_in_source and (build_source / "config.status").exists():
                    print(f"[{dep.name}] RESET previously in-source-configured "
                          "disposable dependency source", flush=True)
                    shutil.rmtree(build_source)
                    shutil.copytree(checkout, build_source,
                                    ignore=shutil.ignore_patterns(".git"))
                    if patch_catalog is not None and matching:
                        apply_dependency_patches(
                            catalog=Path(patch_catalog), dependency=dep.name,
                            source=build_source, platform=target_platform,
                            prefix=prefix, phase="source")
                    bootstrap_stamp.unlink(missing_ok=True)
                    configure = build_source / "configure"
                    autogen = build_source / "autogen.sh"
                if (autogen.is_file() or autotools_input) and (
                    not configure.is_file()
                    or not bootstrap_stamp.is_file()
                    or bootstrap_stamp.read_text(encoding="utf-8") != bootstrap_id
                ):
                    print(f"[{dep.name}] BOOTSTRAP Autotools (missing/stale completion marker)",
                          flush=True)
                    # If there is a generated configure from a failed bootstrap,
                    # it must not be treated as a valid output on a retry.
                    if autogen.is_file():
                        # GNOME-style autogen.sh invokes ./configure unless
                        # NOCONFIGURE is set. A separate out-of-tree configure
                        # is performed below with the private install prefix.
                        bootstrap_env = {**env, "NOCONFIGURE": "1"}
                        run_visible(["/bin/sh", "./autogen.sh"], cwd=build_source,
                                    env=bootstrap_env, label=dep.name)
                    else:
                        run_visible(["autoreconf", "--install", "--force"], cwd=build_source,
                                    env=env, label=dep.name)
                    if not configure.is_file():
                        raise RuntimeError(f"Autotools bootstrap produced no configure: {build_source}")
                    # Some upstream autogen.sh scripts automatically execute
                    # ./configure *in the source directory*.  Our real build
                    # takes place out of tree; leaving that configuration in
                    # place makes the subsequent configure fail with "source
                    # directory already configured".  Use upstream's own
                    # distclean target to undo just that in-source configuration.
                    # Do not remove generated Autotools inputs or the source.
                    if not dep.build_in_source and (build_source / "config.status").exists():
                        print(f"[{dep.name}] AUTOGEN configured source in-place; "
                              "running upstream make distclean for out-of-tree build",
                              flush=True)
                        if not (build_source / "Makefile").is_file():
                            raise RuntimeError(
                                f"{dep.name} autogen.sh generated config.status without "
                                f"a Makefile: {build_source}. Cannot safely clean it.")
                        run_visible(["make", "distclean"], cwd=build_source,
                                    env=env, label=dep.name)
                        if (build_source / "config.status").exists():
                            raise RuntimeError(
                                f"{dep.name}: in-source configuration still exists after "
                                "upstream make distclean")
                    bootstrap_stamp.write_text(bootstrap_id, encoding="utf-8")
                if not configure.is_file():
                    raise RuntimeError(f"Autotools dependency missing configure: {build_source}")
                # Recover worktrees bootstrapped by older builder versions,
                # including when the successful-bootstrap stamp already exists.
                if not dep.build_in_source and (build_source / "config.status").exists():
                    if not (build_source / "Makefile").is_file():
                        raise RuntimeError(f"{dep.name}: in-source config.status exists "
                                           "without Makefile; cannot safely distclean")
                    print(f"[{dep.name}] CLEAN upstream in-source autogen configuration",
                          flush=True)
                    run_visible(["make", "distclean"], cwd=build_source,
                                env=env, label=dep.name)
                    if (build_source / "config.status").exists():
                        raise RuntimeError(f"{dep.name}: make distclean did not remove "
                                           "the in-source configuration")
                actual_build_dir = build_source if dep.build_in_source else build_dir
                # Legacy Autoconf Python-header checks often derive includes via
                # `python-config`, which can select a different installation from
                # the interpreter chosen by AM_PATH_PYTHON (notably with pyenv).
                # Detect that upstream macro instead of special-casing an app or
                # library name, and pass the selected interpreter's actual SDK
                # include directories to the compiler. Never manufacture headers
                # or disable the upstream check.
                configure_env = env.copy()
                if any("AM_CHECK_PYTHON_HEADERS" in candidate.read_text(encoding="utf-8", errors="replace")
                       for candidate in (build_source / "configure.ac", build_source / "configure.in")
                       if candidate.is_file()):
                    import json as _json
                    import sys as _sys
                    python_exe = configure_env.get("PYTHON") or shutil.which("python", path=configure_env.get("PATH"))
                    if not python_exe:
                        raise RuntimeError(f"{dep.name}: Python interpreter required for extension headers")
                    script = ("import json,sysconfig; "
                              "print(json.dumps([sysconfig.get_path('include'), "
                              "sysconfig.get_path('platinclude')]))")
                    include_result = subprocess.run([python_exe, "-c", script],
                                                    env=configure_env, text=True, capture_output=True)
                    if include_result.returncode:
                        raise RuntimeError(f"{dep.name}: cannot inspect Python headers for {python_exe}: "
                                           f"{include_result.stderr.strip()}")
                    includes = list(dict.fromkeys(Path(path) for path in _json.loads(include_result.stdout)
                                                  if path))
                    python_header = next((path / "Python.h" for path in includes
                                          if (path / "Python.h").is_file()), None)
                    if python_header is None:
                        raise RuntimeError(f"{dep.name}: interpreter {python_exe} has no Python.h in "
                                           f"{', '.join(map(str, includes))}; "
                                           "install matching Python development headers during Setup")
                    flags = configure_env.get("CPPFLAGS", "").split()
                    for include in includes:
                        flag = f"-I{include}"
                        if flag not in flags:
                            flags.append(flag)
                    configure_env["CPPFLAGS"] = " ".join(flags)
                    # Some older AM_CHECK_PYTHON_HEADERS macros call
                    # `$PYTHON-config --includes`, which may name a missing
                    # pyenv shim. An audited source rewrite can respect this
                    # interpreter-derived override instead of overwriting it.
                    configure_env["PYTHON_INCLUDES"] = " ".join(
                        f"-I{include}" for include in includes
                    )
                    configure_env["PYTHON"] = python_exe
                    print(f"[{dep.name}] Python extension SDK: {python_exe} -> {python_header}", flush=True)
                try:
                    run_visible([str(configure), f"--prefix={prefix}", *dep.configure_args],
                                cwd=actual_build_dir, env=configure_env, label=dep.name)
                except subprocess.CalledProcessError as error:
                    # Autoconf's short terminal summary hides the actual compiler
                    # diagnostic. Print the failing conftest from its own config.log
                    # so the recipe can be corrected without guessing or repeatedly
                    # asking the developer to locate generated logs. Do not change
                    # flags, skip a feature, or mark the dependency installed.
                    config_log = actual_build_dir / "config.log"
                    if config_log.is_file():
                        contents = config_log.read_text(encoding="utf-8", errors="replace")
                        lines = contents.splitlines()
                        # Print the *last actual configure test*, not a series
                        # of unrelated exploratory compiler probes near the top.
                        # In particular AC_TRY_CPP / AC_COMPILE_IFELSE may fail
                        # without a compiler line literally containing "error:".
                        relevant = [i for i, line in enumerate(lines)
                                    if "checking for headers required to compile python extensions" in line]
                        if relevant:
                            start = relevant[-1]
                            end = next((i for i in range(start + 1, len(lines))
                                        if lines[i].startswith("## ") or lines[i].startswith("configure: error:")),
                                       min(len(lines), start + 90))
                            end = min(len(lines), end + 1, start + 130)
                            selected = range(start, end)
                        else:
                            failures = [i for i, line in enumerate(lines)
                                        if "fatal error:" in line or "error:" in line]
                            selected = range(max(0, failures[-1] - 14),
                                             min(len(lines), failures[-1] + 12)) if failures else ()
                        if selected:
                            print(f"[{dep.name}] CONFIGURE DIAGNOSTICS ({config_log}):", flush=True)
                            for pos in selected:
                                print(f"[{dep.name}] config.log:{pos + 1}: {lines[pos]}", flush=True)
                    raise RuntimeError(
                        f"{dep.name}: upstream configure failed (exit {error.returncode}); "
                        f"full compiler diagnostics: {config_log}"
                    ) from error
                # A previous failed in-source build can leave a fully linked
                # executable without an LC_RPATH (notably vamp-simple-host).
                # `configure` updates the Makefile but does not necessarily
                # relink existing executables. Clean the *disposable* in-source
                # objects before retrying an incomplete installation.
                if dep.build_in_source and stamp.exists() is False and (actual_build_dir / "Makefile").is_file():
                    run_visible(["make", "clean"], cwd=actual_build_dir, env=configure_env, label=dep.name)
                run_visible(["make", "-j", str(os.cpu_count() or 2), *dep.build_args], cwd=actual_build_dir,
                            env=configure_env, label=dep.name)
                run_visible(["make", "install", *dep.install_args], cwd=actual_build_dir, env=configure_env, label=dep.name)
            # The upstream build-stack report also contains modifications to
            # installed pkg-config files (for example libsndfile/sndfile.pc).
            # They cannot be applied until the dependency has installed them.
            if patch_catalog is not None and matching:
                apply_dependency_patches(
                    catalog=Path(patch_catalog), dependency=dep.name,
                    source=build_source, platform=target_platform, prefix=prefix,
                    phase="installed",
                )
            # Cache is marked only after all configure/build/install steps succeed.
            failures = _private_pkgconfig_failures(dep.pkgconfig_modules, prefix, env)
            if failures:
                raise RuntimeError(f"{dep.name}: private pkg-config verification failed after install:\n"
                                   + "\n".join(f"  - {failure}" for failure in failures))
            stamp.write_text(fingerprint, encoding="utf-8")


def _private_pkgconfig_failures(modules: tuple[str, ...], prefix: Path,
                                environment: dict[str, str]) -> list[str]:
    """Explain missing metadata, broken dependency chains, and escaped prefixes.

    pkg-config --exists returns nonzero for *transitive* Requires/Requires.private
    failures too.  Do not report an installed module as missing or silently
    resolve it through the developer's system pkg-config installation.
    """
    import shutil

    if not modules:
        return []
    tool = shutil.which("pkg-config", path=environment.get("PATH"))
    if not tool:
        raise RuntimeError("pkg-config executable is required to verify private dependencies")
    confined = dict(environment)
    directories = [prefix / suffix for suffix in (
        "lib/pkgconfig", "lib64/pkgconfig", "share/pkgconfig")]
    confined["PKG_CONFIG_LIBDIR"] = os.pathsep.join(map(str, directories))
    confined["PKG_CONFIG_PATH"] = confined["PKG_CONFIG_LIBDIR"]
    failures: list[str] = []
    for module in modules:
        locations = [directory / f"{module}.pc" for directory in directories]
        present = [candidate for candidate in locations if candidate.is_file()]
        if not present:
            failures.append(f"{module}: private metadata file not installed "
                            f"(expected one of: {', '.join(map(str, locations))})")
            continue
        checked = subprocess.run([tool, "--print-errors", "--exists", module],
                                 env=confined, capture_output=True, text=True)
        if checked.returncode:
            details = (checked.stderr or checked.stdout).strip()
            failures.append(f"{module}: private metadata exists at {present[0]}, "
                            f"but pkg-config cannot resolve its dependency chain: "
                            f"{details or 'no diagnostic from pkg-config'}")
            continue
        result = subprocess.run([tool, "--variable=pcfiledir", module],
                                env=confined, capture_output=True, text=True)
        pc_dir = result.stdout.strip()
        if result.returncode or not pc_dir or not Path(pc_dir).resolve().is_relative_to(prefix.resolve()):
            failures.append(f"{module}: pkg-config metadata escapes private prefix: "
                            f"{pc_dir or result.stderr.strip()}")
    return failures


def _modules_present(modules: tuple[str, ...], prefix: Path,
                     env: dict[str, str]) -> bool:
    """Verify both local metadata and its private transitive dependency closure."""
    return not _private_pkgconfig_failures(modules, prefix, env)


def private_pkgconfig_environment(prefix: Path, *, modules: tuple[str, ...],
                                  label: str = "dependency") -> dict[str, str]:
    """Require private pkg-config dependencies; refuse host-package fallback."""
    import os
    import shutil
    import subprocess
    prefix = prefix.expanduser().resolve()
    if not prefix.is_dir():
        raise RuntimeError(f"Private dependency prefix missing: {prefix}")
    pc_dirs = [prefix / suffix for suffix in ("lib/pkgconfig", "lib64/pkgconfig", "share/pkgconfig")]
    pc_dirs = [str(path) for path in pc_dirs if path.is_dir()]
    if not pc_dirs:
        raise RuntimeError(f"No private pkg-config metadata at: {prefix}")
    env = os.environ.copy()
    for key in ("CPATH", "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH", "LIBRARY_PATH",
                "PKG_CONFIG_PATH", "PKG_CONFIG_LIBDIR", "DYLD_LIBRARY_PATH",
                "DYLD_FALLBACK_LIBRARY_PATH", "CMAKE_PREFIX_PATH", "BOOST_ROOT"):
        env.pop(key, None)
    # pkg-config, clang, cmake, etc. are *host build tools*, not private
    # libraries. On Apple Silicon pkg-config commonly lives in /opt/homebrew/bin;
    # hiding it makes a correctly installed private stack unusable.
    env["PATH"] = str(prefix / "bin") + os.pathsep + os.environ.get("PATH", "")
    env["PKG_CONFIG_LIBDIR"] = os.pathsep.join(pc_dirs)
    env["PKG_CONFIG_PATH"] = os.pathsep.join(pc_dirs)
    env["CPATH"] = str(prefix / "include")
    env["LIBRARY_PATH"] = str(prefix / "lib")
    env["CMAKE_PREFIX_PATH"] = str(prefix)
    env["BOOST_ROOT"] = str(prefix)
    pkg_config = shutil.which("pkg-config", path=env["PATH"])
    if not pkg_config:
        raise RuntimeError(f"Private dependency stack lacks pkg-config: {prefix}")
    failures = _private_pkgconfig_failures(modules, prefix, env)
    if failures:
        raise RuntimeError(f"{label}: private pkg-config verification failed:\n"
                           + "\n".join(f"  - {failure}" for failure in failures))
    return env


def resolve_tagged_dependency(*, name: str, source_url: str, tag: str,
                              build_system: str, source_cache: Path,
                              configure_args: tuple[str, ...] = (),
                              build_args: tuple[str, ...] = (),
                              install_args: tuple[str, ...] = (),
                              pkgconfig_modules: tuple[str, ...] = (),
                              build_in_source: bool = False) -> Dependency:
    """Resolve a published upstream release tag to an immutable Git commit.

    The tag is a human-auditable release selection, not a moving branch. The
    disposable checkout is kept in the caller's visible DependencySources tree.
    Never mutate a developer-maintained repo or use a hidden global cache.
    """
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", name):
        raise ValueError(f"Invalid dependency name: {name!r}")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+/-]*", tag) or '..' in tag:
        raise ValueError(f"Invalid upstream dependency tag: {tag!r}")
    if not source_url.startswith("https://"):
        raise ValueError(f"Dependency Git origin must be HTTPS: {source_url}")
    source_cache = Path(source_cache).expanduser().absolute()
    source_cache.mkdir(parents=True, exist_ok=True)
    ref = f"refs/tags/{tag}"
    print(f"[dependencies] RESOLVE {name} {source_url} {ref}", flush=True)
    result = subprocess.run(["git", "ls-remote", "--tags", source_url, ref, ref + "^{}"],
                            capture_output=True, text=True, check=True)
    matches = dict(line.split("\t", 1)[::-1] for line in result.stdout.splitlines()
                   if "\t" in line)
    revision = matches.get(ref + "^{}") or matches.get(ref)
    if not revision or not re.fullmatch(r"[0-9a-fA-F]{40}", revision):
        raise RuntimeError(f"Upstream dependency {name} has no verifiable Git tag {tag!r}: {source_url}")
    print(f"[dependencies] PIN {name} {tag} -> {revision}", flush=True)
    return Dependency(name=name, revision=revision.lower(), source_url=source_url,
                      build_system=build_system, configure_args=configure_args,
                      build_args=build_args, install_args=install_args,
                      pkgconfig_modules=pkgconfig_modules, build_in_source=build_in_source)
