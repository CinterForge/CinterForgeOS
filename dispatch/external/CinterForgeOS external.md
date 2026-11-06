# CinterForgeOS — external application operations

**Audience:** Engineers working in the CinterForge repository who need to install, build, stage, launch, inspect, and test Linux and Windows applications **using an already-built, running CinterForgeOS (CFOS)**.

**Scope:** This document describes `creativeSuite/apps/cinterforge-os/external/` and the surrounding operational contract. It does **not** explain how to construct the SteamOS image, build its kernel, or develop the macOS VM launcher. For those subjects, see the CinterForgeOS repository's `docs/technical/CinterForgeOS guest.md`, `CinterForgeOS host.md`, `CinterForgeOS XCode Project.md`, and the overarching architecture document.

## 1. Why this directory exists

CFOS is the runtime environment, **not** the place to maintain application-development procedures. Once it has booted SteamOS and KDE, almost all interactive tasks—launching installed software, examining FEX and Wine, collecting baselines, measuring publisher installers, and running tests—should execute from **host-editable files outside the guest OS image**.

This directory is the **source of those external operational files**, kept in CinterForge alongside the Creative Suite lifecycle code. The installed copies live in the persistent Steam library and are visible to the running guest via the single `/run/steamapps` VirtioFS share.

The intended development loop is:

1. Edit the relevant file in this `external/` tree.
2. From the **Cinter repository root on macOS**, use `./Cinter stage cinterforge-os mac Release` to deploy it through the existing Stage operation.
3. In an **already-running CFOS Konsole**, use `Cinter <command> ...` from the default prompt.
4. Examine results in the persistent `Applications` tree, accessible from macOS and CFOS.

**No CFOS image rebuild, Xcode rebuild, or guest restart should be required for changes confined to external operational scripts.** Changes to an OS-essential bridge, mount, system service, kernel, or KDE boot integration are different and may require an OS rebuild.

## 2. Mental model: two Cinter entry points, one interface

```text
macOS developer shell (working directory: ~/Source/Cinter)
    ./Cinter stage cinterforge-os mac Release
                 |
                 v
CinterForge Creative Suite lifecycle (existing Stage)
    reads this repository's external/ tree
    reads the CFOS app already built in Xcode
    stages/deploys signed app and external files separately
                 |
                 v
Host Steam library: steamapps/common/CinterForge/Applications/
    Test/Dispatcher/Cinter         <- persistent external string router
    Test/Scripts/<group>/*       <- editable implementations
    <platform>/<product>/<version>/ <- installed app versions
    Logs/...                     <- persisted reports and diagnostics
                 ^
                 | single VirtioFS share mounted at /run/steamapps
                 |
SteamOS / KDE / Konsole
    Cinter baseline all
      -> /usr/bin/Cinter               <- minimal OS-side bridge
      -> Test/Dispatcher/Cinter        <- external resolver
      -> Test/Scripts/baseline/baseline.sh
      -> Test/Scripts/baseline/baseline-environments.py
```

The **public command** is `./Cinter` on macOS and `Cinter` in guest Konsole. Paths above describe implementation and storage; they are **not** instructions to invoke individual scripts by hand. The guest's `/usr/bin/Cinter` is the exception that must be inside the OS, because it establishes access to the external command system. External command implementations should not be copied into `/usr/lib/cinterforge/`.

**Do not conflate the CFOS guest with the separate Ubuntu AMD64 environment.** CFOS/KDE is the graphical, ARM64 SteamOS desktop, with FEX executing supported x86-64 Linux binaries and Wine (under FEX) executing supported Windows applications. The separate Ubuntu AMD64 userspace is the **headless Linux x86-64 build/test environment**. A graphical Linux test runs the actual staged application in CFOS; a headless Linux test belongs to the existing Ubuntu pipeline. Windows build tooling belongs to the existing Linux-to-Windows cross-build pipeline; CFOS Wine is for installing, running, and examining Windows outputs, **not** a replacement build infrastructure.

## 3. Directory map and responsibilities

This listing reflects the current reorganized source; `installl/` has **three l's**, intentionally retained exactly as it currently exists. Do not silently rename it without updating all callers and the Stage layout.

