#!/usr/bin/env python3
"""Resolve the actual local Steam library from Steam's libraryfolders.vdf."""
import os
from pathlib import Path
import re
import sys


def steamapps():
    home = Path.home()
    steam = home / 'Library/Application Support/Steam'
    files = [steam / 'config/libraryfolders.vdf', steam / 'steamapps/libraryfolders.vdf']
    candidates = []
    for file in files:
        if not file.is_file():
            continue
        content = file.read_text(encoding='utf-8-sig', errors='replace')
        # Steam's KeyValues format quotes paths and escapes backslashes.
        for match in re.finditer(r'"path"\s+"((?:\\.|[^"\\])*)"', content, re.I):
            path = match.group(1).replace('\\\\', '\\').replace('\\"', '"')
            base = Path(os.path.expanduser(path))
            candidate = base if base.name == 'steamapps' else base / 'steamapps'
            if candidate.is_dir() and candidate not in candidates:
                candidates.append(candidate)
    default = steam / 'steamapps'
    if default.is_dir() and default not in candidates:
        candidates.append(default)
    if not candidates:
        raise RuntimeError('No installed Steam library found in Steam libraryfolders.vdf')
    # Prefer the library containing an existing CinterForge installation.
    for candidate in candidates:
        if (candidate / 'common/CinterForge/Applications').is_dir():
            return candidate
    # For fresh installs, prefer a configured external library over the default.
    for candidate in candidates:
        if candidate != default:
            return candidate
    return candidates[0]


if __name__ == '__main__':
    try:
        print(steamapps())
    except (OSError, RuntimeError) as exc:
        print(f'CFOS: Steam library discovery failed: {exc}', file=sys.stderr)
        sys.exit(1)
