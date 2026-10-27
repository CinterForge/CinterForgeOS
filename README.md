# CinterForge OS
![CinterForge OS development: Xcode, Finder, and the macOS application](docs/dev-environment/Hero%20Shot.png)
### Built by a macOS developer. For macOS developers.

**Build and debug Linux x86-64 and Windows applications on your Mac — in a real SteamOS desktop.**

CinterForge OS brings a SteamOS development environment to Apple Silicon. Keep the Mac, Xcode, and the tools you love. Build with the CinterForge toolchain, run applications in KDE Plasma, and investigate how they behave in Linux and Windows compatibility environments — without maintaining a separate development PC.

![CinterForge OS development: Xcode, Finder, and the macOS application](docs/dev-environment/Screen%201%20-%20XCode%20and%20macOS.png)

## First, thank you

**To the [steamac team](https://github.com/fxgl/steamac):** Thank you for the awesome engineering that made SteamOS on Apple Silicon possible. CinterForge OS builds on that work. If the idea of SteamOS in a Mac window excites you, please explore and support the [original steamac repository](https://github.com/fxgl/steamac). Our [copy of the original steamac README](docs/technical/Steamac%20README.md) is preserved for technical background and attribution.

**To [Valve](https://store.steampowered.com/steamos/):** Thank you for building an amazing operating system, an open PC ecosystem, and the Steam platform that connects developers with players. CinterForge OS uses SteamOS as its guest operating system; it is an independent community development effort, **not an official Valve or steamac release**.

## What is CinterForge OS?

### 1. Built by a macOS developer, for macOS developers

**Build on macOS. Debug where your applications run.**

CinterForge OS is a native Apple Silicon macOS application that starts an ARM64 SteamOS virtual machine using libkrun and displays its KDE Plasma desktop. The development workflow is built around a familiar Mac toolchain: Xcode, VS Code, and the `CinterForge` build/stage/test lifecycle in the companion CinterForge repository.

For Linux x86-64 targets, CinterForge uses a separate headless Ubuntu AMD64 build/test environment and FEX to run x86-64 software on the SteamOS desktop. Windows applications can be exercised through Wine on FEX. These are **compatibility-backed development and testing paths**, not a claim that every Windows or Linux application runs perfectly or that the guest is an x86-64 virtual machine.

![Developing with VS Code and the CinterForge toolchain](docs/dev-environment/Screen%202%20-%20VS%20Code.png)

### 2. Steam to the core

**SteamOS on the inside. Steam applications on the outside.**

Inside the window is Valve's SteamOS: a real Linux operating system with KDE Plasma, Steam integration, and an x86 compatibility stack. Outside the VM, a persistent Steam library holds CinterForge applications, installation artifacts, scripts, logs, and test results. A single shared library connects the macOS host to the guest, so application tooling can change without rebuilding the operating system.

You get a desktop you can use, not a mockup of one.

![CinterForge OS running the SteamOS KDE desktop](docs/dev-environment/Screen%203%20-%20SteamOS.png)

### 3. A creative OS

**The first applications built and deployed with it are the CinterForge Creative Suite.**

CinterForge OS is both an operating environment and a proving ground for creative software. We're starting with tools for artists, makers, game creators, and developers — building, installing, launching, and testing applications across macOS, Linux, and Windows targets.

The goal is a practical creative workstation and a reproducible cross-platform development workflow, built in the open and improved app by app.

### 4. CinterForge IDE — coming in Series 2

The next chapter is **CinterForge IDE**, an integrated development environment for building **IF games**. It's on the roadmap for **Series 2**, not a feature of the current OS release.

## How it works

```text
Apple Silicon Mac
  ├── Xcode / VS Code / CinterForge (CinterForge repository)
  │     └── Build → Stage → Test → Logs
  └── CinterForge OS.app (native macOS host)
        └── ARM64 SteamOS VM (libkrun)
              ├── KDE Plasma desktop + Steam
              ├── Linux x86-64 apps via FEX
              ├── Windows apps via Wine + FEX
              └── Persistent Steam library (shared with macOS)
```

The operating system supplies the desktop and boot/runtime integration. The application lifecycle and test scripts belong to the **CinterForge/CinterForge** repository, and installed apps live in persistent versioned directories. This separation is deliberate: changing an app or test script should not require rebuilding the guest OS.

### Build it, run it, improve it

CinterForge OS is for developers who enjoy getting close to the hardware and the software stack. Start with the architecture and Xcode project guide below. The [original steamac README](docs/technical/Steamac%20README.md) is also useful for its underlying virtualization technologies and build prerequisites.

The native host application is built in **Xcode**. The CinterForge project's `CinterForge` command handles the application's broader **Build → Stage → Test** workflow and external test tooling. Guest image generation and dependencies are separate from the native Swift build; see the technical docs for the current procedure rather than treating a single Xcode click as a complete first-time setup.

This project is evolving. Contributions that improve build reproducibility, graphics, input, app compatibility, diagnostics, documentation, and the developer experience are welcome.

## Technical documentation

| Guide | What's inside |
| --- | --- |
| [Architecture](docs/technical/CinterForgeOS%20Architecture.md) | The complete host/guest/shared-library design and boot-to-test lifecycle. |
| [Guest](docs/technical/CinterForgeOS%20guest.md) | SteamOS image, boot, overlays, mounts, KDE, and guest integration. |
| [Host](docs/technical/CinterForgeOS%20host.md) | Native macOS host, VM lifecycle, graphics, input, storage, and diagnostics. |
| [Xcode project](docs/technical/CinterForgeOS%20XCode%20Project.md) | App target, dependencies, build phases, generated outputs, and staging responsibilities. |
| [Graphics benchmarking](docs/technical/CinterForgeOS%20Graphics%20Benchmarking.md) | Linux Superposition and Windows Heaven showcase targets, proven controls, and verification criteria. |
| [Original steamac README](docs/technical/Steamac%20README.md) | Upstream context, platform prerequisites, and technical foundations. |

For operational commands used to install and test Linux/Windows applications, see the **CinterForge** repository's `creativeSuite/apps/cinterforge-os/external/` documentation. The `CinterForge` entry point inside SteamOS delegates to tools installed outside the guest image.

## Join in

If you build on a Mac and want to help make Linux and Windows development easier, **this is a project worth getting your hands into**. Read the architecture, build the host, explore the guest, try an application, and contribute what you learn.

**Create. Play. Build. Belong.**

---

CinterForge OS is an independent CinterForge project. Steam, SteamOS, and related names are trademarks of Valve Corporation. Upstream components remain subject to their respective licenses; see [LICENSE](LICENSE), [NOTICE](NOTICE), and [LICENSES](LICENSES/).
