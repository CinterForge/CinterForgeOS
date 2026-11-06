#!/usr/bin/env python3
"""Stage existing Creative Suite smoke examples beside every installed application.

This is a staging adapter, not a new smoke implementation. It does not mark a
GUI test as supported if the existing app test reports unsupported.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import shutil

def stage(app: str, platform: str, destination: Path, *, suite_root: Path | None = None) -> list[Path]:
    # The lifecycle controller owns the source root; do not infer it from a
    # shared module's current directory, which can move during refactoring.
    suite = (suite_root or Path(__file__).resolve().parents[2]).resolve()
    source = suite / "apps" / app
    if not source.is_dir():
        raise RuntimeError(f"No Creative Suite application source: {source}")
    tests = destination / "Tests"
    examples = tests / "Examples"
    copied: list[Path] = []
    candidates = [source / "test", source / platform / "test"]
    candidates += [source / platform / f"test.{ext}" for ext in ("sh", "ps1", "py")]
    for candidate in candidates:
        if not candidate.exists():
            continue
        if candidate.is_dir():
            for item in sorted(candidate.rglob("*")):
                if not item.is_file() or item.is_symlink() or "__pycache__" in item.parts:
                    continue
                target = examples / "test" / item.relative_to(candidate)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
                copied.append(target)
        elif candidate.is_file():
            target = examples / platform / candidate.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate, target)
            copied.append(target)
    # Publish actual existing test inputs as readable installed examples. Preserve
    # relative paths (and source script names) instead of inventing per-app fixtures.
    for folder_name in ("Samples", "Fixtures", "TestData"):
        candidate = source / folder_name
        if not candidate.is_dir():
            continue
        for item in sorted(candidate.rglob("*")):
            if not item.is_file() or item.is_symlink() or "__pycache__" in item.parts:
                continue
            target = tests / "Samples" / folder_name / item.relative_to(candidate)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
            copied.append(target)
    # Results intentionally not staged: they belong to the installed app's
    # mutable state and should persist across same-version restaging.
    supported = {"ardour", "gimp", "krita", "inkscape", "scribus", "blender", "kdenlive", "rapidraw", "cfconverter"}
    # Keep the existing all-app document smoke implementation visible alongside
    # every installed Linux application; do not invent separate app tests.
    if platform == 'linux' and app in supported:
        suite_file = suite / 'shared/linux/linux_smoke_tests.py'
        target = examples / 'Shared' / suite_file.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(suite_file, target)
        copied.append(target)
    launcher = tests / "RunSmokeTest.sh"
    launcher.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        "# Reuses the checked-in Creative Suite test implementation; does not fabricate results.\n"
        "# Supply CINTER_SCRIPTS_ROOT (the creativeSuite directory) on a developer machine.\n"
        ": \"${CINTER_SCRIPTS_ROOT:?Set CINTER_SCRIPTS_ROOT to the Creative Suite source directory}\"\n"
        f"exec python3 -u \"$CINTER_SCRIPTS_ROOT/test_suite.py\" --installed-tests \"$(dirname -- \"${{BASH_SOURCE[0]}}\")\" --application '{('Ardour' if app == 'ardour' else {'gimp':'GIMP','krita':'Krita','inkscape':'Inkscape','scribus':'Scribus','blender':'Blender','kdenlive':'Kdenlive','rapidraw':'RapidRAW','cfconverter':'CinterForge Converter'}.get(app, app))}' \"$@\"\n"
        if platform == "linux" and app in supported else
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        "echo 'No standalone smoke execution available for this platform yet; see Examples/.' >&2\nexit 2\n",
        encoding="utf-8")
    launcher.chmod(0o755)
    (tests / "Results").mkdir(parents=True, exist_ok=True)
    (tests / "README.txt").write_text(
        "CinterForge Creative Suite smoke-test examples\n"
        f"Application: {app}\nPlatform: {platform}\n"
        "Examples/ contains the existing checked-in test scripts as reference.\n"
        "Some examples require the developer's Creative Suite shared runner.\n"
        "Use the standalone developer test entrypoint for the existing Linux\n"
        "document-creation/export smoke suite; unimplemented GUI routes fail loudly.\n"
        "Results/ is reserved for this installed version's test outputs.\n",
        encoding="utf-8")
    return copied

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("app")
    parser.add_argument("platform", choices=("mac", "linux", "win"))
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    copied = stage(args.app, args.platform, args.destination)
    print(f"[stage tests] {args.app}/{args.platform}: staged {len(copied)} existing test examples")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
