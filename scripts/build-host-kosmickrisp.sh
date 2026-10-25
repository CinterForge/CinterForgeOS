#!/usr/bin/env bash
# Build the patched KosmicKrisp Mesa Vulkan ICD during Xcode Build.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
export STEAMAC_ROOT="$root"
source "$root/scripts/paths.sh"
# Keep Mesa's C, C++ and Objective-C translation units on the same Xcode
# compiler toolchain. LLVM from Homebrew remains available to mesa-clc via
# llvm-config/PATH, but must not silently become the Objective-C compiler.
# Resolve absolute executables before extending PATH with Homebrew LLVM.
mesa_cc="$(xcrun --sdk macosx --find clang)"
mesa_cxx="$(xcrun --sdk macosx --find clang++)"
[[ -x "$mesa_cc" && -x "$mesa_cxx" ]] || { echo "error: Xcode clang/clang++ unavailable" >&2; exit 1; }
export CC="$mesa_cc" CXX="$mesa_cxx" OBJC="$mesa_cc"
mesa_toolchain_fingerprint="$(printf '%s\n' "$CC" "$CXX" "$OBJC" "$("$CC" --version | head -n 1)" "$(xcrun --sdk macosx --show-sdk-path)" | shasum -a 256 | awk '{print $1}')"
echo "[Xcode] KosmicKrisp Meson compilers: CC=$CC CXX=$CXX OBJC=$OBJC"
fork="${CINTERFORGE_KOSMICKRISP_SOURCE:-$(dirname "$root")/cinterforge-kosmickrisp-mesa}"
work="$STEAMAC_WORK_DIR/build/host-kosmickrisp"
src="$work/src"
clc="$work/clc"
prefix="$STEAMAC_WORK_DIR/out/host"
lib="$prefix/lib/libvulkan_kosmickrisp.dylib"
# Cache identity is the complete patched Mesa source tree, not a single bridge
# file. A previous Xcode build could keep a driver from a different fork
# revision, despite the old scripts always recreating the pinned Mesa + patches.
# This also forces a rebuild of libraries created before the source fingerprint
# was introduced. Never cache generated build outputs in the source checkout.
bridge_rel="src/kosmickrisp/bridge/ns_process_info.m"
bridge_revision="CINTERFORGE_KK_OS_VERSION_BRIDGE_SYSCTL_V2_20261022"
source_fingerprint="$work/source.sha256"
compiler_guard="$root/scripts/patches/guard_mtl4_compiler.py"
heap_guard="$root/scripts/patches/guard_mtl4_heap.py"
library_guard="$root/scripts/patches/guard_mtl4_library.py"
sparse_guard="$root/scripts/patches/guard_mtl4_sparse.py"
# The authoritative original patch series belongs to host/kosmickrisp.
# The external Mesa fork already contains these changes; do not reverse or
# reapply them to a potentially newer patched checkout. The series checksum
# is an additional cache input only, not a substitute for the source hash.
patch_dir="$root/host/kosmickrisp/patches"
patches=("$patch_dir"/[0-9][0-9][0-9][0-9]-*.patch)
[[ "${#patches[@]}" == 47 ]] || { echo "error: expected 47 original KosmicKrisp source patches in $patch_dir, found ${#patches[@]}" >&2; exit 1; }
for i in "${!patches[@]}"; do
    printf -v expected '%04d-' "$((i + 1))"
    [[ "${patches[$i]##*/}" == "$expected"* ]] || { echo "error: KosmicKrisp patch series gap/order at ${patches[$i]}" >&2; exit 1; }
