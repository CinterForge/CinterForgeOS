#!/usr/bin/env python3
"""Prepared Linux application acquisition and smoke contract for blockbench.

Extracted from existing Creative Suite Update Data pipeline. Intentionally
not connected to Cinter command dispatch until the second integration pass.
Shared implementation is local to creativeSuite/shared/linux; no legacy calls.
"""
from __future__ import annotations
import sys
from pathlib import Path

SUITE = Path(__file__).resolve().parents[3]
if str(SUITE) not in sys.path:
    sys.path.insert(0, str(SUITE))
from shared.linux import linux_packages as packages, linux_smoke_tests as smoke

APPLICATION = None
OUTPUTS = None


def find_entry(plan: dict) -> dict:
    if APPLICATION is None:
        raise RuntimeError('This application has no legacy Linux installation-plan entry; use its prepared_build.sh')
    matches = [entry for entry in plan['applications'] if entry['name'] == APPLICATION]
    if len(matches) != 1:
        raise RuntimeError(f'Expected exactly one Linux installation plan entry for {APPLICATION}, found {len(matches)}')
    return matches[0]


def acquire(entry: dict, destination: Path) -> dict:
    """Stage the legacy acquisition format through the NEW shared implementation."""
    return packages.stage_one(entry, destination)


def validate_outputs(native: Path, exported: Path) -> None:
    if OUTPUTS is None:
        raise RuntimeError('No existing document smoke contract for this app')
    smoke.validate_format(native, OUTPUTS[0])
    smoke.validate_format(exported, OUTPUTS[1])
