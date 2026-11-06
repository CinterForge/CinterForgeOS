"""Generic, strictly validated source-build dependency recipes for Creative Suite apps.

Source acquisition, version pins, tool arguments and pkg-config requirements
belong to each platform's recipe.json, not app-specific Python source.
Unresolved dependencies are reported together before any build begins.
"""
from __future__ import annotations

import json
import argparse
from pathlib import Path


SOURCE_SCHEMA = "cinterforge.source-dependencies.v1"
FIELDS = ("name", "upstream_url", "tag", "build_system", "configure_args",
          "pkgconfig_modules", "build_args", "install_args")


def load_source_dependencies(recipe_path: Path) -> tuple[tuple[str, ...], tuple[tuple, ...]]:
    data = json.loads(recipe_path.read_text(encoding="utf-8"))
    config = data.get("source_dependencies")
    if not isinstance(config, dict) or config.get("schema") != SOURCE_SCHEMA:
        raise ValueError(f"Missing or invalid source_dependencies schema in {recipe_path}")
    required = config.get("required_pkgconfig")
    rows = config.get("recipes")
    if not isinstance(required, list) or not all(isinstance(m, str) and m for m in required):
        raise ValueError(f"Invalid required_pkgconfig in {recipe_path}")
    if len(required) != len(set(required)):
        raise ValueError(f"Duplicate required_pkgconfig entries in {recipe_path}")
    if not isinstance(rows, list):
        raise ValueError(f"Invalid source dependency recipes in {recipe_path}")
    recipes = []
    dependencies_by_name = {}
    errors = []
    providers = {}
    names = set()
    allowed_build_systems = {"autotools", "cmake", "meson", "waf"}
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or any(field not in row for field in FIELDS):
            errors.append(f"recipe #{index} missing required fields")
            continue
        name, url, tag, system = (row[field] for field in FIELDS[:4])
        if not all(isinstance(x, str) and x for x in (name, url, tag, system)):
            errors.append(f"recipe #{index} invalid identity/source/build system")
            continue
        if system not in allowed_build_systems:
            errors.append(f"{name}: unsupported build_system {system!r}")
        if name in names:
            errors.append(f"duplicate source recipe: {name}")
        names.add(name)
        if not url.startswith("https://"):
            errors.append(f"{name}: upstream_url must use HTTPS")
        if row.get("distribution", "git") != "git":
            errors.append(f"{name}: archive recipe must be handled by load_archive_dependencies")
        args = []
        for field in FIELDS[4:]:
            values = row[field]
            if not isinstance(values, list) or not all(isinstance(x, str) and x for x in values):
                errors.append(f"{name}: {field} must be a list of nonempty strings")
                values = []
            args.append(tuple(values))
        modules = args[1]
        if not modules:
            errors.append(f"{name}: missing pkgconfig_modules")
        for module in modules:
            providers.setdefault(module, []).append(name)
        depends_on = row.get("depends_on", [])
        if (not isinstance(depends_on, list) or
                not all(isinstance(dep, str) and dep for dep in depends_on) or
                len(depends_on) != len(set(depends_on))):
            errors.append(f"{name}: depends_on must be a list of unique recipe names")
            depends_on = []
        dependencies_by_name[name] = tuple(depends_on)
        build_in_source = row.get("build_in_source", False)
        if type(build_in_source) is not bool or (build_in_source and system != "autotools"):
            errors.append(f"{name}: build_in_source must be boolean and requires autotools")
        recipes.append((name, url, tag, system, *args, build_in_source))
    for module, owners in providers.items():
        if len(owners) != 1:
            errors.append(f"ambiguous pkg-config provider for {module}: {', '.join(owners)}")
    archive_providers = {m for a in config.get("archives", []) for m in a.get("pkgconfig_modules", [])}
    missing = sorted(set(required) - providers.keys() - archive_providers)
    if missing:
        errors.append("missing source recipes for: " + ", ".join(missing))
    unresolved = config.get("unresolved", [])
    if not isinstance(unresolved, list):
        errors.append("unresolved must be a list")
    else:
        unexpected = sorted({entry.get("pkgconfig_module") for entry in unresolved
                             if isinstance(entry, dict) and isinstance(entry.get("pkgconfig_module"), str)}
                            - set(missing))
        if unexpected:
            errors.append("stale unresolved records: " + ", ".join(unexpected))
    # Graph validation and stable topological order are shared by all apps and
    # platforms. A declared prerequisite is never allowed to run after a user.
    recipe_by_name = {recipe[0]: recipe for recipe in recipes}
    archive_names = {entry["name"] for entry in config.get("archives", [])}
    for name, dependencies in dependencies_by_name.items():
        for prerequisite in dependencies:
            if prerequisite not in recipe_by_name and prerequisite not in archive_names:
                errors.append(f"{name}: unknown prerequisite {prerequisite}")
            if prerequisite == name:
                errors.append(f"{name}: cannot depend on itself")
    ordered = []
    visiting = []
    visited = set()

    def visit(name: str) -> None:
        if name in visited or name not in recipe_by_name:
            return
        if name in visiting:
            errors.append("dependency cycle: " + " -> ".join(visiting[visiting.index(name):] + [name]))
            return
        visiting.append(name)
        for prerequisite in dependencies_by_name.get(name, ()):
            visit(prerequisite)
        visiting.pop()
        visited.add(name)
        ordered.append(recipe_by_name[name])

    for recipe in recipes:
        visit(recipe[0])
    if errors:
        raise RuntimeError(f"Source dependency preflight failed ({recipe_path}): " + "; ".join(errors))
    return tuple(required), tuple(ordered)


