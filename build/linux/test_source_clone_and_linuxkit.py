"""No-network CFOS source preflight and LinuxKit guest/host build regression.

Source tests deliberately do not claim a successful macOS VM boot or image.
"""
from __future__ import annotations

import contextlib
import csv
import importlib.util
import io
import struct
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
CFOS = HERE.parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


forks = load_module('cfos_fork_source_test', CFOS / 'scripts' / 'fork.py')
linuxkit = load_module('cfos_linuxkit_build_test', HERE / 'build_linux.py')


class SourcePreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='cfos-source-preflight-')
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name)
        self.csv = self.workspace / 'repositories.csv'
        with self.csv.open('w', newline='') as out:
            writer = csv.DictWriter(out, fieldnames=('Name', 'Git Repo URL', 'GitHub Fork Source URL', 'Source Acquisition'))
            writer.writeheader()
            writer.writerow({'Name': 'cfaudio', 'Git Repo URL': 'https://github.com/Ardour/ardour', 'Source Acquisition': 'source'})
            writer.writerow({'Name': 'cfimage', 'Git Repo URL': 'https://github.com/GNOME/gimp', 'Source Acquisition': 'source'})
        self.patches = [mock.patch.object(forks, 'CSV', self.csv), mock.patch.object(forks, 'SOURCE_ROOT', self.workspace)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def checkout(self, name: str) -> Path:
        path = self.workspace / name
        path.mkdir()
        subprocess.run(['git', 'init', '-q', str(path)], check=True)
        (path / 'keep.txt').write_text('must not change')
        return path

    def run_check(self):
        with mock.patch.object(sys, 'argv', ['fork.py', 'clone', 'all', '--dry-run']), \
             contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()) as errors:
            status = forks.main()
        return status, output.getvalue(), errors.getvalue()

    def test_clone_dry_run_fails_without_local_repos_and_does_not_clone(self):
        self.checkout('CinterForgeAudio')
        status, output, errors = self.run_check()
        self.assertEqual(status, 1)
        self.assertIn('MISSING cfimage', output)
        self.assertIn('./CFOS clone all', errors)
        self.assertFalse((self.workspace / 'CinterForgeImage').exists())
        self.assertEqual((self.workspace / 'CinterForgeAudio/keep.txt').read_text(), 'must not change')

    def test_clone_dry_run_passes_when_all_required_repos_are_git_checkouts(self):
        self.checkout('CinterForgeAudio')
        self.checkout('CinterForgeImage')
        status, output, errors = self.run_check()
        self.assertEqual(status, 0, errors)
        self.assertIn('present=2 missing_or_invalid=0', output)
        self.assertEqual(set(forks.load_repositories()), {'cfaudio', 'cfimage'})

    def test_non_git_and_symlink_paths_fail_without_overwrite(self):
        self.checkout('CinterForgeAudio')
        invalid = self.workspace / 'CinterForgeImage'
        invalid.mkdir()
        (invalid / 'keep.txt').write_text('valuable data')
        status, output, _ = self.run_check()
        self.assertEqual(status, 1)
        self.assertIn('not a Git checkout', output)
        self.assertEqual((invalid / 'keep.txt').read_text(), 'valuable data')

    def test_sibling_source_manifest_does_not_reintroduce_monolith(self):
        with mock.patch.object(forks, 'CSV', CFOS / 'scripts' / 'source_repositories.csv'):
            repositories = forks.load_repositories()
        names = {forks.destination_for(name, row[1]) for name, row in repositories.items()}
        self.assertTrue({'CinterForgeServer', 'CinterForgeWebApp', 'CinterForgePlugins'}.issubset(names))
        self.assertNotIn('CinterForge', names)
        self.assertNotIn('CinterForgeDesktop', names)  # private Desktop not needed to build CFOS
        self.assertEqual(len(repositories), 23)

    def test_complete_public_manifest_dry_run_accepts_only_real_git_checkouts(self):
        with tempfile.TemporaryDirectory(prefix='cfos-all-public-source-') as temp:
            workspace = Path(temp)
            with mock.patch.object(forks, 'CSV', CFOS / 'scripts' / 'source_repositories.csv'), \
                 mock.patch.object(forks, 'SOURCE_ROOT', workspace):
                for name, (_url, upstream, _mirror, acquisition) in forks.load_repositories().items():
                    if acquisition != 'source':
                        continue
                    target = workspace / forks.destination_for(name, upstream)
                    target.mkdir()
                    subprocess.run(['git', 'init', '-q', str(target)], check=True)
                status, output, errors = self.run_check()
                self.assertEqual(status, 0, errors)
                self.assertIn('present=23 missing_or_invalid=0', output)
                self.assertIn('PRESENT cinterforgeserver:', output)
                self.assertIn('PRESENT cinterforgewebapp:', output)
                self.assertIn('PRESENT cinterforgeplugins:', output)
                self.assertNotIn('PRESENT cinterforge:', output)

    def test_server_path_resolver_uses_desktop_and_sibling_builds(self):
        resolver = load_module('cinterforge_source_paths',
                               CFOS.parent / 'CinterForgeServer/cinter/io/CinterIO.py')
        with tempfile.TemporaryDirectory(prefix='cinter-layout-') as temp:
            root = Path(temp)
            desktop = root / 'CinterForgeDesktop'; desktop.mkdir(); (desktop / 'Cinter').touch()
            server = root / 'CinterForgeServer'; server.mkdir()
            plugins = root / 'CinterForgePlugins'; plugins.mkdir()
            webapp = root / 'CinterForgeWebApp'; webapp.mkdir()
            self.assertEqual(resolver.cinterforgeSourceRootPath(server), desktop)
            self.assertEqual(resolver.cinterforgeSourceRootPath(webapp), desktop)
            self.assertEqual(resolver.cinterforgeBuildsRootPath(server), root / 'Builds')
            self.assertEqual(resolver.cinterforgePluginsRootPath(server), plugins)
            self.assertEqual(resolver.cinterforgeWebAppRootPath(desktop), webapp)
            self.assertEqual(resolver.cinterforgeDesktopRootPath(server), desktop)

    def test_real_cfos_manifest_is_self_contained(self):
        with mock.patch.object(forks, 'CSV', CFOS / 'scripts' / 'source_repositories.csv'):
            names = forks.load_repositories()
        for key in ('cfaudio', 'cfcode', 'cfvoxel', 'fex', 'leveldb',
                    'cinterforgeos', 'cinterforgeserver', 'cinterforgewebapp', 'cinterforgeplugins', 'cinterforgelibkrun',
                    'cinterforgemacemu', 'cinterforgespirvcross'):
            self.assertIn(key, names)
        # The one external Kosmickrisp checkout is Mesa; its host adapter lives in CFOS.
        self.assertIn('cinterforgekosmickrispmesa', names)
        self.assertNotIn('cinterforgekosmickrisp', names)
        self.assertNotIn('cinterforge', names)  # removed monolithic checkout
        # Converter is a WebApp product, not a sibling Git checkout.
        self.assertNotIn('cfconvert', names)


