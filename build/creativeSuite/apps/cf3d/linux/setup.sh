#!/bin/sh
# Blender: acquire a distributable build. NOT a source compilation.
set -eu
: "${ROOT:?}" "${DEBIAN:?}"
DEST="$ROOT/Installed/Blender"
mkdir -p "$DEST"
# A provided archive or URL is an explicit prebuilt input, never a fabricated build.
# The guest's persistent Debian filesystem is the package manager's authority.
if [ -n "${CINTER_APP_ARCHIVE:-}" ]; then
  case "$CINTER_APP_ARCHIVE" in
    *.tar.gz|*.tgz|*.tar.xz|*.tar.bz2) tar -xf "$CINTER_APP_ARCHIVE" -C "$DEST" ;;
    *.AppImage|*.appimage) cp "$CINTER_APP_ARCHIVE" "$DEST/application.AppImage"; chmod 755 "$DEST/application.AppImage" ;;
    *.deb) cp "$CINTER_APP_ARCHIVE" "$DEBIAN/tmp/cinter-app-package.deb"; chroot "$DEBIAN" apt-get install -y /tmp/cinter-app-package.deb ;;
    *) echo "CINTER_CREATIVE_SUITE_ERROR unsupported-archive $CINTER_APP_ARCHIVE" >&2; exit 2 ;;
  esac
  echo 'CINTER_CREATIVE_SUITE_PREBUILT_STAGED app=Blender'
elif [ -n "${CINTER_APP_URL:-}" ]; then
  case "$CINTER_APP_URL" in https://*) ;; *) echo 'CINTER_CREATIVE_SUITE_ERROR https-url-required' >&2; exit 2 ;; esac
  archive="$DEBIAN/tmp/cinter-cf3d-download"
  # Downloaded artifact must have an explicit type and SHA256; never silently trust it.
  : "${CINTER_APP_SHA256:?CINTER_APP_SHA256 required for downloads}"
  : "${CINTER_APP_FORMAT:?CINTER_APP_FORMAT required for downloads}"
  chroot "$DEBIAN" /usr/bin/curl --fail --location --retry 4 --output /tmp/cinter-cf3d-download "$CINTER_APP_URL"
  printf '%s  %s\n' "$CINTER_APP_SHA256" "$archive" | sha256sum -c -
  case "$CINTER_APP_FORMAT" in
    tar.gz|tgz|tar.xz|tar.bz2) tar -xf "$archive" -C "$DEST" ;;
    AppImage) cp "$archive" "$DEST/application.AppImage"; chmod 755 "$DEST/application.AppImage" ;;
    deb) chroot "$DEBIAN" apt-get install -y /tmp/cinter-cf3d-download ;;
    *) echo 'CINTER_CREATIVE_SUITE_ERROR unsupported-download-format' >&2; exit 2 ;;
  esac
  echo 'CINTER_CREATIVE_SUITE_PREBUILT_DOWNLOADED app=Blender'
else
  chroot "$DEBIAN" apt-get update
  chroot "$DEBIAN" env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends cf3d
  echo 'CINTER_CREATIVE_SUITE_DISTRO_PACKAGE_INSTALLED app=Blender'
fi

#!/usr/bin/env python3
"""Prepared Linux application acquisition and smoke contract for cf3d.

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

APPLICATION = 'Blender'
OUTPUTS = ('blend', 'glb')


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
