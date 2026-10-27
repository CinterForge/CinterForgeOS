# CinterForgeOS Windows and Linux Development

**26 October 2026**  
**Primary emphasis: terminals, application launching, the single CinterForge bridge, and persistent external scripts**

> **Purpose.** This document belongs with the **CinterForgeOS repository's technical/developer documentation**. It defines what the completed implementation must do, how contributors extend it, which subsystem owns each action, and how to prove that real application launching works. It is **not** a Beta progress report, not a replacement for the overall CinterForge Build System specification, and not a claim that all capabilities have passed live tests. Implementation examples describe **contracts and representative interfaces** unless marked as directly observed in the supplied source.

## 0. Source basis, authority and reading rules

This specification consolidates (a) the user-supplied **CinterForgeOS Engineering Guide** and **CinterForgeOS System Architecture**, (b) the expanded CinterForgeOS architecture/developer guide, (c) the Build System and Creative Suite references, (d) the user-supplied `CinterForgeOS.zip`, and (e) the available *CinterForgeOS desktop and menu launcher audit* dated 25 October 2026. The archive was inspected specifically for `guest/layer/usr/bin/CinterForge`, the guest technical documentation and the 48 KDE `.desktop` entries. This is a **target design**, deliberately separating end-state behavior from source observations. The supplied `CinterForge.zip` and `Cinter.zip` do not establish the entire live persistent Steam installation, and no native guest was booted for this document.

**Authority order for implementation:** the agreed product/architectural decisions in this specification; the active repository source and its actual build-target membership; the applicable upstream toolchain/runtime specifications; then older implementation notes. When an old document or inactive source path disagrees, **do not silently blend both behaviors**. Resolve the active implementation and record the choice in the repository's documentation. Commands and example paths in this guide are **normative only where explicitly called contracts**; example output is not proof of installed behavior.

The CinterForgeOS repository should maintain these documents as separately reviewable, linked references: architecture and boot; host/guest integration; terminal and CinterForge command contract; application installation and launch; graphical/semantic tests; development environments; security and recovery; release/upgrade. The **CinterForge Build System** repository owns the detailed Setup/Build/Stage recipe implementation. CinterForgeOS documents the interfaces at which that system delivers installed applications, guest integration and external tools.

## 1. Product definition and permanent design invariants

CinterForgeOS is a **native KDE Plasma desktop** backed by an ARM64 SteamOS-derived guest on Apple Silicon, extended with persistent creative applications, Steam, Linux x86-64 execution through FEX, Windows x64 execution through explicitly selected Wine/Proton paths, and host-based development tooling. This is an environment for creating, developing, playing, managing files and testing software; it is **not** an HTML faux desktop. Authored HTML/CSS can appear in the macOS launcher/welcome surface or serve as artwork reference, but the interactive Linux desktop, taskbar, panels, file manager, Konsole and app menus are KDE.

Architectural invariants:

1. **Exactly one guest-side CinterForge bridge**: `/usr/bin/CinterForge`. The bridge forwards arguments to **persistent external** dispatcher/scripts. Do not introduce duplicate in-image dispatchers, collectors, application testers or installer algorithms.
2. **Persistent shared applications and developer logic** live under the **actual Steam installation's** `steamapps/common/CinterForge/Applications/…` tree, exposed inside the guest at `/run/steamapps`. Never infer that a macOS path is directly usable by the guest.
3. The guest OS image owns **boot essentials**: kernel, initramfs, SteamOS userspace, systemd, KDE, graphics/FEX runtime, mount configuration and essential startup services. It does **not** own per-application build recipes, inspector scripts, tests, large installed apps, AI model assets, creative projects or Steam downloads.
4. `./CinterForge build …` then `./CinterForge stage …` is the normal lifecycle for preparing, updating and exposing external tools. **No ad hoc activation/deploy/support script** may be invented to bypass it. Staging must respect ownership and preserve user-modified persistent tools where specified.
5. Every installed application is **versioned, complete and independently runnable**. A successful build or a working shortcut to source binaries is not a valid installed application.
6. Built **CinterForge-branded** applications and **unforked/original-publisher** applications remain separate installations and separate launch identities. Original programs are not rebranded merely by changing an icon or a `.desktop` Name field.
7. All commands stream meaningful progress and errors to the invoking terminal and preserve product-owned logs. Missing dependencies, missing mount, missing binaries, missing dispatcher and unsupported installers **fail loudly**; never masquerade as success.
8. The guest image is disposable/replaceable; user cards, apps, versions, projects, external scripts, Steam content and mutable compatibility prefixes survive guest updates.
9. Tests verify **real installed behavior** rather than existence of launcher files, syntax checks or merely a live macOS VM process.
10. Platform selection and implementation must be deterministic and data-driven. Never special-case an app inside the generic OS bridge to disguise an app-specific packaging problem.

## 2. Environment and responsibility map

```text
                       APPLE SILICON DEVELOPER MAC
   ┌─────────────────────────────────────────────────────────────┐
   │ ~/Source/CinterForge/       private ./CinterForge orchestration          │
   │ ~/Source/CinterForge / app upstream sources                 │
   │ ~/Source/Builds/     disposable build products & logs      │
   │ Mac-installed apps / Xcode + LLDB                           │
   └──────────────────┬──────────────────────────────────────────┘
                      │ Build → Stage, persistent installation
                      ▼
   Discovered Steam library: steamapps/common/CinterForge/
      Applications/macOS/<Product>/<Version>/
      Applications/Linux/<Product>/<Version>/
      Applications/Windows/<Product>/<Version>/
      Applications/Test/Dispatcher/CinterForge   ← external command entry
      Applications/.../<app>/<version>/Tests, diagnostics, scripts
      User / Steam / DLC / Models / projects (respective owners)
                      │ host exports VirtioFS tag `steamapps`
                      ▼
    CinterForgeOS.app → host cfos-vm/Swift/libkrun/Venus-Metal
                      │ boots ARM64 Linux kernel + initramfs
                      ▼
   ┌─────────────────────────────────────────────────────────────┐
   │ ARM64 STEAMOS GUEST                                          │
   │ systemd + KDE Plasma + SDDM + Steam + Konsole               │
   │ mount: /run/steamapps  (VirtioFS tag `steamapps`)            │
   │ /usr/bin/CinterForge  (the ONE guest command bridge)               │
   │    └─ /run/steamapps/common/CinterForge/                    │
   │         Applications/Test/Dispatcher/CinterForge                  │
   │             └─ external route/install/test/run scripts      │
   │ Built Linux AMD64 app  → FEX on ARM64                       │
   │ Built Windows x64 app → selected FEX+Wine/Proton route      │
   │ Original Linux/Windows apps → separate installations        │
   └─────────────────────────────────────────────────────────────┘

   Separate: LinuxKit / Ubuntu AMD64 HEADLESS developer build/test
   Separate: Cloudflare AMD64 deployed container, not host-mounted
```

