#!/bin/sh
# Run the existing shared OpenGL probe against Windows/WGL through Proton.
# All executable code and logs reside in persistent host steamapps.
set -eu
scripts=/run/steamapps/common/CinterForge/Applications/Test/Scripts
probe="$scripts/test/cinterforge-gl-probe.exe"
proton="$HOME/.local/share/Steam/steamapps/common/Proton 11.0 (ARM64)/proton"
case "${1:-legacy}" in legacy|core43|compat43) mode=${1:-legacy} ;; *) echo 'Usage: Cinter gl-probe [legacy|core43|compat43]' >&2; exit 2 ;; esac
[ "$#" -le 1 ] || { echo 'Usage: Cinter gl-probe [legacy|core43|compat43]' >&2; exit 2; }
[ -s "$probe" ] || { echo "[GL probe] Windows executable not staged: $probe" >&2; exit 1; }
[ -f "$proton" ] || { echo "[GL probe] Proton missing: $proton" >&2; exit 1; }
logs=/run/steamapps/common/CinterForge/Applications/Logs/Runtime
mkdir -p "$logs"
export STEAM_COMPAT_CLIENT_INSTALL_PATH="$HOME/.local/share/Steam"
export STEAM_COMPAT_DATA_PATH="$HOME/.local/share/cinterforge/proton-gl-probe"
export ENABLE_GAMESCOPE_WSI=0 DISABLE_GAMESCOPE_WSI=1
export PROTON_USE_WINED3D=1
mkdir -p "$STEAM_COMPAT_DATA_PATH"
# The Windows console's stdout is not reliably forwarded by Proton/FEX.
# The probe writes its result into C:, mapped to this exact prefix path.
result="$STEAM_COMPAT_DATA_PATH/pfx/drive_c/cinterforge-wgl-probe-result.log"
rm -f "$result"
printf '[GL probe] Windows WGL mode=%s executable=%s\n' "$mode" "$probe"
status=0
"$proton" run "$probe" "$mode" > "$logs/Windows-WGL-$mode.log" 2>&1 || status=$?
cat "$logs/Windows-WGL-$mode.log"
if [ -f "$result" ]; then
    cat "$result"
else
    echo "[GL probe] Windows result file missing: $result" >&2
fi
# Proton may return success even if the Windows process never started.
# Require the probe to reach its own final result and report success.
if [ "$status" -ne 0 ]; then
    printf "[GL probe] FAIL: Proton returned %s\n" "$status" >&2
    exit "$status"
fi
if ! [ -f "$result" ] || ! grep -Fqx "[wgl-probe] exit=0" "$result"; then
    echo "[GL probe] FAIL: Windows probe did not report successful WGL rendering" >&2
    exit 1
fi
echo "[GL probe] PASS: Windows WGL rendering verified"
