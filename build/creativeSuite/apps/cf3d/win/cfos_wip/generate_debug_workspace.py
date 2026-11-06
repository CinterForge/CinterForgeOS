#!/usr/bin/env python3
"""Generate a Windows debugging workspace without changing the authored VS solution.

The workspace uses the *existing* staged executable; it never claims to build it.
"""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True, help="CinterForge repository checkout")
    parser.add_argument("--builds-root", type=Path, required=True, help="external Builds directory")
    parser.add_argument("--configuration", choices=("Debug", "Release"), default="Debug")
    parser.add_argument("--compiler", choices=("msvc", "mingw"), default="msvc")
    parser.add_argument("--gdb", default="C:/msys64/ucrt64/bin/gdb.exe")
    parser.add_argument("--guest-source-root", help="absolute compiler-side source root for GDB path substitution")
    args = parser.parse_args()
    source = args.source_root.resolve()
    if not (source / "CinterForgeSteam" / "CinterForge.vcxproj").is_file():
        parser.error("--source-root must contain CinterForgeSteam/CinterForge.vcxproj")
    builds = args.builds_root.resolve()
    executable = builds / "win64" / args.configuration / "CinterForge.exe"
    if not executable.is_file():
        parser.error(f"staged executable is missing: {executable}; build and stage it first")
    workspace = builds / "win64" / "DebugWorkspace" / args.configuration / args.compiler
    vscode = workspace / ".vscode"
    vscode.mkdir(parents=True, exist_ok=True)
    path = lambda p: str(p).replace("\\", "/")
    config = {
        "name": f"CinterForge {args.configuration} ({args.compiler})",
        "type": "cppvsdbg" if args.compiler == "msvc" else "cppdbg",
        "request": "launch", "program": path(executable),
        "cwd": path(executable.parent), "args": [], "stopAtEntry": False,
        "environment": [],
    }
    if args.compiler == "mingw":
        config.update({"MIMode": "gdb", "miDebuggerPath": args.gdb, "externalConsole": False})
        if args.guest_source_root:
            guest = args.guest_source_root.replace("\\", "/").rstrip("/")
            config["setupCommands"] = [{"text": f"set substitute-path {guest} {path(source)}", "description": "Map build guest source paths"}]
    (vscode / "launch.json").write_text(json.dumps({"version":"0.2.0","configurations":[config]},indent=2)+"\n")
    (workspace / "CinterForge.code-workspace").write_text(json.dumps({"folders":[{"path":path(source)}],"settings":{},"launch": {"version":"0.2.0","configurations":[config]}},indent=2)+"\n")
    (workspace / "build_identity.json").write_text(json.dumps({"executable":path(executable),"source_root":path(source),"compiler":args.compiler,"configuration":args.configuration,"symbols": "PDB from matching MSVC build" if args.compiler=="msvc" else "DWARF from matching MinGW build; do not strip debug executable"},indent=2)+"\n")
    print(workspace / "CinterForge.code-workspace")

if __name__ == "__main__":
    main()
