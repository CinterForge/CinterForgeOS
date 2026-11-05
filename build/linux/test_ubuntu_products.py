#!/usr/bin/env python3
"""Offline contracts for CFOS's Ubuntu Server/WebApp product assembly.

Real Docker AMD64, Wine, KDE and HTTP 20380 checks must additionally pass on
HOPE-DEV. These tests never install or modify a real Steam library.
"""
from __future__ import annotations
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import ubuntu_products as products
import ubuntu_adapter
import ubuntu_pipeline


class UbuntuProductsTests(unittest.TestCase):
    def setUp(self):
        self.context = tempfile.TemporaryDirectory(prefix='cfos-products-test-')
        self.addCleanup(self.context.cleanup)
        self.workspace = Path(self.context.name)
        self.cfos = self.workspace / 'CinterForgeOS'
        self.cfos.mkdir()
        (self.cfos / 'CFOS').write_text('')
        for name in ('CinterForgeDesktop', 'CinterForgeServer', 'CinterForgeWebApp',
                     'CinterForgePlugins'):
            (self.workspace / name).mkdir()
        (self.workspace / 'CinterForgeDesktop/Cinter').write_text('')
        (self.workspace / 'CinterForgeServer/cinter/io').mkdir(parents=True)
        (self.workspace / 'CinterForgeServer/cinter/io/CinterIO.py').write_text('')
        (self.workspace / 'CinterForgeWebApp/runtime').mkdir()
        (self.workspace / 'CinterForgeWebApp/runtime/package.py').write_text('')
        self.built = products.expected_artifacts(self.cfos)
        (self.built['server'] / 'cinter/io').mkdir(parents=True)
        (self.built['server'] / 'cinter/io/CinterIO.py').write_text('server-code')
        self.built['webapp'].mkdir(parents=True)
        (self.built['webapp'] / 'index.html').write_text('<html>CinterForge</html>')
        (self.built['webapp'] / 'site.css').write_text('body {}')
        self.built['runtime'].mkdir(parents=True)
        (self.built['runtime'] / 'CinterRuntime.py').write_text('runtime-code')
        (self.built['runtime'] / 'manifest.json').write_text('{}')
        manifest = products._manifest_path(self.cfos)
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps({
            'schema': products.SCHEMA,
            'products': {name: products.inventory(path) for name, path in self.built.items()}
        }))
        self.archive = self.workspace / 'Ubuntu/Distro' / ubuntu_pipeline.ARCHIVE
        self.archive.parent.mkdir(parents=True)
        self.archive.write_bytes(b'fake-archive-for-physical-volume-path-test-only')

    def test_requires_real_sibling_repos(self):
        self.assertEqual(set(products.repos(self.cfos)),
                         {'CinterForgeOS', 'CinterForgeDesktop', 'CinterForgeServer',
                          'CinterForgeWebApp', 'CinterForgePlugins'})
        (self.workspace / 'CinterForgeWebApp/runtime/package.py').unlink()
        with self.assertRaisesRegex(RuntimeError, 'packager'):
            products.repos(self.cfos)

    def test_install_retains_separate_server_and_webapp_identities(self):
        products.install(self.cfos, self.archive)
        volume = ubuntu_adapter.steamapps_volume(self.archive)
        apps = volume / 'common/CinterForge/Applications'
        self.assertEqual((apps / 'Server/CinterForgeServer/2026/Source/cinter/io/CinterIO.py').read_text(),
                         'server-code')
        self.assertEqual((apps / 'WebApps/CinterForgeWebApp/2026/websites/dev/index.html').read_text(),
                         '<html>CinterForge</html>')
        self.assertTrue((apps / 'WebApps/CinterForgeWebApp/2026/Server/CinterRuntime.py').is_file())
        self.assertFalse((apps / 'WebApps/CinterForgeWebApp/2026/cinter').exists())
        self.assertTrue((apps / 'CFOS-Ubuntu-Products.json').is_file())
        products.verify_installed(self.cfos, self.archive)
        # A provisioned per-arch Python environment is a SIBLING of Source,
        # not a mutation of its original signed/hashed source inventory.
        python = apps / 'Server/CinterForgeServer/2026/Python'
        python.mkdir()
        (python / 'python-version.txt').write_text('Ubuntu target')
        products.verify_installed(self.cfos, self.archive)

    def test_failed_or_stale_build_never_promotes(self):
        manifest = products.verify_build(self.cfos)
        self.assertEqual(manifest['schema'], products.SCHEMA)
        (self.built['webapp'] / 'index.html').write_text('edited after Build')
        with self.assertRaisesRegex(RuntimeError, 'changed since Build'):
            products.install(self.cfos, self.archive)
        self.assertFalse((self.archive.parent.parent / 'steamapps').exists())

    def test_install_detects_installed_changes(self):
        products.install(self.cfos, self.archive)
        apps = ubuntu_adapter.steamapps_volume(self.archive) / 'common/CinterForge/Applications'
        (apps / 'WebApps/CinterForgeWebApp/2026/websites/dev/index.html').write_text('corrupt')
        with self.assertRaisesRegex(RuntimeError, 'Installed WebApp differs'):
            products.verify_installed(self.cfos, self.archive)

    def test_symlinked_product_is_rejected(self):
        external = self.workspace / 'external-file'
        external.write_text('never publish')
        (self.built['server'] / 'secret').symlink_to(external)
        with self.assertRaisesRegex(RuntimeError, 'symlink'):
            products.verify_build(self.cfos)

    def test_server_credentials_are_not_packaged(self):
        self.assertIn('credentials.json',
                      products._exclude_server_files('cinter/registry', ['credentials.json', 'data.json']))
        self.assertNotIn('data.json',
                         products._exclude_server_files('cinter/registry', ['credentials.json', 'data.json']))
        self.assertIn('.env.production',
                      products._exclude_server_files('cinter', ['.env.production']))

    def test_cinter_build_is_invoked_inside_ubuntu_not_reimplemented(self):
        # A fake Docker invocation writes the output that Cinter build web
        # would produce; it never launches real Docker or downloads content.
        self.built['webapp'].joinpath('index.html').unlink()
        calls = []
        def fake_run(argv, **kwargs):
            calls.append(list(map(str, argv)))
            if argv[0] == 'docker':
                self.built['webapp'].joinpath('index.html').write_text('<html>built</html>')
            return mock.Mock(returncode=0)
        with mock.patch.object(products.subprocess, 'run', side_effect=fake_run):
            products.build(self.cfos, 'docker', {}, 'cinterforge-ubuntu:24.04-amd64')
        command = calls[-1] if calls[-1][0] == 'docker' else calls[1]
        self.assertIn('linux/amd64', command)
        self.assertIn('/workspace/CinterForgeDesktop/Cinter', command)
        self.assertEqual(command[-2:], ['build', 'web'])
        self.assertIn('/workspace/Builds,rw', ' '.join(command))
        self.assertIn('/workspace/CinterForgeServer,ro', ' '.join(command))
        products.verify_build(self.cfos)

    def test_guest_test_isolation_and_runtime_20380(self):
        products.install(self.cfos, self.archive)
        with mock.patch.object(products.subprocess, 'run') as run:
            products.provision_python(self.archive, 'docker', {}, 'built-image')
            products.test(self.cfos, self.archive, 'docker', {}, 'installed-image-id')
        self.assertEqual(run.call_count, 2)
        provision, test = [call.args[0] for call in run.call_args_list]
        self.assertIn('python3 -m venv', ' '.join(provision))
        self.assertIn('--network', provision)
        self.assertIn('none', provision)
        self.assertIn('installed-image-id', test)
        self.assertIn('--network', test)
        self.assertIn('20380', ' '.join(test))
        self.assertIn('/run/steamapps', ' '.join(test))


if __name__ == '__main__':
    unittest.main()
