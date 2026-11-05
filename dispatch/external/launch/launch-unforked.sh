#!/bin/sh
# Manually launch an ORIGINAL publisher app, never a CinterForge-built app.
set -eu
[ "$#" -eq 2 ] || { echo 'Usage: launch-unforked <Linux|Windows> <app>' >&2; exit 2; }
platform=$1 app=$2
case "$app" in GIMP|Krita|Inkscape|Scribus|Blender|Kdenlive|Ardour|cfdocument) ;; *) echo "Unknown upstream app: $app" >&2; exit 2 ;; esac
case "$platform" in
 Linux)
  # First-class portable Linux publisher installation in persistent Steam
  # storage. The fork has a separate Linux/<CinterForgeApp> destination.
  installed=/run/steamapps/common/CinterForge/Applications/Unforked/Linux/$app
  descriptor="$installed/executable.txt"
  if [ -f "$descriptor" ]; then
    relative=$(cat "$descriptor")
    case "$relative" in /*|*..*|'') echo "Unsafe upstream executable path: $relative" >&2; exit 1 ;; esac
    program="$installed/$relative"
    [ -f "$program" ] && [ -x "$program" ] || { echo "Missing installed upstream executable: $program" >&2; exit 1; }
    # AppImages run extracted on demand when FUSE is unavailable in SteamOS.
    export APPIMAGE_EXTRACT_AND_RUN=1
    runner="${CINTERFORGE_TEST_SCRIPTS_DIRECTORY:-/run/steamapps/common/CinterForge/Applications/Test/Scripts}/run/run-linux-amd64.sh"
    [ -x "$runner" ] || { echo "Missing CFOS Linux/FEX runner: $runner" >&2; exit 1; }
    exec "$runner" "$program"
  fi
  # The distribution's own FEX RootFS can also contain upstream applications
  # installed as native x86 packages. Never substitute branded 'cf*' binaries.
  root=/usr/share/cinterforge/fex/RootFS
  case "$app" in GIMP) exe=gimp ;; Krita) exe=krita ;; Inkscape) exe=inkscape ;;
      Scribus) exe=scribus ;; Blender) exe=blender ;; Kdenlive) exe=kdenlive ;;
      Ardour) exe=ardour ;; cfdocument) exe=rapidraw ;; esac
  if [ -x "$root/usr/bin/$exe" ]; then
    runner="${CINTERFORGE_TEST_SCRIPTS_DIRECTORY:-/run/steamapps/common/CinterForge/Applications/Test/Scripts}/run/run-linux-amd64.sh"
    [ -x "$runner" ] || { echo "Missing CFOS Linux/FEX runner: $runner" >&2; exit 1; }
    exec "$runner" "$root/usr/bin/$exe"
  fi
  echo "Original Linux $app is not installed; use Cinter unforked install Linux $app." >&2
  exit 1 ;;
 Windows)
  prefix="$HOME/.local/share/cinterforge/reference-wine/$app"
  [ -d "$prefix/drive_c" ] || { echo "Original Windows $app not installed in Wine prefix: $prefix" >&2; exit 1; }
  # Each upstream app has its own prefix. Require an unambiguous installed exe.
  case "$app" in
   GIMP) pattern='gimp*.exe' ;; Krita) pattern='krita.exe' ;; Inkscape) pattern='inkscape.exe' ;;
   Scribus) pattern='scribus.exe' ;; Blender) pattern='blender.exe' ;; Kdenlive) pattern='kdenlive.exe' ;;
   Ardour) pattern='ardour*.exe' ;; cfdocument) pattern='rapidraw.exe' ;;
  esac
  found=$(find "$prefix/drive_c/Program Files" "$prefix/drive_c/Program Files (x86)" -type f -iname "$pattern" 2>/dev/null || true)
  count=$(printf '%s\n' "$found" | sed '/^$/d' | wc -l)
  [ "$count" -eq 1 ] || { echo "Expected exactly one installed $app executable under $prefix; found $count." >&2; printf '%s\n' "$found" >&2; exit 1; }
  export WINEPREFIX="$prefix"
  runner="${CINTERFORGE_TEST_SCRIPTS_DIRECTORY:-/run/steamapps/common/CinterForge/Applications/Test/Scripts}/wine/cinterforge-wine.sh"
  [ -x "$runner" ] || { echo "Missing CFOS Wine/FEX runner: $runner" >&2; exit 1; }
  exec "$runner" "$found" ;;
 *) echo "Unsupported platform: $platform" >&2; exit 2 ;;
esac
