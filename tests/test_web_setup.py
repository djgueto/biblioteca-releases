"""
Configuración desde la web: bibliotecas, fuentes, asistente y explorador de carpetas.
"""

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient

import config.settings as settings_module
from config.settings import Settings
from core.database.db_manager import DatabaseManager
from web.app import create_app
from web.jobs import JobManager


class WebSetupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        base = Path(self.tmp.name)
        self.series_dir = base / "SERIES"
        self.movies_dir = base / "PELIS"
        for d in (self.series_dir, self.movies_dir):
            d.mkdir()
        self.env = base / "test.env"
        self.env.write_text("# config\nHISPASHARE_PASS=secreta123\n", encoding="utf-8")
        attrs = {"DB_PATH": str(base / "t.db"), "TMDB_API_KEY": "", "HISPASHARE_USER": "", "HISPASHARE_PASS": "secreta123",
                 "EMULE_PASSWORD": "", "TORRENT_WATCH_DIR": "", "DONTORRENT_ENABLED": False,
                 "WOLFMAX_UPGRADE_ENABLED": False}
        from web.routes.setup import SOURCE_FIELDS

        # update_env cambia Settings en memoria: todo lo que se pueda tocar se
        # restaura al acabar.
        for f in SOURCE_FIELDS:
            attrs.setdefault(f.env, getattr(Settings, f.env))
        for cfg in Settings.LIBRARY_CATEGORIES.values():
            attrs[cfg["primary_key"]] = ""
            attrs[cfg["additional_key"]] = ""
        self.patches = [patch.object(settings_module, "ENV_PATH", self.env), patch.dict(os.environ, {}, clear=False)]
        self.patches += [patch.object(Settings, k, v, create=True) for k, v in attrs.items()]
        for p in self.patches:
            p.start()
        DatabaseManager().initialize_database()
        self.client = TestClient(create_app(JobManager({}), init_db=False))

    def tearDown(self):
        self.client.close()
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_setup_requires_tmdb_and_libraries(self):
        steps = {s["id"]: s for s in self.client.get("/api/setup").json()["steps"]}
        self.assertFalse(steps["tmdb"]["done"])
        self.assertFalse(steps["libraries"]["done"])
        Settings.TMDB_API_KEY = "clave"
        self.client.put("/api/settings/libraries", json={"libraries": {"series": [str(self.series_dir)]}})
        self.assertTrue(self.client.get("/api/setup").json()["complete"])

    def test_save_libraries(self):
        r = self.client.put("/api/settings/libraries", json={"libraries": {
            "series": [str(self.series_dir)], "movies": [str(self.movies_dir), str(self.movies_dir)]}})
        self.assertEqual(r.status_code, 200)
        libs = {l["id"]: l for l in r.json()["libraries"]}
        self.assertEqual([p["path"] for p in libs["movies"]["paths"]], [str(self.movies_dir)])
        self.assertTrue(libs["series"]["paths"][0]["accessible"])
        self.assertEqual(Settings.get_paths_for_category("series"), [str(self.series_dir)])
        self.assertIn(f"SERIES_PATH={self.series_dir}", self.env.read_text(encoding="utf-8"))

    def test_libraries_reject_missing_and_duplicated_folders(self):
        r = self.client.put("/api/settings/libraries", json={"libraries": {"series": [str(self.series_dir / "no")]}})
        self.assertEqual(r.status_code, 422)
        r = self.client.put("/api/settings/libraries", json={"libraries": {
            "series": [str(self.series_dir)], "docuseries": [str(self.series_dir)]}})
        self.assertEqual(r.status_code, 422)
        self.assertIn("solo puede estar en una biblioteca", r.json()["detail"])
        self.assertEqual(self.client.put("/api/settings/libraries", json={"libraries": {"nope": []}}).status_code, 422)

    def test_sources_mask_secrets_and_validate(self):
        data = self.client.get("/api/settings/sources").json()
        self.assertEqual(data["hispashare_pass"], "••••••a123")
        r = self.client.put("/api/settings/sources", json={
            "hispashare_user": " yo ", "hispashare_pass": data["hispashare_pass"],
            "emule_url": "http://192.168.1.5:4711", "upgrade_max_links_per_cycle": "40"})
        self.assertEqual(r.status_code, 200)
        text = self.env.read_text(encoding="utf-8")
        self.assertIn("HISPASHARE_USER=yo", text)
        self.assertIn("HISPASHARE_PASS=secreta123", text)  # sin tocar
        self.assertEqual(Settings.UPGRADE_MAX_LINKS_PER_CYCLE, 40)
        self.assertEqual(self.client.put("/api/settings/sources", json={"emule_url": "192.168.1.5"}).status_code, 422)
        self.assertEqual(self.client.put("/api/settings/sources", json={"upgrade_enabled": "si"}).status_code, 422)
        self.assertEqual(self.client.put("/api/settings/sources", json={"otra": 1}).status_code, 422)

    def test_browse_folders(self):
        roots = self.client.get("/api/fs").json()
        self.assertTrue(roots["dirs"])
        listing = self.client.get("/api/fs", params={"path": self.tmp.name}).json()
        self.assertEqual(sorted(d["name"] for d in listing["dirs"]), ["PELIS", "SERIES"])
        self.assertEqual(self.client.get("/api/fs", params={"path": str(self.series_dir / "x")}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
