# CinterForgeOS — Cross-Platform Graphics Benchmarking

**One Apple Silicon Mac. One SteamOS desktop. Two x86-64 application ecosystems.**

CinterForgeOS is built to make running and debugging Linux and Windows software on a Mac feel like a natural part of development. Our graphics showcase brings that ambition into focus with two recognizable, visually rich benchmarks: **UNIGINE Superposition for Linux x86-64** and **UNIGINE Heaven for Windows x64**.

The aim is more than a screenshot. We want repeatable evidence that the application is an x86-64 binary, that its compatibility runtime is working, that a real scene renders in KDE, and—separately—that rendering is **GPU-accelerated** rather than CPU-only.

> **Status, October 24, 2026:** These are the selected showcase targets, **not yet verified successful benchmark runs**. The Windows and Linux GUI execution paths have been demonstrated with smaller applications, and a Linux x86-64 GLMark2 benchmark has rendered using software OpenGL. The Superposition and Heaven downloads are underway; their installation, launch, scores, and GPU acceleration remain to be measured. This page describes the intended successful showcase and tracks what has actually been established.

## Two worlds, one desktop

### Linux x86-64 — UNIGINE Superposition

![UNIGINE Superposition benchmark promotional preview](../dev-environment/Superposition.png)

Superposition is the Linux showcase: a cinematic, interactive 3D scene running as a **native Linux AMD64 executable through FEX**, inside the SteamOS KDE desktop. It demonstrates the same x86-64 application execution path intended for the CinterForge Creative Suite.