**Important distinction:** Ubuntu AMD64 is a **headless build/test userspace**, not a second graphical Linux desktop VM. The graphical Linux/Windows smoke target is the SteamOS/KDE guest. Do not build a new Ubuntu GUI environment or add an unrelated execution guest. Cross-building Windows x64 uses the existing Linux-based compiler infrastructure from the Mac, not native Visual Studio as the default pipeline.

### Subsystem ownership matrix

| Owner | Owns | Must not own |
|---|---|---|
| Private CinterForge orchestration | Developer CLI routing, external build/stage/test calls, private credentials | Public guest OS behavior, application source algorithms |
| Public CinterForge build system | Generic app lifecycle, app-specific recipes and stages, dependency closure | Mutable runtime guest launcher heuristics, private CinterForge-only assumptions |
| CinterForgeOS macOS host | VM lifecycle, guest disks/devices, GPU/display/input/audio/network bridging, mounting host share | Per-app launch logic or writing developer scripts into guest at boot |
| ARM64 guest image | Kernel/init, SDDM/KDE, guest services, essential graphics/FEX and `/usr/bin/CinterForge` | Baseline collectors, inspectors, per-app install/test implementations |
| Persistent external CinterForge dispatcher | Guest CLI dispatch, launch/install/test orchestration, app-aware external scripts | Rebuilding the guest image for every script change |
| App-specific installed tree | Self-contained binaries/data, app-owned tests, metadata needed to launch | Dependence on source/sysroot/Builds or another app's private libraries |
| KDE | Desktop UX, menus, launchers, Konsole, mode indicators | Embedding shell scripts with application-specific repair logic |
| Steam | Steam-managed depots, client state, game downloads, distinct compatibility metadata | Ownership of CinterForge-managed Creative Suite installations |

## 3. CinterForgeOS repository structure and what contributors edit

The supplied `CinterForgeOS.zip` exposes a host implementation, `guest/` operating-system inputs, `scripts/` image assembly, documentation and Xcode/project resources. Its technical docs describe two host-side Swift code layouts: a Swift-package-oriented `host/launcher/Sources/steamac-vm/` tree and an Xcode-oriented `source/Swift Source/{Core,Interface,Graphics,Input,Networking,Storage,…}` tree. **Check the active Xcode target membership before editing**; similarly named files in those trees are not necessarily active or interchangeable.

- `guest/kernel/`: ARM64 kernel configs, patches and build inputs.
- `guest/initramfs/init`: early boot, partition selection, rootfs/overlay assembly, systemd handoff.
- `guest/layer/usr/`: immutable operating-system overlay; one CinterForge bridge, KDE entries/icons/wallpapers and essential system services.
- `guest/fex/`: x86/x64 execution support and compatible userspace construction.
- `guest/mesa/`: guest graphics components.
- `guest/progress-agent/`: Rust guest agent for host/guest integration, progress and control.
- `guest_config/` and image assembly: KDE/SDDM/session-specific configuration and validation helpers **only as boot/session necessities**.
- `docs/technical/`: architecture, host/guest/graphics docs and the developer contracts developed here.
- **External to CinterForgeOS repo:** authoritative dispatcher, application launch scripts, baseline inspection and per-app tests, installed under persistent Steam `Applications/…` by the CinterForge/Creative Suite lifecycle.

Avoid creating a second `CinterForge/scripts/support/cfos-live-tools` or CinterForgeOS-specific hidden support tree. Do not add a separate activation script. Do not change Xcode project metadata to work around a staging/configuration problem unless the compilation membership really requires it.

## 4. Exact terminal model — one user experience, several execution contexts

The word **Terminal** names a user interface, not a magic universal OS. The completed system must make the current execution context discoverable and the commands predictable.

### 4.1 Four terminal surfaces

| Surface | Where the shell runs | Correct entry / scope | Result |
|---|---|---|---|
| Mac Terminal / IDE terminal on macOS | macOS ARM64 host | From CinterForge root: `./CinterForge <command> …` | Developer builds/stages/tests through host orchestration; native macOS tools |
| KDE Konsole in CinterForgeOS | ARM64 SteamOS guest | `CinterForge <command> …` (no `./`, independent of CWD) | `/usr/bin/CinterForge` forwards to persistent guest-accessible dispatcher |
| CinterForge Code integrated terminal in guest | ARM64 guest unless explicit remote target selected | Same `CinterForge` bridge as Konsole | IDE does **not** bypass guest transport or create a second dispatcher |
| Headless Linux AMD64 test shell | Existing LinuxKit/Ubuntu AMD64 environment | Explicit scoped test/build entry provided by host build tooling | Not a native Plasma GUI; architecture and paths differ from guest |

Terminals must clearly display (or make trivially inspectable) **host/guest**, **CPU architecture**, **current mount/workspace**, **platform selection** and selected application version. A terminal *inside* macOS CinterForge Code remains a Mac terminal, whereas a terminal *inside* guest CinterForge Code remains a guest terminal. Do not change the meaning of `CinterForge` based on an IDE window title.

### 4.2 The essential user promise

- From CinterForge repo root on the Mac: `./CinterForge …`.
- From a normal CFOS Konsole prompt: `CinterForge …`.
- Both reach the **appropriate externally owned implementation** via a stable string-based interface. The guest entry is deliberately limited and must not gain a large switch statement or app-specific code.
- The architecture does not require the guest to contain a copy of `~/Source/CinterForge`, access macOS private secrets or share a POSIX path namespace with the host.
- General user shell commands (`ls`, `git`, `python`, etc.) execute in the terminal's current environment. `CinterForge …` is the **controlled cross-subsystem entry point**, not a blanket intercept of arbitrary commands.

