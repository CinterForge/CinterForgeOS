#!/bin/sh
# Manually launch an ORIGINAL publisher app, never a CinterForge-built app.
set -eu
[ "$#" -eq 2 ] || { echo 'Usage: launch-unforked <Linux|Windows> <app>' >&2; exit 2; }
platform=$1 app=$2
case "$app" in GIMP|Krita|Inkscape|Scribus|Blender|Kdenlive|Ardour|cfdocument) ;; *) echo "Unknown upstream app: $app" >&2; exit 2 ;; esac
case "$platform" in
 Linux)
  # Prefer an actual installed executable inside the FEX x86_64 rootfs.
  root=/usr/share/cinterforge/fex/RootFS
  case "$app" in GIMP) exe=cfimage ;; Krita) exe=cfpaint ;; Inkscape) exe=cfvector ;; Scribus) exe=cfdocument ;; Blender) exe=cf3d ;; Kdenlive) exe=cfvideo ;; Ardour) exe=cfaudio ;; cfdocument) exe=rapidraw ;; esac
  if [ -x "$root/usr/bin/$exe" ]; then
    exec Cinter run-linux-amd64 "$root/usr/bin/$exe"
  fi
  # Original publisher distributions live on shared Steam storage. A tarball,
  # deb, run installer or AppImage is NOT evidence of an installed executable.
  echo "Original Linux $app is not installed in the FEX userspace ($root/usr/bin/$exe)." >&2
  echo "Installer: /run/steamapps/common/CinterForge/Applications/UnforkedInstallers/Linux/$app" >&2
  exit 1 ;;
 Windows)
  prefix="$HOME/.local/share/cinterforge/reference-wine/$app"
  [ -d "$prefix/drive_c" ] || { echo "Original Windows $app not installed in Wine prefix: $prefix" >&2; exit 1; }
  # Each upstream app has its own prefix. Require an unambiguous installed exe.
  case "$app" in
   GIMP) pattern='cfimage-*.exe' ;; Krita) pattern='cfpaint.exe' ;; Inkscape) pattern='cfvector.exe' ;;
   Scribus) pattern='cfdocument.exe' ;; Blender) pattern='cf3d.exe' ;; Kdenlive) pattern='cfvideo.exe' ;;
   Ardour) pattern='cfaudio*.exe' ;; cfdocument) pattern='rapidraw.exe' ;;
  esac
  found=$(find "$prefix/drive_c/Program Files" "$prefix/drive_c/Program Files (x86)" -type f -iname "$pattern" 2>/dev/null || true)
  count=$(printf '%s\n' "$found" | sed '/^$/d' | wc -l)
  [ "$count" -eq 1 ] || { echo "Expected exactly one installed $app executable under $prefix; found $count." >&2; printf '%s\n' "$found" >&2; exit 1; }
  export WINEPREFIX="$prefix"
  exec Cinter cinterforge-wine "$found" ;;
 *) echo "Unsupported platform: $platform" >&2; exit 2 ;;
esac
