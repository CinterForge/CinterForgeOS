#!/bin/sh
# Shipping CFOS Ubuntu AMD64 acceptance: real Linux and Windows execution.
# Called within the installed OCI rootfs; no source repo or external files.
set -eu
test "$(dpkg --print-architecture)" = amd64
grep -q 'Ubuntu 24.04' /etc/os-release
dpkg --print-foreign-architectures | grep -qx i386
# The installed image must provide an independent, writable non-root account.
test "$(id -un)" = cinterforge
test "$(id -u)" -ge 1000
test -w /opt/cinterforge
for tool in gcc g++ clang cmake ninja meson make pkg-config python3 git \
            autoconf automake bison flex gfortran qmake6 hugo \
            Xvfb xvfb-run wine x86_64-w64-mingw32-gcc \
            i686-w64-mingw32-gcc ffmpeg; do
    command -v "$tool" >/dev/null || { echo "Missing headless tool: $tool" >&2; exit 1; }
done
# The shared workstation contract must validate its *real*, independently
# available toolchains before app Setup consumes it. GCC 10 native and the
# Ardour GCC 10 Windows cross-compiler are deliberately not conflated.
for tool in gcc-10 g++-10 clang-cl lld-link llvm-rc llvm-mt \
            rustc cargo xwin node npm go java javac mono \
            git-lfs nasm yasm swig glslangValidator; do
    command -v "$tool" >/dev/null || { echo "Missing shared build tool: $tool" >&2; exit 1; }
done
gcc-10 -dumpfullversion | grep -Eq '^10\.'
g++-10 -dumpfullversion | grep -Eq '^10\.'
rustc --version | grep -q '^rustc 1\.98\.'
node --version | grep -q '^v22\.16\.0$'
xwin --version >/dev/null
clang-cl --version >/dev/null
lld-link --version >/dev/null
ldconfig -p | grep -q libglibmm-2.4.so.1
ldconfig -p | grep -q libgtkmm-2.4.so.1
ldconfig -p | grep -q liblrdf.so.0
ldconfig -p | grep -q liblilv-0.so.0
# The shared distribution must include development headers, not just
# dynamically linked runtime libraries. App-specific version pins remain
# owned by the individual Creative Suite Setup recipes.
pkg-config --exists gtk+-3.0 gtk4 libcurl vulkan Qt6Core gstreamer-1.0
pkg-config --exists sdl3
pkg-config --modversion sdl3 | grep -qx '3.4.18'
test -r /opt/cinterforge/SDKs/Windows/x86_64/include/SDL3/SDL.h
test -r /opt/cinterforge/SDKs/Windows/x86_64/lib/libSDL3.dll.a
test -r /opt/cinterforge/SDKs/Windows/x86_64/bin/SDL3.dll
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
printf '%s\n' '#include <stdio.h>' 'int main(void) { puts("CFOS-LINUX-OK"); return 0; }' > "$tmp/check.c"
gcc -O2 "$tmp/check.c" -o "$tmp/linux-check"
"$tmp/linux-check" | grep -qx CFOS-LINUX-OK
printf '%s\n' '#include <iostream>' 'int main() { std::cout << "CFOS-CXX-OK\n"; }' > "$tmp/check.cpp"
g++ -std=c++17 -O2 "$tmp/check.cpp" -o "$tmp/linux-cxx-check"
"$tmp/linux-cxx-check" | grep -qx CFOS-CXX-OK
gcc-10 -O2 "$tmp/check.c" -o "$tmp/gcc10-check"
"$tmp/gcc10-check" | grep -qx CFOS-LINUX-OK
g++-10 -std=c++17 -O2 "$tmp/check.cpp" -o "$tmp/gcc10-cxx-check"
"$tmp/gcc10-cxx-check" | grep -qx CFOS-CXX-OK
printf '%s\n' 'fn main() { println!("CFOS-RUST-OK"); }' > "$tmp/rust_check.rs"
rustc "$tmp/rust_check.rs" -o "$tmp/rust-check"
"$tmp/rust-check" | grep -qx CFOS-RUST-OK
node -e 'console.log("CFOS-NODE-OK")' | grep -qx CFOS-NODE-OK
# CinterForgeDesktop Linux and Windows must use the SAME verified SDL3 API.
# Exercise the dynamic loader under the exported/imported production rootfs.
cat > "$tmp/check_sdl3.c" <<'EOF'
#include <SDL3/SDL.h>
#include <stdio.h>
int main(void) {
    const int version = SDL_GetVersion();
    if (version < SDL_VERSIONNUM(3, 4, 18)) return 3;
    puts("CFOS-SDL3-OK");
    return 0;
}
EOF
cc -O2 $(pkg-config --cflags sdl3) "$tmp/check_sdl3.c" \
    -o "$tmp/sdl3-linux-check" $(pkg-config --libs sdl3)