class LinuxKitImageBuildTests(unittest.TestCase):
    def test_linuxkit_build_compiles_swift_launcher_after_guest_outputs(self):
        with tempfile.TemporaryDirectory(prefix='cfos-linuxkit-test-') as tmp:
            root = Path(tmp) / 'CinterForgeOS'
            root.mkdir()
            (root / 'CFOS').touch()
            destination = root.parent / 'Builds/Linux'
            destination.mkdir(parents=True)
            (destination / 'linuxkit.yml').write_text('init: []\n')
            (destination / 'CinterBDSVM.swift').write_text('// mock Swift source\n')
            commands = []

            def fake_run(argv, **kwargs):
                commands.append(tuple(argv))
                if argv[0] == 'linuxkit':
                    for name in ('cinter-bds-kernel', 'cinter-bds-initrd.img', 'cinter-bds-cmdline'):
                        (destination / name).write_bytes(b'test output')
                    self.assertFalse((destination / 'CinterBDSVM').exists())
                elif argv[0] == 'xcrun':
                    (destination / 'CinterBDSVM').write_bytes(b'test signed Swift launcher')
                else:
                    self.fail(f'unexpected subprocess {argv}')
                return subprocess.CompletedProcess(argv, 0)

            with mock.patch.object(sys, 'argv', ['build_linux.py', '--repo-root', str(root)]), \
                 mock.patch.object(linuxkit, 'stage_sources', return_value=destination), \
                 mock.patch.object(linuxkit, 'shared_source_fingerprint', return_value='source-test\n'), \
                 mock.patch.object(linuxkit, 'ensure_arm64_boot_kernel'), \
                 mock.patch.object(linuxkit, 'verify_shared_bootstrap'), \
                 mock.patch.object(linuxkit.platform, 'system', return_value='Darwin'), \
                 mock.patch.object(linuxkit.shutil, 'which', return_value='/fake/linuxkit'), \
                 mock.patch.object(linuxkit.subprocess, 'run', side_effect=fake_run), \
                 mock.patch.object(linuxkit, 'sign_launcher'):
                self.assertEqual(linuxkit.main(), 0)
            self.assertEqual([c[0] for c in commands], ['linuxkit', 'xcrun'])
            self.assertTrue((destination / 'CinterBDSVM').exists())
            self.assertTrue((destination / 'vm-launcher-source-fingerprint.txt').exists())


