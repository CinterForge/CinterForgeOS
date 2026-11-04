#!/bin/sh
# CinterForgeOS x86 runtime diagnosis. Read-only; no deceptive placeholder installs.
set -eu
status=0
printf '[CinterForgeOS] guest architecture: %s\n' "$(uname -m)"
case "$(uname -m)" in
 aarch64|arm64)
  if command -v FEX >/dev/null 2>&1; then
    printf '[PASS] FEX executable: %s\n' "$(command -v FEX)"
  else
    printf '[FAIL] FEX emulator executable missing (FEXBash alone is not sufficient)\n'
    status=1
  fi
  if command -v FEXRootFSFetcher >/dev/null 2>&1; then
    printf '[INFO] FEXRootFSFetcher: %s\n' "$(command -v FEXRootFSFetcher)"
  fi
  if [ -d "$HOME/.fex-emu/RootFS" ] && [ -n "$(find "$HOME/.fex-emu/RootFS" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
    printf '[INFO] FEX RootFS candidates in %s/.fex-emu/RootFS\n' "$HOME"
  elif [ -d /usr/share/fex-emu/RootFS ]; then
    printf '[INFO] System FEX RootFS directory available\n'
  else
    printf '[FAIL] FEX x86-64 RootFS not installed\n'
    status=1
  fi ;;
 x86_64) printf '[PASS] Native x86-64 guest; FEX unnecessary\n' ;;
 *) printf '[FAIL] Unsupported architecture\n'; status=1 ;;
esac
if command -v wine >/dev/null 2>&1; then
 printf '[INFO] Native Wine command: %s\n' "$(command -v wine)"
else
 printf '[FAIL] Wine not available on host PATH; Windows programs need a compatible x86-64 Wine inside the FEX RootFS or a native ARM64EC Wine build\n'
 status=1
fi
printf '[INFO] FEXBash present: '
command -v FEXBash || true
printf '[INFO] Steam FEX tool directory: '
if [ -d "$HOME/.steam/steam/steamapps/common/FEX-Emu" ]; then echo present; else echo absent; fi
exit "$status"
