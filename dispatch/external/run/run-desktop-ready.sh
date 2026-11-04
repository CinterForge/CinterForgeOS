#!/bin/sh
# Called by KDE XDG autostart through the persistent Cinter dispatcher.
# This is the active guest desktop-ready hook (not the legacy macOS Support hook).
set -eu

home=${HOME:-/home/steamos}
launchers="$home/.local/share/cinterforge/benchmark-launchers"
desktop="$home/Desktop"
applications="$home/.local/share/applications"
mkdir -p "$launchers" "$desktop" "$applications"

cat > "$launchers/heaven-linux.sh" <<'LAUNCHER'
#!/bin/sh
set -eu
app=/run/steamapps/common/CinterForge/Applications/Linux/Superposition/2026/Unigine_Heaven-4.0
[ -f "$app/heaven" ] || { printf >&2 'Linux Heaven executable missing: %s/heaven\n' "$app"; exit 1; }
cd "$app"
export ENABLE_GAMESCOPE_WSI=0 DISABLE_GAMESCOPE_WSI=1
exec FEXBash -c './heaven'
LAUNCHER

cat > "$launchers/heaven-windows.sh" <<'LAUNCHER'
#!/bin/sh
set -eu
app=/run/steamapps/common/CinterForge/Applications/Windows/Heaven/2026/app
proton="$HOME/.local/share/Steam/steamapps/common/Proton 11.0 (ARM64)/proton"
[ -f "$app/bin/browser_x86.exe" ] || { printf >&2 'Windows Heaven launcher missing: %s/bin/browser_x86.exe\n' "$app"; exit 1; }
[ -f "$proton" ] || { printf >&2 'Proton missing: %s\n' "$proton"; exit 1; }
export STEAM_COMPAT_CLIENT_INSTALL_PATH="$HOME/.local/share/Steam"
export STEAM_COMPAT_DATA_PATH="$HOME/.local/share/cinterforge/proton-heaven"
export ENABLE_GAMESCOPE_WSI=0 DISABLE_GAMESCOPE_WSI=1
mkdir -p "$STEAM_COMPAT_DATA_PATH"
cd "$app/bin"
exec "$proton" waitforexitandrun "$app/bin/browser_x86.exe" -config ../data/launcher/launcher.xml
LAUNCHER
chmod 755 "$launchers/heaven-linux.sh" "$launchers/heaven-windows.sh"

cat > "$desktop/cinterforge-heaven-linux.desktop" <<EOF_LINUX
[Desktop Entry]
Type=Application
Version=1.0
Name=Heaven 4.0 (Linux x86-64)
Comment=UNIGINE Heaven via FEX and OpenGL
Exec=$launchers/heaven-linux.sh
Icon=applications-games
Terminal=true
Categories=Game;Graphics;
StartupNotify=true
EOF_LINUX

cat > "$desktop/cinterforge-heaven-windows.desktop" <<EOF_WINDOWS
[Desktop Entry]
Type=Application
Version=1.0
Name=Heaven 4.0 (Windows x86-64)
Comment=UNIGINE Heaven via Proton and DXVK
Exec=$launchers/heaven-windows.sh
Icon=applications-games
Terminal=true
Categories=Game;Graphics;
StartupNotify=true
EOF_WINDOWS
chmod 755 "$desktop/cinterforge-heaven-linux.desktop" "$desktop/cinterforge-heaven-windows.desktop"
# Register with KDE's application menu as well as the desktop folder.
cp "$desktop/cinterforge-heaven-linux.desktop" "$applications/cinterforge-heaven-linux.desktop"
cp "$desktop/cinterforge-heaven-windows.desktop" "$applications/cinterforge-heaven-windows.desktop"
chmod 644 "$applications/cinterforge-heaven-linux.desktop" "$applications/cinterforge-heaven-windows.desktop"
if command -v kbuildsycoca6 >/dev/null 2>&1; then
    kbuildsycoca6 --noincremental >/dev/null 2>&1 || true
elif command -v kbuildsycoca5 >/dev/null 2>&1; then
    kbuildsycoca5 --noincremental >/dev/null 2>&1 || true
fi
printf '[CinterForgeOS] Installed Heaven desktop icons in %s and KDE application entries in %s\n' "$desktop" "$applications"

# Preserve existing per-application smoke testing on desktop readiness.
hook=/run/steamapps/common/CinterForge/Applications/Test/Scripts/cinterforgeis_ready.sh
if [ ! -r "$hook" ]; then
    logger -t cinterforge-support "ERROR: Test automation hook missing: $hook" || true
    exit 1
fi
/bin/sh "$hook" 2>&1 | logger -t cinterforge-support
