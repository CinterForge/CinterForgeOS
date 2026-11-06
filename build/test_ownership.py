"""Regression checks for the CFOS Creative Suite + Server package split."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest

CFOS = Path(__file__).resolve().parents[1]
WORKSPACE = CFOS.parent
SERVER = WORKSPACE / 'CinterForgeServer'
SUITE = CFOS / 'build/creativeSuite'


class OwnershipTests(unittest.TestCase):
    def test_server_package_renamed_without_legacy_source(self):
        self.assertTrue((SERVER / 'cf/io/CinterIO.py').is_file())
        self.assertFalse((SERVER / 'cinter').exists())
        self.assertFalse((SERVER / 'cf/build_cinterforge/creativeSuite').exists())
        self.assertTrue((SERVER / 'Cinter').is_file())

    def test_catalog_and_apps_owned_by_cfos(self):
        self.assertTrue((SUITE / 'input/Creative Suite.csv').is_file())
        self.assertTrue((SUITE / 'lifecycle.py').is_file())
        self.assertTrue((SUITE / 'recipe.py').is_file())
        for target in ('mac', 'linux', 'win'):
            self.assertTrue((SUITE / 'apps/cfimage' / target / 'build.sh').is_file())

    def test_server_path_resolver_uses_canonical_siblings(self):
        path = SERVER / 'cf/io/CinterIO.py'
        spec = importlib.util.spec_from_file_location('cfos_ownership_resolver', path)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.cinterforgeSourceRootPath(CFOS), SERVER)
        self.assertEqual(module.cinterforgeWorkspaceRootPath(CFOS), WORKSPACE)
        self.assertEqual(module.cinterforgeBuildsRootPath(CFOS), WORKSPACE / 'Builds')
        self.assertEqual(module.cinterforgeDesktopRootPath(CFOS), WORKSPACE / 'CinterForgeSteam')

    def test_cfos_and_server_dispatch_all_platforms_without_building(self):
        invocations = (
            (str(CFOS / 'CFOS'), 'build', 'cfimage', 'mac', 'Release', '--dry-run'),
            (str(CFOS / 'CFOS'), 'build', 'cfimage', 'linux', 'Release', '--dry-run'),
            (str(CFOS / 'CFOS'), 'build', 'cfimage', 'win', 'Release', '--dry-run'),
            (str(SERVER / 'Cinter'), 'build', 'cfimage', 'linux', 'Release', '--dry-run'),
            (str(SERVER / 'Cinter'), 'setup', 'cfimage', 'win', 'Release', '--dry-run'),
        )
        for command in invocations:
            with self.subTest(command=command):
                result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, check=False, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('[CFOS Creative Suite]', result.stdout)
                self.assertIn('CinterForgeOS/build/creativeSuite/', result.stdout)
                self.assertNotIn('CinterForgeServer/cinter/', result.stdout)


if __name__ == '__main__':
    unittest.main()
