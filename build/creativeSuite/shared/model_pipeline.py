#!/usr/bin/env python3
"""Prepare external model assets and the three Pleias GGUF variants.

Only Pleias Q4 is intended for the Cinter card; this stage downloads sources.
It never generates standalone model Packs or performs inference.
"""
from __future__ import annotations
import argparse
import csv
import json
import os
from pathlib import Path
import re
import shutil
from urllib.request import Request, urlopen
import sys

CATALOG = Path(__file__).resolve().parents[4] / "CinterForgeServer/cf/build_cinterforge/input/HuggingFace.csv"
SAFE_ID = re.compile(r"^[a-zA-Z0-9_-]+$")

def download_gguf(root, item):
    """Download one CSV-defined GGUF variant without downloading unrelated weights."""
    try:
        from huggingface_hub import HfApi, snapshot_download
    except ImportError as error:
        raise RuntimeError("Install huggingface_hub in the setup Python environment") from error

    precision = item["Precision"]
    repository = item["HuggingFace Repo"]
    revision = item["Revision"]
    destination = root / "Models" / "Catalog" / "pleias-rag-1b" / precision
    manifest_path = destination / "cinterforge-model.json"
    repository_info = HfApi().model_info(
        repo_id=repository,
        revision=revision,
        files_metadata=True,
    )
    gguf_files = [
        entry for entry in repository_info.siblings
        if entry.rfilename.lower().endswith(".gguf")
    ]
    # A repository revision may contain multiple quantizations. Select the
    # precise artifact declared by the catalog rather than requiring a
    # single GGUF in the entire revision.
    expected_filename = item["File Name"]
    if Path(expected_filename).name != expected_filename or not expected_filename.lower().endswith(".gguf"):
        raise ValueError(f"Invalid GGUF filename in HuggingFace.csv: {expected_filename!r}")
    matches = [
        entry for entry in gguf_files
        if Path(entry.rfilename).name == expected_filename
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected GGUF {expected_filename} in {repository}@{revision}; "
            f"found {[entry.rfilename for entry in gguf_files]}"
        )

    model_file = matches[0]
    expected_size = model_file.size
    if not expected_size:
        raise RuntimeError(f"Missing upstream GGUF size for {repository}/{model_file.rfilename}")

    source = f"{repository}@{repository_info.sha}"
    target = destination / model_file.rfilename
    if manifest_path.is_file() and target.is_file():
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        if previous.get("source") == source and target.stat().st_size == expected_size:
            print(f"[models] unchanged: Pleias {precision}", flush=True)
            return

    destination.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=repository,
        revision=repository_info.sha,
        allow_patterns=[model_file.rfilename],
        local_dir=str(destination),
    )
    if not target.is_file() or target.stat().st_size != expected_size:
        raise RuntimeError(f"Incomplete Pleias {precision} download: {target}")

    manifest_path.write_text(json.dumps({
        "id": "pleias-rag-1b",
        "precision": precision,
        "format": "gguf",
        "source": source,
        "filename": model_file.rfilename,
        "size": expected_size,
        "card_payload": item["Card Payload"] == "Cinter",
        "status": "downloaded; inference not verified",
    }, indent=2) + "\n", encoding="utf-8")
    print(f"[models] downloaded: Pleias {precision}: {target}", flush=True)



def entries():
    with CATALOG.open(encoding="utf-8-sig", newline="") as stream:
        items = list(csv.DictReader(stream))
    if len(items) != 7 or len({item["ID"] for item in items}) != 7:
        raise ValueError("Expected three Pleias variants and four other catalog models")
    precisions = {item["Precision"] for item in items if item["ID"].startswith("pleias-rag-1b-")}
    if precisions != {"q4_k_m", "q8_0", "f16"}:
        raise ValueError("Pleias catalog must define Q4_K_M, Q8_0 and F16")
    for item in items:
        if not SAFE_ID.fullmatch(item["ID"]):
            raise ValueError("Unsafe catalog ID: " + item["ID"])
        if item["Download Mode"] not in ("repository", "file"):
            raise ValueError("Unsupported download mode: " + item["ID"])
        if item["Download Mode"] == "repository" and not re.fullmatch(r"[\w.-]+/[\w.-]+", item["HuggingFace Repo"]):
            raise ValueError("Missing Hugging Face repository: " + item["ID"])
        if item["Precision"]:
            if item["Download Mode"] != "repository" or not item["Revision"]:
                raise ValueError("GGUF variant requires a repository and revision: " + item["ID"])
            if item["Card Payload"] not in ("", "Cinter"):
                raise ValueError("Unsupported card payload owner: " + item["ID"])
            if (item["Card Payload"] == "Cinter") != (item["Precision"] == "q4_k_m"):
                raise ValueError("Only Pleias Q4 may be embedded in Cinter")
        if item["Download Mode"] == "file" and not item["Original Download URL"].startswith("https://"):
            raise ValueError("Missing HTTPS file URL: " + item["ID"])
    return items


def prepared(root, item):
    return root / "Models" / "Catalog" / item["ID"]