| Source | Responsibility | Key files |
|---|---|---|
| `dispatch/` | External command resolution and compatibility operations | `cinter-dispatcher.sh`, `cinter-commands.sh` |
| `baseline/` | Guest, FEX, and Wine environment inventory; hashes/diffs | `baseline.sh`, `baseline-environments.py`, `filesystem_inventory.py` |
| `inspect/` | Runtime health and publisher-install inspection | `inspect.sh`, `inspect-unforked-windows.py`, `inspect-x86-runtime.sh`, `ardour-runtime-check.sh`, `desktop-health.sh` |
| `installl/` | Original-publisher installer entry points and installer listings | `installers.sh`, `install-unforked.sh`, `installed-app-gui-test.sh` |
| `launch/` | Opening installed branded/upstream apps; launch diagnostics | `launch-cinterforge.sh`, `launch-unforked.sh`, `launch-cfaudio-linux.sh`, `launch-cfaudio-windows.sh`, `launch-with-log.sh`, `app-launch-diagnostics.sh` |
| `run/` | Application execution plumbing and KDE readiness | `run-linux-amd64.sh`, `run-desktop-ready.sh` |
| `test/` | Guest test routing and FEX/Wine smoke tests | `test.sh`, `test-x86-runtime.sh` |
| `wine/` | Standalone Wine launch through guest FEX | `cinterforge-wine.sh` |
| `cinterforgeis_ready.sh` | Small KDE-ready automation hook | top-level file |

The `.sh` extension denotes shell scripts; `.py` denotes Python. Not every file is intended as a public command: for instance `filesystem_inventory.py` is an importable helper, and `app-launch-diagnostics.sh` is sourced by launchers. Stage copies the tree **recursively**, preserving the grouping.

### Dispatcher, source, and installed layout

- **Source:** `CinterForge/CinterForgeOS/build/creativeSuite/apps/cinterforge-os/external/` (relative to Cinter).
- **Host installation:** `steamapps/common/CinterForge/Applications/Test/Dispatcher/Cinter` and `.../Test/Scripts/{baseline,dispatch,inspect,installl,launch,run,test,wine}/`.
- **Guest view:** `/run/steamapps/common/CinterForge/Applications/Test/Dispatcher/Cinter` and `/run/steamapps/common/CinterForge/Applications/Test/Scripts/`.

`dispatch/cinter-dispatcher.sh` is staged as the **installed dispatcher `Test/Dispatcher/Cinter`**, not as `/usr/bin/Cinter`. At runtime its command resolver looks for exactly one matching `<command>.sh` or `<command>.py` in the grouped script directories. Arguments are forwarded unchanged with `exec`. A missing command, ambiguous duplicate, or non-executable target fails visibly. Because the router discovers names dynamically, adding a command generally requires no guest modification; **avoid two files with the same public basename**.

`cinter-commands.sh` currently handles some higher-level `inspect`, `installers`, `test`, and baseline routing. These are still external files; “legacy” does **not** mean they should be reinstalled inside SteamOS. Inspect the dispatcher and this file before modifying command syntax.

## 4. Stage is the deployment mechanism

The CinterForge Creative Suite `lifecycle.py` implements the existing CFOS Stage behavior:

1. Locates the already-built Xcode Release application (`.../Built/cinterforge-os/mac/Steamac/work/out/XcodeProduct/Release/CinterForge OS.app`).
2. Reads this `external/` source tree. It excludes Python cache directories and the dispatcher seed from the regular scripts payload.
3. Prepares **three separate staged items**: `CinterForge OS.app`, `CinterDispatcher`, and `TestScripts/`.
4. Refreshes the installed macOS app while preserving the SteamOS persistent disk/state.
5. Installs or verifies the external dispatcher and deploys the script tree under persistent `Applications/Test/`.

Stage **does not compile the operating system** and should not run Xcode. For script-only work, the supported host command is:

```sh
./Cinter stage cinterforge-os mac Release
```

On repeat Stage, an installed script is updated when its bytes still match the **previous staged** version. If the installed copy differs (a local edit), Stage reports it as **preserved** instead of overwriting it. The dispatcher has separate protection: Stage preserves an arbitrary user-authored router, but may migrate recognized obsolete routers that call deleted guest paths. Always read Stage's `INSTALLED`, `UPDATED`, `PRESERVED`, or `MIGRATED` lines—**a successful Stage does not prove that a user-edited live script was replaced**. There is no new manifest or separate activation step.

**Renames and moves need particular care:** Stage installs the new grouped path but does not necessarily remove old flat files already present in persistent storage. A stale router or old script can therefore remain in the installation. Diagnose the **installed** dispatcher and file locations when observed behavior differs from the source; do not patch the guest image to compensate.

## 5. Installing and running applications: two distinct lanes

### A. CinterForge-built, branded applications

Use the normal Creative Suite application recipes in the parent `creativeSuite/` tree, with the existing **Cinter Build → Stage → Test lifecycle**. This folder does **not** build GIMP, Ardour, Blender, or Windows applications itself. Build and Stage must publish the actual native executables, libraries, application resources, and versioned installation folders for each application. Setup/readiness must identify missing dependencies and report a blocked build; a configure-only or discovery run is not a successful build.

Application binaries and resources live persistently under:

```text
steamapps/common/CinterForge/Applications/
    Linux/<CinterForge product>/<version>/
    Windows/<CinterForge product>/<version>/
```

A GUI launch in CFOS ultimately uses the external `launch/launch-cinterforge.sh` (public command `Cinter launch-cinterforge <Linux|Windows> <app>`). That script maps upstream names to branded products, selects an installed version, checks for a real executable, and delegates Linux execution to `Cinter run-linux-amd64` or Windows execution to `Cinter cinterforge-wine`. Ardour has specialized external Linux and Windows launcher scripts. **No launcher should manufacture a success from a missing binary, substitute an unforked app, or silently create a second runtime.**

The currently encoded product-name mapping is: GIMP → **CinterForge Image**; Krita → **CinterForge Paint**; Inkscape → **CinterForge Vector**; Scribus → **CinterForge Document**; Blender → **CinterForge 3D**; Kdenlive → **CinterForge Video**; Ardour → **CinterForge Audio**; RapidRAW → **CinterForge Photo**. Source code and installed names must agree; never rebrand an original, unforked upstream distribution merely by renaming its shortcut.

**Linux execution:** CFOS SteamOS is ARM64. `run/run-linux-amd64.sh` runs native executables directly when already on x86-64, or invokes the installed FEX interpreter on ARM64. The presence of a wrapper alone is not proof that the FEX rootfs and runtime are usable. The script reports missing prerequisites instead of pretending an app launched.

**Windows execution:** `wine/cinterforge-wine.sh` selects the separately staged Wine executable from the guest's FEX/Wine runtime and starts it via FEX. The branded launcher uses an application-specific Wine prefix. It is distinct from the **reference Wine prefixes** described below, which are used to inspect original publisher installers. This is not Steam Proton.

### B. Original, unforked publisher applications (reference installs)

Original publisher applications stay separate from CinterForge-built products. `installl/install-unforked.sh` and `launch/launch-unforked.sh` operate on those references. Publisher installers are expected under:

```text
steamapps/common/CinterForge/Applications/UnforkedInstallers/
    Linux/<upstream app>/
    Windows/<upstream app>/
    macOS/<upstream app>/
```

Inside CFOS, inspect available downloads with:

```sh
Cinter installers all
Cinter installers win
```

**Windows reference workflow (currently implemented):** The external Python inspector runs upstream `.exe` or `.msi` installers in an isolated, per-application Wine prefix under the guest user's `~/.local/share/cinterforge/reference-wine/<app>`. The before/install/after and launch snapshots produce reports under the shared `Applications/Logs/UnforkedInspection/Windows/<app>/` tree. Installers require one unambiguous actual EXE/MSI; ZIP distributions are not falsely treated as executable installers.

Example sequence **inside Konsole** (replace the names with an actual downloaded publisher installer and actual installed executable):

```sh
Cinter installers win
Cinter inspect GIMP win before
Cinter inspect GIMP win install gimp-installer.exe
Cinter inspect GIMP win after
Cinter inspect GIMP win launch 'C:\path\to\installed\gimp.exe'
Cinter inspect GIMP win launchafter
```

`gimp-installer.exe` and the launch path above are **illustrative placeholders**, not detected installer names or verified executable paths. Check the installed folder and the preceding report before choosing them. The current legacy inspector exposes the phases `before`, `install`, `after`, `launch`, and `launchafter` and requires a before-snapshot before executing the installer.

**Linux reference workflow:** Original publisher installation footprints for x86-64 Linux belong to the separate **headless Ubuntu AMD64** reference pipeline. Do not assume `Cinter inspect <app> linux ...` in CFOS will install or snapshot Linux publisher packages: the current guest handler explicitly supports Windows inspection and directs Linux reference work to Ubuntu. For real GUI validation, launch an already-built/staged Linux application in CFOS through Cinter.

## 6. Baselines and environment inspection

A **baseline** inventories an environment before modifying it. It is not a build, an installer, or proof that a GUI app works.

Inside CFOS Konsole:

```sh
Cinter baseline all
Cinter baseline linux
Cinter baseline win
Cinter inspect-x86-runtime
Cinter desktop-health
Cinter test runtime
```

The external baseline code captures hashed filesystem metadata and writes JSON into:

```text
Applications/Logs/EnvironmentBaselines/Linux/Baseline.json
Applications/Logs/EnvironmentBaselines/Windows/Baseline.json
```

