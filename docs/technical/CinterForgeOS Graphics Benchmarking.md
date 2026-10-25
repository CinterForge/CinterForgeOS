# CinterForgeOS — Cross-Platform Graphics Benchmarking

**One Apple Silicon Mac. One SteamOS desktop. Two x86-64 application ecosystems.**

CinterForgeOS is built to make running and debugging Linux and Windows software on a Mac feel like a natural part of development. Our graphics showcase brings that ambition into focus with two recognizable, visually rich benchmarks: **UNIGINE Heaven 4.0 for Linux x86-64 (working)** and **UNIGINE Heaven 4.0 for Windows through Proton (engine exit under investigation)**. UNIGINE Superposition remains a separate Linux target requiring OpenGL 4.5..

The aim is more than a screenshot. We want repeatable evidence that the application is an x86-64 binary, that its compatibility runtime is working, that a real scene renders in KDE, and—separately—that rendering is **GPU-accelerated** rather than CPU-only.

> **Status, October 24–25, 2026:** **Linux x86-64 Heaven 4.0 is rendering in native SteamOS/KDE through FEX and GPU-backed Mesa Zink/Venus, at approximately 120–124 FPS observed.** Windows Heaven opens its Proton launcher and briefly starts its engine, but the benchmark window exits; no completed Windows 3D benchmark is established. Earlier GLMark2 llvmpipe results remain historical software-only controls. Superposition requires OpenGL 4.5 and is not the successful Linux control. See the actual screenshot and QuickTime recording below.

## Two worlds, one desktop

![UNIGINE Heaven benchmark promotional preview](../dev-environment/Heaven.png)

Heaven is the Windows showcase running through **Proton 11 ARM64 / Wine / FEX** in the same KDE desktop. Its launcher works, and selecting Run creates a benchmark window and `heaven.exe` process, but the engine exits while the launcher remains alive. No stable Windows rendering or benchmark score is established.

