"""
Tests de config.settings.write_env_values: guardar ajustes en el .env sin
perder el resto y funcionando con un .env oculto en Windows (el caso real:
dotenv.set_key falla con "Permission denied").
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import write_env_values


class TestEnvWriter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.env = Path(self.tmp.name) / ".env"
        self.env.write_text("# config\nTMDB_API_KEY=abc\nCONTENT_SCAN_ENABLED=True\nOTRA=1\n", encoding="utf-8")

    def tearDown(self):
        if os.name == "nt":
            subprocess.run(["attrib", "-h", str(self.env)], capture_output=True)
        self.tmp.cleanup()

    def test_updates_only_given_keys_and_appends_new(self):
        write_env_values(self.env, {"CONTENT_SCAN_ENABLED": "False", "NUEVA": "x"})
        self.assertEqual(self.env.read_text(encoding="utf-8"),
                         "# config\nTMDB_API_KEY=abc\nCONTENT_SCAN_ENABLED=False\nOTRA=1\nNUEVA=x\n")

    def test_shorter_content_is_truncated(self):
        write_env_values(self.env, {"TMDB_API_KEY": ""})
        self.assertNotIn("abc", self.env.read_text(encoding="utf-8"))

    @unittest.skipUnless(os.name == "nt", "atributo oculto de Windows")
    def test_hidden_env_on_windows(self):
        subprocess.run(["attrib", "+h", str(self.env)], check=True, capture_output=True)
        write_env_values(self.env, {"CONTENT_SCAN_ENABLED": "False"})
        self.assertIn("CONTENT_SCAN_ENABLED=False", self.env.read_text(encoding="utf-8"))

    def test_creates_missing_file(self):
        target = Path(self.tmp.name) / "sub" / ".env"
        write_env_values(target, {"A": "1"})
        self.assertEqual(target.read_text(encoding="utf-8"), "A=1\n")


if __name__ == "__main__":
    unittest.main()
