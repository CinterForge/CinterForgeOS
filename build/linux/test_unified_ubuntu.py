#!/usr/bin/env python3
"""Regression checks for the one CFOS Ubuntu AMD64 distribution contract.

These are source-level tests: macOS Docker/Colima, Wine, X11 and Plasma must
additionally pass the real installed-product CFOS Test on supported hardware.
"""
from __future__ import annotations

import json
import io
import tarfile
import os
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import ubuntu_adapter
import ubuntu_pipeline as pipeline
import ubuntu_products
import ubuntu_unforked


class UnifiedUbuntuTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='cfos-ubuntu-contract-')
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name)
        self.repo = self.workspace / 'CinterForgeOS'
        self.repo.mkdir()
        (self.repo / 'CFOS').touch()
        self.build = pipeline.built_path(self.repo)
        self.build.mkdir(parents=True)

    def test_guest_service_controller_is_readable_before_export_and_smoke(self):
        """The host may supply ubuntu_service.sh with root-only mode (0600)."""
        dockerfile = (pipeline.SOURCE / 'Dockerfile.ubuntu').read_text()
        smoke = (pipeline.SOURCE / 'headless_contract.sh').read_text()
        self.assertIn('COPY ubuntu_service.sh /usr/local/lib/cfos-ubuntu-service.sh', dockerfile)
        self.assertIn('chmod 0644 /usr/local/lib/cfos-ubuntu-service.sh', dockerfile)
        self.assertIn('runuser -u cinterforge -- test -r /usr/local/lib/cfos-ubuntu-service.sh', dockerfile)
        self.assertIn('runuser -u cinterforge -- /bin/bash -n /usr/local/lib/cfos-ubuntu-service.sh', dockerfile)
        self.assertIn('Guest-local service controller not readable by cinterforge', smoke)
        self.assertIn('/bin/bash -n /usr/local/lib/cfos-ubuntu-service.sh', smoke)
        self.assertIn('source /usr/local/lib/cfos-ubuntu-service.sh',
                      (pipeline.SOURCE / 'ubuntu_shell.sh').read_text())
        self.assertIn('source /usr/local/lib/cfos-ubuntu-service.sh',
                      (pipeline.SOURCE / 'ubuntu_desktop.sh').read_text())

    def test_wine_smoke_waits_for_its_isolated_server_before_cleanup(self):
        """No false failure from wineserver writing into a deleted scratch prefix."""
        source = (pipeline.SOURCE / 'headless_contract.sh').read_text()
        cleanup = source.split('# CFOS_WINE_CLEANUP_BEGIN', 1)[1].split(
            '# CFOS_WINE_CLEANUP_END', 1)[0]
        self.assertIn('timeout 60 wineserver -w', source)
        self.assertIn('wineserver -k', cleanup)
        self.assertNotIn('trap \'rm -rf', source)
        fake_bin = self.workspace / 'mock-wine-bin'
        fake_bin.mkdir()
        fake_wineserver = fake_bin / 'wineserver'
        fake_wineserver.write_text('''#!/bin/sh
printf '%s\n' "$1" >> "$CFOS_WINE_EVENTS"
case "$1" in
    -w) if [ -f "$WINEPREFIX/stuck" ]; then exit 1; fi
        while [ -f "$WINEPREFIX/running" ]; do sleep 0.02; done ;;
    -k) rm -f "$WINEPREFIX/running" "$WINEPREFIX/stuck" ;;
    *) exit 3 ;;
esac
''')
        fake_wineserver.chmod(0o755)
        for expected_rc in (0, 1):
            scratch = self.workspace / f'smoke-{expected_rc}'
            events = self.workspace / f'wine-events-{expected_rc}.txt'
            script = f"""#!/bin/sh
set -eu
tmp={str(scratch)!r}
mkdir -p "$tmp/wine-prefix"
export WINEPREFIX="$tmp/wine-prefix"
{cleanup}
: > "$WINEPREFIX/running"
(sleep 0.15; rm -f "$WINEPREFIX/running") &
{'false' if expected_rc else 'true'}
"""
            completed = subprocess.run(
                ['/bin/sh', '-ec', script], capture_output=True, text=True,
                env={**os.environ, 'PATH': f'{fake_bin}:' + os.environ['PATH'],
                     'CFOS_WINE_EVENTS': str(events)}, timeout=10)
            self.assertEqual(completed.returncode, expected_rc, completed.stderr)
            self.assertFalse(scratch.exists(), completed.stderr)
            self.assertIn('-w', events.read_text())
            self.assertNotIn('-k', events.read_text())
        # When an orphaned Wine service does not quiesce, the trap must stop
        # ONLY this scratch prefix, then still preserve the original error.
        scratch = self.workspace / 'smoke-stuck'
        events = self.workspace / 'wine-events-stuck.txt'
        script = f"""#!/bin/sh
set -eu
tmp={str(scratch)!r}
mkdir -p "$tmp/wine-prefix"
export WINEPREFIX="$tmp/wine-prefix"
{cleanup}
: > "$WINEPREFIX/stuck"
false
"""
        completed = subprocess.run(
            ['/bin/sh', '-ec', script], capture_output=True, text=True,
            env={**os.environ, 'PATH': f'{fake_bin}:' + os.environ['PATH'],
                 'CFOS_WINE_EVENTS': str(events)}, timeout=10)
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertFalse(scratch.exists())
        self.assertEqual(events.read_text().splitlines(), ['-w', '-k', '-w'])
        self.assertIn('stopping isolated smoke prefix', completed.stderr)

    def test_rejects_legacy_rootfs_without_cinterforge_account(self):
        archive = self.build / pipeline.ARCHIVE
        def make_archive(passwd: bytes):
            with tarfile.open(archive, 'w') as tar:
                for name, payload in (
                    ('./usr/bin/apt-get', b'#!/bin/sh\n'),
                    ('./etc/passwd', passwd),
                ):
                    member = tarfile.TarInfo(name)
                    member.size = len(payload)
                    tar.addfile(member, io.BytesIO(payload))
        make_archive(b'root:x:0:0:root:/root:/bin/bash\n')
        with self.assertRaisesRegex(RuntimeError, 'missing its non-root cinterforge account'):
            pipeline.validate_archive(archive)
        make_archive(b'root:x:0:0:root:/root:/bin/bash\n'
                     b'cinterforge:x:1001:1001::/home/cinterforge:/bin/bash\n')
        pipeline.validate_archive(archive)

    def test_stage_cannot_reuse_archive_after_failed_or_changed_build(self):
        archive = self.build / pipeline.ARCHIVE
        archive.write_bytes(b'fixture rootfs')
        product = ubuntu_products.product_root(self.repo) / 'manifest.json'
        product.parent.mkdir(parents=True)
        product.write_text('{"schema":"cinterforge.cfos.ubuntu-products.v1"}')
        with self.assertRaisesRegex(RuntimeError, 'Build has not completed successfully'):
            pipeline.verify_build(self.repo, self.build)
        with mock.patch.object(pipeline, 'validate_archive'):
            pipeline.certify_build(self.repo, self.build)
            pipeline.verify_build(self.repo, self.build)
            archive.write_bytes(b'changed rootfs after verified build')
            with self.assertRaisesRegex(RuntimeError, 'receipt is stale'):
                pipeline.verify_build(self.repo, self.build)
            archive.write_bytes(b'fixture rootfs')
            product.write_text('{"schema":"changed"}')
            with self.assertRaisesRegex(RuntimeError, 'products_sha256'):
                pipeline.verify_build(self.repo, self.build)
        (self.build / pipeline.BUILD_RECEIPT).unlink()
        with self.assertRaisesRegex(RuntimeError, 'Build has not completed successfully'):
            pipeline.stage(self.build, self.workspace / 'install', self.workspace / 'adapter')

    def test_durable_ubuntu_smoke_log_identifies_failed_command(self):
        log = self.workspace / 'Builds/Logs/CFOS/ubuntu-2026-headless-smoke.log'
        with self.assertRaisesRegex(RuntimeError, 'full trace'):
            pipeline.run_logged(
                ['/bin/sh', '-exc', 'printf "CFOS-MARKER\\n"; false'], log_path=log)
        recorded = log.read_text()
        self.assertIn('CFOS-MARKER', recorded)
        self.assertIn('+ false', recorded)
        self.assertIn('exit=1', recorded)
        receipt = json.loads((log.parent / 'ubuntu-last-failure.json').read_text())
        self.assertEqual(receipt['returncode'], 1)
        self.assertEqual(receipt['log'], str(log))

    def test_durable_ubuntu_smoke_log_pass_has_no_failure_receipt(self):
        log = self.workspace / 'Builds/Logs/CFOS/ubuntu-2026-desktop-smoke.log'
        pipeline.run_logged(['/bin/sh', '-ec', 'echo CFOS-DESKTOP-OK'], log_path=log)
        self.assertIn('CFOS-DESKTOP-OK', log.read_text())
        self.assertIn('exit=0', log.read_text())
        self.assertFalse((log.parent / 'ubuntu-last-failure.json').exists())

    def _test_receipt(self):
        archive = self.build / pipeline.ARCHIVE
        archive.write_bytes(b'synthetic test image used for digest contract only')
        product_receipt = ubuntu_products.product_root(self.repo) / 'manifest.json'
        product_receipt.parent.mkdir(parents=True, exist_ok=True)
        product_receipt.write_text(json.dumps({'schema': ubuntu_products.SCHEMA, 'products': {}}))
        recorded = {
            'schema': 'cinterforge.cfos.ubuntu-headless.v1',
            'status': 'passed',
            'contract': pipeline.HEADLESS_CONTRACT,
            'archive_sha256': pipeline.sha256(archive),
            'dockerfile_sha256': pipeline.sha256(pipeline.SOURCE / 'Dockerfile.ubuntu'),
            'headless_smoke_sha256': pipeline.sha256(pipeline.SOURCE / 'headless_contract.sh'),
            'desktop_script_sha256': pipeline.sha256(pipeline.SOURCE / 'ubuntu_desktop.sh'),
            'shell_script_sha256': pipeline.sha256(pipeline.SOURCE / 'ubuntu_shell.sh'),
            'service_script_sha256': pipeline.sha256(pipeline.SOURCE / 'ubuntu_service.sh'),
            'linuxkit_bootstrap_sha256': 'synthetic-linuxkit-checksum',
            'products_sha256': pipeline.sha256(product_receipt),
            'upstream_suite_sha256': pipeline.sha256(pipeline.SOURCE / 'ubuntu_unforked.py'),
        }
        (self.build / pipeline.READY_FILE).write_text(json.dumps(recorded))
        return recorded

    def test_public_linuxkit_test_and_launch_own_separate_test_and_consumer_paths(self):
        cli = pipeline.SOURCE.parents[1] / 'CFOS'
        base = [str(cli)]
        for args, expected in (
            (['test', 'cinterforge-os', 'linuxkit', 'Release', '--dry-run'],
             ('test_linuxkit.py',)),
            (['launch', 'cinterforge-os', 'linuxkit', 'Release', '--server',
              '/tmp/bedrock-test-only', '--dry-run'],
             ('--verify-only', 'CINTER_BDS_HOST_DIR=', 'CinterBDSVM')),
        ):
            result = subprocess.run(base + args, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            for marker in expected:
                self.assertIn(marker, result.stdout)
            self.assertNotIn('linuxkit build', result.stdout)
        for bad in (
            ['launch', 'cinterforge-os', 'linux', 'Release', '--server', '/tmp'],
            ['stage', 'cinterforge-os', 'linuxkit', 'Release'],
            ['launch', 'cinterforge-os', 'linuxkit', 'Release', '--dry-run'],
        ):
            result = subprocess.run(base + bad, capture_output=True, text=True)
            self.assertEqual(result.returncode, 64, (bad, result.stderr))

    def test_no_prerequisite_without_completed_linux_test(self):
        with self.assertRaisesRegex(RuntimeError, 'Build → Stage → Test'):
            pipeline.verify_prerequisite(self.repo)

    def test_stale_image_or_desktop_script_invalidates_receipt(self):
        self._test_receipt()
        with mock.patch.object(pipeline, 'shared_bootstrap_digest', return_value='synthetic-linuxkit-checksum'):
            pipeline.verify_prerequisite(self.repo)
        (self.build / pipeline.ARCHIVE).write_bytes(b'changed configuration')
        with mock.patch.object(pipeline, 'shared_bootstrap_digest', return_value='synthetic-linuxkit-checksum'):
            with self.assertRaisesRegex(RuntimeError, 'stale or incompatible'):
                pipeline.verify_prerequisite(self.repo)

    def test_linuxkit_source_is_produced_by_cfos_before_ubuntu(self):
        pipeline_source = (pipeline.SOURCE / 'ubuntu_pipeline.py').read_text()
        self.assertIn("str(SOURCE / 'build_linux.py')", pipeline_source)
        self.assertLess(pipeline_source.index("str(SOURCE / 'build_linux.py')"),
                        pipeline_source.index('build(built)', pipeline_source.index('def main()')))
        # LinuxKit and Bedrock test are CFOS-owned, never supplied by the
        # legacy Creative Suite Debian guest runner or update-data.
        test_source = (pipeline.SOURCE / 'test_linuxkit.py').read_text()
        self.assertIn('verify_shared_bootstrap', test_source)
        self.assertIn('--linuxkit-test-install', test_source)
        self.assertIn('--cfos-linuxkit-test-server', test_source)

    def test_ubuntu_gegl_header_package_matches_noble_archive(self):
        # Ubuntu 24.04 packages GEGL headers as libgegl-dev, not the
        # historically assumed libgegl-0.4-dev (an apt exit-100 blocker).
        dockerfile = (pipeline.SOURCE / 'Dockerfile.ubuntu').read_text()
        self.assertIn('libgegl-dev', dockerfile)
        self.assertNotIn('libgegl-0.4-dev', dockerfile)

    def test_linuxkit_is_server_only_and_does_not_provision_debian(self):
        import yaml
        linuxkit = pipeline.SOURCE / 'linuxkit.yml'
        manifest = yaml.safe_load(linuxkit.read_text())
        services = [service['name'] for service in manifest['services']]
        self.assertEqual(services, ['cinter-bds'])
        self.assertTrue(any(entry['path'] == 'usr/local/bin/cinter-bds-guest.sh'
                            for entry in manifest['files']))
        self.assertFalse(any('creative-suite' in entry['path']
                             for entry in manifest['files']))
        for service in manifest['services']:
            self.assertNotIn('cinter.mode=creative-suite', repr(service))
            self.assertNotIn('DebianAMD64', repr(service))
        linux_build = (pipeline.SOURCE / 'build_linux.py').read_text()
        self.assertNotIn('prepare_debian', linux_build)
        self.assertNotIn('render_linuxkit', linux_build)
        self.assertFalse((pipeline.SOURCE / 'prepare_debian.py').exists())
        swift = (pipeline.SOURCE / 'CinterBDSVM.swift').read_text()
        self.assertIn('VZLinuxRosettaDirectoryShare()', swift)
        self.assertIn('CINTER_VM_MODE server-appliance', swift)
        self.assertNotIn('CINTER_LINUX_SYSTEM_DIR', swift)
        self.assertNotIn('DebianAMD64', swift)

    def test_linuxkit_base_images_are_pinned_resolvable_arm64_images(self):
        """The staged YAML must not pass unresolved @pkg paths to LinuxKit."""
        import yaml
        source = pipeline.SOURCE / 'linuxkit.yml'
        config = yaml.safe_load(source.read_text())
        expected = {
            'linuxkit/init:v1.3.0-arm64',
            'linuxkit/runc:v1.3.0-arm64',
            'linuxkit/containerd:v1.3.0-arm64',
            'linuxkit/ca-certificates:v1.3.0-arm64',
            'linuxkit/dhcpcd:v1.3.0-arm64',
        }
        actual = set(config['init']) | {
            item['image'] for item in config['onboot'] if item['name'] == 'dhcpcd'
        }
        self.assertEqual(actual, expected)
        for section in (config['init'], [config['kernel']['image']],
                        [item['image'] for item in config['onboot']],
                        [item['image'] for item in config['services']]):
            for reference in section:
                self.assertNotIn('@pkg:', reference)
                self.assertNotIn('dummy://', reference)
                self.assertNotIn('linuxkit-source/', reference)
                self.assertTrue(':' in reference or '@sha256:' in reference, reference)

    def test_linuxkit_staging_rejects_unexpanded_local_packages(self):
        import build_linux
        with tempfile.TemporaryDirectory(prefix='cfos-linuxkit-yaml-') as directory:
            config = Path(directory) / 'linuxkit.yml'
            config.write_text("init:\n- '@pkg:./linuxkit-source/pkg/init'\n")
            with self.assertRaisesRegex(RuntimeError, 'Unresolved LinuxKit package reference'):
                build_linux.validate_linuxkit_images(config)
            config.write_text("init:\n- linuxkit/init:v1.3.0-arm64\n")
            build_linux.validate_linuxkit_images(config)

    def test_public_linuxkit_build_is_a_separate_command(self):
        cli = pipeline.SOURCE.parents[1] / 'CFOS'
        process = subprocess.run(
            [str(cli), 'build', 'cinterforge-os', 'linuxkit', 'Release', '--dry-run'],
            capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn('build_linux.py', process.stdout)
        self.assertNotIn('--verify-only', process.stdout)
        self.assertNotIn('ubuntu_pipeline.py', process.stdout)

    def test_ubuntu_steamapps_is_persistent_and_matches_steamos_guest_path(self):
        archive = self.workspace / 'Installed/Ubuntu/Distro' / pipeline.ARCHIVE
        archive.parent.mkdir(parents=True)
        archive.write_bytes(b'fixture rootfs')
        volume = ubuntu_adapter.steamapps_volume(archive, create=True)
        self.assertEqual(volume, self.workspace / 'Installed/Ubuntu/steamapps')
        self.assertTrue((volume / 'common/CinterForge/Applications/Linux').is_dir())
        self.assertTrue((volume / 'common/CinterForge/Applications/Windows').is_dir())
        self.assertEqual((volume / 'common/CinterForge/Applications/Test').stat().st_mode & 0o7777, 0o1777)
        self.assertTrue((volume / 'common/CinterForge/Applications/DesktopEntries/share/applications').is_dir())
        self.assertIn('target=/run/steamapps', ubuntu_adapter.steamapps_mount_args(archive)[1])
        test_app = volume / 'common/CinterForge/Applications/Linux/example/2026/Hello.txt'
        test_app.parent.mkdir(parents=True)
        test_app.write_text('must survive distro updates')
        archive.write_bytes(b'new rebuilt rootfs')
        self.assertEqual(ubuntu_adapter.steamapps_volume(archive), volume)
        self.assertEqual(test_app.read_text(), 'must survive distro updates')
        steamos_mount = (pipeline.SOURCE.parents[1] /
                         'guest/layer/usr/lib/systemd/system/run-steamapps.mount').read_text()
        self.assertIn('Where=/run/steamapps', steamos_mount)
        dockerfile = (pipeline.SOURCE / 'Dockerfile.ubuntu').read_text()
        self.assertIn('ENV CINTER_STEAMAPPS=/run/steamapps', dockerfile)
        self.assertIn('ENV CINTER_APPLICATIONS_ROOT=/run/steamapps/common/CinterForge/Applications', dockerfile)

    def test_ubuntu_steamapps_rejects_unowned_volume(self):
        archive = self.workspace / 'Installed/Ubuntu/Distro' / pipeline.ARCHIVE
        archive.parent.mkdir(parents=True)
        with self.assertRaisesRegex(RuntimeError, 'stage CFOS Ubuntu first'):
            ubuntu_adapter.steamapps_volume(archive)
        volume = ubuntu_adapter.steamapps_volume(archive, create=True)
        (volume / 'CFOS-Volume.json').write_text(json.dumps({'schema':'someone-else'}))
        with self.assertRaisesRegex(RuntimeError, 'volume identity'):
            ubuntu_adapter.steamapps_volume(archive)

    def test_new_ubuntu_account_never_assumes_uid_1000_is_free(self):
        dockerfile = (pipeline.SOURCE / 'Dockerfile.ubuntu').read_text()
        self.assertIn('useradd --create-home --shell /bin/bash cinterforge', dockerfile)
        self.assertNotIn('useradd --uid 1000', dockerfile)
        self.assertIn('test "$(id -u cinterforge)" -ge 1000', dockerfile)

    def test_shipping_image_covers_shared_linux_windows_toolchains(self):
        dockerfile = (pipeline.SOURCE / 'Dockerfile.ubuntu').read_text()
        smoke = (pipeline.SOURCE / 'headless_contract.sh').read_text()
        for required in ('qt6-base-dev', 'libgtk-3-dev', 'libvulkan-dev',
                         'g++-mingw-w64-i686-posix', 'gcc-mingw-w64-x86-64-posix'):
            self.assertIn(required, dockerfile)
        for required in ('linux-cxx-check', 'windows-cxx-check.exe',
                         'windows-x86-check.exe', 'wine "$tmp/windows-check.exe"',
                         'gtk+-3.0:libgtk-3-dev', 'gtk4:libgtk-4-dev',
                         'Qt6Core:qt6-base-dev', 'gstreamer-1.0:libgstreamer1.0-dev'):
            self.assertIn(required, smoke)

    def test_gstreamer_unwind_sdk_isolated_from_llvm_and_link_smoked(self):
        dockerfile = (pipeline.SOURCE / 'Dockerfile.ubuntu').read_text()
        smoke = (pipeline.SOURCE / 'headless_contract.sh').read_text()
        # Ubuntu's LLVM dev package virtually provides libunwind-dev without
        # GNU libunwind.pc. Keep LLVM while adding genuine GNU SDK metadata.
        for token in ('apt-get install -y --no-install-recommends libunwind8',
                      'apt-get download libunwind-dev libunwind8',
                      'dpkg-deb -x "$1" "$sdk"',
                      '/opt/cinterforge/SDKs/GNUlibunwind',
                      'pkg-config --exists libunwind gstreamer-1.0',
                      'libc++-dev libc++abi-dev', 'GNUlibunwind/SDK-Receipt.txt'):
            self.assertIn(token, dockerfile)
        self.assertNotIn('apt-get install -y libunwind-dev', dockerfile)
        for token in ('pkg-config --exists libunwind',
                      'pkg-config --cflags --libs gstreamer-1.0',
                      'CFOS-UNWIND-OK', 'CFOS-GSTREAMER-OK'):
            self.assertIn(token, smoke)
        self.assertIn('libunwind.pc', dockerfile)

    def test_pkg_config_failure_identifies_missing_sdk_and_never_silently_passes(self):
        smoke = (pipeline.SOURCE / 'headless_contract.sh').read_text()
        script = smoke.split('# CFOS_PKG_CONFIG_BEGIN', 1)[1].split('# CFOS_PKG_CONFIG_END', 1)[0]
        fake_bin = self.workspace / 'fake-bin'
        fake_bin.mkdir()
        mock_pkg_config = fake_bin / 'pkg-config'
        mock_pkg_config.write_text("""#!/bin/sh
if [ "$1" = --exists ] && [ "$2" = gtk4 ]; then exit 1; fi
if [ "$1" = --print-errors ] && [ "$3" = gtk4 ]; then
    echo 'Package gtk4 was not found' >&2
    exit 1
fi
exit 0
""")
        mock_pkg_config.chmod(0o755)
        result = subprocess.run(['/bin/sh', '-ec', script], capture_output=True, text=True,
                                env={**os.environ, 'PATH': f'{fake_bin}:' + os.environ['PATH']})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('missing pkg-config module: gtk4', result.stderr)
        self.assertIn('libgtk-4-dev', result.stderr)
        self.assertIn('Package gtk4 was not found', result.stderr)

    def test_workstation_compilers_sdks_are_present_and_version_verified(self):
        dockerfile = (pipeline.SOURCE / 'Dockerfile.ubuntu').read_text()
        smoke = (pipeline.SOURCE / 'headless_contract.sh').read_text()
        for package in ('gcc-10 g++-10', 'clang-18 clang-tools-18',
                        'llvm-18-tools', 'libgtk-4-dev', 'libmlt-dev',
                        'libopenimageio-dev', 'git-lfs', 'mingw-w64-i686-dev',
                        'qt6-webengine-dev', 'qtbase5-dev',
                        'libkf5archive-dev', 'libembree-dev', 'mono-devel'):
            self.assertIn(package, dockerfile)
        for tool in ('gcc-10 -dumpfullversion', 'g++-10 -dumpfullversion',
                     'clang-cl --version', 'lld-link --version',
                     'rustc --version', 'xwin --version', 'node --version',
                     'gcc10-cxx-check', 'rust_check.rs', 'CFOS-NODE-OK'):
            self.assertIn(tool, smoke)
        self.assertIn('--default-toolchain 1.98.0', dockerfile)
        self.assertIn('cargo install xwin --version 0.10.0 --locked', dockerfile)
        self.assertIn('CFOS_NODE_SHA256=', dockerfile)
        self.assertNotIn('xwin --accept-license', dockerfile)

    def test_superpowered_workstation_toolchains_are_os_level(self):
        dockerfile = (pipeline.SOURCE / 'Dockerfile.ubuntu').read_text()
        for fragment in (
            'gcc-11 g++-11 gcc-12 g++-12 gcc-14 g++-14',
            'clang-17 clang-19 clang-tools-19',
            'flang-18', 'mold binutils-gold gdb-multiarch',
            'gcc-aarch64-linux-gnu g++-aarch64-linux-gnu',
            'gcc-arm-linux-gnueabihf g++-arm-linux-gnueabihf',
            'gcc-mingw-w64-x86-64-win32 g++-mingw-w64-x86-64-win32',
            'gcc-mingw-w64-i686-win32 g++-mingw-w64-i686-win32',
            'musl-tools emscripten binaryen wabt',
            'openjdk-17-jdk-headless openjdk-21-jdk-headless maven ant gradle',
            'cython3 pybind11-dev python3-pybind11 pipx',
            'libwebkit2gtk-4.1-dev libjavascriptcoregtk-4.1-dev',
            'qt6-websockets-dev qt6-wayland-dev qt6-shadertools-dev',
        ):
            self.assertIn(fragment, dockerfile)
        self.assertIn('rustup component add rustfmt clippy rust-analyzer', dockerfile)
        for target in ('aarch64-unknown-linux-gnu', 'x86_64-unknown-linux-musl',
                       'x86_64-pc-windows-gnu', 'i686-pc-windows-gnu',
                       'x86_64-pc-windows-msvc', 'wasm32-unknown-unknown'):
            self.assertIn(target, dockerfile)
        for pin in ('typescript@5.9.3', 'node-gyp@11.4.2',
                    'pnpm@10.16.1', 'yarn@1.22.22', 'esbuild@0.25.12'):
            self.assertIn(pin, dockerfile)
        self.assertIn('Ubuntu-Toolchain-Packages.tsv', dockerfile)
        self.assertIn('Ubuntu-NonAPT-Toolchains.txt', dockerfile)
        self.assertIn('ENV PATH="/opt/node/bin:${PATH}"', dockerfile)
        self.assertIn('CFOS_GRADLE8_VERSION=8.14.6', dockerfile)
        self.assertIn('CFOS_GRADLE9_VERSION=9.8.2', dockerfile)
        self.assertIn('sha256sum --check --status', dockerfile)

    def test_installed_superpowered_workstation_smokes_real_compiles(self):
        smoke = (pipeline.SOURCE / 'headless_contract.sh').read_text()
        for token in ('"$tmp/mold-cxx-check"', '"$tmp/gold-cxx-check"',
                      '"$tmp/clang19-cxx-check"', 'arm64-cxx-check.o',
                      'armhf-cxx-check.o', 'wat2wasm', 'wasm-opt',
                      'rustup target list --installed', 'cargo build --quiet --offline',
                      'x86_64-pc-windows-gnu/debug/cargo-check.exe',
                      '"$tmp/check.js"', 'CFOS-TS-OK', 'CFOS-JAVA-OK',
                      'CFOS-GO-OK', 'go-windows-check.exe',
                      'Ubuntu-Toolchain-Packages.tsv'):
            self.assertIn(token, smoke)
        self.assertNotIn('apt-get install', smoke)
        self.assertNotIn('rustup target add', smoke)
        self.assertNotRegex(smoke, r'(?m)^\s*npm install\b')

    def test_microsoft_linux_tooling_is_pinned_and_smoked(self):
        dockerfile = (pipeline.SOURCE / 'Dockerfile.ubuntu').read_text()
        smoke = (pipeline.SOURCE / 'headless_contract.sh').read_text()
        for token in ('dotnet-sdk-8.0 dotnet-sdk-10.0',
                      'CFOS_POWERSHELL_VERSION=7.6.6',
                      'CFOS_POWERSHELL_SHA256=9585f38ab5a026c3fc0995486e26e12050777960fef47a22dca98b577c5d27a7',
                      'sha256sum --check --status', 'dotnet msbuild -version',
                      'dotnet nuget --help'):
            self.assertIn(token, dockerfile)
        for token in ('pwsh -NoLogo -NoProfile -NonInteractive',
                      'CFOS-POWERSHELL-OK', "dotnet --list-sdks",
                      'dotnet nuget --help', 'dotnet msbuild',
                      '<TargetFramework>net10.0</TargetFramework>',
                      'CFOS-DOTNET-OK'):
            self.assertIn(token, smoke)
        self.assertNotIn('packages-microsoft-prod.deb', dockerfile)
        self.assertNotIn('apt-get install -y msbuild', dockerfile)

    def test_initial_ardour_inspection_dependencies_are_in_os(self):
        dockerfile = (pipeline.SOURCE / 'Dockerfile.ubuntu').read_text()
        smoke = (pipeline.SOURCE / 'headless_contract.sh').read_text()
        # Earlier generic layers already provide the remaining packages.
        for package in ('libwebsockets-dev', 'libcppunit-dev',
                        'libgtkmm-2.4-dev', 'vamp-plugin-sdk',
                        'lv2-dev', 'libsratom-dev', 'intltool', 'itstool',
                        'libboost-system-dev'):
            self.assertIn(package, dockerfile)
        self.assertIn('Missing inspected Ardour build prerequisite', smoke)

    def test_shipping_steam_sdk_supports_both_gl_platforms(self):
        dockerfile = (pipeline.SOURCE / 'Dockerfile.ubuntu').read_text()
        smoke = (pipeline.SOURCE / 'headless_contract.sh').read_text()
        self.assertIn('ARG CFOS_SDL3_VERSION=3.4.18', dockerfile)
        self.assertIn('ARG CFOS_SDL3_SHA256=9c75cf16330322c217dedd2e0609f1124f1b54b8633e763467b4684d0f4334a3', dockerfile)
        self.assertIn('sha256sum --check --status', dockerfile)
        self.assertIn('-DCMAKE_SYSTEM_NAME=Windows', dockerfile)
        self.assertIn('-DCMAKE_C_COMPILER=x86_64-w64-mingw32-gcc-posix', dockerfile)
        self.assertIn('libavahi-compat-libdnssd-dev', dockerfile)
        self.assertIn('libprotobuf-dev protobuf-compiler', dockerfile)
        self.assertIn('pkg-config --modversion sdl3', smoke)
        self.assertIn('sdl3-windows-check.exe', smoke)
        self.assertIn('wine "$tmp/sdl3-windows-check.exe"', smoke)
        self.assertIn('CFOS-SDL3-OK', smoke)

    def test_imported_rootfs_restores_toolchain_environment(self):
        changes = ubuntu_adapter.IMAGE_IMPORT_CHANGES
        for expected in ('ENV RUSTUP_HOME=/opt/rustup', 'ENV CARGO_HOME=/opt/cargo',
                         'ENV CINTER_STEAMAPPS=/run/steamapps',
                         'ENV CINTER_APPLICATIONS_ROOT=/run/steamapps/common/CinterForge/Applications',
                         'WORKDIR /opt/cinterforge'):
            self.assertIn(expected, changes)
        path = next(x for x in changes if x.startswith('ENV PATH='))
        for component in ('/opt/cargo/bin', '/opt/xwin/bin', '/opt/node/bin'):
            self.assertIn(component, path)
        # The build-side Test and the shipping adapter import must agree.
        self.assertIn('from ubuntu_adapter import IMAGE_IMPORT_CHANGES',
                      (pipeline.SOURCE / 'ubuntu_pipeline.py').read_text())

    def test_installed_adapter_imports_image_with_the_runtime_contract(self):
        archive = self.workspace / 'Installed/Ubuntu-24.04-amd64-rootfs.tar'
        archive.parent.mkdir(parents=True)
        archive.write_bytes(b'fixture rootfs')
        calls = []

        def run(args, **kwargs):
            calls.append(args)
            if args[2] == 'inspect' and sum('inspect' in c for c in calls) == 1:
                return subprocess.CompletedProcess(args, 1, '', 'not present')
            if args[2] == 'import':
                return subprocess.CompletedProcess(args, 0, 'sha256:abc123\n', '')
            return subprocess.CompletedProcess(args, 0, 'sha256:abc123 linux/amd64\n', '')

        with mock.patch.object(ubuntu_adapter.subprocess, 'run', side_effect=run):
            tag = ubuntu_adapter.load_installed_image('docker', {}, archive)
        self.assertTrue(tag.startswith('cinterforge-ubuntu-installed:'))
        imported = next(args for args in calls if args[2] == 'import')
        for change in ubuntu_adapter.IMAGE_IMPORT_CHANGES:
            self.assertIn(change, imported)
        self.assertIn('linux/amd64', imported)

    def test_swift_launcher_offers_ubuntu_steam_and_both(self):
        source = (pipeline.SOURCE.parents[1] / 'source/Swift Source/Core/AppBundle.swift').read_text()
        entry = (pipeline.SOURCE.parents[1] / 'source/Swift Source/Core/main.swift').read_text()
        self.assertIn('choice.addButton(withTitle: "Ubuntu Desktop")', source)
        self.assertIn('choice.addButton(withTitle: "Both")', source)
        self.assertIn('CFOS_DESKTOP_URL=', source)
        self.assertIn('Ubuntu/ubuntu_adapter.py', source)
        self.assertIn('case .ubuntu: UbuntuDesktopWindow.runUbuntuOnly()', entry)
        self.assertIn('case .both:', entry)

    def test_mac_launcher_finds_homebrew_tools_with_short_finder_path(self):
        import managed_backend
        with mock.patch.dict(os.environ, {'PATH': '/usr/bin:/bin'}, clear=True), \
             mock.patch('managed_backend.shutil.which', return_value=None), \
             mock.patch('managed_backend.Path.is_file', return_value=True), \
             mock.patch('managed_backend.os.access', return_value=True):
            self.assertEqual(managed_backend.host_executable('docker'), '/opt/homebrew/bin/docker')
            self.assertEqual(managed_backend.host_executable('colima'), '/opt/homebrew/bin/colima')

    def test_public_verify_is_retired_and_test_is_canonical(self):
        cli = pipeline.SOURCE.parents[1] / 'CFOS'
        result = subprocess.run(['bash', str(cli), 'test', 'cinterforge-os',
                                 'linux', 'Release', '--dry-run'],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('ubuntu_pipeline.py test', result.stdout)
        bad = subprocess.run(['bash', str(cli), 'verify', 'cinterforge-os',
                              'linuxkit', 'Release', '--dry-run'],
                             text=True, capture_output=True)
        self.assertEqual(bad.returncode, 64)

    def test_bundle_uses_exact_shipped_adapter_without_duplication(self):
        self._test_receipt()
        product = self.workspace / 'Products/CinterForge OS.app'
        (product / 'Contents').mkdir(parents=True)
        (product / 'Contents/Info.plist').touch()
        with mock.patch.object(pipeline, 'shared_bootstrap_digest', return_value='synthetic-linuxkit-checksum'):
            pipeline.bundle_adapter(self.repo, product)
        for name in ('ubuntu_adapter.py', 'managed_backend.py', 'headless_contract.sh'):
            self.assertEqual((product / 'Contents/Resources/Ubuntu' / name).read_bytes(),
                             (pipeline.SOURCE / name).read_bytes())
        self.assertFalse((product / 'Contents/Resources/CFOSUbuntu').exists())

    def test_session_console_is_cfos_owned_bundled_and_shared_with_webapp(self):
        import importlib.util
        import shutil
        script = pipeline.SOURCE.parents[1] / 'scripts/session_console.py'
        spec = importlib.util.spec_from_file_location('cfos_session_console', script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        authoring = pipeline.SOURCE.parents[1] / 'resources/html/session-console'
        source = self.repo / 'resources/html/session-console'
        source.mkdir(parents=True)
        for name in module.FILES:
            shutil.copy2(authoring / name, source / name)
        app = self.workspace / 'Built/CinterForge OS.app'
        (app / 'Contents').mkdir(parents=True)
        (app / 'Contents/Info.plist').touch()
        product = module.publish(self.repo, app)
        receipt = module.validate(product)
        bundled = app / 'Contents/Resources/SessionConsole'
        self.assertEqual(module.validate(bundled), receipt)
        webapp = self.workspace / 'CinterForgeWebApp'
        (webapp / 'static/Code/IFPlayer').mkdir(parents=True)
        (webapp / 'static/Code/IFPlayer/ifplayer.html').touch()
        consumer = module.sync_web(self.repo, webapp)
        self.assertEqual(module.validate(consumer), receipt)
        # A changed CFOS source must not silently leave its consumer behind.
        (source / 'session-console.css').write_text('altered')
        with self.assertRaisesRegex(RuntimeError, 'stale'):
            module.sync_web(self.repo, webapp)
        (source / 'session-console.css').write_text((authoring / 'session-console.css').read_text())
        # A damaged bundle is rejected even if file names still exist.
        (bundled / 'session-console.js').write_text('corrupt')
        with self.assertRaisesRegex(RuntimeError, 'stale'):
            module.validate(bundled)
        build_script = (self.repo / 'build/build.sh')
        actual_script = pipeline.SOURCE.parents[1] / 'build/build.sh'
        text = actual_script.read_text()
        self.assertLess(text.index('session_console.py" build'), text.index('ubuntu_pipeline.py" bundle'))
        swift = (pipeline.SOURCE.parents[1] / 'source/Swift Source/Core/Console.swift').read_text()
        self.assertIn('final class SessionConsoleWebView', swift)
        self.assertIn('WKScriptMessageHandler', swift)

    def test_stage_writes_optional_backend_hint_outside_portable_archive(self):
        source = self.workspace / 'Builds/CreativeSuite/Built/cinterforge-os/linux/2026/Ubuntu'
        source.mkdir(parents=True, exist_ok=True)
        destination = self.workspace / 'SteamApps/Ubuntu/Distro'
        destination.mkdir(parents=True)
        adapter = self.workspace / 'SteamApps/Ubuntu/adapter'
        with mock.patch.object(pipeline.sys, 'platform', 'darwin'):
            pipeline.stage_adapter(adapter, destination, source)
        self.assertEqual(json.loads((destination / 'CFOS-backend.json').read_text()), {
            'schema': 'cinterforge.cfos.host-backend.v1',
            'builds_root': str(self.workspace / 'Builds'),
        })
        self.assertTrue((adapter / 'ubuntu_adapter.py').is_file())

    def test_installed_adapter_prefers_recorded_managed_builds(self):
        builds = self.workspace / 'Builds'
        (builds / 'CreativeSuite/CFOS').mkdir(parents=True)
        installed = self.workspace / 'SteamApps/Ubuntu/Distro'
        installed.mkdir(parents=True)
        archive = installed / pipeline.ARCHIVE
        archive.touch()
        (installed / 'CFOS-backend.json').write_text(json.dumps({
            'schema': 'cinterforge.cfos.host-backend.v1',
            'builds_root': str(builds),
        }))
        import managed_backend
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
            managed_backend, 'ensure_backend', return_value=('docker', {'DOCKER_HOST': 'test'})
        ) as ensure:
            self.assertEqual(ubuntu_adapter.backend(archive)[0], 'docker')
            ensure.assert_called_once_with(builds / 'CreativeSuite/Built/cinterforge-os/linux/Source')


class UpstreamUbuntuTests(unittest.TestCase):
    def test_complete_upstream_distribution_catalog_is_installed_in_image(self):
        dockerfile = (pipeline.SOURCE / 'Dockerfile.ubuntu').read_text()
        for name, package, binary in ubuntu_unforked.UPSTREAM_LINUX:
            self.assertIn(package, dockerfile, name)
            self.assertTrue(binary)
        self.assertIn('apt-get install -y --no-install-recommends', dockerfile)
        self.assertIn('pending-publisher-install-and-gui-acceptance', (pipeline.SOURCE/'ubuntu_unforked.py').read_text())
        self.assertIn('gimp krita inkscape scribus blender kdenlive ardour', dockerfile)

    def test_ubuntu_test_runs_upstream_from_installed_image_before_ready(self):
        source = (pipeline.SOURCE / 'ubuntu_pipeline.py').read_text()
        self.assertLess(source.index('ubuntu_unforked.test(repo,'),
                        source.index('certify_tested_install(repo,', source.index('def main()')))
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / 'CinterForgeOS'
            repo.mkdir()
            (repo / 'CFOS').touch()
            archive = Path(tmp) / 'Ubuntu/Distro' / pipeline.ARCHIVE
            archive.parent.mkdir(parents=True)
            archive.write_bytes(b'fixture')
            from ubuntu_adapter import steamapps_volume
            steamapps_volume(archive, create=True)
            with mock.patch.object(ubuntu_unforked, 'docker_test_commands', return_value=[['docker', 'run']]), \
                 mock.patch.object(ubuntu_unforked.subprocess, 'run', side_effect=subprocess.CalledProcessError(7, 'docker')):
                with self.assertRaisesRegex(RuntimeError, 'acceptance failed'):
                    ubuntu_unforked.test(repo, archive, 'docker', {}, 'installed')
            report = json.loads((Path(tmp) / 'Builds/Logs/CFOS/ubuntu-upstream-linux.json').read_text())
            self.assertEqual(report['status'], 'failed')

    def test_upstream_test_uses_guest_distro_and_no_host_ports(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'Ubuntu/Distro' / pipeline.ARCHIVE
            archive.parent.mkdir(parents=True)
            archive.write_bytes(b'fixture')
            from ubuntu_adapter import steamapps_volume
            steamapps_volume(archive, create=True)
            commands = ubuntu_unforked.docker_test_commands('installed-amd64', 'docker', archive)
            self.assertEqual(len(commands), len(ubuntu_unforked.UPSTREAM_LINUX))
            for spec, command in zip(ubuntu_unforked.UPSTREAM_LINUX, commands):
                self.assertIn('linux/amd64', command)
                self.assertIn('--network', command)
                self.assertIn('none', command)
                self.assertNotIn('--publish', command)
                self.assertNotIn('-p', command)
                self.assertIn('installed-amd64', command)
                self.assertIn('xvfb-run', ' '.join(command))
                self.assertEqual(command[-4:], [spec[0], spec[1], spec[2], *ubuntu_unforked.version_args(spec[2])])

    def test_upstream_ffmpeg_uses_valid_publisher_version_flag(self):
        self.assertEqual(ubuntu_unforked.version_args('ffmpeg'), ('-version',))
        self.assertEqual(ubuntu_unforked.version_args('kdenlive'), ('--version',))
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'Ubuntu/Distro' / pipeline.ARCHIVE
            archive.parent.mkdir(parents=True)
            archive.write_bytes(b'fixture')
            from ubuntu_adapter import steamapps_volume
            steamapps_volume(archive, create=True)
            cmd = ubuntu_unforked.docker_test_commands('amd64', 'docker', archive)[-1]
            self.assertEqual(cmd[-4:], ['FFmpeg', 'ffmpeg', 'ffmpeg', '-version'])

    def test_swift_ubuntu_view_uses_one_window_and_closes_guest_only(self):
        source_root = pipeline.SOURCE.parents[1]
        swift = (source_root / 'source/Swift Source/Core/AppBundle.swift').read_text()
        window = (source_root / 'source/Swift Source/Interface/Window.swift').read_text()
        menu = (source_root / 'source/Swift Source/Core/main.swift').read_text()
        for expected in ('UbuntuDesktopSession', 'CFOS_DESKTOP_URL=',
                         'URLRequest(url: url)', 'onHome', 'stdin.fileHandleForWriting.closeFile()',
                         'window.contentView = host'):
            self.assertIn(expected, swift)
        self.assertIn('func dismissUbuntuDesktop() -> Bool', window)
        self.assertIn('view.addSubview(session.container', window)
        self.assertIn('if !wc.dismissUbuntuDesktop()', menu)
        self.assertIn('UbuntuDesktopWindow.runUbuntuOnly()', menu)
        self.assertIn('if !options.headless {', menu)
        self.assertIn('cfos-ubuntu-desktop', (pipeline.SOURCE / 'ubuntu_adapter.py').read_text())

    def test_guest_staging_makes_live_steam_os_commands_executable(self):
        # Exercise the real macOS Stage implementation in a disposable guest
        # volume. A source checkout may have 0644 script modes; exec in SteamOS
        # must work regardless of those source-side permissions.
        import shlex
        import shutil
        if not shutil.which('rsync'):
            self.skipTest('rsync is not installed in this source-test container')
        root = pipeline.SOURCE.parents[1]
        workspace = Path(tempfile.mkdtemp(prefix='cfos-stage-steam-'))
        self.addCleanup(shutil.rmtree, workspace)
        volume = workspace / 'SteamVolume'
        command = f"""set -e
CFOS_ROOT={shlex.quote(str(root))}
CFOS_WORKSPACE={shlex.quote(str(workspace))}
CFOS_DRY_RUN=0
cfos_steamapps() {{ printf '%s\n' {shlex.quote(str(volume))}; }}
source {shlex.quote(str(root/'build/stage.sh'))}
cfos_stage_guest_commands
"""
        subprocess.run(['bash', '-ec', command], check=True, stdout=subprocess.PIPE, text=True)
        staging = workspace / 'Builds/CreativeSuite/Staged/mac/cinterforge-os/External/Current'
        installed = volume / 'common/CinterForge/Applications/Test'
        files = (
            'dispatch/cinter-commands.sh', 'run/run-linux-amd64.sh',
            'wine/cinterforge-wine.sh', 'inspect/inspect-unforked-windows.py',
            'installl/install-unforked.sh', 'launch/launch-unforked.sh',
        )
        self.assertTrue((staging/'CinterDispatcher').is_file())
        self.assertTrue(os.access(installed/'Dispatcher/Cinter', os.X_OK))
        for name in files:
            self.assertTrue(os.access(installed/'Scripts'/name, os.X_OK), name)
            self.assertEqual((root/'dispatch/external'/name).read_bytes(),
                             (installed/'Scripts'/name).read_bytes())

    def test_steamos_uses_real_guest_installer_stage_and_fex_entry(self):
        root = pipeline.SOURCE.parents[1]
        stage = (root / 'build/stage.sh').read_text()
        self.assertIn('cfos_stage_guest_commands', stage)
        self.assertNotIn('stage_external.py', stage.split('cfos_stage() {', 1)[1])
        installer = (root / 'dispatch/external/installl/install-unforked.sh').read_text()
        launcher = (root / 'dispatch/external/launch/launch-unforked.sh').read_text()
        dispatcher = (root / 'dispatch/external/dispatch/cinter-commands.sh').read_text()
        self.assertIn('Unforked/Linux', installer)
        self.assertIn('extractall', installer)
        self.assertIn('APPIMAGE_EXTRACT_AND_RUN', launcher)
        self.assertIn('run/run-linux-amd64.sh', launcher)
        self.assertIn('wine/cinterforge-wine.sh', launcher)
        self.assertIn('unforked)', dispatcher)
        self.assertIn('cfos_stage_guest_commands', stage)

    def test_windows_installers_are_not_confused_with_successful_wine_launches(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            result = ubuntu_unforked.verify_windows_sources(base)
            self.assertIn('Kdenlive', result)
            self.assertEqual(result['Kdenlive'], [])
            installer = base / 'CreativeSuite/UnforkedInstallers/Windows/Kdenlive'
            installer.mkdir(parents=True)
            (installer / 'Kdenlive.exe').write_bytes(b'MZ not an installed executable')
            self.assertEqual(ubuntu_unforked.verify_windows_sources(base)['Kdenlive'], ['Kdenlive.exe'])


if __name__ == '__main__':
    unittest.main()