- **Publisher:** [UNIGINE — Heaven](https://benchmark.unigine.com/heaven)
- **Application:** Windows x64 edition (verify the executable is PE x64)
- **Execution:** Apple Silicon macOS → CinterForgeOS ARM64 SteamOS → Proton 11 ARM64 → Windows application → KDE window
- **Graphics target:** DirectX 11 translated through DXVK/Vulkan where supported; identify the actual Vulkan device
- **Install destination:** persistent `Applications/Windows/Heaven/<version>/` with isolated Proton compatibility data

### Windows x64 — UNIGINE Heaven

![Actual CinterForgeOS Windows DX9 Heaven 4.0 benchmark rendering at approximately 120 FPS](../dev-environment/Windows%20Heaven%20DX9%20-%20AO%20Off.jpeg)
**Actual capture from CinterForgeOS, October 24, 2026.** The on-screen benchmark HUD shows **118.6 FPS** at the captured instant, with minimum **74.3 FPS** and maximum **160.6 FPS** reported during the run. The broader observed run was approximately **120–124 FPS**; these are live frame-rate observations, **not a completed benchmark score**.

**[Watch the Windows DX9 ~120 FPS QuickTime recording](https://cdndev.ucreg.io/Linux%20x86%20gl%204.3%20120FPS.mov)** (`.mov`, captured in the actual CFOS desktop).

**Successful showcase criterion (not yet met):** Heaven sustains its animated scene, completes a benchmark, and records API, GPU/renderer, FPS, score, and driver warnings. **Observed:** the launcher opens and stays alive; `heaven.exe` starts, opens its window, and exits. WineD3D DLLs and OpenGL/Zink/Venus libraries were verified in its process mappings. DirectX 11/DXVK previously triggered a host shader-compiler crash in KosmicKrisp/NIR; WineD3D and native OpenGL attempts also ended with the engine closing. These are distinct failure modes and should not be conflated.

*The Linux Heaven screenshot and QuickTime video above are actual CinterForgeOS captures. The Superposition and Windows Heaven images are promotional reference images, not proof of completed runs.*

### Linux x86-64 — UNIGINE Heaven 4.0 (verified rendering)

![Actual CinterForgeOS Linux x86-64 Heaven 4.0 benchmark rendering at approximately 120 FPS](../dev-environment/Linux%20x86%20gl%204.3%20120FPS.png)

**Actual capture from CinterForgeOS, October 24, 2026.** The on-screen benchmark HUD shows **118.6 FPS** at the captured instant, with minimum **74.3 FPS** and maximum **160.6 FPS** reported during the run. The broader observed run was approximately **120–124 FPS**; these are live frame-rate observations, **not a completed benchmark score**.

**[Watch the Linux x86-64 OpenGL 4.3 ~120 FPS QuickTime recording](https://cdndev.ucreg.io/Linux%20x86%20gl%204.3%20120FPS.mov)** (`.mov`, captured in the actual CFOS desktop).

- **Application:** UNIGINE Heaven 4.0, Linux x86-64 edition, executed under FEX.
- **Graphics:** OpenGL 4.3 Mesa/Zink over Venus/virglrenderer → KosmicKrisp Vulkan/Metal → Apple M4 Max.
- **Execution:** macOS host → CinterForgeOS SteamOS/KDE → FEX → Linux Heaven.
- **Observed:** Real animated 3D rendering and active benchmark HUD; approximately 120–124 FPS.
- **Persistent location:** `Applications/Linux/Superposition/2026/Unigine_Heaven-4.0/` (the Heaven installer extracted inside the Superposition directory because that was the working directory at install time).
- **Reproduction (CFOS Konsole):**

```bash
FEXBash -c 'cd /run/steamapps/common/CinterForge/Applications/Linux/Superposition/2026/Unigine_Heaven-4.0 && ENABLE_GAMESCOPE_WSI=0 DISABLE_GAMESCOPE_WSI=1 ./heaven'
```

**OpenGL verification:** `FEXBash -c 'glxinfo -B'` reports Mesa/Zink on Venus with OpenGL core/compatibility 4.3. FEX GLX error forwarding was corrected; an unsupported OpenGL 4.6 request now returns a handled X error instead of crashing.

### Linux x86-64 — UNIGINE Superposition (separate, not yet working)

![UNIGINE Superposition promotional preview](../dev-environment/Superposition.png)

Superposition remains a target, but its OpenGL 4.5 requirement exceeds the presently verified 4.3 path. 

## What is already demonstrated

| Capability | Evidence / current result |
| --- | --- |
| Linux x86-64 Heaven 4.0 | **Verified real-time 3D rendering** under FEX in SteamOS KDE, approximately 120–124 FPS observed; screenshot and QuickTime recording linked above. |
| Linux GPU-backed OpenGL | **Working:** FEX `glxinfo -B` identifies Mesa/Zink on Venus, OpenGL 4.3; rendering path continues through KosmicKrisp Vulkan/Metal on Apple M4 Max. |
| Linux Superposition | **Not verified working:** requires OpenGL 4.5, above currently observed 4.3. |
| Windows GUI / Proton | Notepad previously worked; Heaven launcher opens and remains alive under Proton 11 ARM64. |
| Windows Heaven engine | **Not working:** `heaven.exe` opens a benchmark window and then exits; no sustained scene or score. |
| Windows WineD3D selection | **Confirmed by process memory maps:** Proton `d3d11.dll`, `wined3d.dll`, `libGL`, Mesa GLX, and `libvulkan_virtio` loaded. Vulkan here is consistent with Zink and does not itself imply DXVK. |
| Windows DXVK shader path | Earlier KosmicKrisp NIR optimization / DCE SIGSEGV while creating Vulkan graphics pipelines; separate from the WineD3D engine exit. |
| Historical Linux GLMark2 | Earlier **llvmpipe software** control produced 756 and 867 FPS on early subtests; these are not comparable with Heaven FPS and do not describe the now-working Zink path. |
| Desktop/VM stability | Separate black-screen / guest-alive and sampler-creation crashes observed. Clipboard patch unverified; Gamescope `--force-composition` proposal unverified and must be checked against bundled binary before use. |

## Beyond a benchmark

These two demonstrations are a showcase for the broader CinterForgeOS goal: **build on a Mac, run on a real desktop, diagnose from evidence, and turn findings into reusable application recipes**. The same infrastructure should support applications across the CinterForge Creative Suite—not merely these two hand-picked benchmark titles.

For the implementation details, see [Architecture](CinterForgeOS%20Architecture.md), [Guest](CinterForgeOS%20guest.md), [Host](CinterForgeOS%20host.md), and the [Xcode project](CinterForgeOS%20XCode%20Project.md). The **Hope/CinterForge** repository owns the application build, staging, inspection, runtime launch, and testing logic.

**SteamOS on the inside. Steam applications on the outside. Create. Play. Build. Belong.**
