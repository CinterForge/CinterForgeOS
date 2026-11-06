#!/usr/bin/env python3
"""Build and package Windows x64 Ardour using dependencies prepared by Setup.

The toolchain image and Windows-target dependency sysroot are owned by
setup.py. This module builds Ardour against that existing environment
without downloading or installing dependency libraries.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import subprocess
import re

_CONTAINER_STAGE = len(sys.argv) > 1 and sys.argv[1] == "--container-stage"
if not _CONTAINER_STAGE:
    _SUITE = next(parent for parent in Path(__file__).resolve().parents if parent.name == "creativeSuite")
    sys.path.insert(0, str(_SUITE))
    from shared.source.source import resolve_maintained_source
    from shared.build.build_runtime import build_lock, product_build_directory, validate_fork
    from shared.source.source import prepare_build_checkout, resolve_revision
    from container import run_mounted_image
    server_root = Path(__file__).resolve().parents[6] / "CinterForgeServer"
    if str(server_root) not in sys.path:
        sys.path.insert(0, str(server_root))
    from cf.io.CinterIO import cinterforgeBuildsRootPath
    # Reuse Setup's exact compiler image and receipt rather than creating another.
    from setup import _MSVCRT_IMAGE, _MSVCRT_DOCKERFILE
else:
    _SUITE = None


def prepare_source(built: Path, recipe: dict) -> Path:
    """Keep a pristine, pinned incremental checkout outside the Cinter repository.

    The maintained Ardour fork is the source of truth. No upstream downloads,
    generated objects, or edits are made to that source checkout.
    """
    cinter = _SUITE.parents[2]
    fork = resolve_maintained_source(cinter, Path(__file__).with_name("recipe.json"))
    # The recipe identifies the upstream baseline, not necessarily the final
    # maintained fork revision. Include committed local fixes when they descend
    # from that baseline. Never silently omit uncommitted source changes.
    baseline = resolve_revision(fork, recipe["source"]["revision"])
    requested = os.environ.get("CINTER_ARDOUR_REVISION", "HEAD")
    pinned = resolve_revision(fork, requested)
    ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", baseline, pinned],
        cwd=fork, capture_output=True, text=True, check=False,
    )
    if ancestry.returncode != 0:
        raise RuntimeError(
            f"Ardour selected revision {pinned} does not descend from "
            f"recipe baseline {baseline}; reconcile the local fork first"
        )
    status = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=fork, text=True,
    ).strip()
    if status:
        raise RuntimeError(
            "Maintained Ardour checkout has uncommitted changes that an "
            f"isolated Git checkout would omit. Commit or reconcile them first:\n{status}"
        )
    print(f"[cfaudio][win] Recipe baseline: {baseline}", flush=True)
    print(f"[cfaudio][win] Selected maintained revision: {pinned}", flush=True)
    destination = built / "Source"
    checkout = prepare_build_checkout(
        fork, destination, revision=pinned, generated_directories=("build",),
        label="CinterForge Audio Windows source", local_only=True,
    )
    print(f"[cfaudio][win] Pinned source: {pinned} at {checkout}", flush=True)
    return checkout



def _windows_container_build() -> None:
    """Python-only container build: target ABI checks, link probes, incremental Waf.

    All generated probes are C/C++ source in /work/Windows-x64 (never repo files).
    Setup alone owns the Windows dependency sysroot and compiler image.
    """
    import json
    import os
    from pathlib import Path
    import re
    import shutil
    import subprocess
    import sys

    source = Path('/source')
    work = Path('/work/Windows-x64')
    prefix = Path('/work/UpstreamWindows-x64/mingw64')
    lib = prefix / 'lib'
    work.mkdir(parents=True, exist_ok=True)
    cc = 'x86_64-w64-mingw32-gcc'
    cxx = 'x86_64-w64-mingw32-g++'
    objdump = 'x86_64-w64-mingw32-objdump'

    def run(*args: str, cwd: Path = source, input: str | None = None,
            log: Path | None = None) -> str:
        """Execute an argv list, never shell text. Preserve failing probe logs."""
        cmd = [str(a) for a in args]
        result = subprocess.run(cmd, cwd=cwd, input=input, text=True,
                                capture_output=log is not None, check=False)
        if log is not None:
            log.write_text(result.stdout + result.stderr)
        if result.returncode:
            if log is not None:
                print(log.read_text(), file=sys.stderr, flush=True)
            raise RuntimeError(f'[cfaudio][win] command failed ({result.returncode}): {cmd!r}')
        return result.stdout if log is not None else ''

    def read(*args: str, input: str | None = None) -> str:
        return subprocess.check_output(list(args), cwd=source, text=True, input=input)

    def write(filename: str, content: str) -> Path:
        path = work / filename
        path.write_text(content)
        return path

    def required(path: Path, description: str) -> Path:
        if not path.is_file() or not path.stat().st_size:
            raise RuntimeError(f'[cfaudio][win] Missing Windows-target {description}: {path}')
        return path

    def link(name: str, program: str, compiler: str, *flags: str,
             extension: str = '.c', output: str = '.exe') -> Path:
        """Compile and link the actual Windows PE ABI before touching Waf."""
        test = write(name + extension, program)
        destination = work / (name + output)
        run(compiler, str(test), *flags, '-o', str(destination),
            log=work / (name + '.log'))
        return required(destination, name + ' Windows cross-link output')

    # Preserve the original target compiler set, isolated MSVCRT stack and Waf
    # flag contract. No UCRT64 or Linux host library directories added here.
    os.environ.update({
        'CC': cc, 'CXX': cxx, 'CPP': 'x86_64-w64-mingw32-cpp',
        'AR': 'x86_64-w64-mingw32-ar', 'LD': 'x86_64-w64-mingw32-ld',
        'NM': 'x86_64-w64-mingw32-nm', 'RANLIB': 'x86_64-w64-mingw32-ranlib',
        'DLLTOOL': 'x86_64-w64-mingw32-dlltool',
        'STRIP': 'x86_64-w64-mingw32-strip',
        'WINDRES': 'x86_64-w64-mingw32-windres',
        'DEPSTACK_ROOT': str(prefix),
        'PKG_CONFIG': 'pkg-config',
        'PKG_CONFIG_LIBDIR': f'{lib}/pkgconfig:{prefix}/share/pkgconfig',
        'PKG_CONFIG_SYSROOT_DIR': '',
        'PKG_CONFIG_SYSTEM_LIBRARY_PATH': '',
        'PKG_CONFIG_SYSTEM_INCLUDE_PATH': '',
        'CFLAGS': f'-mstackrealign -idirafter {prefix}/include',
        'CXXFLAGS': f'-mstackrealign -idirafter {prefix}/include -include sys/types.h',
    })
    os.environ.pop('PKG_CONFIG_PATH', None)
    os.environ.pop('LDFLAGS', None)
    cflags = ['-mstackrealign', '-idirafter', str(prefix / 'include')]
    cxxflags = [*cflags, '-include', 'sys/types.h']

    # C++ thread_local destructors: cross-link a real DLL and match imported
    # __cxa_thread_atexit against the selected GCC 10 runtime exports.
    abi = work / 'CppABI'
    abi.mkdir(exist_ok=True)
    cpp = abi / 'thread_local.cpp'
    cpp.write_text('''struct Counter {
    ~Counter() { ++destructions; }
    static int destructions;
};
int Counter::destructions = 0;
thread_local Counter thread_counter;
extern "C" __declspec(dllexport) int ardour_thread_local_probe() {
    (void)&thread_counter;
    return Counter::destructions;
}
''')
    dll = abi / 'thread_local.dll'
    run(cxx, '-std=c++17', '-shared', '-o', str(dll), str(cpp), log=abi / 'compile.log')

    def pe_info(path: Path) -> str:
        return read(objdump, '-p', str(path))

    pe_imports = pe_info(dll)
    (abi / 'thread_local.imports.txt').write_text(pe_imports)
    providers = {}
    for name in ('libstdc++-6.dll', 'libgcc_s_seh-1.dll', 'libwinpthread-1.dll'):
        selected = read(cxx, '-print-file-name=' + name).strip()
        runtime = required(Path(selected), name + ' toolchain runtime')
        if not runtime.is_absolute():
            raise RuntimeError(f'Compiler returned a non-absolute runtime: {selected}')
        listing = pe_info(runtime)
        (abi / (name + '.exports.txt')).write_text(listing)
        providers[name] = runtime, listing
    for section in re.split(r'(?=\s*DLL Name:)', pe_imports):
        match = re.search(r'DLL Name:\s*(\S+)', section)
        if not match or match.group(1).casefold() not in providers:
            continue
        runtime, listing = providers[match.group(1).casefold()]
        symbols = set(re.findall(r'^\s*[0-9a-fA-F]+\s+([_A-Za-z][_A-Za-z0-9@?]*)\s*$',
                                 section, re.M))
        if '__cxa_thread_atexit' in symbols and not re.search(
                r'^\s*\[\s*\d+\]\s+\+?\s*__cxa_thread_atexit\s*$', listing, re.M):
            raise RuntimeError(f'C++ ABI mismatch: {dll} imports __cxa_thread_atexit '
                               f'but {runtime} does not export it. Reports: {abi}')
    print('[cfaudio][win] C++ thread_local destructor cross-link and import check passed', flush=True)

    # Waf looks for literal windres and gas, not WINDRES/AS. Point both names
    # to the target-prefixed Windows COFF tools in Builds, without editing Waf.
    tools = work / 'toolchain-bin'
    tools.mkdir(exist_ok=True)
    for name, executable in (('windres', os.environ['WINDRES']),
                             ('gas', 'x86_64-w64-mingw32-as')):
        resolved = shutil.which(executable)
        if resolved is None:
            raise RuntimeError(f'[cfaudio][win] Missing MinGW Windows tool: {executable}')
        shortcut = tools / name
        if shortcut.is_symlink() or shortcut.is_file():
            shortcut.unlink()
        elif shortcut.exists():
            raise RuntimeError(f'Unexpected toolchain entry: {shortcut}')
        shortcut.symlink_to(resolved)
    os.environ['PATH'] = str(tools) + os.pathsep + os.environ['PATH']
    os.environ['AS'] = str(tools / 'gas')
    coff = write('assembler-smoke.s', '.text\n.globl ardour_coff_probe\n'
                 '.def ardour_coff_probe; .scl 2; .type 32; .endef\n'
                 'ardour_coff_probe:\n ret\n')
    run(os.environ['AS'], '-o', str(work / 'assembler-smoke.o'), str(coff))
    if 'file format pe-x86-64' not in read(objdump, '-f', str(work / 'assembler-smoke.o')):
        raise RuntimeError('[cfaudio][win] Assembler output is not Windows x64 COFF')
    print('[cfaudio][win] TARGET READY Windows COFF assembler', flush=True)

    # Header/CRT contract: compiler's setjmp.h precedes the dependencies;
    # winpthreads sees pid_t via sys/types.h in every C++ translation unit.
    link('pthread-header-probe', '#include <pthread.h>\n'
         'int main() { pid_t pid = 0; (void)pid; return 0; }\n', cxx,
         *cxxflags, extension='.cpp')
    setjmp = write('setjmp-crt-probe.c', '''#include <setjmp.h>
static jmp_buf state;
int main(void) { if (setjmp(state) == 0) longjmp(state, 1); return 0; }
''')
    run(cc, *cflags, '-H', '-c', str(setjmp), '-o', str(work / 'setjmp-crt-probe.o'),
        log=work / 'setjmp-crt-headers.log')
    run(cc, '-shared', str(work / 'setjmp-crt-probe.o'),
        '-o', str(work / 'setjmp-crt-probe.dll'), log=work / 'setjmp-crt-link.log')
    print('[cfaudio][win] TARGET READY pthread and compiler-owned setjmp/longjmp', flush=True)

    # Use the DLL import archives, not the incompatible static Readline
    # archives. Preserve quarantined archives under /sources rather than delete.
    bin_dir = prefix / 'bin'
    readline_program = '''#include <stdio.h>
#include <readline/readline.h>
#include <readline/history.h>
int main(void) {
    char *line = readline("cfaudio> ");
    rl_complete(0, 0);
    add_history("cfaudio");
    return read_history("nonexistent") + (line == 0);
}
'''
    for component in ('readline', 'history'):
        required(lib / f'lib{component}.dll.a', component + ' import archive')
        required(bin_dir / f'lib{component}8.dll', component + ' DLL')
    probe = write('readline-link-smoke.c', readline_program)
    run(cc, '-I' + str(prefix / 'include'), str(probe),
        str(lib / 'libreadline.dll.a'), str(lib / 'libhistory.dll.a'),
        '-o', str(work / 'readline-link-smoke.exe'),
        log=work / 'readline-link-smoke.log')
    quarantine = Path('/sources/Readline/incompatible-static')
    quarantine.mkdir(parents=True, exist_ok=True)
    isolated = work / 'ReadlineImport/lib'
    isolated.mkdir(parents=True, exist_ok=True)
    for component in ('readline', 'history'):
        static = lib / f'lib{component}.a'
        imported = lib / f'lib{component}.dll.a'
        if static.is_file() and static.read_bytes() != imported.read_bytes():
            preserved = quarantine / static.name
            if preserved.exists() and preserved.read_bytes() != static.read_bytes():
                raise RuntimeError(f'Different quarantined archive already exists: {preserved}')
            if not preserved.exists():
                shutil.move(str(static), str(preserved))
            else:
                static.unlink()  # safely preserved byte-identical copy
        if not static.is_file():
            shutil.copy2(imported, static)
        if static.read_bytes() != imported.read_bytes():
            raise RuntimeError(f'Readline DLL import archive mismatch: {static}')
        shutil.copy2(imported, isolated / static.name)
    run(cc, '-I' + str(prefix / 'include'), str(probe),
        '-L' + str(isolated), '-l:libreadline.a', '-l:libhistory.a',
        '-o', str(work / 'readline-link-smoke.exe'),
        log=work / 'readline-link-smoke.log')
    print('[cfaudio][win] TARGET LINK VERIFIED Readline/history DLL imports', flush=True)

    # Apply exactly the declared pkgconf link closure to the prepared prefix.
    # This does not install or build dependencies; it fails on missing archives.
    closures = json.loads(os.environ['CINTER_PKG_CONFIG_LINK_CLOSURE'])
    for module, libraries in closures.items():
        if not module.replace('-', '').replace('_', '').replace('.', '').isalnum():
            raise RuntimeError(f'Invalid pkg-config module: {module!r}')
        pc = required(lib / 'pkgconfig' / (module + '.pc'), module + ' pkg-config')
        lines = pc.read_text().splitlines(keepends=True)
        indexes = [i for i, line in enumerate(lines) if line.startswith('Libs:')]
        if len(indexes) != 1:
            raise RuntimeError(f'Expected exactly one Libs: line in {pc}')
        index = indexes[0]
        libs = lines[index].strip().split()
        for component in libraries:
            if not component.replace('_', '').replace('-', '').isalnum():
                raise RuntimeError(f'Invalid Windows-target library name: {component!r}')
            if not any((lib / ('lib' + component + suffix)).is_file()
                       for suffix in ('.dll.a', '.a')):
                raise RuntimeError(f'Windows-target import/static library absent: {component}')
            if '-l' + component not in libs:
                libs.append('-l' + component)
        updated = ' '.join(libs) + '\n'
        if lines[index] != updated:
            lines[index] = updated
            pc.write_text(''.join(lines))
        print(f'[cfaudio][win] TARGET LINK CLOSURE {module}: {" ".join(libs[1:])}', flush=True)

    fftw_archives = []
    for component in ('fftw3f_threads', 'fftw3f'):
        selected = next((path for suffix in ('.dll.a', '.a')
                         if (path := lib / ('lib' + component + suffix)).is_file()
                         and path.stat().st_size), None)
        if selected is None:
            raise RuntimeError(f'Missing Windows FFTW archive: {component}')
        fftw_archives.append(str(selected))
    link('fftw-threads-link', '#include <fftw3.h>\n'
         'int main(void) { fftwf_make_planner_thread_safe(); return 0; }\n', cc,
         '-idirafter', str(prefix / 'include'), *fftw_archives)
    print('[cfaudio][win] TARGET LINK VERIFIED FFTW planner thread safety', flush=True)

    boost = required(prefix / 'include/boost/version.hpp', 'Boost headers')
    macros = read(cxx, '-I' + str(prefix / 'include'), '-dM', '-E', '-x', 'c++', '-',
                  input='#include <boost/version.hpp>\n')
    version = re.search(r'^#define BOOST_VERSION\s+(\d+)\s*$', macros, re.M)
    if version is None or int(version.group(1)) < 106800:
        raise RuntimeError('Windows-target Boost >= 1.68 required')
    link('boost-smoke', '#include <boost/version.hpp>\n'
         'static_assert(BOOST_VERSION >= 106800, "Windows Boost 1.68 required");\n'
         'int main() { return 0; }\n', cxx,
         '-I' + str(prefix / 'include'), extension='.cpp')
    print(f'[cfaudio][win] TARGET READY Boost {version.group(1)}', flush=True)

    # Waf's -lintl/-lexchndl/-lmgwhelp checks must only see the isolated
    # Windows DLL import archives; a broad -L sysroot path shadows GCC's CRT.
    required(prefix / 'include/exchndl.h', 'DrMingw exchndl.h')
    for component in ('exchndl', 'mgwhelp', 'intl'):
        imported = required(lib / f'lib{component}.dll.a', component + ' DLL import archive')
        destination = isolated / imported.name
        if destination.is_symlink() or destination.is_file():
            destination.unlink()
        destination.symlink_to(imported)
    drm = '''#include <exchndl.h>
int main(void) { ExcHndlInit(); return 0; }
'''
    link('drmingw-smoke', drm, cc, '-idirafter', str(prefix / 'include'),
         str(lib / 'libexchndl.dll.a'), str(lib / 'libmgwhelp.dll.a'))
    compiler_mingwex = Path(read(cc, '-print-file-name=libmingwex.a').strip())
    required(compiler_mingwex, 'compiler-owned libmingwex.a')
    if not compiler_mingwex.is_absolute():
        raise RuntimeError('Target compiler cannot locate its own libmingwex.a')
    ldflags = ['-L' + str(compiler_mingwex.parent), '-L' + str(isolated),
               '-Wl,--undefined=_MINGW_INSTALL_DEBUG_MATHERR', str(compiler_mingwex)]
    os.environ['LDFLAGS'] = ' '.join(ldflags)
    # Deliberately put the competing dependency lib dir into the smoke test.
    link('matherr-crt-probe', 'extern void _MINGW_INSTALL_DEBUG_MATHERR(void);\n'
         'int main(void) { _MINGW_INSTALL_DEBUG_MATHERR(); return 0; }\n', cc,
         '-L' + str(lib), *ldflags)
    link('drmingw-waf-link-smoke', drm, cc,
         '-idirafter', str(prefix / 'include'), *ldflags,
         '-lintl', '-lexchndl', '-lmgwhelp')
    print('[cfaudio][win] TARGET LINK VERIFIED compiler CRT and DrMingw', flush=True)

    imported_jpeg = required(lib / 'libjpeg.dll.a', 'JPEG DLL import archive')
    destination = isolated / imported_jpeg.name
    if destination.is_symlink() or destination.is_file():
        destination.unlink()
    destination.symlink_to(imported_jpeg)
    link('jpeg-waf-link-smoke', '''#include <stdio.h>
#include <jpeglib.h>
int main(void) {
    struct jpeg_decompress_struct cinfo;
    jpeg_create_decompress(&cinfo);
    jpeg_destroy_decompress(&cinfo);
    return 0;
}
''', cc, *cflags, *ldflags, '-ljpeg')
    print('[cfaudio][win] TARGET LINK VERIFIED Waf-style JPEG', flush=True)

    # Reconfigure only when the original build requirements changed. Keep the
    # Waf cache, toolchain image and compiled dependency artifacts otherwise.
    signature_file = work / 'cfaudio-configure.signature'
    signature = os.environ['CONFIG_SIGNATURE']
    configure = json.loads(os.environ['CINTER_WAF_CONFIGURE_ARGS'])
    if not (source / 'build/c4che/_cache.py').is_file() or not signature_file.is_file() \
            or signature_file.read_text().strip() != signature:
        print('[cfaudio][win] CONFIGURE upstream Windows Waf', flush=True)
        try:
            run('./waf', 'configure', *configure, '--prefix=/output')
        except RuntimeError:
            log = source / 'build/config.log'
            if log.is_file():
                print('\n'.join(log.read_text(errors='replace').splitlines()[-140:]),
                      file=sys.stderr, flush=True)
            raise
        signature_file.write_text(signature + '\n')
    else:
        print('[cfaudio][win] REUSE Waf configure cache', flush=True)
    print('[cfaudio][win] BUILD upstream Windows Waf', flush=True)
    try:
        run('./waf', '-j', str(len(os.sched_getaffinity(0))))
    except RuntimeError:
        print('[cfaudio][win] Verbose Waf failure diagnostics:', file=sys.stderr, flush=True)
        subprocess.run(['./waf', '-v', '-j', '1'], cwd=source, check=False)
        raise
    # Ardour tools/x-win/compile.sh runs Waf's translation build after its
    # native cross-build. Reuse the upstream Waf operation, not a custom copy.
    print('[cfaudio][win] TRANSLATE upstream Windows Waf i18n', flush=True)
    run('./waf', 'i18n')
    print('[cfaudio][win] INSTALL upstream Windows Waf', flush=True)
    run('./waf', 'install')


def build_windows_ardour(built: Path, source: Path, recipe: dict) -> Path:
    # Run the original Waf cross-build using readable Python preflight steps.
    # All work stays under the existing versioned Builds tree.
    backend = _SUITE / 'apps/cinterforge-os/linux'
    sys.path.insert(0, str(backend))
    from managed_backend import ensure_backend
    engine, environment = ensure_backend(cinterforgeBuildsRootPath() / 'CreativeSuite/Built/cinterforge-os/linux/2026/Ubuntu')
    dependencies = built / 'Dependencies'
    output = built / 'Output'
    output.mkdir(parents=True, exist_ok=True)
    configure = recipe['configure']['argv']
    if configure[:2] != ['./waf', 'configure'] or '--dist-target=mingw' not in configure:
        raise RuntimeError('Ardour Windows recipe lacks upstream MinGW Waf configuration')
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()
    link_closure = recipe['windows_cross_dependencies'].get('pkg_config_link_closure', {})
    setup_receipt = dependencies / 'setup-ready.json'
    upstream_receipt = dependencies / 'ardour-upstream-stack-built.json'
    upstream_lock = dependencies / 'ardour-build-tools-lock.json'
    for required_receipt in (setup_receipt, upstream_receipt, upstream_lock):
        if not required_receipt.is_file():
            raise RuntimeError(f'Setup is missing approved upstream stack: {required_receipt}')
    signature = hashlib.sha256(json.dumps({
        'revision': revision, 'configure': configure,
        'approved_setup': hashlib.sha256(setup_receipt.read_bytes()).hexdigest(),
        'upstream_builder': hashlib.sha256(upstream_receipt.read_bytes()).hexdigest(),
        'upstream_lock': hashlib.sha256(upstream_lock.read_bytes()).hexdigest(),
        'upstream_waf_args': 'keepflags-compiler-crt-explicit-archive-v4',
        'toolchain': _MSVCRT_DOCKERFILE,
        'target_assembler': 'x86_64-w64-mingw32-as',
        'windows_link_and_pthread_headers': 'readline-dll-import-explicit-and-pid-t-v1',
        'windows_setjmp_header_policy': 'compiler-first-idirafter-and-link-probe-v1',
        'pkg_config_link_closure': link_closure,
    }, sort_keys=True).encode()).hexdigest()
    # tools/x-win/compile.sh is authoritative for Waf's configure contract.
    # Supply its args as an argv list (do not run its root-only apt/ccache steps).
    configure_args = [*configure[2:], '--keepflags',
                      '--also-include=/work/UpstreamWindows-x64/mingw64/include',
                      '--libdir=/output/lib']
    run_mounted_image(
        engine=engine, image=_MSVCRT_IMAGE, platform='linux/amd64',
        mounts=((dependencies, '/work', False), (source, '/source', False),
                (output, '/output', False),
                (built / 'DependencySources', '/sources', False),
                (Path(__file__).resolve().parent, '/cfaudio', True)),
        variables={
            'CONFIG_SIGNATURE': signature,
            'CINTER_PKG_CONFIG_LINK_CLOSURE': json.dumps(link_closure),
            'CINTER_WAF_CONFIGURE_ARGS': json.dumps(configure_args),
        },
        command=('python3', '-u', '/cfaudio/build.py', '--container-stage', 'build'),
        env=environment, label='Ardour Windows Waf configure/build/install',
    )
    binaries = list(output.rglob('*.exe'))
    if not binaries:
        raise RuntimeError(f'Ardour Windows Waf install produced no .exe in {output}')
    # PE architecture, CRT and recursive import checking happens in the
    # package stage inside the same GCC10 container. No host MinGW assumptions.
    print(f'[cfaudio][win] Built {len(binaries)} verified Windows x64 PE executables', flush=True)
    return output


# Runtime dependency closure and launcher relocation belong to packaging.

def package_pe_runtime_closure() -> None:
    """Bundle GCC10/MSVCRT Windows PE imports from the pinned private sysroot.

    Run inside the pinned Debian AMD64 cross-build image. This packages already-built
    Windows binaries; it never configures or recompiles Ardour. Missing non-system
    imports are fatal. Windows/Proton system DLLs are left to Proton.
    """

    from collections import deque
    from pathlib import Path
    import hashlib
    import re
    import shutil
    import subprocess

    OUTPUT = Path('/payload')
    SYSROOT = Path('/sysroot')
    DEST = OUTPUT / 'lib' / 'ardour9'  # PE loader searches the executable directory.
    CACHE = Path('/runtime_sources')
    # Win32/Proton-provided DLLs, not MinGW-supplied application dependencies.
    SYSTEM = frozenset('''advapi32 bcrypt cfgmgr32 comctl32 comdlg32 crypt32 d2d1 d3d9 d3d11
     d3d12 dbghelp dinput8 dnsapi dsound dwmapi dxgi gdi32 gdiplus hid iphlpapi
     kernel32 kernelbase mpr msvcrt ncrypt netapi32 ntdll ole32 oleaut32
     opengl32 powrprof psapi rpcrt4 secur32 setupapi shell32 shlwapi
     user32 userenv usp10 version win32u winhttp wininet winmm winsock ws2_32
     wtsapi32 imm32 msimg32 normaliz propsys shcore urlmon uxtheme winspool
     wintrust windowscodecs wlanapi wldap32 avrt mmdevapi ksuser oleacc
     wsock32 bcryptprimitives
     winusb xmllite usp10 mfplat mfreadwrite mf mfcore mfplay dxva2 dwrite
     dcomp d3dcompiler_47 api-ms-win-core-path-l1-1-0'''.split())

    def is_system(name: str) -> bool:
        base = name.casefold()
        if base.startswith(('api-ms-win-crt-', 'ext-ms-win-crt-')) or base == 'ucrtbase.dll':
            raise RuntimeError(f'UCRT import forbidden in GCC10/MSVCRT build: {name}')
        if base.startswith(('api-ms-win-', 'ext-ms-win-')):
            return True
        return base.endswith('.dll') and base[:-4] in SYSTEM

    def imports(path: Path) -> list[str]:
        result = subprocess.run(['x86_64-w64-mingw32-objdump', '-p', str(path)],
                                capture_output=True, text=True, check=False)
        if result.returncode:
            raise RuntimeError(f'Failed reading PE imports: {path}: {result.stderr.strip()}')
        return re.findall(r'^\s*DLL Name:\s*(\S+)\s*$', result.stdout, re.M | re.I)

    # Ardour is compiled by the pinned Debian GCC 10/POSIX/MSVCRT compiler.
    # Rolling MSYS2 libraries can have different C++ ABI exports even when
    # their runtime DLLs have identical filenames. Resolve the runtime from the
    # *compiler used for Build*, never from the dependencies' MSYS2 sysroot.
    CXX_RUNTIME = ('libstdc++-6.dll', 'libgcc_s_seh-1.dll',
                   'libwinpthread-1.dll', 'libssp-0.dll')
    REQUIRED_CXX_SYMBOL = '__cxa_thread_atexit'
    COMPILER = 'x86_64-w64-mingw32-g++'


    def compiler_runtime(name: str) -> Path:
        result = subprocess.run([COMPILER, f'-print-file-name={name}'],
                                capture_output=True, text=True, check=True)
        selected = Path(result.stdout.strip())
        if not selected.is_absolute() or not selected.is_file():
            raise RuntimeError(
                f'{COMPILER} cannot locate its own Windows runtime {name}: '
                f'{result.stdout.strip()!r}. Repair the pinned cross-toolchain in Setup.')
        signature = subprocess.check_output(['file', str(selected)], text=True)
        if 'PE32+' not in signature or 'x86-64' not in signature:
            raise RuntimeError(f'Compiler runtime is not a Windows x64 DLL: {selected}: {signature}')
        return selected


    def enforce_compiler_runtime() -> None:
        """Package the Windows runtime from the exact GCC10 compiler used for Build."""
        for name in CXX_RUNTIME:
            source = compiler_runtime(name)
            destination = DEST / name
            if not destination.is_file() or hashlib.sha256(destination.read_bytes()).digest() != hashlib.sha256(source.read_bytes()).digest():
                shutil.copy2(source, destination)
            digest = hashlib.sha256(destination.read_bytes()).hexdigest()
            print(f'[Windows runtime packaging] compiler runtime {name}: '
                  f'{source} sha256={digest}', flush=True)

        # Do not demand an export unless an installed PE actually imports it.
        # Some GCC builds implement thread-local destruction without exporting
        # this particular symbol from libstdc++.


    def imported_symbols(path: Path) -> dict[str, set[str]]:
        """Read PE import descriptors, keeping symbols associated with their DLL."""
        output = subprocess.check_output(
            ['x86_64-w64-mingw32-objdump', '-p', str(path)], text=True)
        result: dict[str, set[str]] = {}
        # Import descriptors begin at DLL Name; the next descriptor terminates
        # the current list. Restrict parsing to the import table (before exports).
        for block in re.split(r'(?=^\s*DLL Name:)', output, flags=re.M)[1:]:
            match = re.match(r'\s*DLL Name:\s*(\S+)', block)
            if not match:
                continue
            name = match.group(1).casefold()
            symbols = result.setdefault(name, set())
            for line in block.splitlines()[1:]:
                if re.match(r'^\s*DLL Name:', line) or 'The Export Tables' in line:
                    break
                found = re.match(r'^\s*[0-9a-fA-F]+\s+([^\s]+)\s*$', line)
                if found and not found.group(1).startswith('0x'):
                    symbols.add(found.group(1))
        return result


    def verify_compiler_abi(binaries: list[Path]) -> None:
        """Require the exact C++ export only when a packaged binary imports it."""
        owners = []
        for binary in binaries:
            if REQUIRED_CXX_SYMBOL in imported_symbols(binary).get('libstdc++-6.dll', set()):
                owners.append(binary)
        if not owners:
            print(f'[Windows runtime packaging] VERIFIED no PE imports '
                  f'{REQUIRED_CXX_SYMBOL} from libstdc++-6.dll; '
                  'no unnecessary export requirement', flush=True)
            return
        runtime = DEST / 'libstdc++-6.dll'
        output = subprocess.check_output(
            ['x86_64-w64-mingw32-objdump', '-p', str(runtime)], text=True)
        if not re.search(r'^\s*\[\s*\d+\]\s+\+?\s*' +
                         re.escape(REQUIRED_CXX_SYMBOL) + r'\s*$', output, re.M):
            names = ', '.join(str(p.relative_to(OUTPUT)) for p in owners)
            raise RuntimeError(
                f'Windows binaries {names} import {REQUIRED_CXX_SYMBOL} from '
                f'libstdc++-6.dll, but compiler-owned {runtime} does not export it. '
                'The compiler/runtime ABI is incompatible; repair Setup, '
                'do not substitute arbitrary DLLs.')
        print(f'[Windows runtime packaging] VERIFIED {REQUIRED_CXX_SYMBOL} '
              f'export satisfies {len(owners)} PE importer(s)', flush=True)


    def assemble_upstream_windows_payload() -> None:
        """Port the essential runtime assembly from Ardour tools/x-win/package.sh.

        Waf install alone does not package all loadable modules, translations,
        GTK2 engines or LV2 assets. Unlike the upstream shell script this
        copies only already-built files, does not run Waf uninstall, download
        optional proprietary plugins or edit the maintained source checkout.
        """
        root = Path('/source')
        if not (root / 'gtk2_ardour/wscript').is_file():
            raise RuntimeError('The pinned Ardour build checkout is not mounted')
        build = root / 'build'
        if not build.is_dir():
            raise RuntimeError('Missing pinned Ardour Waf build for packaging')

        def copy_file(source: Path, destination: Path) -> None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.is_file() and (source.stat().st_size == destination.stat().st_size
                    and hashlib.sha256(source.read_bytes()).digest()
                    == hashlib.sha256(destination.read_bytes()).digest()):
                return
            shutil.copy2(source, destination)

        def copy_glob(pattern: str, destination: Path, *, required: bool = False) -> int:
            matches = sorted(f for f in build.glob(pattern) if f.is_file())
            if required and not matches:
                raise RuntimeError(f'Ardour upstream Windows packaging input missing: build/{pattern}')
            for file in matches:
                copy_file(file, destination / file.name)
            return len(matches)

        # The Ardour core libraries and scan helpers are not all installed by
        # Waf into the self-contained Windows payload.
        core = (
            'libs/gtkmm2ext/gtkmm2ext-*.dll', 'libs/midi++2/midipp-*.dll',
            'libs/evoral/evoral-*.dll', 'libs/ardour/ardour-*.dll',
            'libs/temporal/temporal-*.dll', 'libs/aaf/aaf-*.dll',
            'libs/canvas/canvas-*.dll', 'libs/widgets/widgets-*.dll',
            'libs/waveview/waveview-*.dll', 'libs/pbd/pbd-*.dll',
            'libs/ctrl-interface/midi_surface/ardour*.dll',
            'libs/ctrl-interface/control_protocol/ardour*.dll',
            'libs/ptformat/ptformat-*.dll',
            'libs/audiographer/audiographer-*.dll',
        )
        for pattern in core:
            copy_glob(pattern, DEST)
        if not any(DEST.glob('ardour-*.dll')):
            raise RuntimeError('Ardour core DLLs absent from Waf install and compiled source')
        optional = (
            'libs/tk/ztk/ztk-*.dll', 'libs/tk/ydk/ydk-*.dll',
            'libs/tk/ytk/ytk-*.dll', 'libs/tk/ytkmm/ytkmm-*.dll',
            'libs/tk/ydkmm/ydkmm-*.dll', 'libs/tk/ztkmm/ztkmm-*.dll',
            'libs/tk/ydk-pixbuf/ydk-pixbuf-*.dll',
            'libs/tk/suil/suil-*.dll', 'libs/fst/ardour-vst-scanner.exe',
            'libs/fst/ardour-vst3-scanner.exe',
            'session_utils/*-*.exe', 'luasession/ardour*-lua.exe',
        )
        for pattern in optional:
            copy_glob(pattern, DEST)

        for role in ('surfaces', 'backends', 'panners'):
            count = copy_glob(f'libs/{role}/**/*.dll', DEST / role, required=True)
            print(f'[Windows packaging] {role}: {count} Ardour plugins', flush=True)

        # Required runtime plugins and LV2 plugin metadata (including TTL).
        for name, pattern in (
            ('libardourvampplugins.dll', 'libs/vamp-plugins/*ardourvampplugins*.dll'),
            ('libardourvamppyin.dll', 'libs/vamp-pyin/*ardourvamppyin*.dll'),
        ):
            matches = sorted(f for f in build.glob(pattern) if f.is_file())
            if not matches:
                raise RuntimeError(f'Ardour VAMP build output missing: {pattern}')
            if len(matches) != 1:
                raise RuntimeError(f'Ambiguous Ardour VAMP output: {matches}')
            copy_file(matches[0], DEST / 'vamp' / name)
        for directory in sorted((build / 'libs/LV2').glob('*.lv2')):
            if directory.is_dir():
                shutil.copytree(directory, DEST / 'LV2' / directory.name, dirs_exist_ok=True)
        lv2 = SYSROOT / 'lib/lv2'
        if lv2.is_dir():
            for directory in sorted(lv2.glob('*.lv2')):
                if directory.is_dir():
                    for ttl in directory.glob('*.ttl'):
                        copy_file(ttl, DEST / 'LV2' / directory.name / ttl.name)

        suil = sorted((build / 'libs/tk/suil').glob('suil_win_in_gtk2.dll'))
        if suil:
            copy_file(suil[0], DEST / 'suil' / suil[0].name)
        else:
            for module in sorted((SYSROOT / 'lib').glob('suil-*/*.dll')):
                copy_file(module, DEST / 'suil' / module.name)

        # GTK loadable modules are dynamically discovered and will not appear
        # in the main executable's static PE import graph.
        for parent in ('gtk-2.0', 'gdk-pixbuf-2.0', 'gio/modules'):
            folder = SYSROOT / 'lib' / parent
            if folder.is_dir():
                for module in sorted(folder.rglob('*.dll')):
                    copy_file(module, OUTPUT / 'lib' / parent / module.relative_to(folder))
        engine = build / 'libs/clearlooks-newer/clearlooks.dll'
        if engine.is_file():
            copy_file(engine, OUTPUT / 'lib/gtk-2.0/engines/libclearlooks.dll')
        for parent in ('share/locale', 'share/fontconfig'):
            folder = SYSROOT / parent
            if folder.is_dir():
                shutil.copytree(folder, OUTPUT / parent, dirs_exist_ok=True)

        # The original tools/x-win/package.sh also carries the application
        # data/configuration, license, branded icons and square cursor. Waf
        # normally installs the app data first; source/prefix copies fill gaps.
        version_share = OUTPUT / 'share/ardour9'
        for parent, child in ((SYSROOT / 'share/ardour9', version_share),
                              (SYSROOT / 'etc/ardour9', version_share)):
            if parent.is_dir():
                shutil.copytree(parent, child, dirs_exist_ok=True)
        if not version_share.is_dir():
            raise RuntimeError('Ardour Waf installation lacks share/ardour9 application data')
        license_file = root / 'COPYING'
        if license_file.is_file():
            copy_file(license_file, OUTPUT / 'share/COPYING')
        for name in ('Ardour.ico', 'ArdourBug.ico'):
            icon = root / 'gtk2_ardour/icons' / name
            if icon.is_file():
                copy_file(icon, OUTPUT / 'share' / name)
        cursor = root / 'gtk2_ardour/icons/cursor_square'
        if cursor.is_dir():
            for item in sorted(cursor.iterdir()):
                if item.is_file():
                    copy_file(item, version_share / 'icons' / item.name)
        print('[Windows packaging] Upstream Windows runtime modules/resources assembled', flush=True)

    def main() -> None:
        if not SYSROOT.is_dir() or not OUTPUT.is_dir():
            raise RuntimeError('Required existing sysroot or installed Output missing')
        assemble_upstream_windows_payload()
        binaries = sorted(p for p in OUTPUT.rglob('*') if p.is_file() and p.suffix.lower() in ('.dll', '.exe'))
        if not binaries or not any(p.suffix.lower() == '.exe' for p in binaries):
            raise RuntimeError('Windows Ardour payload contains no .exe')
        # Validate actual compiled PE architecture and CRT before packaging.
        for binary in binaries:
            format_info = subprocess.check_output(
                ['x86_64-w64-mingw32-objdump', '-f', str(binary)], text=True)
            if 'pei-x86-64' not in format_info:
                raise RuntimeError(f'Unexpected Windows PE architecture: {binary}')
            for dll in imports(binary):
                if dll.casefold() == 'ucrtbase.dll' or dll.casefold().startswith(
                        ('api-ms-win-crt-', 'ext-ms-win-crt-')):
                    raise RuntimeError(f'Forbidden UCRT import: {binary} -> {dll}')
        # Reject the observed PE startup stack overflow: broken atexit/_onexit
        # circular calls from a mixed CRT archive. Inspect *in* the GCC10 image.
        for binary in binaries:
            if binary.suffix.lower() != '.exe' or not binary.name.startswith('ardour-'):
                continue
            disassembly = subprocess.check_output(
                ['x86_64-w64-mingw32-objdump', '-d', str(binary)], text=True)
            functions = re.split(r'(?=^[0-9a-fA-F]+ <[^>]+>:$)',
                                 disassembly, flags=re.M)
            def calls(origin: str, destination: str) -> bool:
                return any(re.search(r'<'+re.escape(origin)+r'>:', block.splitlines()[0])
                           and re.search(r'\bcall\s+[^\n]*<'+re.escape(destination)+r'>', block)
                           for block in functions if block.strip())
            if calls('atexit', '_onexit') and calls('_onexit', 'atexit'):
                raise RuntimeError(f'Windows PE has recursive CRT atexit/_onexit calls: {binary}')
        # Search actual installed dependency prefix (plus toolchain's Windows DLLs).
        candidates: dict[str, Path] = {}
        for directory in (SYSROOT / 'bin', SYSROOT / 'lib',
                          Path('/usr/x86_64-w64-mingw32/bin')):
            if not directory.is_dir():
                continue
            for file in sorted(directory.rglob('*')):
                if file.is_file() and file.suffix.lower() == '.dll':
                    candidates.setdefault(file.name.casefold(), file)
        existing = {p.name.casefold(): p for p in binaries if p.parent == DEST}
        payload_candidates: dict[str, Path] = {}
        for binary in binaries:
            if binary.suffix.lower() == '.dll' and binary.parent != DEST:
                payload_candidates.setdefault(binary.name.casefold(), binary)
        queue = deque(binaries)
        visited: set[Path] = set()
        copied = 0
        missing: dict[str, set[str]] = {}
        DEST.mkdir(parents=True, exist_ok=True)
        enforce_compiler_runtime()
        # These GCC 10 runtime files now belong to the staged package; preexisting
        # PE imports must resolve to exactly these binaries, not to MINGW64 FFmpeg.
        for name in CXX_RUNTIME:
            destination = DEST / name
            existing[name.casefold()] = destination
            queue.append(destination)
        while queue:
            binary = queue.popleft()
            if binary in visited:
                continue
            visited.add(binary)
            for name in imports(binary):
                key = name.casefold()
                if is_system(name):
                    continue
                if key in existing:
                    continue
                source = payload_candidates.get(key) or candidates.get(key)
                if source is None:
                    missing.setdefault(name, set()).add(str(binary.relative_to(OUTPUT)))
                    continue
                destination = DEST / name
                if destination.exists():
                    raise RuntimeError(f'Unexpected destination collision: {destination}')
                shutil.copy2(source, destination)
                existing[key] = destination
                queue.append(destination)
                copied += 1
        verify_compiler_abi(sorted(visited))
        if missing:
            problems = '\n'.join(f'  {dll} imported by {", ".join(sorted(owners))}'
                                 for dll, owners in sorted(missing.items()))
            raise RuntimeError('Windows PE runtime import closure incomplete:\n' + problems)
        print(f'[Windows runtime packaging] {len(visited)} PE executables/DLLs checked; '
              f'{copied} Windows runtime DLLs bundled alongside Ardour executable', flush=True)


    main()

"""Host-side Windows Audio packaging.

