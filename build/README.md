# Building CinterForgeOS

## Prerequisites
- macOS 26 or higher
- Xcode 26 or higher
- M2 or higher
- An activated Steam account with Steam installed and working on the machine
- The setup script _should_ install any prerequisites and ask permission as it does - needs testing
- After running setup, you can either run build, stage and test or you can launch the XCode workspace and Build and Run

It should be as simple as Clone, Setup, Build and Run.  If it is not, that is a bug in the build system.

## Build scripts a glance

```text
CinterForgeOS/
  CFOS                  # CLI, argument validation, shared helpers
  build/
    README.md           # this guide
    setup.sh            # cfos_setup: host prerequisites and source acquisition
    build.sh            # cfos_build: Xcode native host build
    stage.sh            # cfos_stage: versioned application installation
    test.sh             # cfos_test: installed native self-test
    all.sh              # cfos_all: setup → build → stage → test
  CinterForgeOS.xcodeproj/
  guest/                # SteamOS guest components and FEX integration
  host/                 # host-side integrations and forks
  scripts/              # existing detailed build/image/driver tooling
```

All five shell files are **sourced** by `CFOS` and define functions; none runs
independently. This ensures each operation shares one command-line contract and
one set of resolved paths.

## Commands

Run these from the repository root on an Apple Silicon Mac:

```bash
./CFOS setup cinterforge-os mac Release
./CFOS build cinterforge-os mac Release
./CFOS stage cinterforge-os mac Release
./CFOS test  cinterforge-os mac Release
./CFOS all   cinterforge-os mac Release
```

Use `Debug` instead of `Release` for an Xcode debug build, or `all` in the fourth
argument to work on **both Debug and Release**:

```bash
./CFOS all cinterforge-os mac all
```

The **first** `all` means all lifecycle phases. The **fourth** `all` means both
Xcode configurations; it does not mean every CPU architecture or platform.
Only the `cinterforge-os mac` application/platform pair is supported by this
standalone command today. Unsupported combinations fail with a usage error.

Append `--dry-run` to preview commands without installing packages or staging
anything. Dry-run also works on a non-macOS machine for command inspection;
this is *not* equivalent to performing a real build or testing the environment.

```bash
./CFOS all cinterforge-os mac all --dry-run
```

## Lifecycle, step by step

### 1. `setup.sh`: Prepare prerequisites

`cfos_setup` checks Apple Silicon macOS, checks for Homebrew, installs missing
formulae from a deliberately explicit list, and checks expected tools. It then
configures a **separate managed Colima profile** (`cinterforge-linux`) under the
workspace's `Builds/CreativeSuite/Built/cinterforge-os/mac/CFOS/` directory, not the developer's default Colima profile.
Finally, it calls the existing FEX source fetch and Wine userspace fetch scripts:

```text
guest/fex/build.sh fetch
guest/fex/runtime.sh fetch
```

The FEX source checkout defaults to `../FEX` (sibling of `CinterForgeOS`, on the same workspace drive) (override with
`FEX_FORK_CHECKOUT`). **This is an unresolved fresh-clone dependency**: Setup
currently expects the CinterForge-maintained `cinterforge/cinterforge` branch to have been
cloned already. The complete native, graphics, guest, and toolchain dependency
closure has not yet been demonstrated from a clean clone.

Setup does **not** create a SteamOS disk. A user decides whether to download or
select a SteamOS image when the Swift application launches.


### Moving an existing managed Colima directory

All generated macOS state is grouped under the **same** application build directory:

```text
Builds/CreativeSuite/Built/cinterforge-os/mac/
└── CFOS/
    ├── Colima/       # symlink to Builds/CFOS/Colima (real VM data)
    ├── Cache/        # managed Colima download cache
    ├── Docker/       # isolated Docker client configuration
    ├── SwiftPM/      # Swift package build products
    └── work/         # compiled guest, host, images, logs, and Xcode outputs
```

Older layouts used `Source/CFOS`, `Builds/CFOS`, or a `Steamac/` directory
containing `work/` and `SwiftPM/`. **Do not delete those directories.**
Stop the `cinterforge-linux` Colima profile first, then migrate the original
contents to `.../mac/CFOS/` without merging nonempty directories. A checkout
with multiple populated old locations needs manual reconciliation. The Setup
command deliberately stops rather than silently picking a second VM.

**Caution:** Lima uses Unix-domain sockets that have strict macOS path length
limits. The deeper location requested here may prevent Colima from starting
on some systems; this must be verified on the target Mac. Do not work around
that by creating another unmanaged VM.

## How paths and environment are managed

The root launcher resolves its own location, so it does not matter what your
current working directory is. It uses the parent of the checkout as its
workspace:

```text
workspace/
  CinterForgeOS/      # source checkout; scripts and Xcode project
  Builds/             # generated build artifacts (not authored inputs)
  Builds/
    CFOS/             # managed Colima VM, disks and cache
```

Optional environment variables:

| Name | Purpose |
| --- | --- |
| `FEX_FORK_CHECKOUT` | Local maintained FEX checkout, defaults to `../FEX` (sibling of `CinterForgeOS`, on the same workspace drive). |
| `CFOS_INSTALL_ROOT` | Installed CinterForgeOS version-container root. |

Other environment variables used inside existing guest/host scripts retain
their own documented meanings. This command does not rewrite Git sources,
change the Xcode project, or create another artifact manifest.

## Security and distribution

CinterForgeOS is a public repository. Do not commit Steam publisher secrets, API credentials, signed-in user state, disk, images, or proprietary cached dependencies. Public users should be able to build without access to Hope's private credentials. Publishing and privileged authentication belong outside this public lifecycle.

When contributing build changes, keep phase ownership obvious: Setup prepares,
Build compiles, Stage installs, Test verifies, and the Swift launcher handles
SteamOS user-disk consent and creation. Keep detailed functionality in its
existing implementation rather than adding new framework layers.

### macOS Colima socket path limit

**Colima/Lima requires a short *physical* home path.** A `/tmp` symlink to
`Builds/CreativeSuite/Built/cinterforge-os/mac/CFOS/Colima` does not fix the
problem, because Lima resolves the link while constructing its Unix sockets.
The error is `UNIX_PATH_MAX` (or `ha.sock: connect: invalid argument`).

The authoritative managed VM home is therefore:

```text
Builds/CFOS/Colima/                     # real VM disks, configs and sockets
Builds/CreativeSuite/Built/cinterforge-os/mac/CFOS/
    Colima -> /absolute/path/to/Builds/CFOS/Colima  # view only; created by Setup
    Cache/
    Docker/
    SwiftPM/
    work/
```

Both directories are within `Builds`. **The short physical path is required
by macOS, not an alternate fallback.** Xcode and `./CFOS setup` use the same
absolute Colima home and the same `cinterforge-linux` profile. Neither uses
the developer's default Colima profile. Setup never silently deletes a VM.