def dependency_report(recipe_path: Path) -> dict:
    """Return a machine-readable application-agnostic dependency inventory.

    Reports all declared build sources, prerequisites and outstanding work,
    without downloading anything or inspecting generated Builds directories.
    """
    data = json.loads(recipe_path.read_text(encoding="utf-8"))
    config = data.get("source_dependencies", {})
    required, ordered = None, None
    validation_error = None
    # The unresolved source inventory is a legitimate planning state, so
    # report its full contents even when the build preflight cannot pass.
    rows = config.get("recipes", [])
    provided = ({module for row in rows for module in row.get("pkgconfig_modules", [])}
                | {module for row in config.get("archives", []) for module in row.get("pkgconfig_modules", [])})
    missing = sorted(set(config.get("required_pkgconfig", [])) - provided)
    try:
        required, ordered = load_source_dependencies(recipe_path)
        load_archive_dependencies(recipe_path)
    except RuntimeError as exc:
        validation_error = str(exc)
        # The current schema fails strict validation when any required recipe
        # is missing; surface that failure instead of masking it.

    return {
        "recipe": str(recipe_path),
        "schema": config.get("schema"),
        "required_pkgconfig": config.get("required_pkgconfig", []),
        "source_recipes": [
            {"name": row["name"], "upstream_url": row["upstream_url"],
             "tag": row["tag"], "build_system": row["build_system"],
             "provides": row["pkgconfig_modules"],
             "depends_on": row.get("depends_on", []),
             "build_in_source": row.get("build_in_source", False)}
            for row in rows
        ],
        "archive_recipes": config.get("archives", []),
        "missing_pkgconfig": missing,
        "unresolved": config.get("unresolved", []),
        "build_order": [recipe[0] for recipe in ordered] if ordered else None,
        "validation_error": validation_error,
        "ready": not missing and validation_error is None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Creative Suite source dependency recipes")
    parser.add_argument("recipes", nargs="+", type=Path, help="Path(s) to platform recipe.json")
    args = parser.parse_args()
    reports = [dependency_report(path) for path in args.recipes]
    print(json.dumps(reports, indent=2))
    return 0 if all(report["ready"] for report in reports) else 2


def load_archive_dependencies(recipe_path: Path) -> tuple[dict, ...]:
    """Validate SHA256-pinned release archives independently of Git recipes."""
    config = json.loads(recipe_path.read_text(encoding="utf-8"))["source_dependencies"]
    records = config.get("archives", [])
    if not isinstance(records, list):
        raise ValueError(f"{recipe_path}: source_dependencies.archives must be a list")
    errors = []
    names = {row["name"] for row in config.get("recipes", [])}
    providers = {module for row in config.get("recipes", []) for module in row["pkgconfig_modules"]}
    archive_names = set()
    for row in records:
        if not isinstance(row, dict):
            errors.append("archive entry must be an object")
            continue
        name = row.get("name", "<unnamed>")
        if not isinstance(name, str) or not name or name in names or name in archive_names:
            errors.append(f"duplicate/invalid archive recipe name: {name}")
        archive_names.add(name)
        for field in ("url", "sha256", "build_system"):
            if not isinstance(row.get(field), str) or not row[field]:
                errors.append(f"{name}: missing {field}")
        if not str(row.get("url", "")).startswith("https://"):
            errors.append(f"{name}: URL must use HTTPS")
        digest = row.get("sha256", "")
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            errors.append(f"{name}: sha256 must be 64 lowercase hex digits")
        if row.get("build_system") not in ("autotools", "waf", "boost"):
            errors.append(f"{name}: unsupported archive build system")
        for field in ("pkgconfig_modules", "configure_args", "build_args", "install_args", "depends_on"):
            value = row.get(field)
            if not isinstance(value, list) or not all(isinstance(x, str) and x for x in value):
                errors.append(f"{name}: {field} must be a list of strings")
        for provider in row.get("pkgconfig_modules", []):
            if provider in providers:
                errors.append(f"{name}: duplicate pkg-config provider {provider}")
            providers.add(provider)
    for row in records:
        if not isinstance(row, dict):
            continue
        for dependency in row.get("depends_on", []):
            if dependency not in names and dependency not in archive_names:
                errors.append(f"{row['name']}: unknown prerequisite {dependency}")
            if dependency in archive_names:
                errors.append(f"{row['name']}: archive-to-archive ordering not supported yet: {dependency}")
    missing = sorted(set(config.get("required_pkgconfig", [])) - providers)
    if missing:
        errors.append("missing source recipes for: " + ", ".join(missing))
    if errors:
        raise RuntimeError(f"Archive dependency preflight failed ({recipe_path}): " + "; ".join(errors))
    return tuple(records)


if __name__ == "__main__":
    raise SystemExit(main())