if __name__ == '__main__':
    unittest.main()

class LinuxKitBedrockAcceptanceTests(unittest.TestCase):
    """No-network CFOS Test ownership and isolated server acceptance."""

    def test_public_test_dispatches_real_bedrock_acceptance_not_verify(self):
        cli = CFOS / 'CFOS'
        result = subprocess.run([str(cli), 'test', 'cinterforge-os', 'linuxkit',
                                 'Release', '--dry-run'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('test_linuxkit.py', result.stdout)
        self.assertNotIn('--verify-only', result.stdout)
        self.assertNotIn('linuxkit build', result.stdout)
        retired = subprocess.run([str(cli), 'verify', 'cinterforge-os', 'linuxkit',
                                  'Release', '--dry-run'], capture_output=True, text=True)
        self.assertEqual(retired.returncode, 64)

    def test_linuxkit_test_uses_same_managed_backend_as_ubuntu(self):
        """An inherited legacy DOCKER_HOST must not leak into LinuxKit Test."""
        test = load_module('cfos_linuxkit_backend_contract', HERE / 'test_linuxkit.py')
        with tempfile.TemporaryDirectory(prefix='cfos-linuxkit-docker-') as temp:
            root = Path(temp)
            osrepo = root / 'CinterForgeOS'; osrepo.mkdir()
            (osrepo / 'CFOS').touch()
            server = root / 'CinterForgeServer'; server.mkdir()
            scripts = server / 'cinter/build_cinterforge/craftmaster'
            scripts.mkdir(parents=True)
            (scripts / 'build_craftmaster_server.py').touch()
            (scripts / 'validate_minecraft_server.py').touch()
            boot = root / 'Builds/Linux'; boot.mkdir(parents=True)
            for name in ('cinter-bds-kernel', 'cinter-bds-initrd.img',
                         'cinter-bds-cmdline', 'CinterBDSVM'):
                (boot / name).write_bytes(b'fixture')
            managed = {'DOCKER_HOST': 'unix:///cfos-managed/docker.sock',
                       'CFOS_COLIMA_PROFILE': 'c'}
            with mock.patch.object(test.platform, 'system', return_value='Darwin'), \
                 mock.patch.object(test.platform, 'machine', return_value='arm64'), \
                 mock.patch.object(test, 'verify_shared_bootstrap'), \
                 mock.patch.object(test, 'ensure_backend', return_value=('/managed/docker', managed)) as backend, \
                 mock.patch.object(test, '_execute_acceptance') as acceptance, \
                 mock.patch.dict('os.environ', {'DOCKER_HOST': 'unix:///stale/docker.sock'}):
                test.test_linuxkit(osrepo)
            backend.assert_called_once_with(test.source_directory(osrepo))
            self.assertEqual(acceptance.call_args.args[-2:], ('/managed/docker', managed))
            evidence = root / 'Builds/Logs/CFOS/linuxkit-bedrock-acceptance.json'
            self.assertEqual(__import__('json').loads(evidence.read_text())['status'], 'passed')

    def test_bedrock_probe_and_glibc_use_managed_docker_environment(self):
        """Every docker step, including cleanup, uses the selected CFOS socket."""
        test = load_module('cfos_linuxkit_managed_docker', HERE / 'test_linuxkit.py')
        with tempfile.TemporaryDirectory(prefix='cfos-linuxkit-runtime-') as temp:
            server = Path(temp) / 'Server'; server.mkdir()
            def write_elf(path: Path, machine: int) -> None:
                header = bytearray(20)
                header[:4] = b'\x7fELF'; header[4] = 2; header[5] = 1
                struct.pack_into('<H', header, 18, machine)
                path.write_bytes(header)
            write_elf(server / 'bedrock_server', 62)
            cinter = server / '.cinter'
            calls = []
            managed = {'DOCKER_HOST': 'unix:///cfos-managed/docker.sock'}
            def fake_run(command, **kwargs):
                calls.append((command, kwargs))
                self.assertEqual(command[0], '/managed/docker')
                self.assertIs(kwargs.get('env'), managed)
                if command[1] == 'run':
                    write_elf(cinter / 'cinter-nethernet-probe', 183)
                if command[1] == 'cp':
                    write_elf(cinter / 'glibc-x86_64/ld-linux-x86-64.so.2', 62)
                output = 'test-container-id\n' if command[1] == 'create' else (
                    '["ubuntu@sha256:fake-digest"]\n' if command[1] == 'image' else '')
                return subprocess.CompletedProcess(command, 0, output, '')
            with mock.patch.object(test, '_run', side_effect=fake_run):
                manifest = test._prepare_runtime(server, '/managed/docker', managed)
            self.assertEqual(manifest['arm64_probe_image'], test.PROBE_IMAGE)
            self.assertEqual(manifest['glibc_image_digests'], ['ubuntu@sha256:fake-digest'])
            self.assertEqual([c[0][1] for c in calls],
                             ['run', 'create', 'cp', 'rm', 'image'])
            self.assertTrue((cinter / 'acceptance-inputs.json').is_file())

    def test_test_provisions_isolated_official_server_then_runs_real_validator(self):
        test = load_module('cfos_linuxkit_acceptance_test', HERE / 'test_linuxkit.py')
        with tempfile.TemporaryDirectory(prefix='cfos-bedrock-contract-') as temp:
            root = Path(temp)
            osrepo = root / 'CinterForgeOS'; osrepo.mkdir()
            (osrepo / 'CFOS').touch()
            server_repo = root / 'CinterForgeServer'; server_repo.mkdir()
            build = server_repo / 'cinter/build_cinterforge/craftmaster'
            build.mkdir(parents=True)
            (build / 'build_craftmaster_server.py').touch()
            (build / 'validate_minecraft_server.py').touch()
            (root / 'Builds').mkdir()
            bootstrap = root / 'Builds/Linux'; bootstrap.mkdir()
            for name in ('cinter-bds-kernel','cinter-bds-initrd.img','cinter-bds-cmdline','CinterBDSVM'):
                (bootstrap / name).write_bytes(b'fixture')
            commands = []
            def fake_run(command, **kwargs):
                commands.append(command)
                if '--linuxkit-test-install' in command:
                    installed = Path(command[command.index('--linuxkit-test-install') + 1])
                    installed.mkdir()
                    (installed / 'bedrock_server').touch()
                return subprocess.CompletedProcess(command, 0)
            with mock.patch.object(test.platform, 'system', return_value='Darwin'), \
                 mock.patch.object(test.platform, 'machine', return_value='arm64'), \
                 mock.patch.object(test, 'verify_shared_bootstrap'), \
                 mock.patch.object(test, 'ensure_backend', return_value=('/usr/bin/docker', {'DOCKER_HOST': 'unix:///managed/docker.sock'})), \
                 mock.patch.object(test, '_run', side_effect=fake_run), \
                 mock.patch.object(test, '_prepare_runtime', return_value={'test':'runtime'}):
                test.test_linuxkit(osrepo)
            self.assertEqual(len(commands), 2)
            self.assertIn('--linuxkit-test-install', commands[0])
            self.assertIn('--cfos-linuxkit-test-server', commands[1])
            evidence = root / 'Builds/Logs/CFOS/linuxkit-bedrock-acceptance.json'
            self.assertEqual(__import__('json').loads(evidence.read_text())['status'], 'passed')
            self.assertFalse((root / 'Builds/Intermediates/CFOS/LinuxKit' / 'Server').exists())
            self.assertFalse((root / 'Builds/steamapps').exists())

    def test_failed_bedrock_acceptance_fails_test_and_retains_evidence(self):
        test = load_module('cfos_linuxkit_acceptance_failure_test', HERE / 'test_linuxkit.py')
        with tempfile.TemporaryDirectory(prefix='cfos-bedrock-failure-') as temp:
            root = Path(temp); osrepo=root/'CinterForgeOS'; osrepo.mkdir()
            (osrepo/'CFOS').touch()
            server_repo=root/'CinterForgeServer'; server_repo.mkdir()
            build=server_repo/'cinter/build_cinterforge/craftmaster'
            build.mkdir(parents=True)
            (build/'build_craftmaster_server.py').touch()
            (build/'validate_minecraft_server.py').touch()
            (root/'Builds').mkdir()
            bootstrap = root/'Builds/Linux'; bootstrap.mkdir()
            for name in ('cinter-bds-kernel','cinter-bds-initrd.img','cinter-bds-cmdline','CinterBDSVM'):
                (bootstrap/name).write_bytes(b'fixture')
            with mock.patch.object(test.platform, 'system', return_value='Darwin'), \
                 mock.patch.object(test.platform, 'machine', return_value='arm64'), \
                 mock.patch.object(test, 'verify_shared_bootstrap'), \
                 mock.patch.object(test, 'ensure_backend', return_value=('/usr/bin/docker', {'DOCKER_HOST': 'unix:///managed/docker.sock'})), \
                 mock.patch.object(test, '_execute_acceptance', side_effect=RuntimeError('Bedrock did not start')):
                with self.assertRaisesRegex(RuntimeError, 'Bedrock did not start'):
                    test.test_linuxkit(osrepo)
            evidence = __import__('json').loads((root/'Builds/Logs/CFOS/linuxkit-bedrock-acceptance.json').read_text())
            self.assertEqual(evidence['status'],'failed')
            self.assertIn('Bedrock did not start', evidence['error'])

    def test_update_data_does_not_install_or_run_bedrock(self):
        data = (CFOS.parent / 'CinterForgeServer/cinter/build_cinterforge/'
                'orchestration/update_data_pipeline.py').read_text()
        self.assertNotIn('build_craftmaster_server.py"', data[data.index('    if not args.skip_acquisition:'):])
        self.assertNotIn('"craftmaster-server-smoke-tests"', data)
