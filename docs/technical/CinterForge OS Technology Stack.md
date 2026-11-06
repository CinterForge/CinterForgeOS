# CinterForge OS Technology Stack
Three desktop cores. One creative workstation. An industrial-strength cross-platform development environment.

CinterForge OS brings together Apple Silicon macOS, a comprehensively provisioned **Ubuntu 24.04 AMD64** workstation, and **SteamOS ARM64**. It combines original publisher applications, independently maintained CinterForge forks, browser applications, a full Python Server, graphics and media libraries, modern programming languages, legacy build chains, compatibility layers and cross-compilation SDKs. A dedicated LinuxKit appliance runs server workloads such as Bedrock.

Explore: **[Libraries](#libraries)** · **[Apps](#apps)** · **[Dev Tools](#dev-tools)** · **[Packaging and distribution](#packaging-and-distribution)**

> **Scope and provenance:** This is the technology *stack and integration contract*, not a certificate that every combination already builds and launches. The Ubuntu Dockerfile is the authoritative initial provisioning list; platform-specific packages, SDK licensing, GUI acceptance and all fork builds have independent tests. Availability differs across macOS, Ubuntu and SteamOS. Named technologies remain the property of their respective projects. Logos link to upstream sources or product sites and do not imply endorsement.

---

## Libraries

### Operating systems, virtualization and compatibility

| | Technology | Role in CinterForge OS |
|:--:|---|---|
| <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons/ubuntu/ubuntu-original.svg" height="35" alt="Ubuntu" /> | [Ubuntu 24.04 LTS](https://ubuntu.com/) | One AMD64 development/workstation image, headless or KDE, with a frozen Linux and Windows build-tool inventory. |
| <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons/linux/linux-original.svg" height="35" alt="Linux" /> | [LinuxKit](https://github.com/linuxkit/linuxkit) | Minimal ARM64 server appliance and Bedrock execution environment, distinct from the full development workstation. |
| <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons/archlinux/archlinux-original.svg" height="35" alt="Linux distribution" /> | [SteamOS](https://store.steampowered.com/steamos/) | ARM64 graphical Steam/KDE core and application runtime. |
| <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons/apple/apple-original.svg" height="35" alt="Apple" /> | [Apple Virtualization](https://developer.apple.com/documentation/virtualization) | Host-native ARM64 guest integration and Rosetta facilities for LinuxKit. |
| <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons/docker/docker-original.svg" height="35" alt="Docker" /> | [Docker](https://www.docker.com/) · [Colima](https://github.com/abiosoft/colima) | CFOS-managed isolated image construction, export, stage and installed-image validation. |
| | [libkrun](https://github.com/containers/libkrun) · [FEX-Emu](https://github.com/FEX-Emu/FEX) | Guest acceleration and x86/x86-64 Linux application compatibility on ARM64. |
| | [Wine](https://www.winehq.org/) · [Proton](https://github.com/ValveSoftware/Proton) | Windows application execution and smoke testing under compatible Linux guests; not substitutes for licensed Windows MSVC. |
| | [Linux kernel](https://kernel.org/) · [QEMU](https://www.qemu.org/) | Guest execution, cross-architecture development and supporting platform utilities. |

### Graphics, compute, video and imaging

| | Technology | Role |
|:--:|---|---|
| <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons/opengl/opengl-original.svg" height="34" alt="OpenGL" /> | [OpenGL](https://www.khronos.org/opengl/) · [Mesa](https://mesa3d.org/) | Rendering, software fallback, drivers and build headers. |
| <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons/vulkan/vulkan-original.svg" height="34" alt="Vulkan" /> | [Vulkan](https://www.vulkan.org/) · [MoltenVK](https://github.com/KhronosGroup/MoltenVK) | Cross-platform graphics APIs and Vulkan-to-Metal translation in the maintained graphics stack. |
| | [SPIR-V Tools](https://github.com/KhronosGroup/SPIRV-Tools) · [glslang](https://github.com/KhronosGroup/glslang) · [shaderc](https://github.com/google/shaderc) | Shader compilation and validation. |
| | [SDL3](https://github.com/libsdl-org/SDL) · [SDL2](https://github.com/libsdl-org/SDL) · [GLFW](https://www.glfw.org/) | Windowing, graphics/input/audio integration and sample applications; Linux and Windows SDL3 SDKs provisioned together. |
| <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons/opencv/opencv-original.svg" height="34" alt="OpenCV" /> | [OpenCV](https://opencv.org/) · [OpenImageIO](https://openimageio.readthedocs.io/) | Image pipelines, interchange, inspection and content production. |
| | [OpenEXR](https://openexr.com/) · [OpenVDB](https://www.openvdb.org/) · [OpenColorIO](https://opencolorio.org/) | Image/scene exchange, volume pipelines and color management (with app-specific availability). |
| | [GEGL](https://gegl.org/) · [babl](https://gegl.org/babl/) · [LittleCMS](https://www.littlecms.com/) | Pixel processing, format conversion and color workflows. |
| | [libheif](https://github.com/strukturag/libheif) · [libjxl](https://github.com/libjxl/libjxl) · [libwebp](https://chromium.googlesource.com/webm/libwebp/) · [LibRaw](https://www.libraw.org/) | Modern photography and image-format libraries. |
| <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons/ffmpeg/ffmpeg-original.svg" height="34" alt="FFmpeg" /> | [FFmpeg](https://ffmpeg.org/) · [MLT](https://www.mltframework.org/) · [GStreamer](https://gstreamer.freedesktop.org/) | Encoding, filters, timeline processing, media I/O and codec-facing tests. |
| | [Assimp](https://github.com/assimp/assimp) · [Embree](https://github.com/RenderKit/embree) · [TBB](https://github.com/uxlfoundation/oneTBB) · [Eigen](https://eigen.tuxfamily.org/) | 3D asset interchange, ray tracing, parallel compute and math. |

### UI, desktop, audio and communication

| | Technology | Role |
|:--:|---|---|
| <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons/qt/qt-original.svg" height="34" alt="Qt" /> | [Qt 5 / Qt 6](https://www.qt.io/) | Native UI, graphics, multimedia, WebEngine and application packaging. |
| <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons/kde/kde-original.svg" height="34" alt="KDE" /> | [KDE Plasma](https://kde.org/plasma-desktop/) · [KDE Frameworks](https://develop.kde.org/) | Full Linux desktops, app integration and the KDE-based Creative Suite. |
| <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons/gtk/gtk-original.svg" height="34" alt="GTK" /> | [GTK 3 / GTK 4](https://www.gtk.org/) · [gtkmm](https://www.gtkmm.org/) · [WebKitGTK](https://webkitgtk.org/) | GIMP, Ardour, native webview and legacy/modern GTK application development. |
| | [JACK](https://jackaudio.org/) · [PipeWire](https://pipewire.pages.freedesktop.org/pipewire/) · [ALSA](https://www.alsa-project.org/) | Audio engines, routing and playback/recording infrastructure. |
| | [LV2](https://lv2plug.in/) · [Vamp](https://www.vamp-plugins.org/) · [Rubber Band](https://breakfastquay.com/rubberband/) · [FFTW](https://www.fftw.org/) | Plugin hosting, analysis, time stretching and DSP. |
| | [Boost](https://www.boost.org/) · [OpenSSL](https://www.openssl.org/) · [libcurl](https://curl.se/libcurl/) · [SQLite](https://sqlite.org/) | C++ abstractions, security, network I/O and structured data. |
| | [Protocol Buffers](https://protobuf.dev/) · [libarchive](https://www.libarchive.org/) · [zstd](https://github.com/facebook/zstd) | Serialization, package exchange and compression. |

> The initial OS image deliberately carries broad SDK headers even when only a subset is required for day-one builds. Third-party library revisions and application-specific ABIs remain pinned by the inspected recipes and are refined during Creative Suite acceptance.

---

## Apps

### Original publisher applications

The original programs stay **independently installed** as baselines; building a CinterForge fork does not replace them. Approved publisher installers are inventoried in `Builds/CreativeSuite/UnforkedInstallers/{Linux,macOS,Windows}/`, with platform-appropriate installation inside the OS. Each platform's executable must pass its own acceptance gate.

| Original | Upstream | Primary creative role |
|---|---|---|
| **Blender** | [blender.org](https://www.blender.org/) · [Source](https://projects.blender.org/blender/blender) | Modeling, rendering, animation, geometry, VFX |
| **GIMP** | [gimp.org](https://www.gimp.org/) · [Source](https://gitlab.gnome.org/GNOME/gimp) | Image composition and raster editing |
| **Krita** | [krita.org](https://krita.org/) · [Source](https://invent.kde.org/graphics/krita) | Digital painting and illustration |
| **Inkscape** | [inkscape.org](https://inkscape.org/) · [Source](https://gitlab.com/inkscape/inkscape) | Vector illustration and SVG workflows |
| **Kdenlive** | [kdenlive.org](https://kdenlive.org/) · [Source](https://invent.kde.org/multimedia/kdenlive) | Video editing, compositing and export |
| **Ardour** | [ardour.org](https://ardour.org/) · [Source](https://github.com/Ardour/ardour) | Professional recording, editing and mixing |
| **Scribus** | [scribus.net](https://www.scribus.net/) · [Source](https://github.com/scribusproject/scribus) | Print and page layout |
| **RapidRAW** | [GitHub](https://github.com/CyberTimon/RapidRAW) | RAW photo development |
| **Unigine Heaven** | [UNIGINE](https://benchmark.unigine.com/heaven) | GPU/graphics acceptance and visual benchmarking |
| **Unigine Superposition** | [UNIGINE](https://benchmark.unigine.com/superposition) | GPU/graphics benchmarking |
| **FFmpeg** | [ffmpeg.org](https://ffmpeg.org/) | Headless multimedia utility and development reference |

### Maintained CinterForge application families

| Creative Suite application | Upstream foundation | Product focus |
|---|---|---|
| **CinterForge3D** | Blender | 3D assets, animation, rendering |
| **CinterForgeImage** | GIMP | Raster/compositing |
| **CinterForgePaint** | Krita | Painting and illustration |
| **CinterForgeVector** | Inkscape | Vector art and SVG |
| **CinterForgeVideo** | Kdenlive | Editing and video production |
| **CinterForgeAudio** | Ardour | Music and audio production |
| **CinterForgeDocument** | Scribus | Desktop publishing |
| **CinterForgePhoto** | RapidRAW | RAW image editing |
| **CinterForgeCode** | [Visual Studio Code / Code - OSS](https://github.com/microsoft/vscode) | Editing and IDE workflows |
| **CinterForgeVoxel** | [Blockbench](https://www.blockbench.net/) | Modeling and voxel content |
| **CinterForge Converter** | CinterForge tooling | Asset and format conversion |

### Built-in browser and OS experiences

**CinterForge WebApp, Collection, Cinter, Forge, Code, IFPlayer, Converter and other approved WebApps** run against the installed, target-native Python Server, rather than assuming a separately running macOS developer server. **Heaven**, shared OS Sessions and the common Loader/desktop experience are cross-core integration targets. **LinuxKit Bedrock** is a server experience, not an additional general-purpose desktop.

The macOS host, Ubuntu and SteamOS expose a single logical application catalog. Launch and installation adapters resolve the correct host/guest core: macOS-native apps use the host bridge; Linux and Windows apps use compatible installed guest runtimes.

---

## Dev Tools

### C, C++, LLVM and cross-compilation

| | Compiler or tool | Installed/provisioned capability |
|:--:|---|---|
| <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons/gcc/gcc-original.svg" height="34" alt="GCC" /> | [GCC](https://gcc.gnu.org/) 10, 11, 12, 13, 14 · [binutils](https://www.gnu.org/software/binutils/) | Historical and modern C/C++ compilation, Gold, objcopy, assembly, archive and link tools. |
| <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons/llvm/llvm-original.svg" height="34" alt="LLVM" /> | [Clang / LLVM](https://llvm.org/) 17, 18, 19 | Clang, clangd, clang-format, clang-tidy, LLVM tooling, compiler runtimes. |
| | [LLD](https://lld.llvm.org/) · [Mold](https://github.com/rui314/mold) · GNU Gold | Fast and alternative linkers; linker choice belongs to each ABI recipe. |
| | [MinGW-w64](https://www.mingw-w64.org/) | Windows x86/x64 cross-compilers and runtime alternatives. |
| | [Clang-CL](https://clang.llvm.org/docs/UsersManual.html#clang-cl) · [LLD-Link](https://lld.llvm.org/windows_support.html) · [xwin](https://github.com/Jake-Shadle/xwin) | MSVC-compatible driver and SDK acquisition routes, without claiming native Microsoft's `cl.exe`. |
| | [GNU ARM cross toolchains](https://gcc.gnu.org/) · [musl](https://musl.libc.org/) | ARM32/ARM64 and alternate Linux/libc cross-target development. |
| <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons/cmake/cmake-original.svg" height="34" alt="CMake" /> | [CMake](https://cmake.org/) · [Ninja](https://ninja-build.org/) · [Meson](https://mesonbuild.com/) | Core native build orchestration across maintained application forks. |
| | [Autoconf](https://www.gnu.org/software/autoconf/) · [Automake](https://www.gnu.org/software/automake/) · [Libtool](https://www.gnu.org/software/libtool/) · [SCons](https://scons.org/) · Waf | Legacy and modern native source build systems. Waf is used from maintained application sources rather than assumed to be a global binary. |
| | [GDB](https://www.sourceware.org/gdb/) · [LLDB](https://lldb.llvm.org/) · [strace](https://strace.io/) · [ccache](https://ccache.dev/) · [Bear](https://github.com/rizsotto/Bear) | Debugging, compilation databases and iteration acceleration. |

### Rust, native web and WebAssembly

| | Toolchain | Role |
|:--:|---|---|
| <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons/rust/rust-original.svg" height="34" alt="Rust" /> | [Rust](https://www.rust-lang.org/) 1.98 · [Cargo](https://doc.rust-lang.org/cargo/) | Rust-native application libraries, tooling and RapidRAW/Tauri builds. |
| | [rustup](https://rustup.rs/) · [rustfmt](https://github.com/rust-lang/rustfmt) · [Clippy](https://github.com/rust-lang/rust-clippy) · [rust-analyzer](https://rust-analyzer.github.io/) | Toolchains, cross-target components, formatting, linting and language services. |
| | Windows GNU/MSVC target libraries, ARM64, musl and wasm32 Rust targets | Preinstalled Rust target coverage; ABI-specific linker/SDK compatibility remains a separate application acceptance test. |
| <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons/wasm/wasm-original.svg" height="34" alt="WebAssembly" /> | [Emscripten](https://emscripten.org/) · [Binaryen](https://github.com/WebAssembly/binaryen) · [WABT](https://github.com/WebAssembly/wabt) | WebAssembly build, optimization, conversion and inspection. |
| | [Tauri](https://tauri.app/) | Rust/JavaScript native-desktop application framework used by selected Creative Suite inputs; application-owned sources still supply their pinned framework revisions. |

### JavaScript, TypeScript and the web application pipeline

| | Toolchain | Role |
|:--:|---|---|
| <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons/nodejs/nodejs-original.svg" height="34" alt="Node.js" /> | [Node.js](https://nodejs.org/) 22 · [npm](https://www.npmjs.com/) | Frontend builds, JS tooling, native modules and editor applications. |
| <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons/typescript/typescript-original.svg" height="34" alt="TypeScript" /> | [TypeScript](https://www.typescriptlang.org/) · [esbuild](https://esbuild.github.io/) | Type checking, bundling and WebApp builds. |
| | [pnpm](https://pnpm.io/) · [Yarn](https://yarnpkg.com/) · [node-gyp](https://github.com/nodejs/node-gyp) | Dependency managers and native Node module compilation. |
| <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons/hugo/hugo-original.svg" height="34" alt="Hugo" /> | [Hugo](https://gohugo.io/) | Compiles authored WebApps and the separate website delivery surface. |
| | [Electron](https://www.electronjs.org/) · [WebKit](https://webkit.org/) | Web/native interface ecosystems, with project-specific integration requirements. |

### Microsoft, PowerShell and managed code

| | Toolchain | Role |
|:--:|---|---|
| <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons/powershell/powershell-original.svg" height="34" alt="PowerShell" /> | [PowerShell](https://github.com/PowerShell/PowerShell) 7.6.6 | Script automation, platform provisioning and Windows-oriented workflows directly in Ubuntu. |
| <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons/dotnetcore/dotnetcore-original.svg" height="34" alt=".NET" /> | [.NET](https://dotnet.microsoft.com/) 8 / 10 SDK | C#, managed SDK projects, compiler/runtime, NuGet CLI, `dotnet msbuild`. |
| | [Mono](https://www.mono-project.com/) | Legacy managed tooling and compatibility. |
| | [Wine](https://www.winehq.org/) · [MinGW-w64](https://www.mingw-w64.org/) · [xwin](https://github.com/Jake-Shadle/xwin) | Cross-building and headless Windows application smoke through supported compatibility routes. |
| | Windows-only Visual Studio and MSVC (external host/guest dependency) | Genuine proprietary compiler/SDK build contracts need correctly licensed Windows tools; they are not claimed as installed Linux executables. |

### Scripting, services and additional languages

| | Toolchain | Role |
|:--:|---|---|
| <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons/python/python-original.svg" height="34" alt="Python" /> | [Python 3.12](https://www.python.org/) · `venv` · pip/pipx · pytest · Cython · pybind11 | Full Server source, test runners, asset processors, native extension bridges and isolated guest runtimes. |
| <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons/java/java-original.svg" height="34" alt="Java" /> | [OpenJDK](https://openjdk.org/) 17/21 · [Maven](https://maven.apache.org/) · [Gradle](https://gradle.org/) 8/9 · [Ant](https://ant.apache.org/) | JVM builds, Java/Kotlin tooling and multi-version application dependencies. |
| <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons/go/go-original.svg" height="34" alt="Go" /> | [Go](https://go.dev/) | Linux and cross-target CLI/service builds. |
| <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons/ruby/ruby-original.svg" height="34" alt="Ruby" /> | [Ruby](https://www.ruby-lang.org/) · [Perl](https://www.perl.org/) · [PHP](https://www.php.net/) · [Lua](https://www.lua.org/) | Common upstream scripting and build-time integrations. |
| | Tcl/Tk · Zsh · Fish · Bash | Interactive and scripting environments, installers and developer utilities. |
| <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons/git/git-original.svg" height="34" alt="Git" /> | [Git](https://git-scm.com/) · [GitHub](https://github.com/) · [rsync](https://rsync.samba.org/) | Source acquisition, versioning and reproducible workspace synchronization. |
| | `zip`, `unzip`, `tar`, `zstd`, `7z`, `rpm`, `dpkg`, Debian packaging | Reproducible release assembly, inspection and installation workflows. |

---

## Packaging and distribution

CinterForgeOS builds are retained under **`Builds/CreativeSuite/Built/cinterforge-os/`**, while Server, WebApp and other named products retain their respective canonical `Builds` roots. Successful **Build → Stage → Test** artifacts are assembled into logical versioned packages under **`Builds/CreativeSuite/Zipped/`**. Packaging does not include arbitrary generated caches, secrets, per-user data or unlicensed third-party installers.

- **Developer/internal Cloudflare:** [cdn-dev.ucreg.io](https://cdn-dev.ucreg.io/) receives approved development artifacts and separately authorized publisher inputs.
- **Public Cloudflare:** [cinterforge.ucreg.io](https://cinterforge.ucreg.io/) receives *only* explicitly approved, redistributable release packages and public resources.
- **Original publisher archives:** `Builds/CreativeSuite/UnforkedInstallers/` remains the canonical installer acquisition source; installed apps live separately in each host/guest's persistent application directories.
- **Website:** the main corporate website is published separately to Namecheap, not mirrored to Cloudflare buckets.

### Published ZIP inventory — source folder → archive → estimated size

The following list is the **target release catalog** for CinterForgeOS and its reusable products. All archive paths are relative to `Builds/CreativeSuite/Zipped/`. The **source folder** identifies the validated product or staged snapshot that is packaged; it is *not* a request to ZIP an entire mutable workspace. Build intermediates stay in `Built`, and source-owned outputs such as `Builds/Server`, `Builds/websites/dev`, and `Builds/SteamPipe/Packs` retain their existing producers. Before Publish, each source is staged/tested, inventoried and given a release manifest and SHA-256 hashes.

**Size key:** approximate **ZIP download sizes** in MiB/GiB, **planning estimates only**, not measured release artifacts. Real size depends on package versions, image sparsity, compression, included upstream apps, Python wheels and selected Packs. `ZIP_STORED` does **not** compress an inner `.tar`. **Current** means an archive name is implemented in `build/publish.sh`; **Planned** means the product and archive still require release-packaging integration. These are *not* success/availability claims.

```text
Builds/CreativeSuite/Zipped/
├── CinterForgeOS-mac-2026.zip                    ~50–300 MiB      [current packager]
├── CinterForgeOS-mac-latest.zip                  same as above    [stable alias, not another payload]
├── CinterForgeOS-linux-amd64-2026.zip            ~10–35 GiB       [current packager; stored rootfs tar]
├── CinterForgeOS-steamos-arm64-2026.zip          ~3–12 GiB        [planned]
├── CinterForgeOS-linuxkit-bedrock-arm64-2026.zip ~80–500 MiB      [planned]
├── CinterForgeOS-ubuntu-apps-amd64-2026.zip      ~3–15 GiB        [planned]
├── CinterForgeOS-steamos-apps-arm64-2026.zip     ~3–15 GiB        [planned]
├── CinterForgeServer-2026.zip                   ~10–100 MiB      [planned]
├── CinterForgeServer-Python-linux-amd64-2026.zip ~0.5–5 GiB       [planned]
├── CinterForgeServer-Python-linux-arm64-2026.zip ~0.5–5 GiB       [planned]
├── CinterForgeServer-Python-mac-arm64-2026.zip   ~0.5–5 GiB       [planned]
├── CinterForgeWebApps-2026.zip                   ~100–900 MiB     [planned]
├── CinterForgeWebApp-Runtime-2026.zip            ~1–30 MiB        [planned]
├── CinterForgePlugins-2026.zip                   ~10–200 MiB      [planned]
├── CinterForgePacks-<pack-id>-2026.zip           ~1 MiB–several GiB per Pack [planned]
├── CinterForgePacks-Catalog-2026.zip             <20 MiB          [planned]
├── CinterForgeAssets-2026.zip                    ~0.2–5 GiB       [planned; curated]
├── CinterForgeLegal-2026.zip                     ~1–100 MiB       [planned; internal only]
├── CinterForgeUniverses-2026.zip                 ~10 MiB–several GiB [planned; internal only]
└── CinterForge-<app>-<platform>-2026.zip          ~50 MiB–6 GiB per app [planned]
```

| Validated source folder / staged product (relative to `Builds/`) | ZIP filename in `CreativeSuite/Zipped/` | Approx. ZIP size | Purpose / contents | Status |
|---|---|---:|---|---|
| `CreativeSuite/Staged/mac/cinterforge-os/` + signed installed `.app` and verified external dispatcher | `CinterForgeOS-mac-2026.zip` | **50–300 MiB** | Standalone Apple Silicon launcher, guest/session UI resources, install/launch/verification logic and external command dispatcher; **no guest images** | Current |
| *Same validated macOS ZIP; no second build folder* | `CinterForgeOS-mac-latest.zip` | **Same size** | Stable download alias; `CinterForgeOS-mac-latest.json` provides version, hash and byte count | Current |
| `CreativeSuite/Built/cinterforge-os/linux/2026/Ubuntu/Ubuntu-24.04-amd64-rootfs.tar` | `CinterForgeOS-linux-amd64-2026.zip` | **10–35 GiB** | Ubuntu 24.04 AMD64 rootfs with KDE, Wine, compilers, SDKs and all baked prerequisites. **Current ZIP_STORED packaging preserves the full uncompressed tar size**; future compressed-image formats may be smaller | Current |
| `CreativeSuite/Built/cinterforge-os/steamos/2026/` *(proposed validated image staging)* | `CinterForgeOS-steamos-arm64-2026.zip` | **3–12 GiB** | SteamOS ARM64 bootable image/layer, kernel/initramfs, FEX/Proton adapters and guest launch resources; not a duplicate Ubuntu distro | Planned |
| `CreativeSuite/CFOS/LinuxKit/` | `CinterForgeOS-linuxkit-bedrock-arm64-2026.zip` | **80–500 MiB** | Signed LinuxKit ARM64 kernel, initrd, command line, VM launcher/bootstrap and verified Bedrock appliance resources; persistent worlds excluded | Planned |
| `CreativeSuite/Built/cinterforge-os/linux/2026/Products/Apps/` *(proposed tested installation snapshot)* | `CinterForgeOS-ubuntu-apps-amd64-2026.zip` | **3–15 GiB** | Reusable **Ubuntu** application-volume seed: redistributable installed upstream apps, Heaven, guest launchers/service configuration, and app integration; no user data or duplicate OS rootfs | Planned |
| `CreativeSuite/Built/cinterforge-os/steamos/2026/Products/Apps/` *(proposed)* | `CinterForgeOS-steamos-apps-arm64-2026.zip` | **3–15 GiB** | Reusable **SteamOS** installed-app seed, approved upstream binaries, Heaven, FEX/Proton launch metadata and desktop entries; no Steam user downloads | Planned |
| `CreativeSuite/Built/cinterforge-os/linux/2026/Products/Server/` (Server source owned by `CinterForgeServer/`) | `CinterForgeServer-2026.zip` | **10–100 MiB** | Full Python Server application code, service entry points, API and version manifest; **not** a copied developer venv | Planned |
| `CreativeSuite/Built/cinterforge-os/linux/2026/Products/Python/linux-amd64/` *(proposed target-native staged environment)* | `CinterForgeServer-Python-linux-amd64-2026.zip` | **0.5–5 GiB** | Ubuntu AMD64 pinned Python interpreter/venv plus installed Server requirements; starts the guest-local API on port 20380 | Planned |
| `CreativeSuite/Built/cinterforge-os/steamos/2026/Products/Python/linux-arm64/` *(proposed)* | `CinterForgeServer-Python-linux-arm64-2026.zip` | **0.5–5 GiB** | Independently built SteamOS ARM64 Python environment; no copying AMD64 Python binaries into ARM64 | Planned |
| `CreativeSuite/Built/cinterforge-os/mac/2026/Products/Python/mac-arm64/` *(proposed)* | `CinterForgeServer-Python-mac-arm64-2026.zip` | **0.5–5 GiB** | macOS ARM64 Python Server distribution where the host component needs local service execution; separate from guest runtimes | Planned |
| `websites/dev/` (produced by `CinterForgeWebApp`/`./Cinter build web`) | `CinterForgeWebApps-2026.zip` | **100–900 MiB** | Compiled browser applications, Collection, Forge, Cinter, Code, IFPlayer and other approved WebApp assets; installed locally, not dependent on the corporate site | Planned |
| `Server/` (portable runtime product owned by `CinterForgeWebApp/runtime`) | `CinterForgeWebApp-Runtime-2026.zip` | **1–30 MiB** | Portable minimal HTTP/init handler, launch/configuration files and runtime product manifest; not a duplicate full Server | Planned |
| `CreativeSuite/Built/cinterforge-os/2026/Products/Plugins/` *(proposed staged output from `CinterForgePlugins`)* | `CinterForgePlugins-2026.zip` | **10–200 MiB** | Approved plugin, Assistant and Pack definitions and runtime catalogs after privacy/license review; generated Pack payloads ship separately | Planned |
| `SteamPipe/Packs/<pack-id>/` (generated from Plugin definitions by `update-assets`) | `CinterForgePacks-<pack-id>-2026.zip` | **1 MiB–several GiB / Pack** | Individually versioned, installable Cards/Packs and content dependencies; install/download only selected Packs | Planned |
| `SteamPipe/Packs/` catalog + canonical `cinterforge_packs.json` product | `CinterForgePacks-Catalog-2026.zip` | **Under 20 MiB** | Stable Pack identities, release hashes, dependencies, entitlement/availability metadata and archive lookup | Planned |
| `CreativeSuite/Artwork/` + approved `Registry/RawImages/` selections | `CinterForgeAssets-2026.zip` | **0.2–5 GiB** | Curated shared original/CFOS assets, artwork, images and reusable presentation resources; large models remain separately installable | Planned |
| `Legal/` | `CinterForgeLegal-2026.zip` | **1–100 MiB** | Legal/licensing resources for internal staging and installation; not the public website's legal-material mirror | Planned; internal |
| `Universes/` | `CinterForgeUniverses-2026.zip` | **10 MiB–several GiB** | Reusable Universe definitions, metadata and approved local content for development/testing | Planned; internal |
| `CreativeSuite/Built/<app>/<platform>/<version>/` (per maintained fork) | `CinterForge-<app>-<platform>-2026.zip` | **50 MiB–6 GiB / app** | Each tested CinterForge Creative Suite fork, code/runtime dependencies and launch metadata; installed independently of its unforked original | Planned; per app |

**Related published products, not silently repackaged:** `Registry/Output/zips/`, `Databases/`, `RAGs/`, `Models/Packages/` and other existing content pipelines retain their own product filenames and release controls. They may be referenced by a CFOS release manifest without being copied into one gigantic OS ZIP. `CreativeSuite/UnforkedInstallers/{Linux,macOS,Windows}/` contains **publisher-supplied input installers**, not CinterForge build outputs: distribute only to the authorized internal CDN, as individual validated inputs or explicitly approved internal archives. Do not bundle non-redistributable EXE/MSI/DMG/AppImage inputs into public CFOS ZIPs or claim installed Wine prefixes are relocatable without testing.

**Installation composition:** the launcher ZIP is small and bootstraps the catalog. Selecting Ubuntu, SteamOS or Bedrock fetches that core's verified OS package and any required **matching** application seed, Python environment, Server, WebApps, catalog and chosen Packs. Installation creates independent persistent writable stores for each core. Versioned dependency manifests prevent mixing mismatched image/Server/Pack versions; the macOS latest alias does not imply guest-image upgrades. Only approved redistributable packages can be copied from the internal CDN to the public CDN.

**Packaging gaps still to close:** `build/publish.sh` currently emits only the versioned Ubuntu rootfs ZIP, versioned macOS launcher ZIP, and macOS latest alias/checksum metadata. The other lines are the required **final-state catalog**, not files already generated by that script. The complete turnkey CFOS pipeline must implement the missing builders, acceptance gates, archive assemblers, signed/hash-checked release catalog and explicitly classified Cloudflare selection before the README's one-download installation promise can be considered verified.

Uploads are explicit, reviewed release actions. A local package, successful source test or build receipt alone does not prove successful external publishing or real runtime acceptance.

---

<sub>Brand artwork is displayed via the open-source <a href="https://github.com/devicons/devicon">Devicon</a> icon collection (loaded from jsDelivr), where an appropriate icon exists. All marks and project names are used for identification and remain subject to their owners' trademark and licensing policies. Additional no-logo projects are linked by name instead of substituting a misleading brand mark.</sub>
