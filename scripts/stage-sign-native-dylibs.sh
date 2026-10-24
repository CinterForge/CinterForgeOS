#!/usr/bin/env bash
# Xcode's last target phase: stage native host libraries and sign each embedded
# Mach-O before Xcode performs its final application-bundle signature.
set -euo pipefail

: "${TARGET_BUILD_DIR:?TARGET_BUILD_DIR must be set by Xcode}"
: "${FULL_PRODUCT_NAME:?FULL_PRODUCT_NAME must be set by Xcode}"
: "${PROJECT_DIR:?PROJECT_DIR must be set by Xcode}"

host_lib_dir="${STEAMAC_NATIVE_LIB_DIR:-$PROJECT_DIR/../Builds/CreativeSuite/Built/cinterforge-os/mac/Steamac/work/out/host/lib}"
app="$TARGET_BUILD_DIR/$FULL_PRODUCT_NAME"
frameworks="$app/Contents/Frameworks"

libraries=(
    libkrun.1.dylib
    libvirglrenderer.1.dylib
    libepoxy.0.dylib
    libMoltenVK.dylib
    libvulkan_kosmickrisp.dylib
)

# libepoxy is provisioned by CFOS Setup as a Homebrew binary, not
# compiled or installed into the project's host build prefix.
# Resolve the already-installed artifact without invoking Homebrew here.
epoxy_library="/opt/homebrew/opt/libepoxy/lib/libepoxy.0.dylib"
[[ -s "$epoxy_library" ]] || {
    echo "error: Setup-provisioned libepoxy missing: $epoxy_library; run CFOS Setup" >&2
    exit 1
}
source_for_library() {
    case "$1" in
        libepoxy.0.dylib) printf '%s\n' "$epoxy_library" ;;
        libvulkan_kosmickrisp.dylib)
            # The Mesa build produces this validated intermediate before the
            # host-prefix copy. host_lib_dir is work/out/host/lib, so the
            # intermediate lives three directory levels above lib (work),
            # NOT in work/out/build. Support previously built artifacts.
            if [[ -s "$host_lib_dir/$1" ]]; then
                printf '%s\n' "$host_lib_dir/$1"
            else
                printf '%s\n' "$(dirname "$(dirname "$(dirname "$host_lib_dir")")")/build/host-kosmickrisp/$1"
            fi
            ;;
        *) printf '%s\n' "$host_lib_dir/$1" ;;
    esac
}

[[ -d "$app/Contents" ]] || { echo "error: Xcode app bundle not found: $app" >&2; exit 1; }
for lib in "${libraries[@]}"; do
    source_path="$(source_for_library "$lib")"
    [[ -s "$source_path" ]] || {
        echo "error: Required native library not found: $source_path" >&2
        exit 1
    }
    file -b "$source_path" | grep -q 'Mach-O.*arm64' || {
        echo "error: Expected macOS arm64 Mach-O: $source_path" >&2
        exit 1
    }
done

mkdir -p "$frameworks"
# Honor the active signing identity when Xcode has one; Debug without an
# identity uses ad-hoc signing. Bundle signing remains Xcode's responsibility.
sign_identity="${EXPANDED_CODE_SIGN_IDENTITY:--}"
if [[ "${CODE_SIGNING_ALLOWED:-YES}" == NO ]]; then
    echo "error: Native-library staging requires code signing to be enabled" >&2
    exit 1
fi

# Stage first so all dependency references can be redirected to the copied
# libraries before signing. Never modify the Setup-managed Homebrew binary.
for lib in "${libraries[@]}"; do
    ditto "$(source_for_library "$lib")" "$frameworks/$lib"
done

# virglrenderer links to Homebrew's absolute libepoxy install name. Without
# rewriting it, the bundled application still depends on the user's Homebrew
# installation at runtime. Apply the same rule to any staged native library
# linking to another library that we embed in Contents/Frameworks.
for lib in "${libraries[@]}"; do
    staged="$frameworks/$lib"
    while IFS= read -r dependency; do
        [[ -n "$dependency" ]] || continue
        for provided in "${libraries[@]}"; do
            [[ "$dependency" == "$provided" || "$dependency" == */"$provided" ]] || continue
            target="@rpath/$provided"
            if [[ "$dependency" != "$target" ]]; then
                install_name_tool -change "$dependency" "$target" "$staged"
                echo "[Xcode] Relinked $lib: $dependency -> $target"
            fi
            break
        done
    done < <(otool -L "$staged" | tail -n +2 | awk '{print $1}')
    # Give embedded dylibs a portable identity (not a Homebrew/source path).
    install_name_tool -id "@rpath/$lib" "$staged"
