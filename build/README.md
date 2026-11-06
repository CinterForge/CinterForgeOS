# Building CinterForgeOS

CinterForgeOS is a standalone source repository. Its public `./CFOS` entry point owns the macOS SteamOS/KDE host product and the Linux AMD64 **headless Ubuntu server/test environment**. CinterForge delegates to these commands; CinterForgeOS does not import CinterForge's build scripts, source checkout or application lifecycle.

## Workspace and ownership

```text
<workspace>/
├── CinterForgeOS/                   # authored OS source and ./CFOS
├── cinterforge-FEX/                # maintained FEX source fork
├── <other maintained forks>/       # sibling source repositories
└── Builds/                         # all generated tools, images and packages

<resolved Steam library>/steamapps/common/CinterForge/Applications/
├── macOS/CinterForgeOS/2026/       # installed CinterForge OS.app
├── Linux/CinterForgeOS/2026/       # installed headless Ubuntu AMD64 runtime
└── Test/                            # staged, external guest dispatch and tools
```

Paths are derived from the `CFOS` script location. The managed Colima VM state lives under `Builds/CreativeSuite/CFOS/` (including `Colima/`); compiled macOS and Linux artifacts remain under `Builds/CreativeSuite/Built/cinterforge-os/`. The only managed generated destinations are the sibling `Builds/` and the resolved Steam application tree. `CFOS_STEAMAPPS` can designate the Steam `steamapps` directory for Stage/Test/Publish; Setup and Build do not require Steam. `CFOS_INSTALL_ROOT` can designate the macOS installed-product root.

## Source checkout bootstrap (required before Ubuntu Setup)

CFOS owns fork creation, cloning, and the checked-in source registry under
`scripts/fork.py` and `scripts/source_repositories.csv`. Start from the
CinterForgeOS checkout, with all other repositories as siblings:

```bash
./CFOS clone all --dry-run   # read-only, fail if any required source is missing/invalid
./CFOS clone all             # explicitly clone missing forks (no implicit Setup clones)
./CFOS clone all --dry-run   # require a clean source-inventory result
```

The repository list includes `cinterforge-kosmickrisp-mesa` (the host
graphics integration is built from the CFOS source tree, not from a separate
`cinterforge-kosmickrisp` checkout). Converter (`cfconvert`) belongs to CinterForgeWebApp and is built through
CinterForge's application lifecycle; it is not a separate source repository.

`clone all --dry-run` does not access GitHub or repair any path. Existing
non-Git source directories are treated as invalid, not overwritten; the
operator must resolve them explicitly before cloning. Products implemented
within CinterForge are not added to the sibling-repository manifest. The GitHub-facing `./CFOS fork all` action is separate and may need
the GitHub CLI and authentication. CinterForge can forward its historical
clone/fork command to CFOS but contains no clone implementation itself.

**Linux and macOS Setup fail at the source-inventory gate before installing
host packages or changing managed VMs.** This check is also executed by the
Ubuntu Setup Python entry point when called directly.

## Lifecycle

Run from any working directory:

```bash
./CFOS setup   cinterforge-os mac   Release
./CFOS build   cinterforge-os mac   Release
./CFOS stage   cinterforge-os mac   Release
./CFOS test    cinterforge-os mac   Release
./CFOS publish cinterforge-os mac   Release

./CFOS setup   cinterforge-os linux Release
./CFOS build   cinterforge-os linux Release
./CFOS stage   cinterforge-os linux Release
./CFOS test    cinterforge-os linux Release
./CFOS publish cinterforge-os linux Release

# LinuxKit is CFOS's minimal ARM64 dedicated-server appliance.
# LinuxKit Build generates kernel/initrd/cmdline; on macOS CFOS subsequently
# compiles and signs CinterBDSVM. Consumers never rebuild either artifact.
./CFOS build cinterforge-os linuxkit Release
./CFOS test cinterforge-os linuxkit Release
./CFOS launch cinterforge-os linuxkit Release --server "$HOME/Source/Builds/CraftMaster/Server/Linux/Server"
```

`./CFOS all cinterforge-os <mac|linux> Release` runs Setup → Build → Stage → Test. Both platforms accept `Debug` as a separate build/install identity and `all` as a configuration selector to process Debug and Release. `publish` accepts Release only. Append `--dry-run` to inspect commands without writing artifacts.

### macOS host product

