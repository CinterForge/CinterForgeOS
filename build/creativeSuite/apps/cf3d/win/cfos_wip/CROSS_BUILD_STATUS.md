# CinterForge Windows cross-build: source audit

The native Visual Studio `CinterForgeSteam/CinterForge.vcxproj` is the current
Windows application build definition. Keep Visual Studio functional while
introducing a separate, source-equivalent MinGW/UCRT64 build definition.

**Do not use `CinterForgeSteam/Makefile` as the Windows build:** it is an
Steamworks Linux example-derived makefile, targets `SteamworksExampleLinux`,
and references example source names that do not match the actual CinterForge source layout. Passing `CC`/`CXX` as
MinGW tools does not turn that makefile into a Windows CinterForge build.

Run from the Cinter root:

```sh
python3 CinterForge/CinterForgeOS/build/creativeSuite/apps/cf3d/win/cfos_wip/audit_cross_build.py \
  --output ../Builds/DerivedData/WindowsCrossBuild/audit.json
```

The audit exits with status 2 until a real Windows GNU Make build and its
libraries have been established. This is deliberate: an audit is not a build,
stage, or test result. Do not mark Windows cross-build readiness green based on
shared compiler provisioning alone. Native MSVC and Visual Studio debugging
remain separate supported development paths.

Next implementation steps: derive a Windows-specific source list and dependency
closure from the native project; resolve DirectX/Steam/Python/Bonjour/other
Windows libraries for the UCRT64 toolchain; compile/link the actual executable;
stage it with its runtime dependencies; then validate it on Windows.

## Native project contract extraction

The `extract_msvc_build_contract.py` tool parses the Visual Studio project as XML
and records configurations, compiler/linker settings, resource inputs, individual
source files, and per-file overrides. It does **not** translate MSVC settings to
GCC or claim Windows cross-build support.

From the Cinter root:

```sh
python3 CinterForge/CinterForgeOS/build/creativeSuite/apps/cf3d/win/cfos_wip/extract_msvc_build_contract.py \
  --output ../Builds/DerivedData/WindowsCrossBuild/msvc_contract.json
```

The generated manifest is a reproducible starting point for implementing a
separate Windows GNU Make target without replacing or modifying the native
Visual Studio project. Preserve MSBuild condition semantics, per-file exclusions,
resource compilation, SDK/library dependencies, and linker settings when
translating it. Do not use the Linux Steamworks sample Makefile.

## Windows x64 dependency closure (Debug and Release)

The native Visual Studio project is the source of truth. Generate separate
machine-readable dependency reports for each x64 configuration:

```sh
python3 CinterForge/CinterForgeOS/build/creativeSuite/apps/cf3d/win/cfos_wip/analyze_cross_dependencies.py \
  --configuration Debug --output ../Builds/DerivedData/WindowsCrossBuild/dependencies_debug.json
python3 CinterForge/CinterForgeOS/build/creativeSuite/apps/cf3d/win/cfos_wip/analyze_cross_dependencies.py \
  --configuration Release --output ../Builds/DerivedData/WindowsCrossBuild/dependencies_release.json
```

Exit code **2** means the dependency inventory was written successfully but the
cross-build remains blocked. The report lists native project compilation units,
per-configuration exclusions, resource files, unresolved MSBuild properties,
MSVC linker libraries and library/include directories. **Do not pass these
MSVC library names directly to MinGW**: UCRT64 equivalents and redistribution
rights must be resolved individually. This report is a porting input, not a
build, staging, runtime test, or release-readiness result.

## Generated Windows GNU Make compile graph

`generate_cross_makefile.py` now generates a real per-source GNU Make compile
and link graph directly from the native Visual Studio project, retaining x64
Debug/Release source exclusions. It writes the Makefile and JSON provenance
report only under external `Builds`.

The generator **requires an explicitly reviewed UCRT64 port JSON** containing
arrays `include_dirs`, `defines`, `cxxflags`, `ldflags`, `libraries`, and
`resource_inputs`, plus `"reviewed_msvc_dependencies": true`. This gate is
intentional: `python310.lib`, `dnssd.lib`, DirectX SDK paths and other MSVC
linker settings cannot be assumed to work with MinGW. No default or fallback
library mappings are invented. The resource list is explicit so a missing
Windows resource cannot silently disappear from the cross-build.

From the Cinter root, after preparing and reviewing that dependency mapping:

```sh
python3 CinterForge/CinterForgeOS/build/creativeSuite/apps/cf3d/win/cfos_wip/generate_cross_makefile.py \
  --configuration Debug \
  --port-config ../Builds/DerivedData/WindowsCrossBuild/port_debug.json \
  --output ../Builds/DerivedData/WindowsCrossBuild/Debug/Makefile
```

