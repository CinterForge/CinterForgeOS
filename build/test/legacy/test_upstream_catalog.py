"""Regression tests for Inspect's authoritative Creative Suite installer catalog."""
from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

import pytest


CATALOG_FILE = (Path(__file__).resolve().parents[4] /
                'CinterForgeOS/build/creativeSuite/shared/upstream_catalog.py')


def load_catalog():
    spec = importlib.util.spec_from_file_location('cinter_upstream_catalog_test', CATALOG_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_creative_suite_catalog_allows_source_only_rows_without_installers():
    catalog = load_catalog()
    entries = {row['name']: row for row in catalog.read_catalog()}
    for name in ('steamtinkerlaunch', 'cfvoxel', 'cfcode', 'leveldb'):
        assert entries[name]['downloadMode'] == 'repo'
    assert len(entries) == 15


def test_reference_installer_catalog_ignores_internal_and_source_only_rows():
    catalog = load_catalog()
    entries = catalog.read_catalog(installer_only=True)
    assert {row['name'] for row in entries} == {
        'cfimage', 'cfpaint', 'cfvector', 'cfdocument',
        'cf3d', 'cfvideo', 'cfaudio', 'cfphoto',
    }
    assert all(row['links']['macos'].startswith('https://') for row in entries)


def test_missing_download_mode_still_fails_for_real_installer(tmp_path, monkeypatch):
    catalog = load_catalog()
    csv_path = tmp_path / 'Creative Suite.csv'
    fields = [*catalog.REQUIRED, 'Source Acquisition', 'Git Repo URL']
    with csv_path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerow({
            'Name': 'broken-app',
            'Download Mode': '',
            'Source Acquisition': 'source',
            'Git Repo URL': 'https://github.com/example/broken-app',
            'Windows x64 URL': 'https://example.org/app.exe',
            'macOS ARM64 URL': 'https://example.org/app.dmg',
        })
    monkeypatch.setattr(catalog, 'CSV', csv_path)
    with pytest.raises(ValueError, match="line 2: invalid download mode ''"):
        catalog.read_catalog(installer_only=True)


def test_blank_mode_without_valid_repo_is_not_implicitly_accepted(tmp_path, monkeypatch):
    catalog = load_catalog()
    csv_path = tmp_path / 'Creative Suite.csv'
    fields = [*catalog.REQUIRED, 'Source Acquisition', 'Git Repo URL']
    with csv_path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerow({'Name': 'orphan', 'Source Acquisition': 'source',
                         'Git Repo URL': 'not-a-url'})
    monkeypatch.setattr(catalog, 'CSV', csv_path)
    with pytest.raises(ValueError, match="line 2: invalid download mode ''"):
        catalog.read_catalog()
