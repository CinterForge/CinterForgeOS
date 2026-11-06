#!/usr/bin/env python3
"""Smoke the installed macOS CinterForge 3D bundle with actual Blender operations."""
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import time


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'gui'
    if mode not in ('gui', 'headless') or sys.platform != 'darwin':
        raise RuntimeError(f'Unsupported Blender macOS smoke mode: {mode}')
    suite = Path(os.environ['CINTER_SCRIPTS_ROOT']).resolve()
    sys.path.insert(0, str(suite))
    from lifecycle import installation_directory
    root = installation_directory('cf3d', 'mac')
    version = os.environ.get('CINTER_INSTALL_VERSION') or os.environ.get('CINTER_APP_VERSION')
    if not version:
        versions = sorted(p.name for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
        if not versions:
            raise RuntimeError(f'No installed CinterForge 3D version in {root}')
        version = versions[-1]
    installed = root / version
    bundles = list(installed.glob('*.app'))
    if len(bundles) != 1 or bundles[0].name != 'CinterForge 3D.app':
        raise RuntimeError(f'Expected CinterForge 3D.app in {installed}; found {bundles}')
    bundle = bundles[0]
    with (bundle / 'Contents/Info.plist').open('rb') as stream:
        info = plistlib.load(stream)
    executable = bundle / 'Contents/MacOS' / info['CFBundleExecutable']
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise RuntimeError(f'Installed Blender executable missing: {executable}')
    results = installed / 'Tests/Results'
    results.mkdir(parents=True, exist_ok=True)
    blend = results / 'BlenderSmoke.blend'
    exported = results / 'BlenderSmoke.glb'
    script = ("import bpy; "
              "bpy.ops.wm.read_factory_settings(use_empty=True); "
              "bpy.ops.mesh.primitive_cube_add(); "
              f"bpy.ops.wm.save_as_mainfile(filepath={str(blend)!r}); "
              f"bpy.ops.export_scene.gltf(filepath={str(exported)!r}, export_format='GLB')")
    for artifact in (blend, exported):
        artifact.unlink(missing_ok=True)
    result = subprocess.run([str(executable), '--background', '--factory-startup',
                             '--python-expr', script], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, timeout=240)
    (results / 'SmokeTest.log').write_text(result.stdout)
    if result.returncode != 0 or not blend.is_file() or not exported.is_file():
        raise RuntimeError(f'Installed Blender document/export test failed ({result.returncode}); see {results / "SmokeTest.log"}')
    if blend.stat().st_size < 100 or exported.read_bytes()[:4] != b'glTF':
        raise RuntimeError('Blender output documents are invalid')
    if mode == 'gui':
        # A real GUI process must remain running; no coordinate clicks or fake success.
        process = subprocess.Popen([str(executable), '--factory-startup'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            time.sleep(6)
            if process.poll() is not None:
                raise RuntimeError(f'Installed Blender GUI exited prematurely: {process.returncode}')
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    (results / 'SmokeTest.json').write_text(json.dumps({
        'application': 'CinterForge 3D', 'platform': 'macOS', 'mode': mode,
        'installed_executable': str(executable), 'passed': True,
        'blend_bytes': blend.stat().st_size, 'glb_bytes': exported.stat().st_size,
    }, indent=2) + '\n')
    print(f'[cf3d][mac] INSTALLED SMOKE PASS ({mode}): {blend}, {exported}', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f'[cf3d][mac] INSTALLED SMOKE FAIL: {error}', file=sys.stderr)
        sys.exit(1)