### 4.3 In-guest bridge: actual supplied source

The supplied `guest/layer/usr/bin/CinterForge` is a 525-byte shell script. Its central behavior is:

```sh
#!/bin/sh
set -eu
dispatcher=/run/steamapps/common/CinterForge/Applications/Test/Dispatcher/CinterForge
if [ ! -f "$dispatcher" ] || [ ! -r "$dispatcher" ]; then
    echo "[CinterForge] Dispatcher missing/unreadable: $dispatcher" >&2
    echo '[CinterForge] Build and Stage CinterForgeOS to install it; guest launch never creates user data.' >&2
    exit 1
fi
exec /bin/sh "$dispatcher" "$@"
```

**Normative boundary:** there is exactly one bridge and it remains that simple. The absolute **guest-side** dispatcher path is an existing interface. All argument parsing, app selection, translation/runtime selection, external script invocation, tests and diagnostics belong outside the OS, in persistent scripts. Missing/unreadable dispatcher exits nonzero with a useful error; guest boot and guest launch must **not auto-provision** it.

The guest's single VirtioFS contract is `steamapps` → `/run/steamapps`. Prior `Applications`/`DeveloperTests` independent mount tags, or `/run/Applications`, are not the target interface. Use the actual host Steam library discovered by the host/staging subsystem, not a hardcoded developer `~/Library/…` guess.

### 4.4 Stable command dispatch contract

The public implementation must specify a **stable command/argument protocol**, with at minimum the established externally routed families below. The examples reflect the current KDE launch audit, **not a claim that every command currently works at runtime**:

```text
CinterForge launch-cinterforge <Linux|Windows> <UpstreamAppKey>
CinterForge launch-unforked    <Linux|Windows> <UpstreamAppKey>
CinterForge install-unforked   <Linux|Windows> <UpstreamAppKey>
CinterForge run-linux-amd64    <qualified installed executable> [arguments...]
CinterForge cinterforge-wine   <qualified installed Windows executable> [arguments...]
```

Other user/developer inspection and test verbs may exist or be added externally, but **must** be documented in the external dispatcher help and tests before use. `CinterForge --help` and unknown-verb diagnostics should be explicit and consistent; an invalid verb must not silently fall through to a different subsystem. Exit status from the chosen command must propagate back to Konsole/KDE. Argument passing must preserve whitespace, Unicode, quotes and filenames with spaces. The bridge must not use `eval` or concatenate untrusted command strings.

### 4.5 Terminal security and capability boundaries

The shared dispatcher path is a powerful executable command source. Stage owns initial placement; runtime should validate access and reject missing/unreadable files. Keep dispatcher/scripts under a controlled ownership and permission policy; ordinary apps should not silently replace the shared entry. Do not run guest-terminal commands as root merely to make installation easier. Use explicit privilege escalation for OS-level operations, with prompts and clear error statuses; never silently write into read-only rootfs or private Steam metadata. Avoid shell injection, glob-based app discovery across unrelated version directories, and PATH-based surprises for the primary dispatcher. Log command identity, route, selected version and executable, without leaking credentials or personal projects.

### 4.6 Startup sequence and readiness for a terminal session

1. The host launches CFOS and boots the guest.
2. The initramfs hands off to systemd; the guest mounts or activates `/run/steamapps` using VirtioFS.
3. KDE Plasma and Konsole become available. Konsole starts an actual guest user shell with stable environment and home directory.
4. User runs `CinterForge`. `/usr/bin/CinterForge` checks the external dispatcher and executes it.
5. Dispatcher validates the requested subcommand, install path, executable/runtime and permissions, then streams progress and returns the child status.

`CinterForge` availability must not depend on launching a particular IDE, opening a specific KDE menu, navigating to the CinterForge repository or running a one-off activation script. If the VirtioFS mount is unavailable, the terminal remains usable as a normal Linux shell; the `CinterForge` command fails **specifically and diagnostically** without creating fallback files in the OS image.

### 4.7 Terminal acceptance tests

The installed guest test must verify: Konsole opens from KDE; keyboard/paste/resize work; `command -v CinterForge` resolves to `/usr/bin/CinterForge`; `CinterForge` reaches the actual staged dispatcher; exit codes and stderr propagate; paths with spaces work; stdout streams live; a deliberately missing dispatcher produces the intended failure; and the dispatcher/scripts persist over OS replacement. The integrated IDE terminal must execute the same tests, plus verify workspace handling and debugger terminal behavior. These tests should be automated through **semantic shell/process assertions** and visible GUI readiness, never coordinate-click scripts.

## 5. Desktop and application launch contract — critical

A `.desktop` file is a **route declaration**, not the application itself. The complete path from KDE click to installed process must be documented and tested:

```text
KDE launcher / desktop shortcut / file association
    │ Exec=CinterForge launch-cinterforge Linux blender (representative)
    ▼
/usr/bin/CinterForge
    ▼
/run/steamapps/common/CinterForge/Applications/Test/Dispatcher/CinterForge
    ▼
externally staged launch-cinterforge / launch-unforked / install-unforked
    ▼
resolve explicit product + platform + selected installed version
    ▼
select one executable within THAT version's install tree
    ▼
validate architecture, resources, permissions and execution backend
    ▼
Linux AMD64: FEX; Windows x64: appropriate Wine or Proton route
    ▼
real installed process + window + required files/resources
    ▼
launch outcome and diagnostics reported to caller
```

### 5.1 Desktop entry families and provenance

The supplied repo contains **48 KDE `.desktop` launch entries** for eight upstream applications (Blender, Ardour, GIMP, Inkscape, Krita, Kdenlive, RapidRAW and Scribus), each with:

- **Built CinterForge Linux** app launcher;
- **Built CinterForge Windows** app launcher;
- **Unforked/original Linux** app launcher;
- **Unforked/original Windows** app launcher;
- **Original Linux installer** shortcut;
- **Original Windows installer** shortcut.

