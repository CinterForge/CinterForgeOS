import struct, tempfile, unittest, zipfile
from pathlib import Path
from cf.build_cinterforge.craftmaster.craftmaster_serve import prepare_world, prepare_world_directory, _read_world_game_mode, resolve_cfos_launcher, launch_server

class CraftMasterServeTests(unittest.TestCase):
    def test_prepares_mcworld_and_online_server(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); server=root/'Server'; server.mkdir(); (server/'bedrock_server').write_text('x')
            (server/'server.properties').write_text('server-name=Cinter\nonline-mode=false\nlevel-name=Old\n')
            world=root/'Candlewood.mcworld'
            with zipfile.ZipFile(world,'w') as z:
                
                game=b'GameType'; payload=(b'\x0a\x00\x00'+b'\x03'+struct.pack('<H',len(game))+game+struct.pack('<i',1)+b'\x00')
                z.writestr('level.dat',struct.pack('<II',10,len(payload))+payload); z.writestr('levelname.txt','Candlewood Bedrock Export Overworld\n'); z.writestr('db/MANIFEST','db')
            name=prepare_world(world,server)
            self.assertEqual(name,'Candlewood Bedrock Export Overworld')
            self.assertTrue((server/'worlds'/name/'level.dat').is_file())
            props=(server/'server.properties').read_text()
            self.assertIn('level-name=Candlewood Bedrock Export Overworld\n',props)
            self.assertIn('server-name=Candlewood Bedrock Export Overworld\n',props)
            self.assertIn('online-mode=true\n',props)
            self.assertIn('allow-list=false\n',props)
            self.assertIn('allow-cheats=true\n',props)
            self.assertIn('content-log-console-output-enabled=true\n',props)
            self.assertNotIn('online-mode=false',props)
            self.assertIn('gamemode=creative\n',props)
            self.assertIn('force-gamemode=false\n',props)
            self.assertNotIn('game-mode=',props)
            self.assertNotIn('enable-lan-visibility=',props)


    def test_prepares_local_world_directory_without_modifying_source(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); server=root/'Server'; server.mkdir(); (server/'bedrock_server').write_text('x')
            source=root/'LocalWorld'; (source/'db').mkdir(parents=True)
            name=b'GameType'; payload=b'\x0a\x00\x00'+b'\x03'+struct.pack('<H',len(name))+name+struct.pack('<i',0)+b'\x00'
            (source/'level.dat').write_bytes(struct.pack('<II',10,len(payload))+payload)
            (source/'levelname.txt').write_text('Local Survival\n'); (source/'db'/'MANIFEST').write_text('original')
            deployed=prepare_world_directory(source,server)
            self.assertEqual(deployed,'Local Survival')
            self.assertEqual((server/'worlds'/deployed/'db'/'MANIFEST').read_text(),'original')
            (server/'worlds'/deployed/'db'/'MANIFEST').write_text('server changed')
            self.assertEqual((source/'db'/'MANIFEST').read_text(),'original')
            props=(server/'server.properties').read_text()
            self.assertIn('level-name=Local Survival\n',props)
            self.assertIn('gamemode=survival\n',props)
            self.assertIn('allow-list=false\n',props)
            self.assertIn('allow-cheats=true\n',props)
            self.assertIn('content-log-console-output-enabled=true\n',props)

    def test_reads_authored_creative_game_mode(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'level.dat'; name=b'GameType'
            payload=b'\x0a\x00\x00'+b'\x03'+struct.pack('<H',len(name))+name+struct.pack('<i',1)+b'\x00'
            p.write_bytes(struct.pack('<II',10,len(payload))+payload)
            self.assertEqual(_read_world_game_mode(p),1)

    def test_missing_cfos_launcher_is_not_silently_rebuilt(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(RuntimeError, 'CFOS public LinuxKit launcher missing'):
                resolve_cfos_launcher(Path(td)/'Server', Path(td)/'missing-CFOS')

    def test_mac_launch_uses_shared_cfos_public_entry(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); server=root/'Server'; server.mkdir()
            cli=root/'CFOS'; cli.write_text('#!/bin/bash\n'); cli.chmod(0o755)
            with patch('cf.build_cinterforge.craftmaster.craftmaster_serve.platform.system',return_value='Darwin'), \
                 patch('cf.build_cinterforge.craftmaster.craftmaster_serve.os.execv') as execv:
                launch_server(server,cli)
            execv.assert_called_once_with(str(cli.resolve()),
                [str(cli.resolve()),'launch','cinterforge-os','linuxkit','Release','--server',str(server.resolve())])

    def test_explicit_cfos_launcher_can_be_used_by_installed_desktop(self):
        with tempfile.TemporaryDirectory() as td:
            cli=Path(td)/'CFOS'; cli.write_text('#!/bin/bash\n'); cli.chmod(0o755)
            self.assertEqual(resolve_cfos_launcher(Path(td)/'Server',cli),cli.resolve())

    def test_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); server=root/'Server'; server.mkdir(); (server/'bedrock_server').write_text('x')
            world=root/'bad.mcworld'
            with zipfile.ZipFile(world,'w') as z: z.writestr('../escape','bad')
            with self.assertRaisesRegex(RuntimeError,'unsafe path'): prepare_world(world,server)

if __name__=='__main__': unittest.main()
