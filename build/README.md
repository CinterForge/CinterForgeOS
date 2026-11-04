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
```

`./CFOS all cinterforge-os <mac|linux> Release` runs Setup → Build → Stage → Test. Both platforms accept `Debug` as a separate build/install identity and `all` as a configuration selector to process Debug and Release. `publish` accepts Release only. Append `--dry-run` to inspect commands without writing artifacts.

### macOS host product

The macOS product requires Apple Silicon, an appropriate Xcode installation, native Homebrew, guest graphics dependencies and the maintained fork sources. Setup provisions pinned host tools, isolated runtimes and a managed Colima profile. Build invokes the established Xcode image/host build machinery. Stage installs the complete app to Steam's CinterForge application area and separately stages the guest command dispatcher from `guest/dispatch/external`. Test verifies the installed native application and available graphics validation. It does not certify every guest application or every Steam title.

### Linux AMD64 headless product

The Linux product is an Ubuntu 24.04 AMD64 OCI userspace for headless application tests, packaging and server-side workflows. It is **not** the ARM64 SteamOS desktop and does not create a Linux GUI VM. On an Apple Silicon Mac, Build uses the managed Colima/Docker environment; on a Linux build host, it uses the existing Docker/Buildx daemon. Setup verifies that environment and its pinned Dockerfile. It does not need Xcode, a CinterForge checkout or an installed Steam client.

Build creates `Builds/CreativeSuite/Built/cinterforge-os/linux/<version>/Ubuntu/Ubuntu-24.04-amd64-rootfs.tar`. Stage verifies and copies that artifact into versioned `Builds/CreativeSuite/Staged/` and the persistent Steam applications tree. Test imports the **installed** archive and verifies AMD64 execution and the read-only installed-applications bridge. `2026-Debug` and `2026` are distinct artifacts. Application-specific headless tests consume this installed environment rather than rebuilding the OS.

### Publishing

Publish packages only a validated, already-staged Release. Linux Publish copies the matching installed rootfs archive into `Builds/CreativeSuite/Zipped`. macOS Publish packages both the installed app and the OS-owned staged guest dispatcher/scripts, retaining their persistent Applications layout. The command prepares the **local distribution**; remote Steam or Cloudflare upload requires separate approval and credentials.

## Architectural constraints

The guest system base is replaceable; installed applications and user projects remain persistent and independently owned. The Linux AMD64 build environment and the graphical ARM64 SteamOS guest are separate. Proton is a test/runtime compatibility layer, not a cross-compiler dependency. `cinterforge-FEX` is the sibling fork checkout name; FEX remains the runtime's technology name.

Never move generated data into source checkouts, hardcode a developer workspace, install into Proton prefixes or reimplement OS setup/build in CinterForge. Preserve valid caches; a changed ABI, toolchain or input revision invalidates only the affected outputs. A passing Linux headless smoke does not establish that the installed macOS KDE or Windows/Proton UI works.

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
