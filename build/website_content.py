#!/usr/bin/env python3
"""Build and install the CFOS-authored website pages as a reusable artifact.

This module belongs to the CFOS *producer*. cinterforge_website owns the Hugo
consumer, which reads the extracted artifact from the sibling Builds tree.
No website source checkout is needed to install/consume the published ZIP.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
from urllib.parse import quote, unquote, urlsplit
import zipfile

VERSION = "2026"
PACKAGE = f"CinterForgeOS-WebSite-Content-{VERSION}.zip"
EXTRACTED = Path("websites/content/CinterForgeOS")
RELEASE_DIR = Path("CreativeSuite/Zipped")
SOURCES = {
    "technology": Path("docs/technical/CinterForge OS Technology Stack.md"),
    "cinterforgeos": Path("README.md"),
}
PAGES = {
    "technology": Path("content/technology/_index.md"),
    "cinterforgeos": Path("content/technology/cinterforgeos/_index.md"),
}
ASSET_PREFIX = "technology/cinterforgeos/assets"
# CFOS owns the shared, static KDE-style desktop presentation. The Website
# consumes it from the same validated content release as the CFOS docs.
DESKTOP_SOURCE = Path("resources/html")
DESKTOP_DESTINATION = "static/cfos/desktop"
DESKTOP_REQUIRED = ("index.html", "app.js", "style.css")
DESKTOP_SUFFIXES = {".html", ".css", ".js", ".png", ".jpg", ".jpeg", ".svg", ".webp"}
REPOSITORY_URL = "https://github.com/CinterForge/CinterForgeOS"
MARKDOWN_LINK = re.compile(r"(!?\[[^\]\n]*\]\()([^\)\n]*)(\))")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _local_url(repo: Path, reference: str, *, label: str, assets: dict[str, bytes]) -> str:
    decoded = unquote(reference)
    url = urlsplit(decoded)
    if url.scheme or decoded.startswith(("#", "/", "//")):
        return reference
    relative = PurePosixPath(url.path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Unsafe CFOS documentation link: {reference}")
    if not relative.parts:
        return reference
    relative_string = relative.as_posix()
    if relative_string == SOURCES["technology"].as_posix():
        return "/technology/" + ("#" + url.fragment if url.fragment else "")
    if relative_string == "README.md":
        return "/technology/cinterforgeos/" + ("#" + url.fragment if url.fragment else "")
    original = repo / relative_string
    if label == "image" or relative_string.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif")):
        if not original.is_file() or not original.resolve().is_relative_to(repo.resolve()):
            raise FileNotFoundError(f"Missing referenced CFOS website asset: {relative_string}")
        assets[relative_string] = original.read_bytes()
        return "/" + ASSET_PREFIX + "/" + quote(relative_string, safe="/") + ("#" + url.fragment if url.fragment else "")
    # Non-image documentation links remain linked to the authoritative public
    # repository; do not require Hugo to copy the entire CFOS source tree.
    target = "tree" if original.is_dir() or relative_string.endswith("/") else "blob"
    return f"{REPOSITORY_URL}/{target}/main/{quote(relative_string, safe='/')}" + (
        "#" + url.fragment if url.fragment else ""
    )


def _rewrite(repo: Path, source: Path, *, front_matter: str, assets: dict[str, bytes]) -> bytes:
    document = (repo / source).read_text(encoding="utf-8")
    # Links in a Markdown file resolve relative to the source location. CFOS
    # README is at repo root; Technology Stack currently uses external assets.
    # Preserve both cases without rewriting HTTP, mailto or fragment links.
    base = source.parent

    def replacement(match: re.Match[str]) -> str:
        opening, destination, closing = match.groups()
        reference = destination.strip()
        if not reference or " " in reference and not reference.startswith(("http://", "https://")):
            # These two authored files do not use Markdown link titles.
            return match.group(0)
        is_image = opening.startswith("!")
        parsed = urlsplit(unquote(reference))
        if not parsed.scheme and not reference.startswith(("/", "#", "//")):
            file_path = (base / unquote(parsed.path)).as_posix()
            if parsed.fragment:
                file_path += "#" + parsed.fragment
            reference = file_path
        rewritten = _local_url(repo, reference, label="image" if is_image else "link", assets=assets)
        return opening + rewritten + closing

    processed = MARKDOWN_LINK.sub(replacement, document)
    return (front_matter + processed).encode("utf-8")


def contents(repo: Path) -> dict[str, bytes]:
    repo = repo.resolve()
    for source in SOURCES.values():
        if not (repo / source).is_file():
            raise FileNotFoundError(f"CFOS website source missing: {repo / source}")
    assets: dict[str, bytes] = {}
    result: dict[str, bytes] = {}
    titles = {"technology": "Technology", "cinterforgeos": "CinterForge OS"}
    for name, source in SOURCES.items():
        url = "/technology/" if name == "technology" else "/technology/cinterforgeos/"
        # Hugo consumes the content via a mount/overlay in cinterforge_website.
        # URL and title are pinned so the site's existing navigation is stable.
        fm = f'---\ntitle: "{titles[name]}"\nurl: "{url}"\n---\n\n'
        result[PAGES[name].as_posix()] = _rewrite(repo, source, front_matter=fm, assets=assets)
    for path, blob in sorted(assets.items()):
        result[f"static/{ASSET_PREFIX}/{path}"] = blob
    desktop = repo / DESKTOP_SOURCE
    for required in DESKTOP_REQUIRED:
        if not (desktop / required).is_file():
            raise FileNotFoundError(f"CFOS Desktop asset missing: {desktop / required}")
    for asset in sorted(desktop.rglob("*")):
        if asset.is_symlink():
            raise ValueError(f"CFOS Desktop asset must not be a symlink: {asset}")
        if not asset.is_file():
            continue
        if asset.suffix.lower() not in DESKTOP_SUFFIXES:
            continue  # README/source metadata is not a deployable asset
        relative = asset.relative_to(desktop)
        result[f"{DESKTOP_DESTINATION}/{relative.as_posix()}"] = asset.read_bytes()
    manifest = {
        "schema": "cinterforge.cfos.website-content.v1",
        "version": VERSION,
        "pages": {key: {"source": str(SOURCES[key]), "path": str(PAGES[key])} for key in SOURCES},
        "contents": [
            {"path": path, "sha256": _digest(blob), "bytes": len(blob)}
            for path, blob in sorted(result.items())
        ],
        "website_urls": ["/technology/", "/technology/cinterforgeos/", "/cfos/desktop/"],
        "source": "CinterForgeOS",
    }
    result["release-manifest.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    return result


def _checked_entries(bundle: zipfile.ZipFile) -> dict[str, bytes]:
    names = bundle.namelist()
    if len(names) != len(set(names)) or "release-manifest.json" not in names:
        raise ValueError("CFOS website ZIP contains duplicate entries or lacks its manifest")
    for name in names:
        p = PurePosixPath(name)
        if p.is_absolute() or ".." in p.parts or not p.parts or name.startswith("/"):
            raise ValueError(f"Unsafe CFOS website ZIP member: {name}")
        info = bundle.getinfo(name)
        if name.endswith("/") or (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise ValueError(f"Unsupported CFOS website ZIP member type: {name}")
    data = {name: bundle.read(name) for name in names}
    manifest = json.loads(data["release-manifest.json"])
    if manifest.get("schema") != "cinterforge.cfos.website-content.v1":
        raise ValueError("Wrong CFOS website manifest schema")
    expected = manifest.get("contents", [])
    verified = set()
    for record in expected:
        name = record["path"]
        if name not in data or len(data[name]) != record["bytes"] or _digest(data[name]) != record["sha256"]:
            raise ValueError(f"CFOS website ZIP hash/size mismatch: {name}")
        verified.add(name)
    if set(data) != verified | {"release-manifest.json"}:
        raise ValueError("CFOS website ZIP has unmanifested or missing members")
    for page in PAGES.values():
        if page.as_posix() not in verified:
            raise ValueError(f"CFOS website ZIP lacks required page: {page}")
    return data


def install(archive: Path, builds: Path) -> Path:
    archive, builds = archive.resolve(), builds.resolve()
    destination = builds / EXTRACTED
    with zipfile.ZipFile(archive) as bundle:
        data = _checked_entries(bundle)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".cfos-website-", dir=destination.parent))
    try:
        for name, blob in data.items():
            path = staging / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(blob)
        if destination.exists():
            # Only our dedicated generated tree is replaced; never touch the
            # other WebApp or corporate website build outputs.
            previous = destination.with_name(destination.name + ".previous")
            if previous.exists():
                raise RuntimeError(f"Unreconciled CFOS website install: {previous}")
            destination.rename(previous)
            try:
                staging.rename(destination)
            except BaseException:
                previous.rename(destination)
                raise
            shutil.rmtree(previous)
        else:
            staging.rename(destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return destination


def build(repo: Path, builds: Path) -> Path:
    builds = builds.resolve()
    target = builds / RELEASE_DIR / PACKAGE
    target.parent.mkdir(parents=True, exist_ok=True)
    data = contents(repo)
    partial = target.with_name(target.name + ".partial")
    if partial.exists():
        raise RuntimeError(f"Unreconciled CFOS website ZIP build: {partial}")
    try:
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
            for name, blob in sorted(data.items()):
                bundle.writestr(name, blob)
        with zipfile.ZipFile(partial) as bundle:
            _checked_entries(bundle)
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)
    install(target, builds)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("build", "install", "verify"))
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--builds-root", type=Path)
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    repo = args.repo_root.resolve()
    builds = (args.builds_root or repo.parent / "Builds").resolve()
    archive = (args.archive or builds / RELEASE_DIR / PACKAGE).resolve()
    if args.operation == "build":
        archive = build(repo, builds)
        print(f"[CFOS website content] BUILD {archive} ({archive.stat().st_size:,} bytes)")
    elif args.operation == "install":
        print(f"[CFOS website content] INSTALLED {install(archive, builds)}")
    else:
        with zipfile.ZipFile(archive) as bundle:
            data = _checked_entries(bundle)
        print(f"[CFOS website content] VERIFIED {archive} ({len(data)-1} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