- **Publisher:** [UNIGINE — Superposition](https://benchmark.unigine.com/superposition)
- **Application:** Linux x86-64 edition (confirm the downloaded file's ELF architecture)
- **Execution:** Apple Silicon macOS → CinterForgeOS ARM64 SteamOS → FEX → Linux AMD64 application → KDE window
- **Graphics target:** OpenGL with GPU acceleration where supported; explicitly document software-rendering fallback
- **Install destination:** persistent `Applications/Linux/Superposition/<version>/`, outside the disposable guest OS

**Successful showcase criterion:** Superposition starts, renders its interactive scene, completes a benchmark, and records the OpenGL vendor/renderer, frame rate, score, and whether the renderer is hardware- or software-backed.

### Windows x64 — UNIGINE Heaven

![UNIGINE Heaven benchmark promotional preview](../dev-environment/Heaven.png)

Heaven is the Windows showcase: a visually rich 3D environment, running as a **Windows x64 executable through Proton 11 ARM64** in the very same SteamOS desktop. We will begin with a windowed DirectX 11 test at conservative settings and increase quality only after stable rendering is proven.

- **Publisher:** [UNIGINE — Heaven](https://benchmark.unigine.com/heaven)
- **Application:** Windows x64 edition (verify the executable is PE x64)
- **Execution:** Apple Silicon macOS → CinterForgeOS ARM64 SteamOS → Proton 11 ARM64 → Windows application → KDE window
- **Graphics target:** DirectX 11 translated through DXVK/Vulkan where supported; identify the actual Vulkan device
- **Install destination:** persistent `Applications/Windows/Heaven/<version>/` with isolated Proton compatibility data

**Successful showcase criterion:** Heaven starts, displays its animated scene, completes a benchmark, and records the graphics API, GPU/renderer name, FPS, score, and any Vulkan/driver warnings.

*The images above are UNIGINE website previews supplied for this documentation, not screenshots captured from running benchmarks inside CinterForgeOS. Replace or supplement them with actual CFOS screenshots once the showcase runs have been verified.*

## What is already demonstrated

| Capability | Evidence / current result |
| --- | --- |
| Windows GUI | Proton 11 ARM64 successfully launched Windows Notepad in SteamOS KDE. |
| Windows execution | Proton ran `cmd.exe` and wrote a test file to the isolated Windows prefix. |
| Linux AMD64 execution | FEX successfully executed the x86-64 Bash from the Debian 13 rootfs. |
| Linux AMD64 3D rendering | The downloaded Debian AMD64 GLMark2 executable rendered 3D through Mesa **llvmpipe** (software). |
| Linux OpenGL details | GLMark2 reported OpenGL 4.3 and `llvmpipe (LLVM 19.1.7, 256 bits)`; two early subtests reported 756 and 867 FPS at 800×600 before the test timed out. These are **software-rendering** numbers, not GPU scores. |
| Linux GPU acceleration | **Not yet working:** Mesa Zink reported `vkCreateInstance failed (VK_ERROR_INCOMPATIBLE_DRIVER)` during the normal GLMark2 attempt. |
| Windows GPU acceleration | **Not yet verified:** Proton reported a Virtio-GPU Venus (Apple M4 Max) device, but no completed DirectX/Vulkan 3D benchmark yet. |
| Heaven / Superposition success | **Pending actual installations and runs.** |

## Reference workflows

These examples are **diagnostic commands in the CFOS Konsole**, not a new application installation system or additional standalone scripts. Production installation, inspection, staging, and test automation belongs to the **Hope/CinterForge** repository and its existing `Build → Stage → Test` lifecycle.

### Windows compatibility control — Proton

The following combination has already launched Notepad successfully; it does **not** guarantee Heaven works:

```bash
PROTON="$HOME/.local/share/Steam/steamapps/common/Proton 11.0 (ARM64)/proton"
export STEAM_COMPAT_CLIENT_INSTALL_PATH="$HOME/.local/share/Steam"
export STEAM_COMPAT_DATA_PATH="$HOME/.local/share/cinterforge/proton-heaven"
mkdir -p "$STEAM_COMPAT_DATA_PATH"

# Replace with the real downloaded Windows installer, not a placeholder.
ENABLE_GAMESCOPE_WSI=0 "$PROTON" waitforexitandrun "/actual/path/to/Heaven-installer.exe"
```

`ENABLE_GAMESCOPE_WSI=0` was necessary in the working Notepad test to avoid a Gamescope swapchain-layer conflict. The ARM64 Proton distribution also logged a nonfatal `amdxcffx64.dll` copy warning; the condition must be recorded rather than represented as repaired.

### Linux AMD64 graphics control — GLMark2

GLMark2 was manually extracted to persistent application storage without altering the read-only SteamOS `/usr` or the FEX rootfs:

```bash
APP="/run/steamapps/common/CinterForge/Applications/Linux/GLMark2/2026"
FEXBash -c "timeout -s INT 30s env \
  LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe \
  MESA_LOADER_DRIVER_OVERRIDE=llvmpipe \
  '$APP/usr/bin/glmark2' \
  --data-path '$APP/usr/share/glmark2' --size 800x600"
```

This is a **CPU/software** control. Remove the forced software settings only when diagnosing the x86-64 OpenGL/Vulkan GPU path; the normal run currently fails to initialize Zink/Vulkan. Superposition is expected to put more pressure on the graphics stack than GLMark2, so a successful software-rendered GLMark2 scene does not imply Superposition compatibility.

## How we will substantiate the showcase

For **each platform**, keep evidence of the binary format and architecture, exact application/runtime version, command line and environment, executable exit status, renderer identity, API version, screenshots or video captured inside CFOS, benchmark settings, FPS and score, and relevant driver errors. Preserve reports and logs under the persistent `Applications/Logs/` hierarchy. Avoid comparing FPS between the two different UNIGINE tests as if they were equivalent workloads.

**A window opening is a GUI success. A scene animating is a rendering success. A GPU name in a log is not yet proof of GPU acceleration.** We will only claim a hardware-accelerated benchmark when driver selection and the observed renderer are verified, with no forced llvmpipe/software fallback.

The tests must also be reproducible: installation stays in versioned persistent `Applications/Linux` or `Applications/Windows` directories, not the guest image; graphical testing uses the existing native KDE desktop; and updates to operational scripts stay in Hope/CinterForge. No special-purpose VM, guest overlay changes, or duplicate scripts are necessary.

## Beyond a benchmark

These two demonstrations are a showcase for the broader CinterForgeOS goal: **build on a Mac, run on a real desktop, diagnose from evidence, and turn findings into reusable application recipes**. The same infrastructure should support applications across the CinterForge Creative Suite—not merely these two hand-picked benchmark titles.

For the implementation details, see [Architecture](CinterForgeOS%20Architecture.md), [Guest](CinterForgeOS%20guest.md), [Host](CinterForgeOS%20host.md), and the [Xcode project](CinterForgeOS%20XCode%20Project.md). The **Hope/CinterForge** repository owns the application build, staging, inspection, runtime launch, and testing logic.

**SteamOS on the inside. Steam applications on the outside. Create. Play. Build. Belong.**
