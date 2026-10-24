# CinterForgeOS — Guest Architecture and Source Audit

**Source reviewed:** user-supplied `CinterForgeOS.zip`, 24 October 2026. **Audience:** engineers entering the repository. **Scope:** `guest/`, its connections to `scripts/steps/`, and observable host interfaces. This is a source audit, not a runtime certification.

## Purpose and boundary

`guest/` contains the pieces that make Valve-derived ARM64 SteamOS run in the CinterForgeOS virtual machine: an ARM64 kernel, a custom early userspace, a read-only OS overlay, graphics support, a separate FEX x86-64 execution runtime, and agents for host/guest integration. These are **guest OS build inputs**, not a home for day-to-day application build, installer, baseline, or inspection algorithms. The intended operating boundary is: **OS startup/integration stays inside; operational commands are invoked through the single `/usr/bin/Hope` bridge and execute from persistent Steam storage.**

## Directory map

| Path | Responsibility | When used |
|---|---|---|
| `guest/kernel/` | Linux ARM64 kernel build, configuration fragments, patches, container build, smoke checks. | Guest image Build; resulting kernel starts each VM boot. |
| `guest/initramfs/init` | First guest userspace (PID 1) using BusyBox ash; partition discovery, SteamOS A/B boot selection, initial mounts, provisioning, `/usr` overlay, handoff to systemd. | **Every guest boot**, before KDE or Steam. |
| `guest/layer/usr/` | Authored filesystem overlay: systemd units, SteamOS compatibility shims, desktop/session integration, KDE assets/launchers, `/usr/bin/Hope`. | Assembled by `scripts/steps/30-layer.sh`; merged into guest `/usr` at boot. |
| `guest/fex/` | FEX acquisition/build and x86-64 RootFS/Wine userspace acquisition. | `scripts/build-image.sh fex` and `userspace`; layer consumes their built results. |
| `guest/mesa/` | Guest graphics stack/Venus preparation and graphics-related image inputs. | Guest graphics build; resulting files copied into layer. |
| `guest/progress-agent/` | Rust `fx-progress-agent`, supporting clipboard, controls, clock, progress, sleep, UI and logging integration. | Built in step 25; service instances run at guest startup and on demand. |

## Boot: ordered walkthrough

1. **Host creates the VM** via Swift/libkrun, selecting the kernel, initramfs, disks and ports. This host code is *not* in `guest/`.
2. **Kernel launches `guest/initramfs/init` as PID 1.** The script mounts minimal `/proc`, `/sys`, `/dev`, `/run`, locates GPT partitions by PARTLABEL and performs SteamOS A/B boot selection and retry bookkeeping. It can provision a fresh guest disk when requested by the host.
3. **Initramfs assembles the OS.** The SteamOS rootfs is preserved; a separately built EROFS layer is used above the stock `/usr`. Temporary overlays, firmware/boot-state compatibility and other bind mounts are configured before handoff to systemd. The source header documents slot retry and rollback behavior in detail.
4. **systemd enters desktop startup.** Guest-authored service units prepare KDE/SDDM; SteamOS session integration and the `fx-progress-agent` connect guest events to host virtio ports. Desktop entries appear in application menus.
5. **The persistent Steam library is mounted** using `run-steamapps.mount`/`.automount`, declaring `What=steamapps`, `Where=/run/steamapps`, `Type=virtiofs` and `Options=rw`. The host must export the matching tag; missing mount is an integration failure.
6. **A user or KDE launcher runs `Hope ...`.** `guest/layer/usr/bin/Hope` is intentionally short: it checks for `/run/steamapps/common/CinterForge/Applications/Test/Dispatcher/Hope` and `exec /bin/sh` forwards all arguments. Neither the command dispatcher nor baseline/installer implementations should be in the guest image.

## What exactly goes into the guest image?

`scripts/steps/30-layer.sh` copies `guest/layer/usr` into a scratch tree, installs required executables with known permissions, checks 32 built/unforked KDE launchers plus 16 installer launchers, stages steamapps mount units, installs the Hope bridge, merges FEX RootFS/Wine, installs selected `guest_config/` KDE/systemd helpers, copies Venus graphics assets, and installs `fx-progress-agent`. It then generates an EROFS layer. **Its job is to install what the guest source declares; source ownership must be corrected at its input rather than replacing the general layer builder.** The file still contains a loop scanning `guest/layer/usr/lib/cinterforge/*`; those entries are not in the current extracted source directory, but the same layer script explicitly installs four startup helpers into `/usr/lib/cinterforge/` from `guest_config/`. Consequently, `/usr/lib/cinterforge/` is **not literally absent from the built image**. It contains boot/session-related helpers; it should not contain baseline collection or application-installation logic.

