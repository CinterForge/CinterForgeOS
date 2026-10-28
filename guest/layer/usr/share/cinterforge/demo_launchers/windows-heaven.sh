#!/usr/bin/env bash
# Unigine Heaven (Windows) via Proton; the Steam library is the only host share.
set -uo pipefail
app='/run/steamapps/common/CinterForge/Applications/Windows/Heaven/2026/app'
steamapps='/run/steamapps'
logdir="$HOME/.local/share/cinterforge/logs"
mkdir -p "$logdir"
log="$logdir/heaven-windows.log"
exec > >(tee -a "$log") 2>&1
# Avoid silently closing the terminal; the desktop entry opens Konsole with --hold.
printf '\n[%s] Starting Windows Heaven\n' "$(date -Is)"
if ! mountpoint -q "$steamapps"; then
  echo "ERROR: $steamapps is not mounted. Check CINTER_STEAM_APPS_ROOT on the macOS host." >&2
  exit 1
fi
if [[ ! -d "$app" ]]; then
  echo "ERROR: Windows Heaven installation not found at $app" >&2
  exit 1
fi
# Prefer the installed Heaven launcher because it owns the correct graphics
# parameters. The x86 executable is a fallback for stripped distributions.
exe=''
for candidate in "$app/bin/browser_x86.exe" "$app/bin/heaven.exe" "$app/heaven.exe"; do
  if [[ -f "$candidate" ]]; then exe="$candidate"; break; fi
done
if [[ -z "$exe" ]]; then
  echo "ERROR: No Windows Heaven executable found under $app" >&2
  find "$app" -maxdepth 3 \( -iname '*heaven*.exe' -o -iname 'browser*.exe' \) -print >&2 || true
  exit 1
fi
steam_root="$HOME/.local/share/Steam"
if ! /usr/lib/steamac/ensure-steam-proton --ensure; then
  echo "ERROR: Steam/Proton provisioning failed; see $HOME/.local/state/cinterforge/steam-provision.log" >&2
  exit 1
fi
proton="$steam_root/steamapps/common/Proton 11.0 (ARM64)/proton"
export STEAM_COMPAT_CLIENT_INSTALL_PATH="$steam_root"
export STEAM_COMPAT_DATA_PATH="$HOME/.local/share/cinterforge/proton-heaven"
export ENABLE_GAMESCOPE_WSI=0 DISABLE_GAMESCOPE_WSI=1
mkdir -p "$STEAM_COMPAT_DATA_PATH"
printf 'Proton: %s\nExecutable: %s\nLog: %s\n' "$proton" "$exe" "$log"
cd "$app/bin"
# Proton handles the x86-64 Windows executable and DXVK. Do not pass an
# invented configuration XML path or launch the game with FEXBash directly.
# Preserve the exit status and diagnostics. Proton may return immediately
# because its own Wine/Proton prerequisites are unavailable.
"$proton" waitforexitandrun "$exe" -config ../data/launcher/launcher.xml
status=$?
printf 'Windows Heaven/Proton exited with status %s\n' "$status"
exit "$status"
