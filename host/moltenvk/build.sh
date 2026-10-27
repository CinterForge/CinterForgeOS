#!/bin/sh
# Build MoltenVK for the host Venus renderer (virglrenderer -> MoltenVK -> Metal).
#
#   host/moltenvk/build.sh        build/refresh work/out/host (MoltenVK dylib, ICD json, headers)
#   host/moltenvk/build.sh clean  drop the source/build tree (next build is from scratch)
#
# Inputs: committed HEAD of ~/Source/cinterforge-moltenvk and
# ~/Source/cinterforge-spirv-cross. Neither fork is modified.
# The forks carry our changes; patch archives are not used.
# External dependency retrieval by MoltenVK remains supported.
#
# Build is UTM's (scripts/build_dependencies.sh build_moltenvk): ./fetchDependencies --macos
# then `make macos` (Release, universal), then the arm64 slice is extracted.
#
# Metal private API (MVK_USE_METAL_PRIVATE_API=1: logicOp, wideLines, ...) is NOT enabled, same
# as UTM and Homebrew: the fork's geometry-shader mesh pipeline path does not compile with it
# (MTLMeshRenderPipelineDescriptor has no logicOperation*/sampleMask private properties).
# See MOLTENVK.txt.
#
# Output (work/out/host):
#   lib/libMoltenVK.dylib                     arm64, install_name @rpath/libMoltenVK.dylib, ad-hoc signed
#   share/vulkan/icd.d/MoltenVK_icd.json      library_path ../../../lib/libMoltenVK.dylib
#   include/MoltenVK/                         mvk_vulkan.h, mvk_private_api.h, ... (they
#                                             include <vulkan/...>, from the Vulkan SDK/Homebrew
#                                             vulkan-headers or from the MoltenVK package in
#                                             work/build/host-moltenvk/src/Package/Release/MoltenVK/include)
#   MOLTENVK.txt                              provenance + enabled features
# The dylib is first staged in work/build/host-moltenvk/stage and verified there: the probe in
# probe/ (fails when a feature steamac depends on is missing) and the repros in repro/ (all of
# gamescope 3.16.28's cs_*.comp pipelines + a pixel check, also with robustBufferAccess2; zink-style
# geometry shaders + pixel checks; draws with a VK_NULL_HANDLE pipeline bound; robustBufferAccess2
# bounds; null set layouts and unsupported sample counts; ... see repro/run.sh). Only then is it
# installed, by temp file + rename (a running VM may have the old dylib mapped).
set -eu

MVK_REPO=https://github.com/utmapp/MoltenVK.git
MVK_BRANCH=geometry-shaders
MVK_COMMIT=05604465d691118cfd20f53a48ecf1aad9c12f93
SPVC_REPO=https://github.com/utmapp/SPIRV-Cross.git
SPVC_COMMIT=939b40b33a44443c404c4078823c406e3c94866f

here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
STEAMAC_SOURCE_ROOT="$root"
. "$STEAMAC_SOURCE_ROOT/scripts/work-path.sh"
work=$STEAMAC_WORK_DIR/build/host-moltenvk
src=$root/../cinterforge-moltenvk
spvc=$root/../cinterforge-spirv-cross
out=$STEAMAC_WORK_DIR/out/host

if [ "${1:-}" = clean ]; then
	rm -rf "$work"
	exit 0
fi

xcrun --find metal > /dev/null

mkdir -p "$work"

# Forks already contain the previous Steamac patch series. No patch application.
[ -f "$src/fetchDependencies" ] || { echo "Missing local MoltenVK source: $src" >&2; exit 1; }
[ -f "$spvc/spirv.hpp" ] || { echo "Missing local SPIRV-Cross source: $spvc" >&2; exit 1; }
echo ">> MoltenVK: building directly from $src"
MVK_COMMIT=$(git -C "$src" rev-parse HEAD)
SPVC_COMMIT=$(git -C "$spvc" rev-parse HEAD)
MVK_REPO="$root/../cinterforge-moltenvk"
SPVC_REPO="$root/../cinterforge-spirv-cross"
# Use fork commits to identify dependencies and provenance, not deleted patch files.
patch_rev=$(printf '%s\n%s\n' "$MVK_COMMIT" "$SPVC_COMMIT" | shasum -a 256 | cut -c1-8)
# MoltenVK source may still use this generated header for shader cache separation.
if [ -f "$src/MoltenVK/MoltenVK/GPUObjects/MVKPatchRevision.h" ]; then
  printf '// Generated from local fork commits\n#define MVK_PATCH_REVISION 0x%s\n' "$patch_rev" > "$src/MoltenVK/MoltenVK/GPUObjects/MVKPatchRevision.h"
fi

# --- external dependencies (SPIRV-Cross, SPIRV-Tools, Vulkan-Headers, ... -> xcframeworks)
deps_key=$(printf '%s\n%s\n' "$MVK_COMMIT" "$SPVC_COMMIT" | shasum -a 256 | cut -d' ' -f1)
deps_stamp=$src/External/.steamac-deps
if [ "$(cat "$deps_stamp" 2> /dev/null)" != "$deps_key" ]; then
	rm -f "$deps_stamp"
	# Same environment scrub as UTM (env -i): no Homebrew/SDK variables leak into xcodebuild.
	(cd "$src" && env -i PATH="$PATH" HOME="$HOME" LANG="${LANG:-en_US.UTF-8}" \
		./fetchDependencies --macos --spirv-cross-root "$spvc")
	echo "$deps_key" > "$deps_stamp"
fi

