import tempfile
import unittest
from pathlib import Path
from betpilot_v3.private_sqlite_mount import mount,MountError

class Fake:
    def __init__(self):self.commands=[]
    def __call__(self,cmd,**opts):
        from types import SimpleNamespace
        self.commands.append(cmd)
        if 'init' in cmd:
            (Path(cmd[2])/'.git/info').mkdir(parents=True,exist_ok=True)
        if 'ls-tree' in cmd:
            return SimpleNamespace(returncode=0,stdout='''data/catalogue.sqlite\ndata/eng-premier/2024-2025.sqlite\ndata/eng-premier/2025-2026.sqlite\ndata/eng-premier/2026-2027.sqlite\ndata/esp-laliga/2025-2026.sqlite\ndata/esp-laliga/2026-2027.sqlite\n''')
        return SimpleNamespace(returncode=0,stdout='')

class TestPrivateReadonlyMount(unittest.TestCase):
    def test_selects_recent_provider_original_sqlite_without_secret_in_cmd(self):
        with tempfile.TemporaryDirectory() as td:
            f=Fake();out=Path(td)/'mount'
            info=mount(out,token='EXAMPLE_PRIVATE_TOKEN_NOT_REAL',league='eng-premier',seasons=2,runner=f)
            self.assertEqual(info['database_count'],3)
            self.assertIn('data/eng-premier/2026-2027.sqlite',info['database_paths'])
            self.assertNotIn('data/eng-premier/2024-2025.sqlite',info['database_paths'])
            self.assertTrue(all('EXAMPLE_PRIVATE_TOKEN_NOT_REAL' not in ' '.join(cmd) for cmd in f.commands))
            self.assertIn('/data/catalogue.sqlite', (out/'.git/info/sparse-checkout').read_text())
    def test_requires_read_only_private_repo_token(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(MountError):mount(Path(td)/'mount',token='')
    def test_rejects_unsupported_league(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(MountError):
                mount(Path(td)/'mount',token='EXAMPLE_PRIVATE_TOKEN_NOT_REAL',league='esp-segunda',runner=Fake())

if __name__=='__main__':unittest.main()
