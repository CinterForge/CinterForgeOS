#!/bin/sh
# Launch a staged CinterForge app; never fall back to an original publisher install.
set -eu
[ "$#" -ge 2 ] || { echo 'Usage: launch-cinterforge <Linux|Windows> <app>' >&2; exit 2; }
platform=$1 app=$2
shift 2
case "$platform" in Linux|Windows) ;; *) echo "Unsupported platform: $platform" >&2; exit 2 ;; esac
case "$app" in GIMP|Krita|Inkscape|Scribus|Blender|Kdenlive|Ardour|cfdocument) ;; *) echo "Unknown app: $app" >&2; exit 2 ;; esac
# Branded products are installed under their own names, not upstream labels.
case "$app" in
 GIMP) product='CinterForge Image' ;; Krita) product='CinterForge Paint' ;;
 Inkscape) product='CinterForge Vector' ;; Scribus) product='CinterForge Document' ;;
 Blender) product='CinterForge 3D' ;; Kdenlive) product='CinterForge Video' ;;
 Ardour) product='CinterForge Audio' ;; cfdocument) product='CinterForge Photo' ;;
esac
base="/run/steamapps/common/CinterForge/Applications/$platform/$product"
[ -d "$base" ] || { echo "CinterForge $platform $app is not staged: $base" >&2; exit 1; }
root=$(find "$base" -mindepth 1 -maxdepth 1 -type d -print | LC_ALL=C sort -V | tail -n 1)
[ -n "$root" ] || { echo "No installed version in $base" >&2; exit 1; }
if [ "$app" = Ardour ]; then
    # Only the dedicated Audio launcher knows its installed versioned PE and
    # test-only Proton runtime. Do not maintain another Windows launch path.
    if [ "$platform" = Windows ]; then
        exec Cinter launch-cfaudio-windows "$root" "$@"
    fi
    exec Cinter launch-cfaudio-linux "$root" "$@"
fi
case "$app" in Ardour) exe=cfaudio ;; GIMP) exe=cfimage ;; Krita) exe=cfpaint ;; Inkscape) exe=cfvector ;;
 Scribus) exe=cfdocument ;; Blender) exe=cf3d ;; Kdenlive) exe=cfvideo ;; cfdocument) exe=rapidraw ;; esac
# Detect the actual staged executable, including upstream's self-contained
# distribution subfolder (for example cf3d-5.x-linux-x64/cf3d). Never
# substitute an executable from an original-publisher installation.
# A release with multiple matching executables is ambiguous and must fail.
find_installed_executable() {
    if [ "$app" = Ardour ] && [ "$platform" = Windows ]; then
        find "$root/lib/ardour9" -maxdepth 1 -type f -name 'cfaudio-[0-9]*.exe' -print
    elif [ "$platform" = Linux ]; then
        find "$root" -maxdepth 5 -type f \
            \( -name "$exe" -o -name "$exe-[0-9]*" \) -perm /111 -print
    else
        find "$root" -maxdepth 5 -type f \
            \( -iname "$exe.exe" -o -iname "$app.exe" -o -iname "$exe-[0-9]*.exe" \) -print
    fi
}
# Restrict candidates to the real platform binary format, not launch scripts,
# Python helpers or a bundled tool from another architecture.
selected=
count=0
while IFS= read -r candidate; do
    [ -n "$candidate" ] || continue
    magic=$(od -An -tx1 -N4 "$candidate" 2>/dev/null | tr -d ' \n')
    case "$platform:$magic" in
        Linux:7f454c46|Windows:4d5a*) ;;
        *) continue ;;
    esac
    selected=$candidate
    count=$((count + 1))
done <<EOF
$(find_installed_executable)
EOF
if [ "$count" -ne 1 ]; then
    echo "Expected one $platform executable for CinterForge $app under $root; found $count" >&2
    find "$root" -maxdepth 3 -type f \( -name "$exe" -o -iname "$exe.exe" \) -print >&2
    exit 1
fi
printf '[CinterForgeOS] CinterForge %s %s: %s\n' "$platform" "$app" "$selected" >&2
if [ "$platform" = Linux ]; then
    exec Cinter run-linux-amd64 "$selected" "$@"
fi
proton="$HOME/.local/share/Steam/steamapps/common/Proton 11.0 (ARM64)/proton"
[ -f "$proton" ] || { echo "Proton runtime missing: $proton" >&2; exit 1; }
export STEAM_COMPAT_CLIENT_INSTALL_PATH="$HOME/.local/share/Steam"
export STEAM_COMPAT_DATA_PATH="$HOME/.local/share/cinterforge/proton-built-$app"
export ENABLE_GAMESCOPE_WSI=0 DISABLE_GAMESCOPE_WSI=1
mkdir -p "$STEAM_COMPAT_DATA_PATH"
cd "$(dirname "$selected")"
exec "$proton" waitforexitandrun "$selected" "$@"