Examples from the supplied guest documentation include `Exec=CinterForge launch-cinterforge Linux Krita` and `Exec=konsole --hold -e CinterForge install-unforked Windows Krita`. The **category naming**, not the eight-app count, is the extensible contract: other apps/IDE/terminal entries must follow the same clear ownership separation. A menu icon is neither evidence of an installed app nor authorization to download/install it silently.

### 5.2 Product identities and installation locations

The **CLI app key** (for example `blender`) is not necessarily the **product-facing installation name** (for example `CinterForge 3D`). The prescribed install pattern is:

```text
<discovered Steam install>/Applications/
  macOS/<CinterForge product>/<version>/<complete native .app or bundle>
  Linux/<CinterForge product>/<version>/<complete Linux distribution>
  Windows/<CinterForge product>/<version>/<complete Windows distribution>
  Test/Dispatcher/CinterForge
```

Canonical creative-suite names: Blender → **CinterForge 3D**; GIMP → **CinterForge Image**; Krita → **CinterForge Paint**; Inkscape → **CinterForge Vector**; Ardour → **CinterForge Audio**; Kdenlive → **CinterForge Video**; Scribus → **CinterForge Document**; Code - OSS → **CinterForge Code**; Blockbench → **CinterForge Voxel**; FFmpeg/Assimp/ImageMagick+ → **CinterForge Converter**; RapidRAW → **CinterForge Photo**. Audacity/Sound/Wave is retired. Original apps remain original publisher-branded and separate. Product branding applies to **actual binaries/bundles/install directories/launch targets**, not merely KDE shortcut labels.

A launcher must not choose an arbitrary directory by wildcard, a previous `Built/Output` folder, another app's shared dependency, or an original publisher binary when a branded app is missing. Selection must be scoped to the **requested product and version**; a current-version pointer may be used only when it unambiguously names a completed installed version. Side-by-side older versions stay intact.

### 5.3 Executable discovery: deterministic and fail loud

Real upstream distributions have different layouts: root executable, `bin/`, `usr/bin/`, nested product/version folder, `.app/Contents/MacOS/` on macOS, or an app-specific wrapper. The 25 October audit identified a Blender Linux distribution nested as `blender-5.2.2-linux-x64/blender`; shallow `bin/`-only assumptions failed. The desired resolver must:

1. Read explicit installed product/version and app-owned launch metadata/known upstream layout rules where present.
2. Constrain search **within that version's directory**; never search all `Applications` or the source tree for a plausible basename.
3. Confirm a real executable and appropriate **ELF AMD64 / Windows PE x64 / native `.app`** as relevant, not a README, shell placeholder or unrelated helper.
4. Select a **single unambiguous candidate**. If none or multiple equally valid candidates exist, explain and fail nonzero; do not silently pick the first.
5. Preserve the working directory, expected resources and explicit environment needed by the app.
6. Invoke the selected execution backend; report the process status and capture logs.

The resolver may support layouts declared by app-specific integration, but **the generic guest bridge remains app-agnostic**. No exception such as “always use Blender from external source” belongs in the OS bridge.

### 5.4 Linux AMD64: built application path

On ARM64 SteamOS, Linux x86-64 binaries run through **FEX** with a properly prepared AMD64 userspace. On real AMD64 targets, run natively when platform selection confirms it. The external `CinterForge run-linux-amd64` route is responsible for validating the ELF architecture and FEX runtime, setting correct library/resource search paths, and launching the **fully installed** self-contained application. Native dependencies required by that version must be inside its distribution (with system essentials intentionally delegated only where documented). The guest **must not** fall back to build sysroots or another application's libraries.

### 5.5 Windows x64: distinguish Wine from Proton

The October 25 launcher audit documents two distinct pathways and they must stay distinct in the interface and tests:

- **CinterForge-built non-Ardour Windows apps:** externally dispatched `CinterForge cinterforge-wine`, using the explicitly staged FEX + x86-64 Wine toolchain.
- **CinterForge-built Windows Ardour:** a dedicated **Proton 11.0 (ARM64)** path and compatibility prefix were present in the inspected launcher. That path is an **implementation-specific contract to verify and maintain**, not evidence every installed guest has that Proton available.
- **Unforked/original Windows apps:** separate original-publisher executable and isolated `reference-wine/<App>/drive_c/Program Files*` compatibility storage under the guest user state.

Do not casually substitute Steam Proton for standalone Wine, or run all Windows apps through the Ardour compatibility prefix. The selection must be **explicit, versioned, app-owned and testable**; do not globally rename all Windows paths “Proton” merely because the desktop button is labeled *Proton Mode*. Steam-managed game launches continue to use Steam's own compatibility mechanisms.

### 5.6 Unforked/original Linux launch and install

The supplied launcher audit found original Linux launch paths pointing into the **read-only FEX rootfs** at `/usr/share/cinterforge/fex/RootFS/usr/bin/<program>`. That works only if the original app was **actually installed within that compatible rootfs**. Downloading a DEB/AppImage/tarball to `UnforkedInstallers/Linux` is **not installation** and must never be treated as such. The desired system needs a supported writable location and real package/install handling for each format, preserving original identity and dependencies. An unsupported installer must report what is unsupported and exit nonzero—no fake installed status. Where original apps legitimately live in a separate writable AMD64 environment, document its storage and invocation contract rather than pretending the read-only system root has been mutated.

### 5.7 Original Windows install and run

Original Windows installers and installed files must be tracked separately from branded apps, with app-specific prefixes and architecture/runtime verification. Installation is an **explicit user/developer command**; an icon click for *Run* should not download the installer in the background or create arbitrary prefixes as a fallback. Preserve user content and prefix ownership through upgrades. Runtime validation includes the program's own output/version, actual window and dependency resolution.

### 5.8 KDE shortcut management and repair policy

Guest system-provided menu `.desktop` entries belong to the authored layer. Persistent user desktop/panel shortcuts must be updated through the existing ownership-aware KDE installer rather than overwritten indiscriminately. The launcher audit identified that an outdated owned shortcut could retain a stale `Exec=`; the target migration rules must refresh **only identifiable CinterForge-owned entries**, preserve unrelated user-created shortcuts, and leave pinned user state and personal launchers alone. Migration must be idempotent; running Stage twice must not duplicate dock icons or reset the user's unrelated KDE customizations.

