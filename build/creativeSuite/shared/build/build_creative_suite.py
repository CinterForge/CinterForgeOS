#!/usr/bin/env python3
"""Generate the self-contained CinterForge Creative Suite from Kevin's approved baseline."""
from __future__ import annotations

import argparse
import csv
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse
# Import the shared Python filesystem authority from this checkout.
_CINTER_PACKAGE_PARENT = next(
    parent for parent in Path(__file__).resolve().parents
    if (parent / "CinterForgeServer" / "cf" / "io" / "CinterFileIO.py").is_file()
)
if str(_CINTER_PACKAGE_PARENT / "CinterForgeServer") not in sys.path:
    sys.path.insert(0, str(_CINTER_PACKAGE_PARENT / "CinterForgeServer"))
from cf.io.CinterIO import cinterforgeBuildsRootPath


REQUIRED_COLUMNS = (
    "Name", "Extension", "Downloads", "Icon URL", "Product Page",
    "Donate URL", "Windows x64 URL", "macOS ARM64 URL", "Linux ARM64 URL", "Download Mode",
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


def valid_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def read_apps(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        missing = [column for column in REQUIRED_COLUMNS if column not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"Creative Suite CSV is missing required columns: {', '.join(missing)}")
        rows = []
        for line_number, raw in enumerate(reader, start=2):
            row = {column: str(raw.get(column) or "").strip() for column in REQUIRED_COLUMNS}
            if not row["Name"]:
                raise ValueError(f"Creative Suite CSV line {line_number} has no Name")
            for column in ("Downloads", "Icon URL", "Product Page", "Donate URL", "Windows x64 URL", "macOS ARM64 URL"):
                if not valid_http_url(row[column]):
                    raise ValueError(f"Creative Suite CSV line {line_number} has invalid {column}: {row[column]!r}")
            linux_source = row["Linux ARM64 URL"]
            if not valid_http_url(linux_source) and not linux_source.startswith("sudo add-apt-repository ") and linux_source not in {"apt:scribus", "apt:ardour", "apt:ffmpeg", "apt:converter"}:
                raise ValueError(
                    f"Creative Suite CSV line {line_number} has invalid Linux ARM64 URL or repository command: {linux_source!r}"
                )
            if row["Download Mode"] not in {"direct", "browser", "repo"}:
                raise ValueError(
                    f"Creative Suite CSV line {line_number} has invalid Download Mode: {row['Download Mode']!r}"
                )
            rows.append(row)
    if not rows:
        raise ValueError("Creative Suite CSV contains no applications")
    return rows


def app_catalog(rows: list[dict[str, str]]) -> list[dict]:
    return [
        {
            "name": row["Name"],
            "extension": row["Extension"],
            "links": {
                "downloads": row["Downloads"],
                "product": row["Product Page"],
                "donate": row["Donate URL"],
                "windows": row["Windows x64 URL"],
                "macos": row["macOS ARM64 URL"],
                "linux": row["Linux ARM64 URL"],
            },
            "styleKey": row["Name"].replace(" ", ""),
            "downloadMode": row["Download Mode"],
        }
        for row in rows
    ]


def installation_plan(rows: list[dict[str, str]]) -> dict:
    """Describe both required platform installations without treating a URL as an install."""
    applications = []
    for row in rows:
        # Web tools and the CinterForgeOS source repository are not upstream
        # desktop application installers and have no Linux package plan.
        if row["Name"] in {"CinterForge Converter", "SteamMac"}:
            continue
        linux_source = row["Linux ARM64 URL"]
        if linux_source in {"apt:scribus", "apt:ardour", "apt:ffmpeg", "apt:converter"}:
            linux_architecture = "repository-dependent"
            linux_package = "apt-repository"
        elif linux_source.startswith("sudo add-apt-repository "):
            linux_architecture = "repository-dependent"
            linux_package = "apt-repository"
        else:
            filename = urlparse(linux_source).path.lower()
            if any(marker in filename for marker in ("x86_64", "x64", "amd64")):
                linux_architecture = "x86_64"
            elif any(marker in filename for marker in ("aarch64", "arm64")):
                linux_architecture = "aarch64"
            else:
                raise ValueError(
                    f"Cannot determine Linux architecture for {row['Name']}: {linux_source}"
                )
            linux_package = "download"

        applications.append({
            "name": row["Name"],
            "macos": {
                "architecture": "arm64",
                "source": row["macOS ARM64 URL"],
                "required": True,
            },
            "linux": {
                "architecture": linux_architecture,
                "source": linux_source,
                "sourceType": linux_package,
                "required": True,
                "emulationRequiredOnArm64": linux_architecture == "x86_64",
            },
        })

    return {
        "schemaVersion": 1,
        "hostPlatform": platform.system().lower(),
        "macosAndLinuxRequiredOnMac": True,
        "smokeTestOutput": "Builds/CreativeSuite/Tests",
        "applications": applications,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Builds/CreativeSuite from the approved Creative Suite baseline")
    parser.add_argument("--repo-root", type=Path, default=repo_root())
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    root = args.repo_root.expanduser().resolve()
    script_dir = Path(__file__).resolve().parent
    baseline = script_dir / "Baseline"
    if not (baseline / "index.html").is_file():
        raise RuntimeError(f"Creative Suite approved baseline is incomplete: {baseline}")

    csv_path = args.csv or script_dir.parent / "input" / "Creative Suite.csv"
    rows = read_apps(csv_path.resolve())
    output = (args.output or cinterforgeBuildsRootPath(root) / "CreativeSuite").resolve()

    # Preserve user-installed applications and downloaded catalogs across Update Data.
    # Only generated files are refreshed; runtime state is never deleted.
    output.mkdir(parents=True, exist_ok=True)
    shutil.copytree(baseline, output, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store", "Databases", "Installed"))

    # The CSV is the single source of truth for visible application cards.
    # The historical baseline contains obsolete Natron/Tenacity markup.
    from html import escape
    index_path = output / "index.html"
    index_html = index_path.read_text(encoding="utf-8")
    card_start = index_html.index('<section class="grid">') + len('<section class="grid">')
    card_end = index_html.index('</section>', card_start)
    cards = []
    for row in rows:
        name = escape(row["Name"], quote=True)
        links = (
            ("◎", "Product Page", row["Product Page"]),
            ("↓", "Downloads", row["Downloads"]),
            ("♡", "Donate", row["Donate URL"]),
            ("⊞", "Windows x64", row["Windows x64 URL"]),
            ("●", "macOS ARM64", row["macOS ARM64 URL"]),
        )
        actions = "".join(
            f'<a class="action" href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">'
            f'<span class="action-icon">{icon}</span><span>{label}</span><span class="external">↗</span></a>'
            for icon, label, url in links
        )
        logos = ""
        if row["Name"] == "CinterForge Converter":
            logos = '<div class="converter-libraries" aria-label="Converter engines">' + "".join(
                f'<span><img src="https://www.google.com/s2/favicons?domain={domain}&amp;sz=64" alt="">{label}</span>'
                for label, domain in (("FFmpeg", "ffmpeg.org"), ("ImageMagick", "imagemagick.org"), ("Assimp", "assimp.org"))
            ) + '</div>'
        cards.append(
            f'<article class="app-card"><div class="app-heading">'
            f'<img class="app-icon" src="{escape(row["Icon URL"], quote=True)}" alt="{name} logo">'
            f'<div><h2>{name}</h2><span class="category">{escape(row["Extension"])}</span></div></div>'
            f'{logos}<nav class="actions" aria-label="{name} links">{actions}</nav></article>'
        )
    # Browser tools are built products, not desktop installers from the CSV.
    for name, category, icon, runtime in (
        ("Blockbench", "3D · Web", "https://www.google.com/s2/favicons?domain=blockbench.net&sz=128", "/blockbench/"),
        ("Code-OSS", "Code · Web", "https://www.google.com/s2/favicons?domain=code.visualstudio.com&sz=128", "/codeoss/out/vs/code/browser/workbench/workbench.html"),
    ):
        cards.append(
            f'<article class="app-card" data-web-runtime="{runtime}">'
            f'<div class="app-heading"><img class="app-icon" src="{icon}" alt="">'
            f'<div><h2>{name}</h2><span class="category">{category}</span></div></div>'
            f'<button class="install-action open-web-app" type="button">Open {name}</button>'
            f'<div class="install-status">Included · Web application</div></article>'
        )
    # Converter is already a CSV card. Launch its browser UI in the same Suite window.
    cards = [card.replace(
        '<nav class="actions" aria-label="CinterForge Converter links">',
        '<button class="install-action open-web-app" data-web-runtime="/creative-suite/media-encoder/" '
        'type="button">Open Converter</button><nav class="actions" aria-label="CinterForge Converter links">'
    ) for card in cards]
    index_html = index_html[:card_start] + "".join(cards) + index_html[card_end:]
    index_path.write_text(index_html, encoding="utf-8")

    # The maintained CSV remains authoritative for installer URLs and application identity.
    (output / "apps.json").write_text(json.dumps(app_catalog(rows), indent=2) + "\n", encoding="utf-8")

    # Both macOS and Linux are mandatory on Mac. This plan is not proof of
    # installation: platform installers and document smoke tests must report
    # their own actual execution results before either platform can pass.
    (output / "InstallationPlan.json").write_text(
        json.dumps(installation_plan(rows), indent=2) + "\n", encoding="utf-8"
    )

    # Rebuild the approved CinterForge style payloads every Update Data run.
    style_generator = output / "CFStyleGenerator" / "generate_cf_styles.py"
    subprocess.run([sys.executable, str(style_generator)], cwd=output / "CFStyleGenerator", check=True)

    print(f"[creative-suite] generated approved baseline bundle {output} apps={len(rows)} styles=generated", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
