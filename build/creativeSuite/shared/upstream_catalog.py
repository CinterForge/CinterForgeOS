#!/usr/bin/env python3
"""Single source of truth for original Creative Suite application metadata.

Read the maintained input/Creative Suite.csv directly. The generated apps.json
is an output of update-data, never an authoritative input for reference tools.
"""
from __future__ import annotations
import csv
from pathlib import Path
from urllib.parse import urlparse

CSV = Path(__file__).resolve().parents[1] / "input" / "Creative Suite.csv"
REQUIRED = ("Name", "Extension", "Downloads", "Icon URL", "Product Page",
            "Donate URL", "Windows x64 URL", "macOS ARM64 URL", "Linux ARM64 URL", "Download Mode")
# These CSV rows are web/server or OS components, not upstream desktop installers.
# Adapter IDs in Creative Suite.csv are canonical, but retain older display names.
NON_INSTALLERS = frozenset({"cfconverter", "cinterforge converter", "steammac"})


def read_catalog(*, installer_only: bool = False) -> list[dict]:
    if not CSV.is_file():
        raise FileNotFoundError(f"Authoritative Creative Suite catalog missing: {CSV}")
    with CSV.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        missing = set(REQUIRED) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Creative Suite CSV missing columns: {sorted(missing)}")
        entries = []
        names = set()
        for line, row in enumerate(reader, 2):
            name = (row.get("Name") or "").strip()
            if not name or name.casefold() in names:
                raise ValueError(f"Creative Suite CSV line {line}: blank or duplicate name {name!r}")
            names.add(name.casefold())
            mode = (row.get("Download Mode") or "").strip().lower()
            # Source-only rows added for Inspect (e.g. Code, Voxel, LevelDB)
            # have no publisher installer and historically left Download Mode
            # empty. Classify only unambiguous repository-source rows as repo;
            # never silently accept a missing mode for an installer row.
            if not mode:
                source = (row.get("Source Acquisition") or "").strip().lower()
                repository = (row.get("Git Repo URL") or "").strip()
                has_installer = any((row.get(field) or "").strip() for field in
                                    ("Windows x64 URL", "macOS ARM64 URL", "Linux ARM64 URL"))
                parsed_repo = urlparse(repository)
                if (source == "source" and not has_installer and
                        parsed_repo.scheme == "https" and parsed_repo.netloc):
                    mode = "repo"
            if installer_only and (name.casefold() in NON_INSTALLERS or mode == "repo"):
                continue
            if mode not in {"direct", "browser", "repo"}:
                raise ValueError(f"Creative Suite CSV line {line}: invalid download mode {mode!r}")
            links = {
                "downloads": (row.get("Downloads") or "").strip(),
                "product": (row.get("Product Page") or "").strip(),
                "donate": (row.get("Donate URL") or "").strip(),
                "windows": (row.get("Windows x64 URL") or "").strip(),
                "macos": (row.get("macOS ARM64 URL") or "").strip(),
                "linux": (row.get("Linux ARM64 URL") or "").strip(),
            }
            if installer_only:
                for key in ("windows", "macos"):
                    url = links[key]
                    if urlparse(url).scheme != "https":
                        raise ValueError(f"Creative Suite CSV line {line}: {key} installer URL must be HTTPS")
            entries.append({"name": name, "category": (row.get("Extension") or "").strip(),
                            "extension": (row.get("Extension") or "").strip(),
                            "icon": (row.get("Icon URL") or "").strip(),
                            "links": links, "styleKey": name.replace(" ", ""),
                            "downloadMode": mode})
    if not entries:
        raise ValueError(f"Creative Suite CSV has no applicable entries: {CSV}")
    return entries