done
patch_fingerprint="$(cat "${patches[@]}" | shasum -a 256 | awk '{print $1}')"
[[ -s "$heap_guard" ]] || { echo "error: missing Metal heap source patch: $heap_guard" >&2; exit 1; }
[[ -s "$sparse_guard" ]] || { echo "error: missing Metal sparse placement source patch: $sparse_guard" >&2; exit 1; }
[[ -s "$library_guard" ]] || { echo "error: missing Metal library source patch: $library_guard" >&2; exit 1; }
[[ -s "$compiler_guard" ]] || { echo "error: missing Metal 4 compiler source fix: $compiler_guard" >&2; exit 1; }
source_changed=1
# The old build ran a Khronos-loader physical-device and logical-device probe before publishing.
# Xcode must never reuse or publish an ICD that fails the same basic check.
probe_source="$root/scripts/test/kosmickrisp-probe.c"
probe="$work/kosmickrisp-probe"
icd="$work/kosmickrisp_icd.json"
probe_log="$work/probe.log"
# This probe is a checked-in build input, not a generated Mesa artifact.
# Check it before invoking either Meson build, rather than compiling hundreds
# of files only to discover that the source was omitted from a patch/deploy.
if [[ ! -s "$probe_source" ]]; then
    echo "error: missing KosmicKrisp physical-device and logical-device probe source: $probe_source" >&2
    echo "error: restore scripts/test/kosmickrisp-probe.c in the CinterForgeOS checkout" >&2
    exit 1
fi
[[ -f "$fork/$bridge_rel" ]] || { echo "error: KosmicKrisp Objective-C bridge missing: $fork/$bridge_rel" >&2; exit 1; }
# Reject partial source copies rather than accidentally combining them with
# artifacts of a prior build. meson.build is the root of the full Mesa checkout.
[[ -f "$fork/meson.build" ]] || { echo "error: KosmicKrisp fork is incomplete (missing Mesa meson.build): $fork" >&2; exit 1; }
# Stable fingerprint over path + file contents, excluding source control and
# generated directories. Building on a different source tree must never reuse
# a previously staged dylib.
fork_fingerprint="$(cd "$fork" && find . -type f ! -path './.git/*' ! -path './build/*' ! -path './target/*' -print0 | LC_ALL=C sort -z | xargs -0 shasum -a 256 | shasum -a 256 | awk '{print $1}')"
new_fingerprint="$(printf '%s\n' "$fork_fingerprint" "$(shasum -a 256 "$compiler_guard" | awk '{print $1}')" "$(shasum -a 256 "$heap_guard" | awk '{print $1}')" "$(shasum -a 256 "$library_guard" | awk '{print $1}')" "$(shasum -a 256 "$sparse_guard" | awk '{print $1}')" "$patch_fingerprint" "$mesa_toolchain_fingerprint" | shasum -a 256 | awk '{print $1}')"
if [[ -f "$source_fingerprint" && "$(cat "$source_fingerprint")" == "$new_fingerprint" && -f "$work/src/$bridge_rel" ]] && cmp -s "$fork/$bridge_rel" "$work/src/$bridge_rel"; then
    source_changed=0
else
    echo '[Xcode] KosmicKrisp source identity changed; rebuilding complete driver'
fi
[[ -f "$fork/meson.build" ]] || { echo "error: patched KosmicKrisp Mesa checkout missing meson.build: $fork" >&2; exit 1; }
[[ "$(uname -m)" == arm64 ]] || { echo 'error: KosmicKrisp requires an ARM64 macOS host' >&2; exit 1; }
[[ "$(sw_vers -productVersion | cut -d. -f1)" -ge 26 ]] || { echo 'error: KosmicKrisp needs macOS 26 (Metal 4)' >&2; exit 1; }
export PATH="/opt/homebrew/opt/llvm/bin:/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
for tool in git rsync meson ninja pkg-config python3 xcrun install_name_tool codesign nm otool file clang brew; do command -v "$tool" >/dev/null || { echo "error: missing $tool; run CFOS Setup" >&2; exit 1; }; done
# The original working host recipe required the Xcode Metal compiler and the
# complete SPIR-V/LLVM toolchain. Validate the exact tools before Meson can
# accidentally consume a different installation from PATH.
xcrun --find metal >/dev/null || { echo "error: Xcode Metal compiler unavailable; complete CFOS Setup" >&2; exit 1; }
for dependency in llvm spirv-llvm-translator spirv-tools vulkan-loader glslang; do
    if ! brew list --versions "$dependency" >/dev/null 2>&1; then
        echo "error: missing required KosmicKrisp build dependency: $dependency; complete CFOS Setup" >&2
        exit 1
    fi