`baseline/baseline-environments.py` inventories the SteamOS guest and the staged FEX Linux x86-64 RootFS for Linux, and the Wine payload plus any initialized reference Wine-prefix tree for Windows. `baseline/filesystem_inventory.py` supplies the inventory and comparison operations. The guest baseline deliberately excludes virtual/mount-heavy roots such as `/proc`, `/sys`, `/dev`, `/run`, `/tmp`, `/mnt`, and `/media`; it **must not** be interpreted as full coverage of every separately mounted, installer-writable guest path. An uninitialized Wine reference prefix is recorded as such rather than inventing files. `complete: true` signifies no reported inventory errors in the **scanned scope**, not universal filesystem coverage.

Use `Cinter test runtime` to smoke-test the staged FEX/Wine prerequisites and obtain a runtime log. The optional GUI-related switches are implemented by the runtime tester; check its actual output before claiming a GUI test passed. `Cinter test linux <absolute-executable> [args...]` routes to the external Linux/FEX execution helper. These are distinct from **per-application installed GUI tests**, whose scripts and results should be retained inside each installed application's own version directory rather than a centralized test share.

## 7. Where to look when something fails

| Symptom | Check first | What it usually means |
|---|---|---|
| `Cinter: command not found` in Konsole | `/usr/bin/Cinter` and the booted guest image | The OS-side bridge or PATH is wrong; external Stage alone cannot install a missing OS bridge. |
| Error mentions `/usr/lib/cinterforge/baseline-environments` | Installed `Applications/Test/Dispatcher/Cinter` | A retired router is still being executed; running a new source file elsewhere won't change the live router. |
| `Expected one implementation` | `Test/Scripts/<group>/` installed files | Duplicate command basenames or retired flat copies are making routing ambiguous. |
| `No module named filesystem_inventory` | Installed `baseline/` group | The Python helper is missing from the installed directory or a retired internal collector was invoked. |
| `PRESERVED user dispatcher` after Stage | Installed dispatcher contents and Stage output | Stage protected a modified file. Do not assume source and live routing match. |
| Linux app fails to launch | `Cinter inspect-x86-runtime`, installed version and executable | Missing FEX/rootfs/runtime or missing installed executable. |
| Windows app fails to launch | `Cinter test runtime`, Wine payload, app-specific prefix and logs | Distinguish FEX/Wine readiness from the application's own installer/runtime failures. |
| No before/after report | `Applications/Logs/UnforkedInspection/Windows/<app>` | Wrong app/phase, incomplete install, or unwritable shared output. |
| Xcode/CFOS image build complains about a deleted desktop resource | CFOS Xcode **Copy Bundle Resources** and project references | An OS image source/build issue, not something to fix by embedding more operational scripts. |

Logs and full filesystem inventories can be large and may contain usernames, environment variables, arguments, and installation paths. Preserve them as engineering artifacts and review before sharing externally.

## 8. Editing rules for engineers

**Use the existing architecture, rather than adding another one.** Every user-facing operation begins with `./Cinter` from the Cinter repository root on macOS or `Cinter` from the default Konsole prompt. No manual `cd` to a script directory, custom one-off deployment command, or direct guest `/usr/lib/cinterforge` invocation should be required.

For changes to this folder:

- Keep authoritative operational sources here and stage them **outside the OS**; the guest retains only OS-essential startup, mount/session infrastructure, and the thin Cinter bridge.
- Keep shell scripts as `.sh` and Python scripts as `.py`. Update all callers and internal relative paths when relocating files.
- Preserve stable public Cinter command names even if implementation paths change; avoid duplicate basenames because the dispatcher resolves by name.
- Make failures explicit and diagnostics durable. Do not bypass a failed build, suppress missing dependencies, or claim a configure step is a completed application.
- Preserve versioned installed applications and their per-app tests. Never make installed runtime depend on developer `Builds` directories.
- Inspect Stage's preservation messages; a staged script can differ from a user-edited installed copy by design.
- Use source-controlled files and the existing lifecycle, not hidden cache directories, parallel dispatchers, extra manifests, or ad hoc activation mechanisms.

## 9. Related material

**CinterForgeOS repository:** `docs/technical/CinterForgeOS guest.md` for the OS filesystem/boot/KDE sequence; `CinterForgeOS host.md` for the Swift/libkrun launcher and VirtioFS; `CinterForgeOS XCode Project.md` for Xcode build phases; and the overall `CinterForgeOS Architecture.md` for the complete host–guest picture.

**CinterForge repository:** sibling `creativeSuite/apps/cinterforge-os/{mac,linux,win}/` integrations and the existing `creativeSuite/lifecycle.py`; individual Creative Suite application recipes for how a particular product is built and staged. These are the proper places to understand **how an application is produced**. This `external/` directory describes **how installed artifacts are operated and inspected in a running CFOS**.

**Source-review note:** This document describes the checked-in implementation and intended use. It does not assert that every listed application builds, that every upstream installer is present, or that all GUI tests have passed. Treat Build, Stage, installed-command behavior, and actual GUI execution as separate verification states.
