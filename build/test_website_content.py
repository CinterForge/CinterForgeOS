"""Contract tests for CFOS-authored, website-consumable content."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile

SOURCE = Path(__file__).with_name('website_content.py')
spec = importlib.util.spec_from_file_location('cfos_website_content', SOURCE)
website = importlib.util.module_from_spec(spec)
spec.loader.exec_module(website)


class WebsiteContentTests(unittest.TestCase):
    def fixture(self, root: Path) -> Path:
        repo = root / 'CinterForgeOS'
        (repo / 'docs/technical').mkdir(parents=True)
        (repo / 'docs/dev-environment').mkdir(parents=True)
        (repo / 'README.md').write_text(
            '# CFOS\n[![Hero](docs/dev-environment/Hero%20Shot.png)](https://www.cinterforge.com/technology/cinterforgeos/?demo=desktop)\n'
            '[Stack](docs/technical/CinterForge%20OS%20Technology%20Stack.md)\n'
            '[Build](build/README.md#foo)\n'
            '<a href=\"https://www.cinterforge.com/technology/cinterforgeos/?demo=desktop\" data-cf-web-demo-url=\"/cfos/desktop/\">Try Web Demo</a>\n', encoding='utf-8')
        (repo / 'docs/technical/CinterForge OS Technology Stack.md').write_text(
            '# Stack\n[Source](https://example.com/)\n[Apps](#apps)\n', encoding='utf-8')
        (repo / 'docs/dev-environment/Hero Shot.png').write_bytes(b'fake-image-bytes')
        desktop = repo / 'resources/html'
        desktop.mkdir(parents=True)
        (desktop / 'index.html').write_text('<html>CFOS Desktop</html>')
        (desktop / 'app.js').write_text('console.log("desktop")')
        (desktop / 'style.css').write_text('body{}')
        (desktop / 'assets').mkdir()
        (desktop / 'assets/icon.png').write_bytes(b'desktop-icon')
        return repo

    def test_build_zip_and_unpack_without_repo(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.fixture(root)
            builds = root / 'Builds'
            archive = website.build(repo, builds)
            self.assertEqual(archive.name, website.PACKAGE)
            extracted = builds / website.EXTRACTED
            self.assertTrue((extracted / 'content/technology/_index.md').is_file())
            os_page = (extracted / 'content/technology/cinterforgeos/_index.md').read_text()
            self.assertIn('/technology/cinterforgeos/assets/docs/dev-environment/Hero%20Shot.png', os_page)
            self.assertIn('](/technology/)', os_page)
            self.assertIn('github.com/CinterForge/CinterForgeOS/blob/main/build/README.md#foo', os_page)
            self.assertIn('data-cf-web-demo-url="/cfos/desktop/"', os_page)
            self.assertIn('[![Hero](/technology/cinterforgeos/assets/docs/dev-environment/Hero%20Shot.png)](https://www.cinterforge.com/technology/cinterforgeos/?demo=desktop)', os_page)
            self.assertIn('Try Web Demo', os_page)
            self.assertEqual((extracted / 'static/technology/cinterforgeos/assets/docs/dev-environment/Hero Shot.png').read_bytes(), b'fake-image-bytes')
            self.assertEqual((extracted / 'static/cfos/desktop/index.html').read_text(), '<html>CFOS Desktop</html>')
            self.assertEqual((extracted / 'static/cfos/desktop/assets/icon.png').read_bytes(), b'desktop-icon')
            with zipfile.ZipFile(archive) as package:
                self.assertEqual(package.testzip(), None)
                website._checked_entries(package)
            # Site Setup can unpack from published ZIP without the CFOS tree.
            import shutil
            shutil.rmtree(repo)
            shutil.rmtree(extracted)
            website.install(archive, builds)
            self.assertTrue((extracted / 'content/technology/cinterforgeos/_index.md').is_file())

    def test_rejects_archive_tampering_and_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.fixture(root)
            builds = root / 'Builds'
            archive = website.build(repo, builds)
            with zipfile.ZipFile(archive, 'a') as package:
                package.writestr('../outside', b'bad')
            with self.assertRaises(ValueError):
                website.install(archive, builds)

    def test_fails_on_missing_linked_image(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self.fixture(root)
            (repo / 'docs/dev-environment/Hero Shot.png').unlink()
            with self.assertRaises(FileNotFoundError):
                website.build(repo, root / 'Builds')

    def test_pipeline_build_and_setup_are_integrated(self):
        contents = (SOURCE.parent / 'linux/ubuntu_pipeline.py').read_text()
        self.assertIn('build/website_content.py', contents)
        self.assertIn('CinterForgeOS-WebSite-Content-2026.zip', contents)


if __name__ == '__main__':
    unittest.main()