done
llvm_prefix="$(brew --prefix llvm)"
[[ -x "$llvm_prefix/bin/llvm-config" ]] || { echo "error: missing LLVM llvm-config under $llvm_prefix" >&2; exit 1; }
export PATH="$llvm_prefix/bin:$PATH"
# The original recipe also required Python >= 3.10; do not defer this until
# a late Meson/Python generator error.
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || {
    echo 'error: Mesa requires Python >= 3.10' >&2; exit 1;
}
# Match the original script's staging gate: invoke the actual Vulkan ICD
# through the Khronos loader, including vkEnumeratePhysicalDevices. Merely
# checking arm64 and vk_icdGetInstanceProcAddr cannot detect this crash.
validate_icd() {
    local candidate="$1"
    local loader_prefix loader_lib vulkan_headers rc
    echo "[Xcode] KosmicKrisp Vulkan probe prerequisites: $candidate"
    if ! loader_prefix="$(brew --prefix vulkan-loader 2>&1)"; then
        echo "error: Homebrew vulkan-loader is required for the physical-device and logical-device probe: $loader_prefix" >&2
        return 1
    fi
    loader_lib="$loader_prefix/lib"
    if [[ ! -f "$loader_lib/libvulkan.dylib" ]]; then
        echo "error: Vulkan loader missing $loader_lib/libvulkan.dylib" >&2
        return 1
    fi
    vulkan_headers="${CINTERFORGE_VULKAN_HEADERS:-}"
    if [[ -z "$vulkan_headers" ]]; then
        # The old build sourced headers from MoltenVK's checkout, but Xcode
        # builds the host dependencies independently. The Khronos headers
        # installed with the Vulkan SDK are equally valid for this C probe.
        if [[ -f "$STEAMAC_WORK_DIR/build/host-moltenvk/src/Package/Release/MoltenVK/include/vulkan/vulkan.h" ]]; then
            vulkan_headers="$STEAMAC_WORK_DIR/build/host-moltenvk/src/Package/Release/MoltenVK/include"
        elif [[ -f /opt/homebrew/include/vulkan/vulkan.h ]]; then
            vulkan_headers=/opt/homebrew/include
        else
            local hprefix
            hprefix="$(brew --prefix vulkan-headers 2>/dev/null || true)"
            [[ -n "$hprefix" ]] && vulkan_headers="$hprefix/include"
        fi
    fi
    if [[ ! -f "$vulkan_headers/vulkan/vulkan.h" ]]; then
        echo "error: Vulkan headers missing (checked MoltenVK and Homebrew; use CINTERFORGE_VULKAN_HEADERS to select a valid include directory)" >&2
        return 1
    fi
    if [[ ! -s "$candidate" ]]; then
        echo "error: KosmicKrisp candidate dylib missing or empty: $candidate" >&2
        return 1
    fi
    # The function symbol existed in older, crashing builds too. Verify the
    # implementation revision before trusting a cached dylib or probing it.
    if ! grep -aFq "$bridge_revision" "$candidate"; then
        echo "error: KosmicKrisp dylib lacks current OS-version bridge revision: $candidate" >&2
        return 1
    fi
    # The source patcher verifies the exact Metal 4 guard implementation
    # before compilation, and its SHA-256 participates in the driver cache
    # identity. Never inspect compiler diagnostic strings in a release dylib:
    # the optimizer can remove/merge those strings without changing safety.
    # The Vulkan physical-device AND logical-device probe below is the
    # executable acceptance gate, including for reused cached drivers.
    mkdir -p "$work"
    echo "[Xcode] Vulkan loader: $loader_lib; headers: $vulkan_headers"
    if ! MACOSX_DEPLOYMENT_TARGET=26.0 clang -std=c11 -Wall -Werror -O1 -g -I"$vulkan_headers" "$probe_source" \
        -L"$loader_lib" -lvulkan -Wl,-rpath,"$loader_lib" -o "$probe"; then
        echo "error: unable to compile KosmicKrisp physical-device and logical-device probe" >&2
        return 1
    fi
    printf '{"file_format_version":"1.0.1","ICD":{"library_path":"%s","api_version":"1.4.0"}}\n' \
        "$candidate" > "$icd"
    echo "[Xcode] Probing KosmicKrisp physical devices: $candidate"
    rc=0
    printf "probe: starting candidate=%s\nprobe: ICD=%s\n" "$candidate" "$icd" > "$probe_log"
    # Log loader diagnostics together with the feature probe. This separates
    # missing ICD/loader wiring from an actual Mesa device initialization fault.
    VK_LOADER_DEBUG=error,warn VK_DRIVER_FILES="$icd" "$probe" >> "$probe_log" 2>&1 || rc=$?
    cat "$probe_log"
    if [[ "$rc" != 0 ]]; then
        # Preserve the actual entry instructions next to the failing probe;
        # a symbol+offset stack trace alone cannot tell which instruction
        # dispatched to a null Objective-C class or function pointer.
        if xcrun --find llvm-objdump >/dev/null 2>&1; then
            xcrun llvm-objdump --disassemble --symbolize-operands --no-show-raw-insn "$candidate" \
                > "$work/driver-disassembly.log" 2>&1 || true
            echo "[Xcode] Driver disassembly: $work/driver-disassembly.log"
        elif command -v llvm-objdump >/dev/null 2>&1; then
            llvm-objdump --disassemble --symbolize-operands --no-show-raw-insn "$candidate" \
                > "$work/driver-disassembly.log" 2>&1 || true
            echo "[Xcode] Driver disassembly: $work/driver-disassembly.log"
        fi
        echo "error: KosmicKrisp Vulkan device probe failed (exit $rc; details: $probe_log)" >&2
        return 1
    fi
}