### 5.9 Linux Mode and Proton Mode are UI decisions, not magical runtime translation

The KDE desktop starts in the authored **Proton Mode** presentation, with the original initial dock **Files, Chromium, Steam, Trash**. Linux Mode and Proton Mode are KDE controls that visibly change wallpaper and affect **CinterForge Creative Suite application** launch routing. The mode switch must not change how Files, Chromium, Steam or Trash launches. Only installed routes that can run are offered as working; unavailable installed variants should be handled explicitly. Built and original apps should not be conflated by mode. A direct click on a platform-specific `Linux` or `Windows` menu entry should retain its explicit platform even when the desktop's general preferred mode differs, unless the menu design explicitly declares otherwise.

### 5.10 File-manager and URL launches

Integration should use normal KDE/XDG app entry registration, MIME declarations and expected working directories. File associations must route to the **selected installed app and version**, not `Built/` files. Universal-link/URL handling and CinterForge registry/card links, if exposed in the guest, must have explicit registration, input validation and diagnostics. Opening a file should not secretly switch Wine prefixes or run code from the file's path. Application launch arguments must be escaped and passed as argv, never interpolated into a shell template.

### 5.11 Launch acceptance matrix

Test at least these cases for each supported branded Linux/Windows app and original app: icon exists; click dispatches correct CinterForge verb; right product name/version; nontrivial install layout; executable format; launch runtime; process stays alive; UI draws; file open/save or equivalent meaningful operation; expected resources/plugins found; clean exit; logs retained; missing install yields clear error; repeated Stage preserves shortcut semantics. Include a cross-version test and a path-with-spaces test. A `48/48` static icon/path audit is valuable but **does not replace runtime launch verification**.

## 6. Guest boot, host mounts and failure isolation

### 6.1 Ordered boot chain

```text
CinterForgeOS.app (single native user-facing host app)
  → active Swift `cfos-vm` target
  → libkrun virtualization + virtio devices + guest disks
  → ARM64 Linux Image / initramfs
  → GPT/SteamOS A/B selection / read-only rootfs + EROFS layer
  → systemd + mount units + guest services
  → SDDM / native KDE Plasma session
  → Steam + Files + Chromium + Trash + Konsole
  → registered Creative Suite / original app desktop entries
  → /usr/bin/CinterForge → persistent external dispatcher
```

Host macOS graphics/display and guest KDE readiness are separate: a macOS `.app` process starting does not prove the guest has reached Plasma. Similarly, a KDE wallpaper is not proof the Steam library mount or external dispatcher works. Each boundary needs a status probe and clear error surface.

### 6.2 Host/guest mount contract

- Host exports exactly the agreed **`steamapps`** VirtioFS tag from the discovered Steam library.
- Guest systemd unit mounts it at **`/run/steamapps`**, read/write with an explicit writable policy.
- Guest can read, write, atomically rename and execute representative files under the appropriate shared subtree. Test long filenames, spaces, Unicode, symlinks and permission handling.
- Use host-only source path strings solely in the Mac build system; persist **relative paths or guest-native mapped paths** in app configuration, never opaque Mac developer absolute paths.
- Mount unavailable means application launch/test failure with actionable diagnostics; do **not** copy guest apps into the image as an implicit fallback.
- Do not create competing shares for `Applications`, `DeveloperTests` or helper scripts if the existing single `steamapps` mount is the agreed interface.

### 6.3 Steam content and ownership

Steam client executable remains guest-side. User downloads, shader caches, compatibility data, Creative Suite versions and project storage are persistent, but **Steam metadata ownership is distinct**. Do not allow multiple independent Steam clients to write the same manifests/compatdata concurrently. Steam-managed depots remain Steam-owned; CinterForge-managed apps never overwrite the CinterForge Steam executable/depot. Keep user-generated cards, registry assets, RAG, models and DLC/Packs outside the immutable guest OS image.

### 6.4 OS essential vs developer script test

If a file is necessary before mounting `/run/steamapps` or before showing KDE, it may belong in the guest (for example initramfs, mount units, desktop session selection, graphics service). If a file can be updated to change an installed app's launch, a baseline inspection, a test, an installer or a dependency probe **without changing the OS**, it belongs outside the image and is invoked through CinterForge. This is the litmus test for contributors considering a new `/usr/lib/cinterforge/` helper.

## 7. Persistent external script organization and update lifecycle

The external namespace exists within the **persistent Steam Applications tree**. The specific dispatcher path is part of the existing guest contract:

```text
/run/steamapps/common/CinterForge/Applications/Test/Dispatcher/CinterForge
```

The October 25 audit identifies external script families under the public build-system source:

```text
creativeSuite/apps/cinterforge-os/external/
  launch/        # branded and original app resolution/routing
  run/           # Linux AMD64 / FEX execution
  wine/          # Windows / Wine / Proton integration
  installl/      # original-app installation integration in the inspected tree
  hope/          # dispatcher/bridge implementation assets
```

`installl/` is the spelling present in the audit, not a recommendation to introduce new inconsistent naming. Verify the exact active directory and update via a deliberate migration if standardizing. The **source** is in the public build system, while the **runtime scripts** are staged to persistent Steam storage; do not assume the source path and installed location are the same.

### Normal change workflow

1. Edit the owning source script(s) in the **correct** repository/source tree.
2. Validate shell/Python syntax and unit/mock routing tests locally.
3. Run existing `./CinterForge build cinterforge-os mac …` as required to assemble intended build inputs.
4. Run `./CinterForge stage cinterforge-os mac …` to install/update **external dispatcher/scripts and actual authored KDE content**. Stage must preserve live user-owned edits under the agreed ownership policy; it must not blindly overwrite unrelated persistent files.
5. Boot/test the installed CFOS guest; call the route from Konsole and from the actual KDE icon.
6. Capture concrete launch evidence and report exactly which environment/runtime was exercised.

Do **not** create `activate-cfos-tools.sh`, a second dispatcher, a duplicate tree under CinterForge, or an ad hoc manual `cp` into `/usr/bin` as a standard workflow. Guest boot never silently writes the persistent dispatcher.

