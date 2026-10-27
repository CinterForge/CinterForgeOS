# CinterForgeOS — Host Architecture and Source Audit

**Source reviewed:** user-supplied `CinterForgeOS.zip`, 24 October 2026. **Audience:** engineers joining the repository. **Scope:** `host/`, the macOS application source, build steps and host–guest contracts. This documents source behavior, not a fresh successful runtime test.

## Purpose and boundary

The **host** is the native **Apple Silicon macOS** side of CinterForgeOS. It creates and runs a libkrun VM, opens the app UI, presents the guest display using the host graphics pipeline, passes input/audio/network/clipboard between macOS and SteamOS, and manages disks and diagnostics. It is distinct from the guest ARM64 Linux image. Host native components are built for macOS and belong in the signed app; guest applications and external CinterForge automation are not host application code.

## `host/` directory inventory

| Path | Role | Build/runtime point |
|---|---|---|
| `host/launcher/` | Swift Package-based native launcher, app bundling, entitlements, resources, smoke harness. Contains `Sources/steamac-vm/` and C interop modules `CKrun`/`CZstd`. | Host executable compile and app bundling; runtime entry point is `main.swift`. |
| `host/libkrun/` | Builds patched macOS libkrun hypervisor library, `patches/` for virtualization and device behavior, `test/` for audio/GPU/resize smoke programs. | Before/alongside host app link; VM creation and devices at runtime. |
| `host/virglrenderer/` | Vulkan/Venus host renderer build, patches and GPU diagnostic programs. | Graphics dependency; guest Vulkan commands handled via host stack. |
| `host/moltenvk/` | MoltenVK build, patches, repros, benchmark and probes. | Host Vulkan-on-Metal implementation; rendered guest output. |
| `host/kosmickrisp/` | Alternate Mesa/KosmicKrisp Vulkan-on-Metal backend, patches and build recipe. | Optional driver selection; source describes macOS 26+/Metal 4 constraint. |
| `host/libepoxy/` | libepoxy build support. | Host graphics loader dependency. |

`host/launcher/Sources/steamac-vm/` contains familiar source groups by function even though this older Swift-Package tree is largely flat: `VM.swift`, `main.swift`, `Options.swift` (startup), `Window.swift`/`Renderer.swift`/`Presenter.swift` (display), `LinuxInput.swift`/`Gamepad.swift`/`Clipboard.swift` (input and integration), `Network.swift`/`RemotePlayRelay.swift` (network), `Provision.swift`/`GPT.swift`/`DiskCreator.swift`/`RaucBundle.swift` (storage), `Report.swift`/`CrashReporting.swift` (diagnostics), and `*SelfTest.swift` (developer tests).

## Important: there are TWO host-side Swift source layouts

This repo also contains an Xcode-oriented tree at **`source/Swift Source/`**, organized into `Core/`, `Interface/`, `Graphics/`, `Input/`, `Networking/`, `Storage/`, `Diagnostics/`, `Updates/`, `Tests/`. `CinterForgeOS-Xcode.md` describes that layout and states `Core/main.swift` is the entry point, `Core/VM.swift` controls the guest, and the executable is `cfos-vm`.

**Audit finding:** These are not interchangeable snapshots. The older `host/launcher/Sources/steamac-vm/VM.swift` includes optional developer VirtioFS shares tagged `Applications` and `DeveloperTests`; the Xcode-oriented `source/Swift Source/Core/VM.swift` contains `krun_add_virtiofs4(..., "steamapps", ...)`, shares the host Steam library at `/run/steamapps`, and includes simplified guest ownership semantics. Engineers should inspect the **active Xcode target's source membership**, not assume the code in `host/launcher` drives the Xcode build. The newer guest contract demands the single `steamapps` share; the older optional paths are a compatibility/duplication risk if compiled or reused. This audit does not prove which path was compiled for a particular user's build.

## Host boot and execution path

