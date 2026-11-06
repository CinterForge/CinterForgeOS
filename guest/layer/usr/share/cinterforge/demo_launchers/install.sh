#!/usr/bin/env bash
set -euo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
target="$HOME/.local/share/cinterforge/demo-launchers"
desktop="${XDG_DESKTOP_DIR:-$HOME/Desktop}"
mkdir -p "$target" "$desktop"
install -m 755 "$here/linux-heaven.sh" "$target/linux-heaven.sh"
install -m 755 "$here/windows-heaven.sh" "$target/windows-heaven.sh"
install -m 755 "$here/Heaven-Linux.desktop" "$desktop/Heaven-Linux.desktop"
install -m 755 "$here/Heaven-Windows.desktop" "$desktop/Heaven-Windows.desktop"
if command -v gio >/dev/null 2>&1; then
  gio set "$desktop/Heaven-Linux.desktop" metadata::trusted true 2>/dev/null || true
  gio set "$desktop/Heaven-Windows.desktop" metadata::trusted true 2>/dev/null || true
fi
printf 'Installed both KDE launchers into %s\n' "$desktop"