### Updates, rollback and recovery

Code and host-image updates must be independent. A change only to external routing scripts should not force rebuilding kernel/Mesa/rootfs. A change to essential guest mounts, `/usr/bin/CinterForge`, KDE menu assets or boot services is a guest layer change, staged by the established lifecycle—not written during boot. An interrupted Stage must not leave a half-updated executable script treated as valid: use a staging-and-rename/validated install strategy appropriate to the filesystem and ownership model. Preserve prior installed application versions; do not delete user content. Recovery instructions must distinguish (a) no VM, (b) no KDE, (c) no VirtioFS, (d) no CinterForge dispatcher, (e) no installed app, and (f) broken FEX/Wine/Proton runtime.

## 8. CinterForge Code IDE and developer experience

CinterForge Code is the **branded built-from-source Code - OSS application**, with a complete native installation—not a renamed shortcut to an arbitrary system `code` executable. The product is introduced after macOS Creative Suite and the CinterForge integrations in the planned sequence, before dedicated terminal integration and broader Windows/Linux app expansion. The IDE belongs in the same versioned application system, with original Code - OSS separately identifiable where distributed.

The IDE must support: opening persistent workspace/project folders; file editing with source-control integration; installing supported, policy-approved extensions into persistent user storage; selecting macOS vs guest/AMD64 environment explicitly; launching the **installed** app under test; making build/test output visible without silently rebuilding; and attaching platform-appropriate debuggers where viable. Workspace locations and debug configurations must not assume the user's CinterForge source tree is embedded in CFOS. An **integrated terminal** is a presentation of the selected environment's real shell; guest-terminal CinterForge calls travel through `/usr/bin/CinterForge`, with the same errors and logging as Konsole. Avoid introducing hidden privileged IDE-side build dispatchers.

For app developers, documentation must distinguish three separate activities: (1) edit source, (2) invoke the correct host Build/Stage lifecycle, (3) launch/test the installed staged app from the intended environment. A successful IDE syntax check or open source project is not evidence of installed app correctness. Source debugging requires a mapping from **installed binary and symbols** back to **the exact source revision** that produced it, including executable architecture, working directory and paired version identity.

## 9. App-owned tests and desktop-readiness execution

### Test placement

Every installed application owns its own **Test scripts, fixtures and results inside its installed version folder**, e.g.:

```text
Applications/Linux/CinterForge Audio/2026/
  <complete app distribution>
  Tests/
    <app-specific semantic test scripts>
    Results/
      <logs, assertions, screenshots/artifacts as applicable>
```

Do not create a new central `DeveloperTests` guest share or move all tests into a global directory. A common external dispatcher may *invoke* app-owned tests, but the test algorithms and outputs stay with the installed app. Tests should survive disposable build folder deletion and should never execute from an upstream source tree as a substitute for installed execution.

### Desktop readiness

Graphical smoke tests are triggered only **after the actual native KDE session is ready**: the session is running, the intended user context exists, `/run/steamapps` is available, the external dispatcher resolves, the relevant installed versions are present, and the graphics path is usable. Do not rely on a fixed sleep, boot splash completion or merely a host process PID. Headless AMD64 tests run in the existing isolated build/test environment; they do not claim KDE visual success. A desktop-ready hook should dispatch **app-owned** tests by installed version; it must not copy their scripts into OS read-only storage.

### Test mechanisms

Prefer deterministic CLI/scripting/semantic UI APIs, accessibility interfaces, file-output verification, actual process/window readiness and application-supported automation. **No coordinate-based mouse click tests** as release gates. For Linux guest apps verify FEX plus user-visible window and outputs; for Windows guest apps verify the intended Wine/Proton path and window; for macOS app tests verify the installed `.app`; for headless tests validate concrete outputs; for web applications launch the existing CinterForge server and test the served app through it. Browser/web apps remain **web apps**; do not repackage as invented Bun-native desktop apps.

### Recommended result contract (design proposal)

Test results should be machine-readable and human-readable, with fields for app/product, installed version, platform, chosen runtime, test ID, timestamp, exact command, exit code, assertions, generated artifacts, logs and failure classification. This is an **interface requirement to standardize**, not a claim that an existing JSON schema with those exact field names has already been implemented. Progress streams to Konsole/Terminal and app-owned logs should correlate using one test/run identifier when introduced.

## 10. Graphics, input, audio, networking and debugger responsibilities

### Graphics path

Host native renderer: Apple GPU/Metal via the selected Vulkan translation/virtual GPU stack. Guest: KDE/Wayland-or-X11-as-configured, Mesa/Venus and FEX-provided x86 userspace graphics where required. The exact working renderer must be explicit in logs. A successful GL/Vulkan probe does not alone demonstrate a complex GUI, and a window does not prove correct colors, frame presentation, resize or input. Maintain dedicated diagnostics for OpenGL/Vulkan capabilities, host-to-guest frame transport, color correctness and 3D application smoke tests. Do not disable application graphics features merely because a toolchain component is missing; provision compatible dependencies or report a real unsupported platform capability.

### Input, clipboard and sound

The host launcher and guest progress/integration agents mediate input devices, keyboard layout, clipboard, window focus, audio and controller traffic. Tests must include text entry into Konsole and IDE terminal, copy/paste Unicode, resize, drag/drop if supported, audio playback/capture if applicable and clean session shutdown. Treat permission prompts and application entitlements as first-class failures rather than timing flakes.

### Networking

Distinguish guest network access from host loopback. `localhost` means **the current environment**; it is not a transparent tunnel between Mac, guest, headless LinuxKit and cloud. The CinterForge local server/Bonjour discovery, guest browser access, ports and firewall permissions require explicit address/transport mappings. Do not persist a developer's private IP/hostname as the canonical app configuration. Cloudflare execution cannot mount the Mac Steam library; its distribution and data access must be self-contained/remote and declared.

### Debugging