1. **Build-time acquisition.** The host dependency recipes under `host/*/build.sh` assemble graphics and virtualization libraries from pinned sources/patches. These are build recipes, not guest operating scripts.
2. **Guest artifacts built separately.** `scripts/build-image.sh` drives guest kernel/rootfs/initramfs/layer/disk and uses the builder container; its output includes `steamos.img`, `initramfs.cpio.gz`, `steamac-layer.img`. The host launcher consumes these artifacts but should not mutate guest OS scripts during normal runtime.
3. **Xcode app build.** `CinterForgeOS.xcodeproj` builds the native `cfos-vm` app and links the host libraries; resources and executable are assembled as a macOS `.app`. This is the user's native build route.
4. **App startup.** `Core/main.swift` (active Xcode-oriented tree per project documentation) starts the app; UI and options load. Disk-preparation and provisioning code prepare SteamOS images and required runtime assets.
5. **VM creation.** `Core/VM.swift` configures libkrun VM sizing, kernel/initramfs, disks, virtio GPU/input/sound/network/console integration, and the Steam library share where configured. The VM starts and boots the guest; the guest's initramfs takes over boot and later starts KDE.
6. **Interaction.** Host Swift displays guest frames through the macOS UI/graphics backend; input/clipboard/sound are bridged to guest devices and agents. Host-side reporting handles failures.
7. **Stage and installed runtime.** The existing CinterForge lifecycle stages the Xcode-built app and operational scripts; persistent CinterForge apps and tests remain outside the disposable guest OS. Source-stage responsibilities live in CinterForge/CinterForge, not in `host/`.

## Key host/guest interface contracts

- **Native runtime:** `cfos-vm` + macOS frameworks and patched libkrun, virglrenderer, libepoxy, MoltenVK/KosmicKrisp where selected.
- **Boot payload:** kernel, initramfs, guest disk, and read-only layer built from `guest/`/`scripts/steps/`; changes to these artifacts require the corresponding image/native build, not merely script Stage.
- **Steam library share:** Xcode-oriented `Core/VM.swift` binds a `steamapps` VirtioFS tag to host Steam installation and guest `/run/steamapps`. Guest unit `run-steamapps.mount` expects this exact tag.
- **CinterForge commands:** host terminal uses `./CinterForge` in the `~/Source/CinterForge` root; guest Konsole uses `CinterForge` anywhere. The guest bridge forwards to an external dispatcher on the persistent share. Updating that dispatcher and test/installer scripts should be Stage-only.
- **Debug/release distinction:** compiling in Xcode updates the *native app*, not necessarily the complete CinterForge Stage payload. `./CinterForge stage cinterforge-os mac Release` must locate the built app and install staged external tools using the existing system.

## Audit observations

1. **Duplicate architecture needs clear ownership documentation.** `host/launcher/` houses a full Swift Package launcher while `source/Swift Source/` is the Xcode-organized launcher. Their `VM.swift` implementations disagree about mounts. Avoid copying fixes between them without verifying active compile inputs; do not silently delete the older source without a caller check.
2. **The guest layer explicitly expects a single Steam share**, while the older launcher includes deprecated optional shares; test against the Xcode-compiled path when investigating mount bugs.
3. **Graphics components are specialized, patched dependencies.** Do not replace MoltenVK/KosmicKrisp/virglrenderer/libkrun with generic Homebrew outputs: inspect the custom patch recipes and deployment/linkage first.
4. **Native app sign/relocation and Stage are different operations.** Changes to signed app contents require rebuilding/restaging the app appropriately; changes to external scripts should not require Xcode or a guest OS rebuild.
5. **Source files alone do not establish shipping artifact correctness.** Verify actual Xcode build membership, deployed libraries, entitlements, active mount contract and launch behavior before claiming a release is tested.

## Engineer orientation: what to edit

Host Swift behavior: use **`source/Swift Source/` and its active Xcode project membership** (check before editing). Virtualization device/library defects: use relevant `host/libkrun/`, `host/virglrenderer/`, `host/moltenvk/`, `host/kosmickrisp/` recipes and patches. Guest boot/session: use `guest/` and the existing image build. App install, baseline inventory, external dispatcher, per-app tests: use the **CinterForge/CinterForge operational scripts**, then **Stage**, with no guest reboot. Do not create a parallel staging or activation tool.
