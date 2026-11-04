"""CFOS managed Colima path and state-selection regression tests.

Run: python3 -m unittest discover -s build/linux -p 'test_managed_backend.py'
The checks do not require Colima or create/discard VMs.
"""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import subprocess

from managed_backend import (SHORT_PROFILE, LEGACY_PROFILE, SOCKET_PATH_LIMIT,
                             backend_environment, managed_colima_settings, storage_root, ensure_backend)


def build_source(workspace: Path) -> Path:
    return workspace / 'Builds/CreativeSuite/Built/cinterforge-os/linux/Source'


class ColimaPathTests(unittest.TestCase):
    def test_source_checkout_not_required_for_installed_adapter(self):
        source = build_source(Path('/Volumes/External Workspace/Projects'))
        self.assertEqual(storage_root(source), source.parents[4] / 'CreativeSuite/CFOS')

    def test_default_socket_is_compact(self):
        with tempfile.TemporaryDirectory(prefix='cfos', dir='/tmp') as directory:
            source = build_source(Path(directory))
            home, lima, profile = managed_colima_settings(source)
            self.assertEqual(profile, SHORT_PROFILE)
            self.assertEqual(lima, home.parent / 'L')
            socket = lima / f'colima-{profile}/ssh.sock.1234567890123456'
            self.assertLess(len(os.fsencode(socket)), SOCKET_PATH_LIMIT)
            env = backend_environment(source, create=False)
            self.assertEqual(env['DOCKER_HOST'], f'unix://{home / profile / "docker.sock"}')
            self.assertFalse(home.exists(), 'pure environment selection must not create VM state')

    def test_long_existing_legacy_left_intact(self):
        with tempfile.TemporaryDirectory(prefix='cfos', dir='/tmp') as directory:
            workspace = Path(directory) / ('deep-' * 4)
            root = workspace / 'Builds/CreativeSuite/CFOS'
            old = root / 'Colima/_lima/colima-cinterforge-linux'
            old.mkdir(parents=True)
            marker = old / 'keep-disk'
            marker.write_text('owned')
            _, lima, profile = managed_colima_settings(build_source(workspace))
            self.assertEqual(profile, SHORT_PROFILE)
            self.assertEqual(lima, root / 'L')
            self.assertEqual(marker.read_text(), 'owned')

    def test_reuse_usable_legacy(self):
        with tempfile.TemporaryDirectory(prefix='cfos', dir='/tmp') as directory:
            workspace = Path(directory)
            root = workspace / 'Builds/CreativeSuite/CFOS'
            old = root / 'Colima/_lima/colima-cinterforge-linux'
            old.mkdir(parents=True)
            # Validate selection independently of the calling OS's test path.
            with patch('managed_backend.SOCKET_PATH_LIMIT', 200):
                _, lima, profile = managed_colima_settings(build_source(workspace))
            self.assertEqual(profile, LEGACY_PROFILE)
            self.assertEqual(lima, root / 'Colima/_lima')

    def test_compact_wins_when_both_exist(self):
        with tempfile.TemporaryDirectory(prefix='cfos', dir='/tmp') as directory:
            workspace = Path(directory)
            root = workspace / 'Builds/CreativeSuite/CFOS'
            (root / 'Colima/_lima/colima-cinterforge-linux').mkdir(parents=True)
            (root / 'L/colima-c').mkdir(parents=True)
            _, lima, profile = managed_colima_settings(build_source(workspace))
            self.assertEqual((lima, profile), (root / 'L', SHORT_PROFILE))

    def test_path_limit_reports_real_host_constraint(self):
        with tempfile.TemporaryDirectory(prefix='cfos', dir='/tmp') as directory:
            source = build_source(Path(directory) / ('deep-' * 20))
            with self.assertRaisesRegex(RuntimeError, 'macOS.*limit'):
                managed_colima_settings(source)
            self.assertFalse(source.parents[4].exists())

    def test_existing_profile_is_not_downgraded_for_linux(self):
        """Linux must reuse a stopped 32 GiB macOS builder without shrinking it."""
        with tempfile.TemporaryDirectory(prefix='cfos', dir='/tmp') as directory:
            source = build_source(Path(directory))
            instance = source.parents[4] / 'CreativeSuite/CFOS/L/colima-c'
            instance.mkdir(parents=True)
            calls = []

            def run(command, **kwargs):
                calls.append(command)
                if 'status' in command:
                    return subprocess.CompletedProcess(command, 1, '', '')
                return subprocess.CompletedProcess(command, 0, '', '')

            with (patch('managed_backend.platform.system', return_value='Darwin'),
                  patch('managed_backend.platform.machine', return_value='arm64'),
                  patch('managed_backend.shutil.which', side_effect=lambda n: f'/usr/bin/{n}'),
                  patch('managed_backend.Path.is_socket', return_value=True),
                  patch('managed_backend.subprocess.run', side_effect=run)):
                docker, env = ensure_backend(source)
            self.assertEqual(docker, '/usr/bin/docker')
            self.assertEqual(env['CFOS_COLIMA_PROFILE'], SHORT_PROFILE)
            starts = [cmd for cmd in calls if 'start' in cmd]
            self.assertEqual(starts, [['/usr/bin/colima', 'start', '--profile', 'c', '--activate=false']])
            self.assertFalse(any('delete' in cmd for cmd in calls))

    def test_whitespace_paths_are_preserved(self):
        with tempfile.TemporaryDirectory(prefix='CFOS Source ', dir='/tmp') as directory:
            source = build_source(Path(directory))
            env = backend_environment(source, create=False)
            self.assertIn('CFOS Source ', env['LIMA_HOME'])
            self.assertEqual(env['CFOS_COLIMA_PROFILE'], SHORT_PROFILE)


if __name__ == '__main__':
    unittest.main()