- **Mac apps/host launcher**: Xcode/LLDB with the exact built/staged bundle and source revision; verify code signing/entitlement constraints.
- **Guest ARM64/KDE**: journalctl/systemd logs, mount diagnostics, process tree, Qt/KDE logs, display and FEX runtime logs, app-owned test evidence.
- **Linux AMD64 apps**: architecture-correct ELF symbols and gdb/lldb where supported; separately headless and guest-FEX GUI execution.
- **Windows x64 apps**: matching PE/PDB or supported native debug symbols, Wine/Proton logs, exact prefix/runtime and installed DLL closure. Avoid implying a debugger can attach natively across ARM64/FEX/Proton without explicit transport support.

## 11. Security, data durability and version rules

- **Trust zones:** private macOS CinterForge credentials and host files; public CinterForge source; signed/distributed host app; replaceable guest image; persistent shared external scripts and Creative Suite apps; user data and independent Steam-managed content. Access across each boundary must be explicit.
- **Guest privileges:** normal UI/terminal work as unprivileged user. Installer operations requiring privilege must be explicit, audited and narrow; no automatic `sudo` from clicking a creative-app launcher.
- **Executable provenance:** stage only from the intended Build output; original publisher apps need a documented acquisition/verification path. Enforce correct CPU/ABI and dependency closure, no accidental execution of developer source scripts or random files in user-controlled folders.
- **Versions:** product-facing version progression `YYYY` → `YYYY.MM` → `YYYY.MM.DD` → `.1`, `.2` as needed; ordinary Debug replaces selected development version; `New` explicitly advances; Stage **never** invents/bump versions; Release publishes finalized version identity only. Internal build timestamp is distinct from installed product version.
- **Safe remove:** only delete a selected CinterForge-owned app version and its specifically owned launchers; never delete user projects, Steam downloads, other installed versions, models/Packs, shared dispatcher or arbitrary prefixes without matching ownership.
- **Independent applications:** each installed app carries its runtime DLLs/frameworks/plugins/assets even when that duplicates libraries across apps. Do not deduplicate before shipping, depend on old rolling package sets or borrow another app's lib directory.
- **Signing and release:** macOS signing belongs **after final Stage assembly** and before publication; no post-signing bundle mutation. Cloudflare and Steam receive the intended finalized staged artifacts. Release uploads and independent smoke checks have separate gates.

## 12. Operations: inspect the failure at the correct layer

This table defines the **diagnostic classification**, not a literal assertion that the specified commands/tools are available in a particular live guest. Use the repo's exact external inspection commands once documented and staged.

| Symptom | First responsibility to inspect | Expected evidence |
|---|---|---|
| macOS app fails to open | Host bundle / signature / entitlements | host crash/error log, exact bundle path |
| macOS app opens, black/failed guest | libkrun, guest kernel/initramfs, GPU | host VM log, boot console, guest stage reached |
| Guest reaches boot but not desktop | systemd, SDDM, KDE | unit status, session journal, XDG environment |
| Wallpaper shown but `CinterForge` fails | VirtioFS mount, bridge, external dispatcher | `/run/steamapps`, bridge stderr, dispatcher readability |
| Konsole works, app icon does nothing | `.desktop` Exec and external route | desktop entry, command exit, launch logs |
| Linux app says not found | installed product/version/executable resolver | explicit searched version, missing/ambiguous candidate |
| Linux app fails on AMD64 libraries | FEX rootfs / installed runtime closure | ELF architecture, loader and library report |
| Windows app won't start | selected Wine/Proton, prefix and DLL closure | PE architecture, executable/prefix path and Wine logs |
| Original Linux app says installed but cannot run | original app's actual writable installed location | package receipt, executable existence, rootfs policy |
| Stage updates script but KDE uses old behavior | staged external script / persistent shortcut migration | script checksum/version, shortcut Exec, ownership |
| Built app works, installed fails | missing runtime closure or wrong launch root | installed-only test, dependency and resource paths |
| Test says passed with no window/output | broken readiness/assertion contract | actual GUI/process/file-output evidence |
| Guest OS update loses app | persistent mount and storage ownership | before/after version and content comparison |

Operational recovery must preserve evidence. Do not wipe guest images, rootfs, Steam compatdata or installed versions simply to mask a missing file. Provide a **bounded diagnostic** and an app-owned/log-owned next action.

## 13. Contributor workflow — changes to launch/terminal behavior

**For an external launch or test behavior change:** modify its owning public Creative Suite external source; update the external command help/test; validate offline with explicit fake installed version layouts; use existing Build → Stage; verify the command from **Konsole and the KDE icon** on a running guest; store app-owned test output. No OS rebuild should be required solely because a launch algorithm changes.

**For a KDE desktop entry/icon/mode change:** edit authored KDE/layer source; ensure icon exists, Exec calls `CinterForge` with the correct arguments, permissions and desktop-file syntax are valid; Build/Stage with existing lifecycle; prove the guest sees the correct menu entry, owns/migrates only intended persistent shortcuts and switches modes as specified.

**For bridge or mount change:** change only the minimal OS-essential `/usr/bin/CinterForge` or host/guest mount/service implementation; maintain stable dispatcher protocol, bootstrap error behavior and user data durability; build/stage the correct guest image/layer; run guest mount tests including failure cases.

**For a new installed application:** integrate branding/install/dependency closure under app-specific build-system files; provide Linux/Windows/macOS executable layouts; install complete versioned distribution; supply app-owned semantic tests; expose only valid KDE launcher declarations; maintain original and branded identities separately. Do not place the install algorithm in `/usr/bin/CinterForge`.

### Review checklist

- [ ] Edited the **active** source/target, not an older duplicate.
- [ ] No duplicate bridge, no special activation script, no old `/run/Applications` mount assumption.
- [ ] Correct installed product name, explicit platform and selected version.
- [ ] Installed-only execution and complete dependency closure, no source/Builds fallback.
- [ ] Original app and CinterForge-branded app remain distinct.
- [ ] Linux AMD64 → FEX and Windows → explicitly correct Wine/Proton route; no accidental backend substitution.
- [ ] Konsole command works independent of CWD and IDE installation.
- [ ] Desktop icon/mode route works from real KDE and propagates diagnostics.
- [ ] App-owned tests/results stay under installed version; no central guest tests folder.
- [ ] Owner-safe Stage/Remove; persistent user/Steam state preserved.
- [ ] Logs stream and persist; failure status is nonzero and actionable.
- [ ] Native runtime tests actually executed; otherwise state precisely what was only inspected.

