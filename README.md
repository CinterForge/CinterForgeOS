# CinterForge OS
### Branch of steamac — Valve's official ARM64 SteamOS (the Steam Frame image) in a VM on Apple Silicon

<High levbel description>


# XCode Project

The macOS ARM64 host launcher for CinterForgeOS. This reorganizes the existing Swift files without changing their Swift module or compiled semantics. Xcode groups mirror physical subfolders.

- **Core/** — Application startup, argument parsing, VM lifecycle, host platform helpers.
- **Interface/** — AppKit windows, preferences screens, boot overlays and runtime status indicators.
- **Graphics/** — Metal/Vulkan display pipeline, presentation, EDID and performance measurements.
- **Input/** — Keyboard, mouse, controllers, clipboard, audio and host↔guest input translation.
- **Networking/** — Guest network bridge, connectivity errors and Steam Remote Play relay.
- **Storage/** — SteamOS disk creation, filesystem images, provisioning, integrity and licenses.
- **Diagnostics/** — Crash reports and user-facing diagnostic reports.
- **Updates/** — Update checking and guest suspend/resume integration.
- **Tests/** — In-process diagnostics, deterministic self-tests and debug controls.

`Core/main.swift` is the application entry point. `Core/VM.swift` coordinates the guest. `Graphics/` renders frames, while `Input/` translates controls. `Storage/` prepares guest disks; `Interface/` provides the native macOS UI.

The executable is `cfos-vm`; macOS bundle identity, persisted settings domains, internal API names, and external work/build directory names remain unchanged for compatibility.

