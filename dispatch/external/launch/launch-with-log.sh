#!/bin/bash
# First-stage KDE application launcher: log *before* entering the app launcher.
# Used to diagnose missing staged scripts/binaries and failed runtime startup.
set -uo pipefail
case "${1:-}" in
    linux) kind=Ardour-Linux; target=launch-cfaudio-linux ;;
    windows) kind=Ardour-Windows; target=launch-cfaudio-windows ;;
    *) echo 'Usage: launch-with-log linux|windows [arguments]' >&2; exit 2 ;;
esac
shift
stamp="$(date +%Y-%m-%d_%H-%M-%S)-$$"
root="/run/steamapps/common/CinterForge/Applications/Logs/$kind/$stamp"
if ! mkdir -p "$root" 2>/dev/null || ! ( : > "$root/.probe" ) 2>/dev/null; then
    echo "ERROR: Applications log share is not writable; refusing an unrecorded GUI test: $root" >&2
    exit 1
fi
rm -f "$root/.probe"
# Redirect before examining any runtime prerequisite. Both desktop clicks and
# absent executable/guest layer binaries therefore produce a persistent log.
exec > >(tee -a "$root/desktop-launch.log") 2>&1
printf '[desktop-launch] app=%s start=%s user=%s pid=%s\n' "$kind" "$(date -Is)" "$(id -un)" "$$"
printf '[desktop-launch] guest release: '; cat /etc/os-release | grep -E '^(NAME|VERSION|BUILD_ID)=' || true
printf '[desktop-launch] target=%s\n' "$target"
printf '[desktop-launch] target details: '; command -v Cinter || true
printf '[desktop-launch] guest layer: '; findmnt -T /run/steamapps/common/CinterForge/Applications/Test -o TARGET,SOURCE,FSTYPE,OPTIONS -n || true
printf '[desktop-launch] Applications: '; findmnt -T /run/steamapps/common/CinterForge/Applications -o TARGET,SOURCE,FSTYPE,OPTIONS -n || true
printf '[desktop-launch] env session=%s display=%s wayland=%s path=%s\n' "${XDG_SESSION_TYPE:-}" "${DISPLAY:-}" "${WAYLAND_DISPLAY:-}" "$PATH"
printf '\n[desktop-launch] installed tree (top levels)\n'
platform=Linux
[[ $kind == Ardour-Windows ]] && platform=Windows
base="/run/steamapps/common/CinterForge/Applications/$platform/CinterForge Audio"
ls -lad "/run/steamapps/common/CinterForge/Applications/$platform" "$base" 2>&1 || true
if [[ -d "$base" ]]; then
    find "$base" -maxdepth 4 -mindepth 1 \( -type f -o -type d \) -printf '%y %p\n' 2>&1 | head -250 || true
fi
printf '\n[desktop-launch] runtime commands\n'
for tool in bash FEX FEXBash wine wine64 steam file readelf; do command -v "$tool" || true; done
if ! command -v Cinter >/dev/null 2>&1; then
    printf '[desktop-launch] ERROR: staged launcher missing from guest layer: %s\n' "$target"
    code=127
elif [[ ! -x "/run/steamapps/common/CinterForge/Applications/Test/Scripts/launch/${target}.sh" ]]; then
    printf '[desktop-launch] ERROR: staged launcher not executable: %s\n' "$target"
    code=126
else
    printf '\n[desktop-launch] invoking target...\n'
    Cinter "$target" "$@"
    code=$?
fi
printf '\n[desktop-launch] exit=%s at=%s log=%s\n' "$code" "$(date -Is)" "$root"
printf '%s\n' "$code" > "$root/exit-code.txt"
printf '%s\n' "$target" > "$root/launcher-path.txt"
# Give tee a moment to flush before a GUI window closes.
sleep 1
exit "$code"