Generating a Makefile does **not** prove the executable builds or runs. The
cross-compiler, SDK/import libraries, Windows resource inputs, and final
staging/runtime closure remain required before enabling this as a release gate.

### Generator correctness hardening (October 2026)

The generated graph now selects the MinGW cross-compiler instead of GNU Make's
built-in host `g++` default (while still allowing an explicit `CXX` override),
quotes compiler arguments for the guest shell, tracks header dependencies with
`-MMD -MP`, resolves resource inputs relative to `CinterForgeSteam/`, and
rejects missing resources and malformed/unexpanded dependency entries.

A synthetic, explicitly marked **non-shipping** port configuration generated
257 compilation rules, and `make -n` parsed the resulting graph successfully.
This is a Makefile syntax check only: it does **not** establish SDK closure,
Windows compilation, staging, runtime testing, or release readiness. The
reviewed UCRT64 dependency mapping is still required for a real build.

## Dependency port worksheet (October 2026)

Generate a **non-approved** UCRT64 mapping worksheet from the real MSVC project:

```sh
python3 CinterForge/CinterForgeOS/build/creativeSuite/apps/cf3d/win/cfos_wip/prepare_port_config.py \
  --configuration Debug \
  --output ../Builds/DerivedData/WindowsCrossBuild/port_debug.json
```

The worksheet includes the native resource inputs and enumerates unresolved
MSVC libraries, include directories and properties under `_review`. It leaves
UCRT64 libraries and include paths empty and `reviewed_msvc_dependencies=false`.
The Makefile generator refuses that worksheet until dependencies are resolved
and explicitly approved. It now also refuses any port configuration that drops
a resource declared in the native Visual Studio project. Neither operation
compiles or tests CinterForge.

## Executing a reviewed Windows cross-build

`run_cross_build.py` is the build executor, distinct from the worksheet and
Makefile generators. Run it **inside the provisioned Linux AMD64 guest** with
`make`, `x86_64-w64-mingw32-g++`, and `x86_64-w64-mingw32-windres` on `PATH`:

```sh
python3 creativeSuite/apps/cf3d/win/cfos_wip/run_cross_build.py \
  --repo-root /path/to/CinterForge \
  --builds-root /path/to/Builds \
  --configuration Debug \
  --port-config /path/to/Builds/DerivedData/WindowsCrossBuild/port-debug.json
```

The executor rejects unreviewed configurations and missing compiler tools.
It writes `Makefile`, `build.log`, `build_report.json`, and the executable (on
success) under `Builds/DerivedData/WindowsCrossBuild/<configuration>/`.
A successful compile is explicitly reported as **built, not staged or tested**.
The UCRT64 dependency mapping and Windows runtime acceptance remain outstanding.

## Checked-in Steamworks SDK inputs (source-verified)

The existing CinterForgeSteam tree already carries Steamworks headers and
platform redistributables. `Makefile` and `flags.mak` are Linux Steamworks
example-derived build rules; they demonstrate `libsteam_api.so` linking and
Linux SDL dependencies. They are **not** a Windows cross-build definition.
The native Visual Studio project explicitly includes `steam_api64.lib` and
stages `steam_api64.dll`. These SDK redistributables are supplied binaries,
not Steamworks code compiled from scratch.

Run from Cinter root:

```sh
python3 CinterForge/CinterForgeOS/build/creativeSuite/shared/win/verify.py \
  --repo-root CinterForge \
  --output ../Builds/DerivedData/WindowsCrossBuild/steam_sdk.json
```

The cross-build executor now checks these inputs before invoking GNU Make.
The existing MSVC import library is **not** evidence of a validated MinGW
import library. Python, Bonjour, DirectX, other platform dependencies, and
Windows execution still require a reviewed toolchain/link contract and a
real compile/link/run. Visual Studio remains the native Windows build path.

## Steamworks MinGW import library (October 2026)

The checked-in Windows Steamworks SDK contains `steam_api64.dll` and an MSVC
`steam_api64.lib`. A GNU import library can be produced deterministically from
the DLL exports without modifying the vendor SDK or relying on MSVC import
library compatibility:

```sh
python3 CinterForge/CinterForgeOS/build/creativeSuite/shared/win/build.py \
  --builds-root ../Builds
```

This requires `gendef` and `x86_64-w64-mingw32-dlltool` in the provisioned
cross-build environment. It produces `Builds/DerivedData/WindowsCrossBuild/Steamworks/libsteam_api64.a`
and an explicit provenance report. The reviewed UCRT64 port configuration must
link that generated library and staging must include the original Windows DLL.
Generating the import library is not a successful CinterForge link or Windows
runtime test. The remaining Python, Bonjour, DirectX, and SDK dependencies
must still be resolved before the complete executable can be built.
