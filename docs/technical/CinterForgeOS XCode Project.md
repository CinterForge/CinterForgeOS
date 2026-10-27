# Native CinterForgeOS macOS application

The canonical application target is `CinterForgeOS.xcodeproj` (`CinterForgeOS` scheme).
Xcode owns Swift/C compilation, icon and localization compilation, declared
`Copy Bundle Resources`, splash `Copy Files`, each signed native dylib in
`Copy Files → Frameworks` with `Code Sign on Copy`, application entitlements,
and macOS application signing. Do not run `bundle.sh` or use SwiftPM output
as the distributable app.

`./CinterForge setup cinterforge-os mac` provisions the build tools/native dependencies.
Guest image generation is separate because Xcode cannot natively produce the
SteamOS EROFS artifacts. `./CinterForge build cinterforge-os mac Release` invokes
native `xcodebuild` after guest preparation and copies the *unchanged* completed
signed app to the versioned Build Output. Stage must not modify the .app.

The only retained application-target shell phase exports the KDE-ready test
hook outside the app, to the external Built/Test directory. It is not used
for bundle resource packing, signing, or staging.


CinterForgeOS: Project Files & Build Architecture
Engineering reference  |  22 October 2026  |  Current local source snapshot
Purpose: identify which files own which responsibilities, how they reference one another, what Xcode compiles directly, what Xcode delegates to scripts, where generated outputs live, and what remains uncertain. This documents the checked-in snapshot; it is not a claim that a complete build succeeds on the Mac.
1. At a glance: two build systems under one Xcode build
Xcode builds the native macOS application target (Swift/C, linking, signing, bundle copies). The same application target also has several shell-script phases that assemble Linux guest artifacts in Docker/Colima. Therefore “Build in Xcode” does not mean that Xcode compiles all guest code with Apple clang. Scripts perform Linux compilation, Xcode triggers them and checks outputs.
Source folders are not build-output folders. The deleted top-level host/ source directory must not be reintroduced. Paths such as Builds/.../work/out/host/lib and .../host/bin refer to host-side binary outputs and do not mean a source-level host/ directory exists.
2. Repository layout and file ownership


3. Actual Xcode target dependency graph
Observed build log: 4 targets: CinterForgeOS → CZstd and Sentry; Sentry → SentryCppHelper. The main project file defines two native targets: CinterForgeOS (macOS .app) and CZstd (libCZstd.a). Sentry is an external Swift Package dependency, not a local fork target.


Important distinction: the project includes “Signed native dylibs” Copy Files phase, which references five dylibs. Copying/signing a dylib does not compile it. The provided build trace stops inside guest Mesa and does not prove that dylib copying/linking or app completion occurred.
4. Ordered Xcode application build phases


The phase order above follows PBXNativeTarget.buildPhases in the current project file; Xcode can schedule unrelated build tasks concurrently subject to dependency constraints. A check reporting “verified; no rebuild” is a receipt validation path, not evidence of an actual rebuild in that invocation.
5. Guest build script call chain
Xcode shell phases set STEAMAC_SOURCE_ROOT=$PROJECT_DIR and STEAMAC_WORK_DIR=$PROJECT_DIR/../Builds/CreativeSuite/Built/cinterforge-os/mac/CFOS/work, then source scripts/xcode-docker-env.sh when Docker is needed. That helper selects the managed CFOS Colima profile/socket. scripts/build-image.sh is the main guest-image dispatcher. scripts/config.env and scripts/steps/lib.sh provide shared parameters/functions. scripts/builder/Dockerfile constructs the Linux builder image. The scripts/steps directory supplies the individual image stages.


6. Native graphics and execution dataflow
Intended runtime dataflow, per checked-in README (architecture, not verified runtime success): x86 Windows game → Proton/Wine → DXVK or vkd3d-proton → Vulkan inside guest. Linux x86 code uses FEX on the ARM64 guest. Guest Vulkan through Venus/virtio-gpu reaches virglrenderer on macOS; host Vulkan implementation is MoltenVK or optional KosmicKrisp; Metal presents the result. libkrun supplies VM execution and the native macOS app supplies UI, controls, integration and launch.
These are distinct roles: FEX translates guest x86-64 instructions; Mesa Venus x86_64/i386 supplies guest Vulkan graphics libraries for emulated x86 applications. Compiling x86 Mesa is NOT equivalent to installing FEX. The supplied Xcode phase list explicitly builds x86 Mesa but does not contain a named FEX build/install phase.
7. External forks and how they appear in Xcode


PBXFileSystemSynchronizedRootGroup references are project navigator views of real sibling folders. They do not copy fork source into this repository, run builds, or make fork compilation a dependency. The workspace XML itself references only CinterForgeOS.xcodeproj. The desired MoltenVK navigator limit (MoltenVK, Common, include) is reflected in the current project file.
8. What links/copies what at final app assembly


9. Build outputs and cache validity
The output tree is outside CinterForgeOS source. Representative paths: work/out/Image (kernel), work/out/initramfs.cpio.gz, work/out/mesa-venus/, work/out/steamac-layer.img, work/out/host/bin/{gvproxy,desync}, work/out/host/lib/*.dylib, work/out/XcodeProduct/Debug/CinterForge OS.app, and work/build/mesa-venus/build-x86.log. These are generated products, not editable fork source.
guest-artifacts.py computes input hashes from Git-visible inputs plus output hashes. If a receipt passes, a phase may safely skip work. If it fails, that phase calls its script and validates again. A successful Mesa ARM64 phase and a cache-verified kernel/initramfs do not imply that x86 Mesa, native dylibs, bundling or signing succeeded.
10. Setup, Build, Stage, Run: boundaries


11. Current observed build and open questions
The supplied 22 October build trace reaches guest Mesa x86_64+i386 in Xcode; ARM64 Mesa was reported verified. x86 Meson configured for virtio Vulkan and started 273 Ninja steps. The earlier attempt failed when wayland-scanner generated src/loader/fifo-v1-client-protocol.h from staging/fifo/fifo-v1.xml. No later successful x86 Mesa completion or completed CinterForge OS.app build is shown in that trace.
Need to verify, rather than assume: (1) production of each of the five native host dylibs, since the observed target graph does not build them; (2) actual guest FEX binary installation and rootfs injection, as no named Xcode FEX phase exists; (3) whether generated guest images are embedded into the app by a defined bundle copy mechanism; (4) whether stale README references to deleted host/* source scripts reflect current architecture; and (5) whether scripts/steps/40-disk.sh and 50-check.sh are invoked through a later lifecycle command.
Additional consistency issue: checked-in CinterForgeOS-Xcode.md says the only retained application shell phase exports the KDE-ready hook, but the current project.pbxproj actually declares multiple guest/host-tools phases. Treat project.pbxproj as authoritative for the Xcode build graph, not this older description. The local snapshot also still contains a legacy host/ folder; its presence here does not override the user’s deletion of that folder in the active Mac project. Modern builds must not rely on it.
12. Quick navigation / troubleshooting


Source basis: local canonical CinterForgeOS snapshot at /mnt/data/current_files/steamac inspected 22 Oct 2026, specifically project.pbxproj, workspace XML, scripts/guest-artifacts.py, scripts/xcode-build-macos.sh, guest/ directory structure, CinterForgeOS-Xcode.md, README.md, plus user-supplied Xcode log. Statements about what is not confirmed are intentional: no macOS Xcode build was executed in this environment.
