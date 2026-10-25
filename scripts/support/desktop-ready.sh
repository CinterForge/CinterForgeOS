#!/bin/sh
# Invoked by KDE after the user desktop session starts. Edit this file in
# Applications/macOS/CinterForgeOS/<version>/Support/ without rebuilding.
# Runs in the SteamOS guest as steamos; no sudo or root privileges.
set -eu
printf '[CinterForgeOS Support] desktop ready: user=%s session=%s desktop=%s\n' "$(id -un)" "${XDG_SESSION_TYPE:-unset}" "${XDG_CURRENT_DESKTOP:-unset}"

# Keep benchmark launchers in persistent user data, not the disposable RootFS.
# The actual benchmarks stay in their existing versioned Applications folders.
launchers="$HOME/.local/share/cinterforge/benchmark-launchers"
desktop="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
[ -n "$desktop" ] || desktop="$HOME/Desktop"
mkdir -p "$launchers" "$desktop"

cat > "$launchers/linux-heaven.sh" <<'LAUNCHER'
#!/bin/sh
set -eu
app='/run/steamapps/common/CinterForge/Applications/Linux/Superposition/2026/Unigine_Heaven-4.0'
[ -f "$app/heaven" ] || { printf >&2 'Linux Heaven executable not found: %s/heaven\n' "$app"; exit 1; }
cd "$app"
export ENABLE_GAMESCOPE_WSI=0 DISABLE_GAMESCOPE_WSI=1
exec FEXBash -c './heaven'
LAUNCHER

cat > "$launchers/windows-heaven.sh" <<'LAUNCHER'
#!/bin/sh
set -eu
app='/run/steamapps/common/CinterForge/Applications/Windows/Heaven/2026/app'
proton="$HOME/.local/share/Steam/steamapps/common/Proton 11.0 (ARM64)/proton"
[ -f "$app/bin/browser_x86.exe" ] || { printf >&2 'Windows Heaven executable not found: %s/bin/browser_x86.exe\n' "$app"; exit 1; }
[ -f "$proton" ] || { printf >&2 'Proton not found: %s\n' "$proton"; exit 1; }
export STEAM_COMPAT_CLIENT_INSTALL_PATH="$HOME/.local/share/Steam"
export STEAM_COMPAT_DATA_PATH="$HOME/.local/share/cinterforge/proton-heaven"
export ENABLE_GAMESCOPE_WSI=0 DISABLE_GAMESCOPE_WSI=1
mkdir -p "$STEAM_COMPAT_DATA_PATH"
cd "$app/bin"
exec "$proton" waitforexitandrun "$app/bin/browser_x86.exe" -config ../data/launcher/launcher.xml
LAUNCHER
chmod 755 "$launchers/linux-heaven.sh" "$launchers/windows-heaven.sh"

cat > "$desktop/CinterForgeOS Linux Heaven.desktop" <<EOF_DESKTOP
[Desktop Entry]
Type=Application
Version=1.0
Name=Heaven 4.0 — Linux x86-64 (FEX)
Comment=Native Linux OpenGL Heaven benchmark on CinterForgeOS
Exec=$launchers/linux-heaven.sh
Icon=applications-games
Terminal=true
Categories=Game;Graphics;
StartupNotify=true
EOF_DESKTOP

cat > "$desktop/CinterForgeOS Windows Heaven.desktop" <<EOF_DESKTOP
[Desktop Entry]
Type=Application
Version=1.0
Name=Heaven 4.0 — Windows x86-64 (Proton)
Comment=Windows DirectX Heaven benchmark through FEX, Proton and DXVK
Exec=$launchers/windows-heaven.sh
Icon=applications-games
Terminal=true
Categories=Game;Graphics;
StartupNotify=true
EOF_DESKTOP
chmod 755 "$desktop/CinterForgeOS Linux Heaven.desktop" "$desktop/CinterForgeOS Windows Heaven.desktop"
printf '[CinterForgeOS Support] Heaven desktop launchers installed: %s\n' "$desktop"