Consume the already installed Waf Output and dependency sysroot. Run the
application-local PE import scanner in the existing cross-build container,
then adapt the upstream launcher. No source compilation or dependency setup.
"""
from pathlib import Path

# The host-side packaging entry point lives alongside the container-side
# PE import scanner so that Build only orchestrates compilation.
def package_windows_runtime(built: Path) -> None:
    """Assemble upstream Windows runtime assets and validate PE closure.

    No source compilation, dependency provisioning or wrapper rewriting.
    """
    import os
    import sys
    from pathlib import Path
    from container import run_mounted_image
    from setup import _MSVCRT_IMAGE
    suite_root = next(parent for parent in Path(__file__).resolve().parents if parent.name == 'creativeSuite')
    backend = suite_root / 'apps/cinterforge-os/linux'
    sys.path.insert(0, str(backend))
    from managed_backend import ensure_backend
    engine, environment = ensure_backend(cinterforgeBuildsRootPath() / 'CreativeSuite/Built/cinterforge-os/linux/2026/Ubuntu')
    dependencies = built / 'Dependencies' / 'UpstreamWindows-x64' / 'mingw64'
    output = built / 'Output'
    runtime_sources = built / 'DependencySources' / 'RuntimeClosure'
    runtime_sources.mkdir(parents=True, exist_ok=True)
    # Resolve PE imports from the actual GCC10/MSVCRT sysroot, never UCRT.
    # The module below checks real imported DLL names and fails on gaps.
    # Setup and Build run the checked-in functions from a read-only source mount.
    # No inspect/getsource, generated Python file, shell program or second packager.
    if not dependencies.is_dir() or not output.is_dir():
        raise RuntimeError('Windows Ardour dependency sysroot/installed Output missing: run full Build first')
    run_mounted_image(engine=engine, image=_MSVCRT_IMAGE, platform='linux/amd64',
                      mounts=((output, '/payload', False),
                              (built / 'Source', '/source', True),
                              (dependencies, '/sysroot', True),
                              (runtime_sources, '/runtime_sources', False),
                              (Path(__file__).resolve().parent, '/cfaudio', True)),
                      variables={}, command=('python3', '-u', '/cfaudio/build.py',
                                              '--container-stage', 'package'),
                      env=environment, label='Ardour Windows PE runtime dependency closure')
    # Leave upstream Waf's bin/ardour9 launcher unmodified. On Windows the PE
    # binary is the entry point; on CinterForgeOS the already staged guest
    # launch-cfaudio-windows.sh selects the installed PE and supplies Proton.
    # Rewriting bin/ardour9 into a second Proton launcher creates two fragile
    # launch pipelines and previously hard-coded an obsolete Ardour version.
    engines = sorted((output / 'lib' / 'ardour9').glob('ardour-[0-9]*.exe'))
    if len(engines) != 1:
        raise RuntimeError(f'Expected exactly one native Ardour Windows PE: {engines}')
    print(f'[cfaudio][win] Native Windows entry point: {engines[0]}', flush=True)





def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("win", "windows"), default="win")
    parser.add_argument("--prepare-dependencies", action="store_true")
    parser.add_argument("--package-only", action="store_true")
    args = parser.parse_args()
    version = os.environ.get("CINTER_INSTALL_VERSION") or os.environ.get("CINTER_APP_VERSION")
    if not version:
        raise RuntimeError("Cinter must resolve CINTER_INSTALL_VERSION before Ardour Windows setup/build")
    built = product_build_directory(cinterforgeBuildsRootPath(), "cfaudio", "win", version)
    declared = os.environ.get("CINTER_BUILT_ROOT")
    if declared and Path(declared).resolve() != built.resolve():
        raise RuntimeError(
            f"Cinter Windows Audio Build root mismatch: lifecycle={declared}; "
            f"canonical={built}. Refusing to use a different dependency prefix."
        )
    recipe = json.loads((Path(__file__).parent / "recipe.json").read_text())
    if recipe["identity"]["build_host"] != "Linux x86-64":
        raise RuntimeError("Windows recipe must target Linux-hosted cross compilation")
    with build_lock(built):
        if args.package_only:
            package_windows_runtime(built)
            return 0
        if args.prepare_dependencies:
            raise RuntimeError("Dependency preparation is a Setup operation; use ./Cinter setup cfaudio win Release")
        # The build is a consumer of Setup, never a dependency provisioner.
        # Check the receipt before even touching the maintained source checkout.
        receipt_path = built / "Dependencies" / "setup-ready.json"
        if not receipt_path.is_file():
            raise RuntimeError(
                "Windows dependency Setup has not completed for this version. "
                "Run ./Cinter setup cfaudio win Release before Build."
            )
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        expected_recipe_hash = hashlib.sha256(
            Path(__file__).with_name("recipe.json").read_bytes()
        ).hexdigest()
        if (
            receipt.get("schema") != "cfaudio.windows.setup.v1"
            or receipt.get("target") != "win-x86_64-msvcrt-gcc10"
            or receipt.get("build_directory") != str(built.resolve())
            or receipt.get("recipe_sha256") != expected_recipe_hash
        ):
            raise RuntimeError(
                "Windows dependency Setup receipt is stale or belongs to a "
                "different build/recipe. Run ./Cinter setup cfaudio win Release."
            )
        closure_path = built / "Dependencies" / "msvcrt-binary-closure.json"
        if not closure_path.is_file():
            raise RuntimeError("GCC 10/MSVCRT PE dependency closure has not been verified by Setup")
        closure = json.loads(closure_path.read_text(encoding="utf-8"))
        if not closure.get("binary_import_closure") or closure.get("unresolved"):
            raise RuntimeError("GCC 10/MSVCRT PE dependency closure is incomplete; refusing mixed-CRT linking")
        if receipt.get('closure_sha256') != hashlib.sha256(closure_path.read_bytes()).hexdigest():
            raise RuntimeError('Windows Setup dependency closure changed after verification; rerun Setup')
        source = prepare_source(built, recipe)
        # Setup owns the Windows dependency closure. Build consumes it unchanged.
        build_windows_ardour(built, source, recipe)
        package_windows_runtime(built)
    return 0


if __name__ == "__main__":
    try:
        if _CONTAINER_STAGE:
            if len(sys.argv) != 3 or sys.argv[2] not in ("build", "package"):
                raise RuntimeError("Unknown Windows Audio container stage")
            if sys.argv[2] == "build":
                _windows_container_build()
            else:
                package_pe_runtime_closure()
        else:
            raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as error:
        print(f"[cfaudio][win] ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
