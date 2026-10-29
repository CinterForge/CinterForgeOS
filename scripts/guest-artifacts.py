#!/usr/bin/env python3
"""Content-based build receipts; called by the guest builds and release packagers.

Only selected Git inputs are hashed, using working-tree bytes (not HEAD/index).
A receipt is published only after a successful build with unchanged inputs, and
binds those inputs to the output bytes. Never adopt an unproven existing output.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
WORK_ROOT = Path(os.environ.get("STEAMAC_WORK_DIR", str(ROOT.parent / "Builds/CreativeSuite/Built/cinterforge-os/mac/CFOS/work")))
OUT = WORK_ROOT / "out"
MESA_INPUTS = (
    "guest/mesa/build.sh", "guest/mesa/versions.env", "guest/mesa/container",
    "scripts/config.env", "scripts/steps/10-rootfs.sh",
    "scripts/builder/Dockerfile", "scripts/keys/steamdeck-images.pem",
)
INPUTS = {
    "kernel": ("guest/kernel/build.sh", "guest/kernel/container-build.sh",
               "guest/kernel/Dockerfile", "guest/kernel/check-config.sh",
               "guest/kernel/config", "guest/kernel/patches"),
    "initramfs": ("guest/initramfs/init", "scripts/build-image.sh",
                  "scripts/steps/20-initramfs.sh", "scripts/config.env",
                  "scripts/builder/Dockerfile"),
    "mesa": MESA_INPUTS,
    "mesa-aarch64": MESA_INPUTS,
    "mesa-x86": MESA_INPUTS,
    # Include the entire authored FEX directory, including newly added files.
    # A new/edited probe, build recipe, or graphics patch must dirty the layer
    # receipt before Xcode can decide to skip its build phase.
    "layer": ("guest/layer/usr", "guest/progress-agent", "guest/fex",
              "scripts/build-image.sh", "scripts/steps/25-progress-agent.sh",
              "scripts/steps/30-layer.sh") + MESA_INPUTS,
}
OUTPUTS = {
    "kernel": (OUT / "Image",),
    "initramfs": (OUT / "initramfs.cpio.gz",),
    "mesa": (OUT / "mesa-venus",),
    "mesa-aarch64": (OUT / "mesa-venus/usr/lib/libvulkan_virtio.so", OUT / "mesa-venus/usr/share/vulkan",
                     WORK_ROOT / "build/mesa-venus/bin/vkprobe-aarch64"),
    "mesa-x86": (OUT / "mesa-venus/usr/share/guestos", OUT / "mesa-venus/usr/lib/steamac",
                 WORK_ROOT / "build/mesa-venus/bin/vkprobe-x86_64",
                 WORK_ROOT / "build/mesa-venus/bin/vkprobe-i386",
                 WORK_ROOT / "build/mesa-venus/bin/fault-report-test"),
    "layer": (OUT / "steamac-layer.img",),
}
COMMANDS = {
    "kernel": "guest/kernel/build.sh",
    "initramfs": "scripts/build-image.sh builder initramfs",
    "mesa": "scripts/build-image.sh builder rootfs && guest/mesa/build.sh",
    "mesa-aarch64": "guest/mesa/build.sh fetch aarch64",
    "mesa-x86": "guest/mesa/build.sh fetch x86",
    "layer": "scripts/build-image.sh builder layer",
}


def fail(kind, reason):
    raise ValueError(f"{kind}: {reason}; rerun `{COMMANDS[kind]}`")


def stamp_path(kind, resources=None):
    if resources is not None:
        return resources / (OUTPUTS[kind][0].name + ".inputs.json")
    if kind.startswith("mesa-"):
        return OUT / (kind + ".inputs.json")
    return OUTPUTS[kind][0].with_name(OUTPUTS[kind][0].name + ".inputs.json")


def add_record(digest, *fields):
    for field in fields:
        data = os.fsencode(field)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)


def file_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def hash_entry(digest, path, name):
    if path.is_symlink():
        add_record(digest, name, "symlink", os.readlink(path))
    elif path.is_file():
        add_record(digest, name, "executable" if path.stat().st_mode & 0o111 else "file",
                   file_digest(path))
    else:
        raise ValueError(f"missing or unsupported input: {path}")


def tree_digest(root):
    digest = hashlib.sha256()
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(directory) / name
            rel = path.relative_to(root)
            if path.is_dir() and not path.is_symlink():
                add_record(digest, str(rel), "directory")
            else:
                hash_entry(digest, path, str(rel))
        dirs.sort()
    return digest.hexdigest()


def input_digest(kind, components=None):
    paths = subprocess.check_output(
        ["git", "-C", str(ROOT), "ls-files", "-z", "--cached", "--others",
         "--exclude-standard", "--", *INPUTS[kind]]).split(b"\0")
    digest = hashlib.sha256()
    add_record(digest, "steamac-guest-inputs-1", kind)
    for name in sorted(set(p for p in paths if p)):
        path = ROOT / os.fsdecode(name)
        # `git ls-files --cached` includes tracked files deleted in the working
        # tree. A deliberate deletion is a source change, not an invalid input.
        # Record a tombstone so removing (or restoring) a file invalidates any
        # prior build receipt. Required sources are checked by the build step.
        label = os.fsdecode(name)
        if not os.path.lexists(path):
            add_record(digest, name, "deleted")
            if components is not None:
                components[label] = "deleted"
            continue
        try:
            if components is not None:
                if path.is_symlink():
                    components[label] = "symlink:" + os.readlink(path)
                elif path.is_file():
                    components[label] = ("executable:" if path.stat().st_mode & 0o111 else "file:") + file_digest(path)
                else:
                    raise ValueError(f"unsupported input: {path}")
            hash_entry(digest, path, name)
        except (OSError, ValueError) as error:
            fail(kind, f"missing/invalid source input ({error})")
    if kind in ("initramfs", "layer"):
        epoch = os.environ.get("SOURCE_DATE_EPOCH", "1767225600")
        add_record(digest, "SOURCE_DATE_EPOCH", epoch)
        if components is not None:
            components["SOURCE_DATE_EPOCH"] = epoch
    if kind == "layer":
        fex = OUT / "fex" / "usr"
        if not (fex / "bin" / "FEX").is_file():
            fail("layer", "standalone FEX runtime missing (run scripts/build-image.sh fex)")
        fex_digest = tree_digest(fex)
        add_record(digest, "fex-runtime", fex_digest)
        if components is not None:
            components["built:fex-runtime"] = fex_digest
        userspace = OUT / "fex-userspace"
        if not (userspace / "wine-executable.txt").is_file() or not (userspace / "RootFS/usr/bin/bash").is_file() or not (userspace / "RootFS/usr/lib/x86_64-linux-gnu/libglib-2.0.so.0").exists() or not (userspace / "RootFS/usr/lib/x86_64-linux-gnu/libpangomm-1.4.so.1").exists():
            fail("layer", "FEX x86-64 RootFS/Wine missing (run scripts/build-image.sh userspace)")
        userspace_digest = tree_digest(userspace)
        add_record(digest, "fex-userspace", userspace_digest)
        if components is not None:
            components["built:fex-userspace"] = userspace_digest
        check("mesa")
        mesa_digest = file_digest(stamp_path("mesa"))
        add_record(digest, "mesa-receipt", mesa_digest)
        if components is not None:
            components["built:mesa-receipt"] = mesa_digest
    if kind == "mesa":
        for architecture in ("mesa-aarch64", "mesa-x86"):
            check(architecture)
            add_record(digest, architecture, file_digest(stamp_path(architecture)))
    return digest.hexdigest()


def output_digest(kind, resources=None):
    digest = hashlib.sha256()
    paths = OUTPUTS[kind] if resources is None else (resources / OUTPUTS[kind][0].name,)
    for index, path in enumerate(paths):
        if path.is_dir() and not path.is_symlink():
            add_record(digest, str(index), "directory")
            for directory, dirs, files in os.walk(path, followlinks=False):
                # Include symlinked directories without following them.
                for name in sorted(dirs + files):
                    entry = Path(directory) / name
                    relative = entry.relative_to(path)
                    if entry.is_dir() and not entry.is_symlink():
                        add_record(digest, str(index), str(relative), "directory")
                    else:
                        hash_entry(digest, entry, f"{index}/{relative}")
                dirs.sort()
        else:
            hash_entry(digest, path, str(index))
    return digest.hexdigest()


def require_current_glx_probe():
    """A receipt must never accept a layer built from an older FEX GLX probe.

    The diagnostic is part of the rootfs, but its version is established by
    the *authored* C source rather than by mere presence of an executable.
    Checking this before accepting a cached layer forces the existing Xcode
    phase to rebuild the probe and layer together.
    """
    source = ROOT / "guest/fex/glx-probe.c"
    binary = OUT / "fex-userspace/RootFS/usr/bin/cinterforge-glx-probe"
    try:
        import hashlib
        digest = hashlib.sha256(source.read_bytes() + (source.parent / "gl-probe-render.h").read_bytes()).hexdigest()
        expected = b"CINTERFORGE_GLX_PROBE_SHA=" + digest.encode("ascii")
        if expected not in binary.read_bytes():
            fail("layer", "FEX GLX probe is stale against guest/fex/glx-probe.c")
    except OSError as error:
        fail("layer", f"FEX GLX probe missing/unreadable ({error})")


def check(kind, resources=None):
    if kind == "layer":
        require_current_glx_probe()
    try:
        receipt = json.loads(stamp_path(kind, resources).read_text())
    except (OSError, ValueError) as error:
        fail(kind, f"missing/invalid input stamp ({error})")
    if not isinstance(receipt, dict) or receipt.get("version") != 1 or receipt.get("kind") != kind:
        fail(kind, "invalid input stamp")
    if receipt.get("inputs") != input_digest(kind):
        fail(kind, "stale source inputs")
    try:
        output = output_digest(kind, resources)
    except (OSError, ValueError) as error:
        fail(kind, f"missing/invalid artifact ({error})")
    if receipt.get("output") != output:
        fail(kind, "artifact bytes differ from successful build")


def main():
    action = sys.argv[1]
    if action in ("check-release", "check-bundle"):
        resources = Path(sys.argv[2]) if action == "check-bundle" else None
        for kind in ("kernel", "initramfs", "layer"):
            try:
                check(kind, resources)
            except ValueError as error:
                if resources is not None:
                    raise ValueError(f"{error}; then rerun `host/launcher/build.sh` to refresh the app") from error
                raise
        return
    kind = sys.argv[2]
    stamp = stamp_path(kind)
    if action in ("begin", "invalidate"):
        stamp.unlink(missing_ok=True)
        if action == "begin":
            if kind == "layer":
                components = {}
                inputs = input_digest(kind, components)
                print(json.dumps({"digest": inputs, "components": components}, sort_keys=True))
            else:
                print(input_digest(kind))
    elif action == "finish":
        prior = sys.argv[3]
        if kind == "layer":
            try:
                snapshot = json.loads(prior)
                previous = snapshot["components"]
                expected = snapshot["digest"]
                if not isinstance(previous, dict) or not isinstance(expected, str):
                    raise ValueError("invalid snapshot")
            except (ValueError, KeyError, TypeError) as error:
                fail(kind, f"invalid begin snapshot ({error})")
            current = {}
            inputs = input_digest(kind, current)
            if inputs != expected:
                changed = sorted(k for k in previous.keys() | current.keys()
                                 if previous.get(k) != current.get(k))
                if changed:
                    for name in changed:
                        print(f"[layer] INPUT CHANGED: {name}: {previous.get(name, '<absent>')} -> {current.get(name, '<absent>')}", file=sys.stderr)
                else:
                    print("[layer] overall input digest changed without a component difference; check Git input enumeration", file=sys.stderr)
                fail(kind, f"{len(changed)} input component(s) changed during build")
        else:
            inputs = input_digest(kind)
            if inputs != prior:
                fail(kind, "inputs changed during build")
        receipt = {"version": 1, "kind": kind, "inputs": inputs,
                   "output": output_digest(kind)}
        temporary = stamp.with_name(stamp.name + f".tmp.{os.getpid()}")
        try:
            temporary.write_text(json.dumps(receipt, sort_keys=True) + "\n")
            temporary.replace(stamp)
        finally:
            temporary.unlink(missing_ok=True)
    elif action == "check":
        check(kind)
    else:
        raise ValueError(f"unknown action: {action}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f"guest artifacts: {error}")
