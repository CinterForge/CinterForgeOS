#!/bin/bash
# Installed Windows x64 CinterForge Audio: use SteamOS Heaven's pinned Proton runtime.
# This is a test/launch runtime, never a Windows build or package dependency.
set -uo pipefail
base="${CINTER_STEAMAPPS_ROOT:-/run/steamapps}/common/CinterForge/Applications/Windows/CinterForge Audio"
if [ "$#" -gt 0 ] && [ -d "$1" ]; then root=$1; shift
else
    [ -d "$base" ] || { echo "Windows CinterForge Audio installation missing: $base" >&2; exit 1; }
    root=$(find "$base" -mindepth 1 -maxdepth 1 -type d -print | LC_ALL=C sort -V | tail -n 1)
fi
[ -n "${root:-}" ] && [ -d "$root" ] || { echo "No installed Windows version under $base" >&2; exit 1; }
# Find the single versioned PE produced by Waf, not a hardcoded old build.
shopt -s nullglob
engines=("$root"/lib/ardour9/ardour-[0-9]*.exe)
if [ "${#engines[@]}" -ne 1 ]; then
    printf 'ERROR: expected exactly one installed native Ardour PE under %s (found %s)\n' "$root/lib/ardour9" "${#engines[@]}" >&2
    exit 1
fi
engine="${engines[0]}"
# Match the working Windows Heaven runtime. Give Ardour its own Proton prefix;
# do not touch Heaven's prefix or the historical standalone Wine prefix.
steam_root="$HOME/.local/share/Steam"
if ! /usr/lib/steamac/ensure-steam-proton --ensure; then
    echo "ERROR: Steam/Proton provisioning failed; see $HOME/.local/state/cinterforge/steam-provision.log" >&2
    exit 1
fi
# This exact location is also the Heaven launcher's SteamOS Proton selection.
# Do not select a host Wine build, search random Proton versions, or combine
# Proton DLLs with the installed application; the test must be reproducible.
proton="$steam_root/steamapps/common/Proton 11.0 (ARM64)/proton"
[ -f "$proton" ] || { echo "Missing SteamOS Proton launcher: $proton" >&2; exit 1; }
proton_version="$(dirname "$proton")/version"
if [ -f "$proton_version" ]; then
    printf '[CinterForgeOS] Proton version: %s\n' "$(head -n 1 "$proton_version")" >&2
fi
unset WINEPREFIX WINEARCH WINELOADER WINESERVER ARDOUR_DATA_PATH ARDOUR_CONFIG_PATH ARDOUR_DLL_PATH ARDOUR_SELF VAMP_PATH
export STEAM_COMPAT_CLIENT_INSTALL_PATH="$steam_root"
export STEAM_COMPAT_DATA_PATH="$root/Logs/ProtonPrefix"
export ENABLE_GAMESCOPE_WSI=0 DISABLE_GAMESCOPE_WSI=1
# Preserve explicit loader diagnostics requested by the caller. Default runs
# remain identical to Heaven; CINTER_AUDIO_DLL_DEBUG=1 enables a one-run trace.
if [ "${CINTER_AUDIO_DLL_DEBUG:-0}" = 1 ]; then
    # Records direct and LoadLibrary plugin DLLs, including Proton builtin vs native.
    export WINEDEBUG=+loaddll,+module
fi
mkdir -p "$STEAM_COMPAT_DATA_PATH"
# Ardour owns its startup environment. Delegate to its packaged launcher.
case "$root" in
    /*) windows_root="Z:$(printf '%s' "$root" | tr / '\\')" ;;
    *) echo "Windows Ardour installation must have an absolute path: $root" >&2; exit 1 ;;
esac
export ARDOUR_INSTALL_ROOT="$root"
export ARDOUR_WINDOWS_ROOT="$windows_root"
printf '[CinterForgeOS] Installed native Ardour PE: %s\n' "$engine" >&2
printf '[CinterForgeOS] SteamOS Proton runtime: %s\n' "$proton" >&2
printf '[CinterForgeOS] DLL trace: %s\n' "${WINEDEBUG:-disabled}" >&2
cd "$root/lib/ardour9" || exit 1
# Test-only runtime adapter. Windows package contains no Proton runtime bits.
exec "$proton" waitforexitandrun "$engine" "$@"