done

# The Xcode linker records libkrun's bare install name (libkrun.1.dylib).
# A bare name is NOT resolved using LD_RUNPATH_SEARCH_PATHS; dyld needs an
# @rpath-prefixed dependency to find the copy in Contents/Frameworks.
# Rewrite the built executable before Xcode applies its final bundle signature.
executable="$app/Contents/MacOS/${EXECUTABLE_NAME:-steamac-vm}"
[[ -f "$executable" ]] || {
    echo "error: Built application executable missing: $executable" >&2
    exit 1
}
while IFS= read -r dependency; do
    [[ -n "$dependency" ]] || continue
    for provided in "${libraries[@]}"; do
        [[ "$dependency" == "$provided" || "$dependency" == */"$provided" ]] || continue
        target="@rpath/$provided"
        if [[ "$dependency" != "$target" ]]; then
            install_name_tool -change "$dependency" "$target" "$executable"
            echo "[Xcode] Relinked executable: $dependency -> $target"
        fi
        break
    done
done < <(otool -L "$executable" | tail -n +2 | awk '{print $1}')

# Fail loudly if any embedded dependency still uses a bare/absolute path.
for binary in "$executable" "${libraries[@]/#/}"; do
    [[ "$binary" == "$executable" ]] || binary="$frameworks/$binary"
    while IFS= read -r dependency; do
        for provided in "${libraries[@]}"; do
            # @rpath is the intended portable reference, including the
            # dylib's own install ID reported by otool -L. Only reject
            # a bare filename or an absolute/non-@rpath location.
            if [[ "$dependency" == "$provided" || ( "$dependency" == */"$provided" && "$dependency" != "@rpath/$provided" ) ]]; then
                echo "error: Non-portable native dependency in $binary: $dependency" >&2
                exit 1
            fi
        done
    done < <(otool -L "$binary" | tail -n +2 | awk '{print $1}')
done

for lib in "${libraries[@]}"; do
    codesign --force --sign "$sign_identity" "$frameworks/$lib"
    codesign --verify --strict "$frameworks/$lib"
    echo "[Xcode] Staged and signed $lib"
done

# Validate the finished Xcode application payload *inside Xcode*, before its
# final app-bundle CodeSign step. The Hope Build packaging validator checks
# the same product later, but direct Xcode builds must fail on missing assets.
resources="$app/Contents/Resources"
required_resources=(
    Image
    initramfs.cpio.gz
    steamac-layer.img
    steamdeck-images.pem
    gvproxy
    desync
)
for name in "${required_resources[@]}"; do
    if [[ ! -s "$resources/$name" ]]; then
        echo "error: Xcode app missing required VM resource: $resources/$name" >&2
        exit 1
    fi
done
for name in index.html splash.css splash.js cinterforge-logo.png; do
    if [[ ! -s "$resources/cinterforge-os-splash/$name" ]]; then
        echo "error: Xcode app missing splash resource: $resources/cinterforge-os-splash/$name" >&2
        exit 1
    fi
done
for lib in "${libraries[@]}"; do
    if [[ ! -s "$frameworks/$lib" ]]; then
        echo "error: Xcode app missing signed Frameworks library: $lib" >&2
        exit 1
    fi
    codesign --verify --strict "$frameworks/$lib" || {
        echo "error: Xcode app contains invalid signed Frameworks library: $lib" >&2
        exit 1
    }
done
[[ -s "$executable" ]] || {
    echo "error: Xcode app missing native executable: $executable" >&2
    exit 1
}
echo "[Xcode] CinterForgeOS payload validated: native executable, ${#libraries[@]} signed libraries, ${#required_resources[@]} VM resources, splash assets"