The macOS product requires Apple Silicon, an appropriate Xcode installation, native Homebrew, guest graphics dependencies and the maintained fork sources. Setup provisions pinned host tools, isolated runtimes and a managed Colima profile. Build invokes the established Xcode image/host build machinery. Stage installs the complete app to Steam's CinterForge application area and separately stages the guest command dispatcher from `guest/dispatch/external`. Test verifies the installed native application and available graphics validation. It does not certify every guest application or every Steam title.

### Linux AMD64 headless product

The Linux product is **one Ubuntu 24.04 AMD64 OCI userspace** for both Linux/Windows cross-build and headless tests **and** an optional KDE Plasma desktop session. It is not the ARM64 SteamOS guest or another libkrun disk. Its KDE session uses Xvfb and noVNC over Mac loopback, embedded inside the native Swift/WebKit app; XQuartz is not required. SteamOS retains its independent libkrun/Proton launch path. The CFOS launcher can select Ubuntu, SteamOS, or both. On an Apple Silicon Mac, Build uses the managed Colima/Docker environment; on a Linux build host, it uses the existing Docker/Buildx daemon. Setup first validates that every checkout in the CFOS-owned source registry is present locally, including the maintained CinterForge and Creative Suite forks. It then verifies the host Docker/Colima prerequisites and pinned Dockerfile. It does not need Xcode or an installed Steam client for the Linux-only build, and it never clones source automatically.

Build creates `Builds/CreativeSuite/Built/cinterforge-os/linux/<version>/Ubuntu/Ubuntu-24.04-amd64-rootfs.tar`. Stage verifies and copies that artifact into versioned `Builds/CreativeSuite/Staged/` and the persistent Steam applications tree. Test imports the **installed** archive, compiles and runs native Linux code plus a MinGW Windows PE through Wine/Xvfb, tests a KDE Plasma session, and verifies the read-only installed-applications bridge. A `headless-verified.json` receipt records the exact installed rootfs hash, Dockerfile, headless tests and KDE launcher. CFOS macOS and CinterForge apps require that successful Ubuntu Build → Stage → Test before they begin. The shared Ubuntu **Release** product supplies both macOS Debug and Release; a changed image or test script invalidates its receipt until it is rebuilt, restaged and retested. `2026-Debug` and `2026` are distinct artifacts. Application-specific headless tests consume this installed environment rather than rebuilding the OS.

### Ubuntu Server and WebApp product integration

The Ubuntu Release lifecycle now includes the independently owned sibling
`CinterForgeServer` and `CinterForgeWebApp` products. **Build** uses Ubuntu's
own AMD64 toolchain to invoke the existing `cinterforge_website/Cinter build web`
command (Hugo and the WebApp-owned minimal `CinterRuntime.py` packager); it does
not implement another Hugo builder. CFOS packages the full authored Server
source separately, without host Python environments or Git metadata. The
resulting product fingerprint is recorded under
`Builds/CreativeSuite/Built/cinterforge-os/linux/2026/Products/manifest.json`.

**Stage** installs the Server source under
`Ubuntu/steamapps/common/CinterForge/Applications/Server/CinterForgeServer/2026/Source/`,
and the WebApp under
`.../Applications/WebApps/CinterForgeWebApp/2026/`, using the normal
`WebApps/CinterForgeWebApp/2026/websites/dev/` and `runtime/CinterRuntime.py` layout. Ubuntu's rootfs is
replaceable; these application products are retained in the separate Ubuntu
`steamapps` volume. The Server's Python environment is created *inside the
Ubuntu AMD64 image* at `.../CinterForgeServer/2026/Python/`—never copied from
macOS. This initially provides the interpreter and standard-library services;
the full optional AI/creative Server Python dependency closure requires its
own validated, pinned install contract before full Server functionality can
be called production-ready.

**Test** mounts the installed volume into the *installed* Ubuntu image,
imports the Server's central IO module, launches the WebApp runtime on **guest
loopback port 20380**, and checks both API health and HTML. No host port is
published. Only after the headless Linux/Windows and KDE acceptance **and**
these installed product tests pass does CFOS commit its Ubuntu readiness receipt.
Both headless shell and KDE mode start the same installed WebApp API through
one CFOS guest service controller, with no macOS host port publication. A KDE
desktop entry opens the local browser frontend. Credential files and private
settings are excluded from the Server source product; the published Server
repository must still undergo its own public-source security review.

Native Linux Creative Suite application builds still belong to their existing
Cinter app recipes and adapters. This product integration does not mark
those incomplete Inspect recipes as executable or claim their Setup, Build,
Stage, and Test are already passing. Their subsequent Stage operations must
install into the *same* Ubuntu `/run/steamapps` and their own receipts must be
verified independently.