if [[ "$source_changed" == 0 && -s "$lib" ]] && file "$lib" | grep -q arm64 && nm -gU "$lib" | grep -q _vk_icdGetInstanceProcAddr; then
    if validate_icd "$lib"; then
        echo "[Xcode] KosmicKrisp already staged and probed: $lib"
        exit 0
    fi
    echo "[Xcode] Existing KosmicKrisp failed Vulkan device initialization; discard cache eligibility and rebuild from the current source" >&2
    source_changed=1
fi
# Recover a completed build that previously stopped after producing its
# intermediate dylib. Do not discard a working Mesa build and recompile it.
intermediate="$work/libvulkan_kosmickrisp.dylib"
if [[ "$source_changed" == 0 && -s "$intermediate" ]] && file "$intermediate" | grep -q arm64 && nm -gU "$intermediate" | grep -q _vk_icdGetInstanceProcAddr; then
    if validate_icd "$intermediate"; then
        mkdir -p "$prefix/lib"
        cp "$intermediate" "$lib.tmp.$$"
        mv -f "$lib.tmp.$$" "$lib"
        echo "[Xcode] KosmicKrisp restored from probed build: $lib"
        exit 0
    fi
    echo "[Xcode] Cached intermediate KosmicKrisp failed Vulkan device initialization; rebuilding from the current source" >&2
    source_changed=1
fi
mkdir -p "$work" "$prefix/lib"
# macOS can report "Directory not empty" during recursive deletion when a
# concurrent process creates files in the old Mesa source tree. Do not let a
# failed cleanup abort the rebuild or reuse any part of the old checkout.
# Prepare a fresh source directory, then atomically replace the old path.
fresh_src="$work/src.new.$$"
mkdir -p "$fresh_src"
rsync -a --exclude=.git --exclude=build --exclude=target "$fork/" "$fresh_src/"
if [[ -e "$src" ]]; then
    retired_src="$work/src.old.$$"
    mv "$src" "$retired_src"
fi
mv "$fresh_src" "$src"
# The retired checkout is disposable. A concurrent writer may keep it busy;
# its removal must not affect the newly populated source checkout.
if [[ -n "${retired_src:-}" ]]; then
    rm -rf "$retired_src" 2>/dev/null || echo "[Xcode] Warning: old KosmicKrisp source cleanup deferred: $retired_src" >&2
