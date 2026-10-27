##!/usr/bin/env bash
## Build the patched MoltenVK fork during the Xcode Build (not Setup).
#set -euo pipefail
#root="$(cd "$(dirname "$0")/.." && pwd)"
#export STEAMAC_ROOT="$root"
#source "$root/scripts/paths.sh"
#fork="${CINTERFORGE_MOLTENVK_SOURCE:-$(dirname "$root")/cinterforge-moltenvk}"
#spvc="${CINTERFORGE_SPIRV_CROSS_SOURCE:-$(dirname "$root")/cinterforge-spirv-cross}"
#work="$STEAMAC_WORK_DIR/build/host-moltenvk"
#src="$work/src"
#prefix="$STEAMAC_WORK_DIR/out/host"
#lib="$prefix/lib/libMoltenVK.dylib"
#[[ -f "$fork/Makefile" && -x "$fork/fetchDependencies" ]] || { echo "error: patched MoltenVK fork missing Makefile/fetchDependencies: $fork" >&2; exit 1; }
#[[ -f "$spvc/spirv.hpp" ]] || { echo "error: patched SPIRV-Cross fork missing spirv.hpp: $spvc" >&2; exit 1; }
#for tool in rsync make xcrun lipo otool install_name_tool codesign file; do command -v "$tool" >/dev/null || { echo "error: missing $tool; run CFOS Setup" >&2; exit 1; }; done
#source "$root/scripts/host-source-identity.sh"
#identity_file="$work/source.sha256"
#source_identity="$(cfos_combine_identity "$(cfos_source_identity "$fork")" "$(cfos_source_identity "$spvc")" "$(shasum -a 256 "$0" | awk '{print $1}')")"
#if [[ -s "$lib" && -f "$prefix/share/vulkan/icd.d/MoltenVK_icd.json" && -d "$prefix/include/MoltenVK" && -s "$identity_file" ]] && \
#   [[ "$(cat "$identity_file")" == "$source_identity" ]] && file "$lib" | grep -q 'arm64'; then
#    echo "[Xcode] MoltenVK already staged: $lib"
#    exit 0
#fi
#mkdir -p "$work" "$prefix/lib" "$prefix/include" "$prefix/share/vulkan/icd.d"
## Retain the external build tree across Xcode retries. Deleting it would
## discard MoltenVK's pinned dependency clones and compiled intermediates.
## Keep the authored fork authoritative while preserving its generated trees.
#mkdir -p "$src"
#rsync -a --delete --exclude='/.git/' --exclude='/External/' \
#  --exclude='/Package/' --exclude='/build/' --exclude='/target/' \
#  "$fork/" "$src/"
#
## MoltenVK's fetchDependencies unconditionally re-clones these repositories.
## Run it only when the required pinned dependency checkout is absent. Reusing
## the cache is particularly important after a packaging failure/retry.
#required_dependencies=(cereal Vulkan-Headers SPIRV-Tools Vulkan-Tools Volk)
#need_fetch=0
#for dep in "${required_dependencies[@]}"; do
#  if [[ ! -d "$src/External/$dep/.git" ]]; then
#    need_fetch=1
#    break
#  fi
#done
#if (( need_fetch )); then
#  echo '[Xcode] Fetching missing MoltenVK dependencies (cached across builds)'
#  ( cd "$src" && ./fetchDependencies --macos --spirv-cross-root "$spvc" ) \
#    > "$work/fetch.log" 2>&1 || {
#      echo "error: MoltenVK dependency fetch failed; last 35 log lines (full log: $work/fetch.log)" >&2
#      tail -n 35 "$work/fetch.log" >&2
#      exit 1
#    }
#else
#  echo '[Xcode] Reusing cached MoltenVK dependency checkouts'
#fi
#( cd "$src" && make macos ) \
#  > "$work/build.log" 2>&1 || {
#    echo "error: MoltenVK build failed; last 35 log lines (full log: $work/build.log)" >&2
#    tail -n 35 "$work/build.log" >&2
#    exit 1
#  }
#pkg="$src/Package/Release/MoltenVK"
#built="$pkg/dynamic/dylib/macOS/libMoltenVK.dylib"
#[[ -s "$built" ]] || { echo "error: MoltenVK build did not produce $built" >&2; exit 1; }
#staged="$work/libMoltenVK.dylib"
## lipo -thin requires a universal binary. The upstream macOS-only
## package may already contain a single-architecture arm64 dylib.
#archs="$(lipo -archs "$built")"
#case " $archs " in
#  *" arm64 "*) ;;
#  *) echo "error: MoltenVK result lacks arm64 architecture: $built ($archs)" >&2; exit 1 ;;
#esac
#if [[ "$archs" == "arm64" ]]; then
#  cp "$built" "$staged"
#else
#  lipo "$built" -thin arm64 -output "$staged"
#fi
#file "$staged" | grep -q 'arm64' || { echo 'error: MoltenVK result is not arm64' >&2; exit 1; }
#install_name_tool -id '@rpath/libMoltenVK.dylib' "$staged"
#codesign --force --sign - "$staged"
#[[ -s "$pkg/include/MoltenVK/mvk_vulkan.h" ]] || { echo "error: MoltenVK public headers missing in $pkg/include" >&2; exit 1; }
#[[ -s "$pkg/dynamic/dylib/macOS/MoltenVK_icd.json" ]] || { echo 'error: MoltenVK ICD metadata missing' >&2; exit 1; }
#cp "$staged" "$lib.tmp.$$"
#mv -f "$lib.tmp.$$" "$lib"
#rm -rf "$prefix/include/MoltenVK"
#cp -R "$pkg/include/MoltenVK" "$prefix/include/MoltenVK"
#sed 's#"library_path"[[:space:]]*:[[:space:]]*"[^"]*"#"library_path": "../../../lib/libMoltenVK.dylib"#' \
#  "$pkg/dynamic/dylib/macOS/MoltenVK_icd.json" > "$prefix/share/vulkan/icd.d/MoltenVK_icd.json"
#printf '%s\n' "$source_identity" > "$identity_file"
#echo "[Xcode] macOS arm64 MoltenVK ready: $lib"