"$tmp/sdl3-linux-check" | grep -qx CFOS-SDL3-OK
sdk=/opt/cinterforge/SDKs/Windows/x86_64
x86_64-w64-mingw32-gcc -O2 "$tmp/check_sdl3.c" \
    -I"$sdk/include" -L"$sdk/lib" -lSDL3 -o "$tmp/sdl3-windows-check.exe"
file "$tmp/sdl3-windows-check.exe" | grep -q PE32+
cp "$sdk/bin/SDL3.dll" "$tmp/SDL3.dll"
x86_64-w64-mingw32-gcc -O2 "$tmp/check.c" -o "$tmp/windows-check.exe"
file "$tmp/windows-check.exe" | grep -q 'PE32+'
x86_64-w64-mingw32-g++ -std=c++17 -O2 "$tmp/check.cpp" -o "$tmp/windows-cxx-check.exe"
file "$tmp/windows-cxx-check.exe" | grep -q 'PE32+'
i686-w64-mingw32-gcc -O2 "$tmp/check.c" -o "$tmp/windows-x86-check.exe"
file "$tmp/windows-x86-check.exe" | grep -q 'PE32 executable'
# Launch a real Windows console PE in a clean, headless Wine prefix. Only
# checking wine --version is insufficient for Windows application testing.
export WINEPREFIX="$tmp/wine-prefix" WINEDEBUG=-all WINEDLLOVERRIDES=mscoree,mshtml=
xvfb-run -a timeout 120 wine "$tmp/windows-check.exe" > "$tmp/windows.log" 2>&1 || {
    cat "$tmp/windows.log" >&2
    echo 'CFOS Windows Wine execution failed' >&2
    exit 1
}
grep -q CFOS-LINUX-OK "$tmp/windows.log" || {
    cat "$tmp/windows.log" >&2
    echo 'CFOS Windows console output missing' >&2
    exit 1
}
# Test that Windows SDL3 DLLs load under Wine in the shipping image.
xvfb-run -a timeout 120 wine "$tmp/sdl3-windows-check.exe" > "$tmp/sdl3-windows.log" 2>&1 || {
    cat "$tmp/sdl3-windows.log" >&2
    echo 'CFOS Windows SDL3 Wine execution failed' >&2
    exit 1
}
grep -q CFOS-SDL3-OK "$tmp/sdl3-windows.log" || {
    cat "$tmp/sdl3-windows.log" >&2
    echo 'CFOS Windows SDL3 output missing' >&2
    exit 1
}
echo '[CFOS Steam workstation] PASS: pinned Linux SDL3 and Windows x64 SDL3 DLL/import library, real Linux and Wine execution'
echo '[CFOS Ubuntu toolchain] PASS GCC 10 + modern Linux C/C++, Clang/LLVM Windows tools, Rust/Cargo/xwin, Node, Linux/Windows cross-compilers, Wine PE execution and development SDKs'
# Every shipping image also contains KDE; the build must not treat a missing
# graphical payload as a successful headless-only distribution.
for tool in startplasma-x11 kwin_x11 x11vnc websockify; do
    command -v "$tool" >/dev/null || { echo "Missing Ubuntu desktop tool: $tool" >&2; exit 1; }
done
test -s /usr/share/novnc/vnc.html
test -x /usr/local/bin/cfos-ubuntu-desktop
test -x /usr/local/bin/cfos-ubuntu-shell
test -r /usr/local/lib/cfos-ubuntu-service.sh
echo '[CFOS Ubuntu distribution] PASS headless build/test tools and desktop payload are in the SAME image'

