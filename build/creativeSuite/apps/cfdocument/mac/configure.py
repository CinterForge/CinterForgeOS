#!/usr/bin/env python3
"""Scribus-owned Qt6, ICU and GraphicsMagick CMake configuration."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess


def append_cmake_options(command: list[str], built: Path) -> dict[str, str]:
    command.append("-DBUILD_OSX_BUNDLE=ON")
    qt_prefix = subprocess.check_output(
        ["brew", "--prefix", "qt"], text=True
    ).strip()
    qt_config = Path(qt_prefix) / "lib/cmake/Qt6/Qt6Config.cmake"
    if not qt_config.is_file():
        raise RuntimeError(f"Qt6 is missing: {qt_config}; run Setup first")
    # Scribus overwrites CMAKE_PREFIX_PATH from QT_PREFIX internally.
    command.append("-DQT_PREFIX=" + qt_prefix)
    # ICU is keg-only in Homebrew; harfbuzz-icu.pc references icu-uc.
    icu_prefix = subprocess.check_output(
        ["brew", "--prefix", "icu4c@79"], text=True
    ).strip()
    icu_pkgconfig = Path(icu_prefix) / "lib/pkgconfig"
    if not (icu_pkgconfig / "icu-uc.pc").is_file():
        raise RuntimeError(f"ICU metadata missing: {icu_pkgconfig / 'icu-uc.pc'}; run Scribus Setup")
    environment = os.environ.copy()
    environment["PKG_CONFIG_PATH"] = os.pathsep.join(filter(None, (
        str(icu_pkgconfig), environment.get("PKG_CONFIG_PATH", "")
    )))
    # Preserve upstream's optional GraphicsMagick image import capability.
    command.append("-DWANT_GRAPHICSMAGICK=1")
    return environment


def build_environment(built: Path) -> dict[str, str]:
    """Reconstruct application-owned pkg-config environment for Build/Install."""
    command: list[str] = []
    return append_cmake_options(command, built)