def download(root, item):
    destination = prepared(root, item)
    manifest = destination / "cinterforge-model.json"
    if manifest.is_file():
        data = json.loads(manifest.read_text())
        if data.get("source") == (item["HuggingFace Repo"] or item["Original Download URL"]) and any(p.is_file() and p.name != manifest.name for p in destination.rglob("*")):
            print("[models] unchanged:", item["ID"], flush=True)
            return
    destination.mkdir(parents=True, exist_ok=True)
    if item["Download Mode"] == "repository":
        try:
            from huggingface_hub import snapshot_download
        except ImportError as error:
            raise RuntimeError("Install huggingface_hub in the setup Python environment") from error
        snapshot_download(repo_id=item["HuggingFace Repo"], local_dir=str(destination))
    else:
        filename = item["File Name"]
        if Path(filename).name != filename or filename in ("", ".", ".."):
            raise ValueError("Unsafe model filename")
        target = destination / filename
        temporary = destination / (filename + ".part")
        try:
            with urlopen(Request(item["Original Download URL"], headers={"User-Agent": "CinterForge/1.0"}), timeout=120) as response, temporary.open("wb") as output:
                if "text/html" in response.headers.get("Content-Type", "").lower():
                    raise RuntimeError("Server returned HTML, not model weights")
                shutil.copyfileobj(response, output, 1024 * 1024)
            if not temporary.stat().st_size:
                raise RuntimeError("Empty model download")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    manifest.write_text(json.dumps({"id": item["ID"], "name": item["Name"], "kind": item["Category"], "source": item["HuggingFace Repo"] or item["Original Download URL"], "license": item["License"], "status": "prepared; inference compatibility not verified"}, indent=2) + "\n")
    print("[models] prepared:", item["ID"], flush=True)



def model_installer():
    """Load the same selection engine copied into the Creative Suite runtime."""
    import importlib.util
    location = Path(__file__).resolve().parents[4] / "CinterForgeServer/cf/csRuntime/model_selection.py"
    spec = importlib.util.spec_from_file_location("cinterforge_model_selection", location)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare_builtins(items):
    """Install only CSV-declared models into Steam, using metadata-first selection."""
    server_root = Path(__file__).resolve().parents[4] / "CinterForgeServer"
    if str(server_root) not in sys.path:
        sys.path.insert(0, str(server_root))
    from cf.io.CinterIO import cinterforgeSteamAppInstallRootPath

    installer = model_installer()
    destination_root = Path(cinterforgeSteamAppInstallRootPath()) / "BuiltIn"
    runtime_ids = {
        "pleias-rag-1b-f16": "pleias-rag-1b",
        "mitsua-diffusion-one": "mitsua-diffusion-one",
        "depth-anything-v2-small": "depth-anything-v2-small",
        "realesrgan-x4plus": "realesrgan-x4plus",
        "rembg": "rembg",
    }
    selected = {item["ID"]: item for item in items if item["ID"] in runtime_ids}
    if set(selected) != set(runtime_ids):
        raise ValueError("HuggingFace.csv must declare all five BuiltIn runtime models")

    for model_id, folder_name in runtime_ids.items():
        item = selected[model_id]
        folder = destination_root / folder_name
        if item["Download Mode"] == "repository":
            specified_file = item["File Name"] if item["File Name"].lower().endswith(".gguf") else None
            plan = installer.inspect(item["HuggingFace Repo"], item["Revision"] or None, specified_file)
            installer.install(plan, folder)
        elif item["Download Mode"] == "file":
            # Direct release assets have no Hugging Face metadata. Keep their exact CSV filename.
            folder.mkdir(parents=True, exist_ok=True)
            filename = item["File Name"]
            if Path(filename).name != filename:
                raise ValueError(f"Unsafe filename: {filename}")
            target = folder / filename
            if not target.is_file() or target.stat().st_size < 1024 * 1024:
                temporary = folder / (filename + ".part")
                try:
                    with urlopen(Request(item["Original Download URL"], headers={"User-Agent": "CinterForge/1.0"}), timeout=120) as response, temporary.open("wb") as output:
                        if "text/html" in response.headers.get("Content-Type", "").lower():
                            raise RuntimeError(f"HTML returned instead of weights for {model_id}")
                        shutil.copyfileobj(response, output, 1024 * 1024)
                    os.replace(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
            (folder / "cinterforge-model.json").write_text(json.dumps({
                "id": model_id, "source": item["Original Download URL"],
                "files": [filename], "bytes": target.stat().st_size,
                "status": "downloaded; inference not verified"
            }, indent=2) + "\n", encoding="utf-8")
        else:
            raise ValueError(f"Unsupported download mode for {model_id}")
        print(f"[models] BuiltIn ready: {model_id} -> {folder}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "prepare-builtins"))
    parser.add_argument("--builds", required=True, type=Path)
    args = parser.parse_args()
    if args.mode == "prepare-builtins":
        prepare_builtins(entries())
        return
    # Only the card-owned Q4 GGUF belongs in Builds. All other model
    # variants and runtime models are outside the asset preparation contract.
    card_models = [item for item in entries() if item["Card Payload"] == "Cinter"]
    if len(card_models) != 1 or card_models[0]["Precision"] != "q4_k_m":
        raise ValueError("Exactly one Cinter card model (Pleias Q4_K_M) is required")
    download_gguf(args.builds, card_models[0])

if __name__ == "__main__":
    main()