## 14. Expected repository documentation structure

This is an **organizational recommendation** for CinterForgeOS docs, not a demand to move code or introduce new runtime folders:

```text
docs/technical/
  CinterForgeOS Architecture.md        # host → boot → KDE → installed apps
  CinterForgeOS host.md                # Swift/libkrun/graphics/input ownership
  CinterForgeOS guest.md               # kernel/init/overlay/systemd/mounts
  CinterForgeOS CinterForge Bridge.md         # ONE bridge + external dispatcher protocol
  CinterForgeOS Terminals.md           # Konsole, IDE terminal, environment boundaries
  CinterForgeOS Application Launch.md  # built/original, Linux/Windows, Wine/Proton
  CinterForgeOS Storage.md             # mounts, versions, prefixes and ownership
  CinterForgeOS Developer Testing.md   # readiness and app-owned installed tests
  CinterForgeOS Diagnostics.md         # logs, failure classes and recovery
  CinterForgeOS Security.md            # trust and privilege boundaries
```

Do not duplicate the full application build-system recipe implementation into these files; link to the **CinterForge Build System** and **Creative Suite** architecture docs for its detailed contract. The OS repo is authoritative for the **host/guest execution, terminal, launch, mounts and desktop** boundaries.

## 15. Complete end-state acceptance scenario

A new contributor should be able to perform and explain the following, without manually placing helper scripts in the guest:

1. Start the installed **CinterForgeOS.app** and reach branded **native Plasma**, not an HTML substitute.
2. Confirm Files, Chromium, Steam, Trash and a functioning **Konsole**. Show the correct initial mode and switch Linux/Proton wallpaper without changing Steam's behavior.
3. Inspect `/run/steamapps`; demonstrate host ↔ guest read/write against a safe temporary file and verified execution semantics.
4. From Konsole, run `CinterForge` with a documented command; observe the single bridge and persistent dispatcher, with visible output and correct exit status.
5. Launch a **CinterForge-branded Linux AMD64** installed app from an icon, verify FEX, usable UI, app-local resources and functional output.
6. Launch a **CinterForge-branded Windows x64** installed app from an icon, verify the selected Wine/Proton route and usable UI; use a separately verified Ardour Proton path when that app requires it.
7. Launch an independently installed **unforked/original** application without accidentally redirecting to its branded counterpart or treating a downloaded archive as installed.
8. Open **CinterForge Code**, open a persistent workspace, run `CinterForge` from its integrated guest terminal and confirm the same external dispatcher/exit semantics.
9. Run an installed app's **own smoke test** after KDE readiness; inspect results in its installed version folder and confirm failure when the app or its backend is absent.
10. Upgrade or restage the guest layer and external scripts using the established Build → Stage workflow. Confirm application versions, original app states, user content, Steam library and KDE user preferences persist.
11. Demonstrate a controlled failure for a missing dispatcher, missing executable and wrong runtime, each with actionable error text—not a silent fallback.
12. Show a reproducible source-to-installed-binary debugging path and a meaningful log handoff for host, guest, terminal and application failures.

The **primary non-negotiable acceptance criterion** is: **one `/usr/bin/CinterForge` in the guest, one coherent external dispatcher in persistent Steam storage, and a genuinely functional terminal-to-installed-app launch path through KDE and the appropriate runtime**. This is the connection that allows the OS image to remain small and replaceable while CinterForge applications, tests and developer tools evolve independently.

---

## Appendix A — Confirmed source-level observations, distinct from requirements

The following facts are grounded in the supplied archive and 25 October static launcher audit; they are included to help developers locate implementation points, **not** as a live-runtime readiness report:

- `guest/layer/usr/bin/CinterForge` delegates to `/run/steamapps/common/CinterForge/Applications/Test/Dispatcher/CinterForge`, with explicit nonzero failure when missing/unreadable.
- `guest/layer/usr/share/applications/` includes 48 desktop entries: eight apps × six built/original/install and platform variants.
- The guest technical documents describe `run-steamapps.mount` with the VirtioFS tag `steamapps` and mountpoint `/run/steamapps`.
- The host technical document distinguishes active Xcode-oriented Swift layout from older Swift Package host sources and warns against mixing their mount interfaces.
- The static launcher audit says branded non-Ardour Linux apps use FEX, branded non-Ardour Windows apps use FEX/Wine, branded Windows Ardour has a dedicated Proton 11 ARM64 route, and originals have separate Linux/Windows execution paths.
- The static audit also found nested Blender executable layout and ownership-aware KDE shortcut migration as important launcher cases.
- None of those static checks prove a complete native guest application launch, integrated IDE terminal or debugger runtime.

## Appendix B — Terminology and anti-patterns

**Host**: Apple Silicon macOS VM launcher and source-build orchestrator. **Guest**: ARM64 SteamOS/KDE session. **Linux AMD64 headless**: existing build/test userspace, not a KDE guest. **Built app**: CinterForge-owned branded distribution. **Unforked**: original publisher's application, separately installed. **Wine**: Windows compatibility runtime; **Proton**: selected Steam-related compatibility distribution, not interchangeable with standalone Wine. **FEX**: x86/x64 userspace execution on ARM64 Linux. **Stage**: consume completed Build and install assembled product/tools; not recompile source. **Terminal**: interactive shell in a named environment. **CinterForge bridge**: `/usr/bin/CinterForge` only, not the implementation of every command. **External dispatcher**: stable persistent Steam-root entry for all guest CinterForge subcommands. **Test**: real installed application behavior in the correct runtime. **Proton Mode**: KDE preferred application-routing mode, not a request to change the Steam client's own launch mechanism.

**Forbidden shortcuts:** duplicate in-guest CinterForge script trees; source-code fallback in the installed launcher; treating a KDE icon as install success; treating Python syntax or host process existence as GUI verification; blindly updating user `.desktop` files; recreating a shared `DeveloperTests` mount; copying large applications into the OS layer; using rolling incompatible runtime DLLs; hiding unmet dependencies with disabled features; creating special Stage activation scripts; losing ownership distinctions between Steam, user and CinterForge-managed files.