### OS-essential internal helpers

`guest_config/select-kde-desktop-session`, `select-kde-wallpaper`, `verify-virtiofs-mounts` and `install-kde-places` are staged by the layer script for SDDM/KDE/mount initialization. `guest/layer/usr/lib/steamac/` contains the compatible SteamOS session, shader, client, graphics, kernel and crash handling scripts; `usr/bin/splctl` and `usr/lib/rauc/post-install.sh` participate in boot/update compatibility. These **have legitimate internal startup/runtime callers** and should not be removed solely to make the directory disappear. Consult the unit files before relocating any one of them.

### User applications / desktop entries

The desktop entries under `guest/layer/usr/share/applications/` separate **built CinterForge-branded** and **unforked/original publisher** apps, each for Linux/Windows, plus original-publisher installer entries. They use commands such as `Exec=Hope launch-cinterforge Linux Krita` and `Exec=konsole --hold -e Hope install-unforked Windows Krita`. Their job is UI integration; implementation belongs to the external Hope scripts. Icons, KDE menu definitions and wallpapers are authored under `usr/share/cinterforge/`.

## Guest integration contracts

- **One mutable shared filesystem:** the guest's declared `steamapps` VirtioFS mount at `/run/steamapps`. It is separate from the guest rootfs. Never use old `/run/Applications` or `/run/DeveloperTests` as new interface contracts.
- **External Hope dispatcher:** guest `/usr/bin/Hope` forwards arguments; the dispatcher in persistent `Applications/Test/Dispatcher/Hope` routes to persistent `Applications/Test/Scripts/`. This allows changing baseline, diagnostic and installer code without a guest OS rebuild or reboot.
- **FEX and Wine:** in this source, `30-layer.sh` packages FEX and x86-64 RootFS/Wine under `/usr/share/cinterforge/fex/`. This differs from the external *scripts* policy: binaries/userspace are baked into the image by the current build; changing their content is not stage-only unless a separate supported external provisioning route is implemented.
- **Host device integration:** virtio console ports, sound, GPU and input require matching host/libkrun implementations. `fx-progress-agent` supports multiple services; inspect `systemd` units for their actual triggers.

## Audit observations / engineering cautions

1. **The guest Hope bridge is minimal and correctly external.** It never imports `filesystem_inventory.py` or calls `/usr/lib/cinterforge/baseline-environments`; that functionality is external.
2. **KDE guest-config helpers remain internal deliberately.** `cinterforge-kde-prepare.service` calls `/usr/lib/cinterforge/select-kde-desktop-session` and `select-kde-wallpaper`; `cinterforge-virtiofs-verify.service` calls `verify-virtiofs-mounts`. Their presence does not indicate a stray baseline collector.
3. **The layer step's old generic `/usr/lib/cinterforge/*` copier remains.** It is effectively inert for current source files; it is a possible future source-ownership regression and should be reviewed before adding files there, without changing the layer build semantics casually.
4. **Possible host-source divergence:** `host/launcher/Sources/steamac-vm/VM.swift` retains optional developer `Applications` and `DeveloperTests` VirtioFS paths, while `source/Swift Source/Core/VM.swift` implements the `steamapps` mount. Establish which source Xcode compiles before assuming the old host tree controls the production VM. See the companion host report.
5. **No test execution performed.** The presence of launcher entries, units, scripts or a completed baseline does not prove each launcher or service works in the installed guest.

## Where to change things

For guest boot failures, follow `guest/initramfs/init`, `guest/kernel/`, and the relevant systemd unit; a guest image rebuild may be justified. For KDE menu labels, see `guest/layer/usr/share/applications/`, graphics, and `guest_config`; those are image assets today. For `Hope baseline`, `Hope inspect`, application install, runtime diagnostics, and application test behavior, **edit the CinterForge-owned external scripts and use the existing `./Hope stage cinterforge-os mac Release` from the Hope root**. Do not put such scripts back into `guest/layer/usr/lib/cinterforge/`. Scripts moved outside the guest are deployed by Stage, not by boot.