# fetchDependencies creates External/SPIRV-Cross as a symlink to the pinned checkout.
# A relocated external work directory can leave the symlink pointing at the old
# checkout even when .steamac-deps is valid. Xcode then fails with
# MoltenVKShaderConverter/SPIRVSupport.cpp: 'spirv.hpp' file not found.
# Rebind it deterministically on every build; do not re-fetch or rebuild all
# dependencies merely to repair a source-directory link.
if [ ! -f "$spvc/spirv.hpp" ]; then
    echo "FAIL: pinned SPIRV-Cross checkout has no spirv.hpp: $spvc" >&2
    exit 1
fi
if [ "$(readlink "$src/External/SPIRV-Cross" 2>/dev/null || true)" != "$spvc" ]; then
    echo ">> MoltenVK: repairing stale External/SPIRV-Cross dependency link"
    rm -rf "$src/External/SPIRV-Cross"
    ln -s "$spvc" "$src/External/SPIRV-Cross"
fi
if [ ! -f "$src/External/SPIRV-Cross/spirv.hpp" ]; then
    echo "FAIL: SPIRV-Cross dependency is not visible to the MoltenVK Xcode project" >&2
    exit 1
fi

# Skip the costly Xcode build and GPU regressions if source and recipe are unchanged.
cache=$work/.cfos-build-fingerprint
cache_tool=$root/scripts/host-build-cache.py
cache_output=$out/lib/libMoltenVK.dylib
if python3 "$cache_tool" check "$cache" "$cache_output" "$src" "$here/build.sh" "$spvc/spirv.hpp"; then
    echo "[Xcode] MoltenVK unchanged (source + dirty files + build script); skipping build and probes"
    exit 0
fi
rm -f "$cache"

# --- MoltenVK (Release, universal)
(cd "$src" && env -i PATH="$PATH" HOME="$HOME" LANG="${LANG:-en_US.UTF-8}" make macos)

pkg=$src/Package/Release/MoltenVK
built=$pkg/dynamic/dylib/macOS

# --- stage
stage=$work/stage
rm -rf "$stage"
mkdir -p "$stage/lib"
staged=$stage/lib/libMoltenVK.dylib
lipo "$built/libMoltenVK.dylib" -thin arm64 -output "$staged"
test "$(otool -D "$staged" | sed -n 2p)" = "@rpath/libMoltenVK.dylib"
codesign --force -s - "$staged"

# --- probe (links the staged dylib directly, like virglrenderer)
probe=$work/probe
xcrun clang -std=c11 -Wall -Werror -O1 -I"$pkg/include" "$here/probe/probe.c" \
	-L"$stage/lib" -lMoltenVK -Wl,-rpath,"$stage/lib" -o "$probe"
echo ">> probe: $staged"
probe_log=$work/probe.log
MVK_CONFIG_LOG_LEVEL=1 "$probe" > "$probe_log" 2>&1 || { cat "$probe_log"; exit 1; }
cat "$probe_log"
# MoltenVK validates its shader-cache (cereal) archive layouts at vkCreateInstance and prints
# [MVK-BUILD-ERROR] when a patch changed a serialized struct without updating it.
if grep -q 'MVK-BUILD-ERROR' "$probe_log"; then
	echo "probe: MoltenVK reported a build error (see above)" >&2
	exit 1
fi

# --- repros: gamescope compute pipelines, geometry shaders, null pipeline binds
echo ">> repro: gamescope compute pipelines, geometry shaders, null pipeline binds"
repro_log=$work/repro.log
"$here/repro/run.sh" "$stage/lib" > "$repro_log" 2>&1 || { cat "$repro_log"; exit 1; }
grep -v '^\[mvk-info\]\|^	' "$repro_log"

# --- install (temp + rename, never rewrite a mapped dylib in place)
mkdir -p "$out/lib" "$out/share/vulkan/icd.d" "$out/include"
lib=$out/lib/libMoltenVK.dylib
cp "$staged" "$lib.tmp.$$"
mv -f "$lib.tmp.$$" "$lib"

icd=$out/share/vulkan/icd.d/MoltenVK_icd.json
sed 's#"library_path"[[:space:]]*:[[:space:]]*"[^"]*"#"library_path": "../../../lib/libMoltenVK.dylib"#' \
	"$built/MoltenVK_icd.json" > "$icd.tmp.$$"
grep -q '"library_path": "../../../lib/libMoltenVK.dylib"' "$icd.tmp.$$"
mv -f "$icd.tmp.$$" "$icd"

rm -rf "$out/include/MoltenVK.tmp.$$"
cp -R "$pkg/include/MoltenVK" "$out/include/MoltenVK.tmp.$$"
rm -rf "$out/include/MoltenVK"
mv "$out/include/MoltenVK.tmp.$$" "$out/include/MoltenVK"

# --- provenance, derived strictly from local committed forks
{
  echo 'MoltenVK for CinterForgeOS (local source forks, no build-time patches)'
  echo "MoltenVK source: $MVK_REPO @ $MVK_COMMIT"
  echo "SPIRV-Cross source: $SPVC_REPO @ $SPVC_COMMIT"
  echo "dependency key: $deps_key"
  echo "Xcode: $(xcodebuild -version | tr '\n' ' ')"
  echo "built: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo 'probe:'
  sed 's/^/  /' "$probe_log"
  echo 'repros:'
  sed 's/^/  /' "$repro_log"
} > "$out/MOLTENVK.txt"

echo ">> $lib"
otool -L "$lib"

python3 "$cache_tool" record "$cache" "$cache_output" "$src" "$here/build.sh" "$spvc/spirv.hpp"
