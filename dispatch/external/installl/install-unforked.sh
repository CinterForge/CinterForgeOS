#!/bin/sh
# Install an ORIGINAL publisher distribution beside (never over) CinterForge apps.
# In SteamOS ARM64, portable x86 Linux releases are run through CFOS FEX;
# Ubuntu .deb packages are provisioned in the separate CFOS Ubuntu AMD64 image.
set -eu
[ "$#" -eq 2 ] || { echo 'Usage: install-unforked <Linux|Windows> <app>' >&2; exit 2; }
platform=$1 app=$2
case "$app" in GIMP|Krita|Inkscape|Scribus|Blender|Kdenlive|Ardour|cfdocument) ;; *) echo "Unsupported upstream application: $app" >&2; exit 2 ;; esac
apps=/run/steamapps/common/CinterForge/Applications
root="$apps/UnforkedInstallers"
case "$platform" in
 Windows)
    folder="$root/Windows/$app"
    [ -d "$folder" ] || { echo "Publisher installer folder missing: $folder" >&2; exit 1; }
    count=$(find "$folder" -maxdepth 1 -type f \( -iname '*.exe' -o -iname '*.msi' \) | wc -l)
    [ "$count" -eq 1 ] || { echo "Expected exactly one EXE/MSI in $folder; found $count" >&2; exit 1; }
    installer=$(find "$folder" -maxdepth 1 -type f \( -iname '*.exe' -o -iname '*.msi' \) -print -quit)
    inspector="$apps/Test/Scripts/inspect/inspect-unforked-windows.py"
    [ -x "$inspector" ] || { echo "Missing staged Windows installer inspector: $inspector" >&2; exit 1; }
    "$inspector" "$app" before
    "$inspector" "$app" install --installer "${installer##*/}"
    "$inspector" "$app" after
    echo "[unforked Windows] $app installed; use Cinter unforked launch Windows $app to test."
    ;;
 Linux)
    folder="$root/Linux/$app"
    [ -d "$folder" ] || { echo "Publisher installer folder missing: $folder" >&2; exit 1; }
    # One publisher file, not stale .part, screenshots, or a previous test log.
    count=$(find "$folder" -maxdepth 1 -type f \( -iname '*.AppImage' -o -iname '*.tar.xz' -o -iname '*.tar.gz' \) | wc -l)
    [ "$count" -eq 1 ] || { echo "Expected one portable publisher Linux AppImage/tarball in $folder; found $count" >&2; exit 1; }
    installer=$(find "$folder" -maxdepth 1 -type f \( -iname '*.AppImage' -o -iname '*.tar.xz' -o -iname '*.tar.gz' \) -print -quit)
    base="$apps/Unforked/Linux"
    mkdir -p "$base"
    target="$base/$app"
    [ ! -e "$target.previous-stage" ] || { echo "Unreconciled previous installation: $target.previous-stage" >&2; exit 1; }
    scratch=$(mktemp -d "$base/$app-stage.XXXXXXXX")
    trap 'rm -rf "$scratch"' EXIT HUP INT TERM
    case "$installer" in
        *.AppImage|*.appimage)
            cp "$installer" "$scratch/application.AppImage"
            chmod 0755 "$scratch/application.AppImage"
            printf '%s\n' 'application.AppImage' > "$scratch/executable.txt"
            ;;
        *.tar.xz|*.tar.gz)
            # Never unpack publisher-controlled absolute paths, traversal,
            # symlinks, hardlinks or device nodes into persistent Steam storage.
            python3 - "$installer" "$scratch" "$app" <<'PY'
import os, pathlib, sys, tarfile
archive, output, app = sys.argv[1:]
root = pathlib.Path(output)
options = {'GIMP':'gimp', 'Krita':'krita', 'Inkscape':'inkscape',
           'Scribus':'scribus', 'Blender':'blender', 'Kdenlive':'kdenlive',
           'Ardour':'ardour', 'cfdocument':'rapidraw'}
with tarfile.open(archive, 'r:*') as bundle:
    for item in bundle.getmembers():
        path = pathlib.PurePosixPath(item.name)
        if not item.name or path.is_absolute() or '..' in path.parts or not (item.isfile() or item.isdir()):
            sys.exit('Unsafe publisher archive entry: ' + item.name)
    bundle.extractall(root / 'payload', filter='data')
name = options[app]
executables = [x for x in (root/'payload').rglob(name) if x.is_file() and os.access(x, os.X_OK)]
if len(executables) != 1:
    sys.exit(f'Expected exactly one upstream {name} executable; found {len(executables)}')
(root/'executable.txt').write_text(executables[0].relative_to(root).as_posix() + '\n')
PY
            ;;
        *) echo "Unsupported Linux publisher distribution: $installer" >&2; exit 2 ;;
    esac
    sha256sum "$installer" > "$scratch/publisher.sha256"
    if [ -d "$target" ]; then mv "$target" "$target.previous-stage"; fi
    if mv "$scratch" "$target"; then
        trap - EXIT HUP INT TERM
        [ ! -d "$target.previous-stage" ] || rm -rf "$target.previous-stage"
    else
        if [ -d "$target.previous-stage" ] && [ ! -e "$target" ]; then mv "$target.previous-stage" "$target"; fi
        exit 1
    fi
    echo "[unforked Linux] INSTALLED $app: $target (original publisher, not fork)"
    ;;
 *) echo "Unsupported platform: $platform" >&2; exit 2 ;;
esac