fi
# The fork is already patched. Preserve its complete contents as supplied.
# Reverse-applying old patches here would mutate the disposable build tree and
# can reject valid additional source changes after the original patch series.
echo "[Xcode] Building current patched KosmicKrisp fork; original 47-patch series identified ($patch_fingerprint)"
python3 "$compiler_guard" "$src/src/kosmickrisp/bridge/mtl_compiler.m"
python3 "$heap_guard" "$src/src/kosmickrisp/bridge/mtl_heap.m"
python3 "$library_guard" "$src/src/kosmickrisp/bridge/mtl_compiler.m"
python3 "$sparse_guard" "$src/src/kosmickrisp/bridge/mtl_device.m"
if [[ ! -x "$work/venv/bin/python3" ]]; then python3 -m venv "$work/venv"; fi
"$work/venv/bin/python3" -m pip install 'mako==1.3.10' 'pyyaml==6.0.3' 'packaging==25.0' > "$work/python-deps.log" 2>&1 || { cat "$work/python-deps.log" >&2; exit 1; }
export PATH="$work/venv/bin:$PATH"
export MACOSX_DEPLOYMENT_TARGET=26.0
# Mesa's native shader compilers are required to build its Metal 4 Vulkan ICD.
rm -rf "$work/build-clc" "$clc" "$work/build"
(
  meson setup "$work/build-clc" "$src" --prefix="$clc" --buildtype=release -Db_ndebug=true \
    -Dplatforms= -Dvulkan-drivers= -Dgallium-drivers= -Dopengl=false -Dglx=disabled \
    -Degl=disabled -Dgbm=disabled -Dzstd=disabled \
    -Dllvm=enabled -Dshared-llvm=enabled -Dmesa-clc=enabled -Dinstall-mesa-clc=true \
    -Dprecomp-compiler=enabled -Dinstall-precomp-compiler=true
  ninja -C "$work/build-clc" install
  PATH="$clc/bin:$PATH" meson setup "$work/build" "$src" --buildtype=release -Db_ndebug=true \
    -Dplatforms=macos -Dvulkan-drivers=kosmickrisp -Dgallium-drivers= -Dopengl=false \
    -Dglx=disabled -Degl=disabled -Dgbm=disabled -Dzstd=disabled -Dexpat=disabled \
    -Dllvm=disabled -Dspirv-tools=disabled -Dmesa-clc=system -Dprecomp-compiler=enabled \
    --prefer-static
  PATH="$clc/bin:$PATH" ninja -C "$work/build"
) > >(tee "$work/build.log") 2> >(tee -a "$work/build.log" >&2)
built="$work/build/src/kosmickrisp/vulkan/libvulkan_kosmickrisp.dylib"
[[ -s "$built" ]] || { echo "error: KosmicKrisp build did not produce $built" >&2; exit 1; }
staged="$work/libvulkan_kosmickrisp.dylib"
cp "$built" "$staged"
file "$staged" | grep -q arm64 || { echo 'error: KosmicKrisp dylib is not arm64' >&2; exit 1; }
install_name_tool -id '@rpath/libvulkan_kosmickrisp.dylib' "$staged"
nm -gU "$staged" | grep -q _vk_icdGetInstanceProcAddr || { echo 'error: KosmicKrisp missing Vulkan ICD export' >&2; exit 1; }
# Unlike virglrenderer, this driver must not depend on Homebrew at runtime.
if otool -L "$staged" | sed '1,2d' | grep -qv -e '^[[:space:]]*/usr/lib/' -e '^[[:space:]]*/System/'; then
  echo 'error: KosmicKrisp depends on a non-system dylib' >&2; otool -L "$staged" >&2; exit 1
fi
codesign --force --sign - "$staged"
# Remember the successfully built source, not probe success: failures in the
# loader, headers or device enumeration must be fixable without recompiling
# all of Mesa. Publishing to out/host/lib still requires the probe to pass.
printf '%s\n' "$new_fingerprint" > "$source_fingerprint.tmp.$$"
mv -f "$source_fingerprint.tmp.$$" "$source_fingerprint"
validate_icd "$staged" || { echo "error: refusing to publish unprobed KosmicKrisp; original working build required this gate" >&2; exit 1; }
cp "$staged" "$lib.tmp.$$"
mv -f "$lib.tmp.$$" "$lib"
echo "[Xcode] macOS arm64 KosmicKrisp ready: $lib (source $new_fingerprint)"