### Downloadable release layout

The user-facing entry point is the stable [**Latest OS Build — macOS Apple Silicon**](https://cinterforge.ucreg.io/CreativeSuite/Zipped/CinterForgeOS-mac-latest.zip). After a successful macOS Release packaging step, CFOS retains the versioned `CinterForgeOS-mac-2026.zip` **and** produces `CinterForgeOS-mac-latest.zip` in `Builds/CreativeSuite/Zipped/` so the Cloudflare publisher can promote the stable object name without rewriting the README. The versioned package includes the validated staged macOS `.app`, guest-command distribution and its embedded SHA-256 release manifest. The stable alias must be published only after its corresponding versioned release passes acceptance. An alias existing locally is not evidence that it has been uploaded or is ready for end users.

A complete installation is **not one giant disk**. Its independently updatable release units are:

| Release unit | What is downloaded | Persistent state / guest applications |
| --- | --- | --- |
| **macOS host** | Signed/notarized `CinterForge OS.app`, bundled host adapters, launchers, installation UX and guest release catalog; packaged as `CinterForgeOS-mac-<version>.zip` plus a stable latest alias. | macOS system apps remain on the host. The host app does not contain writable guest disks or publisher installer archives. |
| **Ubuntu 24.04 AMD64** | Immutable Docker-exported `Ubuntu-24.04-amd64-rootfs.tar` and validated build/test metadata, packaged as `CinterForgeOS-linux-amd64-<version>.zip`. The Ubuntu rootfs is **not** a raw VM `.img` in the current implementation. | A **distinct Ubuntu-owned** `steamapps` directory is mounted at `/run/steamapps`. A distributable *seed* of tested Server, WebApps, original apps and Packs can be installed there on first run. Never overwrite a user's persisted volume with an update. |
| **SteamOS ARM64** | Versioned, bootable SteamOS image and integration/runtime payload; package name reserved as `CinterForgeOS-steamos-arm64-<version>.zip`. | SteamOS owns its **own** persistent Steam library, installed original/fork apps, Server/WebApps and Packs. It does not share Ubuntu's writable volume. |
| **LinuxKit / Bedrock** | ARM64 LinuxKit kernel, initrd, boot cmdline and CFOS-built/signed host VM launcher; package name reserved as `CinterForgeOS-linuxkit-arm64-<version>.zip`. | Bedrock server binaries (subject to publisher terms), configurations and saved worlds are supplied or downloaded separately and kept persistent. LinuxKit is an appliance, not a full desktop or third Creative Suite build OS. |

The SteamOS and LinuxKit ZIP names describe the **target release contracts**; their complete publication and first-run installation flows are separate from the macOS/Ubuntu archives currently assembled by `build/publish.sh`. Do not publish placeholder ZIPs or claim a guest is available before its real Build → Stage → Test has succeeded.

The release catalog used by the installer must identify the exact platform/architecture, version, object key, byte size, SHA-256 and required companion/seed products. Only first-party or explicitly redistributable packages may be placed on the public `cinterforge.ucreg.io` bucket. The `cdn-dev.ucreg.io` bucket is for approved internal/development packages (subject to its access controls and third-party licensing); original publisher installers are sourced from `Builds/CreativeSuite/UnforkedInstallers` or an explicitly authorized CDN object, **never** from another user's installed `steamapps` tree.

The launcher should download **only** the selected OS/server and its missing prerequisites, verify hashes, install the immutable system artifact, create or retain a separate writable data volume, and offer Install/Launch in the unified desktop. Updating a system archive cannot delete applications, games, Packs or Bedrock worlds. A first-time macOS user should not need Git, source checkouts, `./CFOS` or a manually mounted guest image; any remaining required host service (such as Docker/Colima for the present Ubuntu adapter) must be provisioned by the supported turnkey installer before this is considered a fulfilled release.

### Publishing

Publish packages only a validated, already-staged Release. Linux Publish copies the matching installed rootfs archive into `Builds/CreativeSuite/Zipped`. macOS Publish packages both the installed app and the OS-owned staged guest dispatcher/scripts, retaining their persistent Applications layout. The command prepares the **local distribution**; remote Steam or Cloudflare upload requires separate approval and credentials.

## Architectural constraints

The guest system base is replaceable; installed applications and user projects remain persistent and independently owned. The Linux AMD64 build environment and the graphical ARM64 SteamOS guest are separate. Proton is a test/runtime compatibility layer, not a cross-compiler dependency. `cinterforge-FEX` is the sibling fork checkout name; FEX remains the runtime's technology name.

Never move generated data into source checkouts, hardcode a developer workspace, install into Proton prefixes or reimplement OS setup/build in CinterForge. Preserve valid caches; a changed ABI, toolchain or input revision invalidates only the affected outputs. A passing Ubuntu Test proves the general compiler, Wine executable smoke, Plasma session, and installed Applications bridge, not every application-specific toolchain or Windows/Proton GUI. Both the CFOS macOS app and CinterForge Desktop bundle the **same** CFOS-authored host Ubuntu adapter under `Contents/Resources/Ubuntu/`; each installed .app depends on one separately installed Ubuntu rootfs archive. It can launch in a native Swift/WebKit desktop window without a source checkout.

### Shared LinuxKit Bedrock launcher

CFOS Build validates the source-fingerprinted ARM64 LinuxKit artifacts, while
`./CFOS test cinterforge-os linuxkit Release` installs a disposable official
Bedrock server and exercises Rosetta, guest networking, NetherNet, console
`list`, graceful `stop`, and exit status using the existing CraftMaster smoke.
The internal validation also checks kernel/initrd and the signed macOS
Virtualization launcher. The macOS-only `launch cinterforge-os linuxkit Release --server <directory>` command verifies
that image and the explicitly supplied Bedrock server, its runtime loader,
NetherNet probe and configuration, and then starts the same CFOS VM host.
The macOS Desktop and CinterForge CraftMaster call this command instead of
packaging or rebuilding another LinuxKit kernel, Swift launcher or Debian
guest. An installed Desktop may locate its CFOS entry through
`CINTER_CFOS_LAUNCHER`; that installed launcher must resolve its independently
versioned CFOS runtime. `--dry-run` prints the intended operations only.

### Ubuntu consolidation boundary

Ubuntu's installed distribution includes a **separate persistent Ubuntu-owned `steamapps` directory** beside the versioned rootfs `Distro` folder. In headless and KDE modes CFOS bind-mounts this directory at `/run/steamapps`, matching SteamOS's guest-visible path `common/CinterForge/Applications/...` without sharing SteamOS's actual writable library. It holds Linux/Windows installed application versions, the `DesktopEntries/share/applications` KDE menu directory, Test and Logs, and is preserved when the Ubuntu rootfs is restaged. Stage initializes the volume; Test must mount it and prove read/write visibility from inside the installed AMD64 image. App install/shortcut production must target the guest mount and be validated separately before release.

CFOS produces three distinct Linux execution artifacts. **LinuxKit** is the minimal ARM64 Bedrock/server appliance and obtains Rosetta translation from the Apple Silicon macOS host at launch; it does not contain a Debian build disk or Creative Suite job runner. **Ubuntu 24.04 AMD64** is the sole deep Linux/Windows build-and-test userspace, with headless and optional KDE desktop sessions. **SteamOS ARM64** is the graphical consumer environment and supports compatible Linux x86-64 and Windows applications via its runtime stack. The ARM64 SteamOS image assembly currently invokes tooling within a Debian Trixie builder container; this is a build-host detail that must be migrated to the verified Ubuntu build environment, not a fourth shipped OS. Each artifact requires native Build → Stage → Test evidence before it is accepted by dependent products.

### Shared Linux/Windows workstation toolchains

The Ubuntu AMD64 rootfs includes native GCC 10 and modern GCC/Clang, LLVM's
`clang-cl`/`lld-link`/resource tools, the distro's MinGW-w64 i686 and x86-64
cross-compilers, Wine, GCC/Clang debugging tools, Rust 1.98/Cargo, `xwin`,
Node.js 22, Go, Java, Mono, **PowerShell 7.6.6**, the Ubuntu-packaged
**.NET 8 and .NET 10 SDKs** (including `dotnet msbuild` and `dotnet nuget`),
Qt, GTK, KDE and broad audio/video/graphics SDKs. PowerShell is installed
from the exact Microsoft release `.deb` with its published SHA-256; no rolling
Microsoft APT feed is added. The installed rootfs Test runs PowerShell,
restores and builds a C# `net10.0` console application without a remote NuGet
feed, and executes the resulting assembly as the non-root guest user. These
cross-platform tools supplement Clang-CL, `xwin` and MinGW; they do **not**
replace Windows-only Visual Studio, MSVC `cl.exe` or Windows-specific MSBuild
tasks.
Linux Test must compile and execute GCC 10 C/C++ and Rust code, run Node, and
compile Windows PE binaries and launch one under Wine; installed tools are
checked rather than merely assumed from Dockerfile declarations. The same
rootfs includes upstream SDL3 **3.4.18**, checksum-pinned and built for both
Linux AMD64 and Windows x64 (MinGW POSIX). Linux Test compiles and runs a
SDL3-linked Linux executable and a Windows PE linked to the matching SDL3.dll
under Wine. This is the shared OpenGL/SDL3 foundation for CinterForgeSteam;
Steamworks SDK/app sources and their own ABI/package tests remain app-owned.

The first Inspector-derived Ubuntu dependency pass also includes Ardour's
previously missing Linux development packages (GTKmm 2 headers, LV2, Vamp,
WebSockets, CppUnit and the required Boost components). The rootfs contract
checks their installed package status. This is an initial closure, not proof
that all application recipes have complete build dependencies.

Docker `export` produces a portable rootfs tar but does not retain `ENV`,
`WORKDIR` or `CMD`. Both CFOS Test and the installed Mac adapter restore the
same toolchain environment on import, including Rust/Cargo, xwin and Node PATH;
the imported image tag also includes the configuration digest. The Swift
launcher can select **SteamOS**, **Ubuntu Desktop**, or **Both**. Its Ubuntu
window embeds KDE Plasma through a loopback-only noVNC WebKit view, does not
require XQuartz, and discovers Docker/Colima even when started by Finder with
an abbreviated PATH. This is a software-rendered desktop pending GPU/session
performance validation on macOS; it is **not** equivalent to native Vulkan
acceleration or a libkrun guest. The Mac adapter requires a supported installed
Docker backend; it does not redistribute Docker or Colima binaries.
 Because the
Ubuntu build definition and tests are in its success receipt, adding or
changing these tools requires rebuilding, staging and retesting Ubuntu before
downstream Setup or Build is authorized.

The maintained Creative Suite forks require distinct application **ABIs**:

| Fork | Additional platform-specific dependency owner |
| --- | --- |
| CinterForgeAudio (Ardour) | Pinned GCC **10 POSIX MinGW/MSVCRT** and its `x-mingw.sh` dependency closure; this is **not** native Ubuntu `gcc-10` or generic MinGW 13 |
| CinterForge3D (Blender) | Clang-CL and `xwin`-acquired Windows SDK/CRT with pinned Blender libraries; some Windows features require CUDA, HIP, OptiX and DLSS SDKs |
| CinterForgeDocument (Scribus) | Genuine MSVC/Visual Studio and matching Qt 6 and Scribus Windows libraries for its MSVC target |
| CinterForgeImage (GIMP) | MSYS2/GTK/GEGL/babl, with its upstream dependency build scripts and optional vcpkg target |
| CinterForgePaint (Krita) | KDE CI dependency management and Qt 6, Windows package tooling |
| CinterForgePhoto (RapidRAW) | Rust 1.98, Tauri 2, frontend Node tooling and target-specific webview dependencies |
| CinterForgeVector (Inkscape) | Windows **MSYS2 UCRT64** dependency stack, separate from MSVCRT MinGW |
| CinterForgeVideo (Kdenlive) | Its pinned KDE Frameworks/MLT/FFmpeg dependency versions |

Some Windows SDKs and proprietary vendor toolkits cannot be redistributed
inside a public Ubuntu rootfs. CFOS ships the open-source cross-toolchain and
SDK-acquisition *tools*; the application Setup stage obtains and validates
licensed or pinned vendor inputs into its versioned `Builds/Dependencies`
location. True `cl.exe`/MSBuild targets require a licensed Windows-capable
execution environment with the required Microsoft Build Tools and SDK; Wine
plus MinGW does not become MSVC simply by installing more packages. `xwin`
provides SDK/CRT inputs for the Clang-CL route but not Microsoft's compiler.
No application is considered built or tested until its own staged executable
and ABI closure pass its platform-specific Test. Prefer retaining independently
versioned app dependencies in Builds across ordinary source edits, and
invalidate only the requirements affected by a material toolchain change.

### macOS Colima memory for SteamOS builds

The Debian guest-image builder runs inside the same managed Colima profile
used for the Linux headless tests. Linux tests can start that profile with 8
GiB, but macOS SteamOS Build requires a larger allocation. CFOS macOS Setup
and Build target **32 GiB guest memory** by default. They measure the RAM
inside the running VM, and when it is undersized, stop and restart that same
profile with the new allocation **without deleting its disk or Docker images**.
The physical Mac must also leave at least 4 GiB for macOS/Xcode; a 32 GiB VM
therefore needs at least 36 GiB of physical RAM. An override is available for
machines with a validated smaller build configuration:
`CFOS_MAC_BUILD_MEMORY_GIB=<integer> ./CFOS build cinterforge-os mac Release`.
If the host cannot accommodate the requested size, CFOS reports the limitation
without stopping an already running VM. This memory requirement is for the
SteamOS *builder*, not the runtime guest's configured memory.

### macOS Colima socket paths

Source checkouts may be located under any workspace path; the CFOS resolver derives the
shared sibling `Builds` directory rather than using `$HOME` or fixed mountpoints.
Lima itself uses pathname-based Unix sockets with a macOS length limit. To
avoid imposing short repository names, new CFOS-managed instances use a
compact profile (`c`) with Lima state at `Builds/CreativeSuite/CFOS/L/`,
while Colima configuration remains at `Builds/CreativeSuite/CFOS/Colima/`.
These directories are on the same selected build volume. Existing legacy
instances are reused when their socket path fits, or left untouched when a
compact instance is needed. Neither Setup nor Build creates a symlink or
relocates a user's VM disk automatically.

The operating-system socket limit cannot be removed by renaming the project.
For exceptionally deep physical mount paths that exceed the limit even with
the compact layout, CFOS reports the specific socket path before creating
VM state. Linux-native hosts use their preconfigured Docker daemon instead.

### cinterforge_website documentation content package

The **CFOS Linux Build** produces a standalone Hugo-content package from the
current CinterForgeOS source checkout. The private corporate website's build
consumes the extracted package from `Builds`; it must **not** read a sibling
CFOS Git checkout or maintain independent copies of the documentation.

| Public website URL | CFOS source authority |
|---|---|
| `/technology/` | `docs/technical/CinterForge OS Technology Stack.md` |
| `/technology/cinterforgeos/` | `README.md` |

Producer command (also invoked from `./CFOS build cinterforge-os linux Release`
after the Server/WebApp products build):

```bash
python3 build/website_content.py build
```

Outputs, resolved relative to the sibling shared Builds root:

```text
Builds/
├── CreativeSuite/Zipped/
│   └── CinterForgeOS-WebSite-Content-2026.zip
└── websites/content/CinterForgeOS/
    ├── content/technology/_index.md
    ├── content/technology/cinterforgeos/_index.md
    ├── static/technology/cinterforgeos/assets/docs/dev-environment/
    │   ├── Hero Shot.png
    │   ├── Screen 1 - XCode and macOS.png
    │   ├── Screen 2 - VS Code.png
    │   └── Screen 3 - SteamOS.png
    └── release-manifest.json
```

The ZIP includes SHA-256 and byte counts for every page and asset. The
`install` operation validates all entries and installs them in the same
Builds location **without requiring CFOS source or Git access**:

```bash
python3 build/website_content.py install \
  --archive ../Builds/CreativeSuite/Zipped/CinterForgeOS-WebSite-Content-2026.zip
```

CFOS Setup also installs an already-present ZIP (for example one previously
retrieved from the internal development CDN). The dedicated extracted tree is
replaced without disturbing other website builds or WebApps.

**Corporate website consumer contract:** `cinterforge_website` Setup acquires
this ZIP if absent and validates/unpacks it into
`Builds/websites/content/CinterForgeOS`. Its Hugo Build mounts or overlays
that tree's `content/` and `static/` into the website build source, replacing
only the two technology routes. The Namecheap website output remains outside
Cloudflare buckets. The generated Hugo pages retain their URLs; relative
CFOS image links are rewritten to site-local `/technology/cinterforgeos/assets/`
paths and other CFOS repository-document links lead to the upstream GitHub
repository. The source Markdown includes inline HTML logos: the website's
Goldmark configuration must allow intended trusted inline HTML while its
content inputs remain restricted to the verified release package. Do not edit
generated content under Builds; edit the two CFOS Markdown files instead.

A ZIP in `Zipped` is not evidence of a Cloudflare upload. Publishing it to
`cdn-dev.ucreg.io` remains an explicit developer-CDN operation and should not
mirror the corporate Hugo website into Cloudflare.
