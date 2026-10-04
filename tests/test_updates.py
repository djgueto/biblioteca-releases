"""
Comprobación e instalación de actualizaciones (sin llamar a GitHub de verdad).
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient

import config.settings as settings_module
from config.settings import Settings
from core.database.db_manager import DatabaseManager
from web import updates
from web.app import create_app
from web.jobs import JobManager

RELEASE = {
    "tag_name": "v1.0.2", "body": "- Actualizaciones automáticas", "html_url": "https://github.com/x/y/releases/tag/v1.0.2",
    "published_at": "2026-10-05T10:00:00Z",
    "assets": [{"name": "Biblioteca-windows.zip", "url": "https://api.github.com/assets/1",
                "browser_download_url": "https://github.com/x/y/releases/download/v1.0.2/Biblioteca-windows.zip", "size": 10}],
}


def _response(status, payload=None):
    r = MagicMock(status_code=status)
    r.json.return_value = payload or {}
    r.raise_for_status.side_effect = None if status < 400 else RuntimeError(status)
    return r


class UpdatesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.patches = [
            patch.object(settings_module, "ENV_PATH", Path(self.tmp.name) / "t.env"),
            patch.object(Settings, "DB_PATH", str(Path(self.tmp.name) / "t.db")),
            patch.object(Settings, "APP_VERSION", "v1.0.1"),
            patch.object(Settings, "UPDATE_GITHUB_TOKEN", ""),
            patch.object(Settings, "UPDATE_CHECK_ENABLED", True),
            patch.object(updates, "state", updates.UpdateState()),
            patch.object(updates, "_release", None),
        ]
        for p in self.patches:
            p.start()
        DatabaseManager().initialize_database()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_version_compare(self):
        self.assertTrue(updates.is_newer("v1.0.2", "v1.0.1"))
        self.assertTrue(updates.is_newer("v1.10.0", "v1.9.9"))
        self.assertFalse(updates.is_newer("v1.0.1", "v1.0.1"))
        self.assertFalse(updates.is_newer("v1.0.2", "dev"))  # desarrollo: nunca avisa

    def test_check_finds_newer_release(self):
        with patch("web.updates.requests.get", return_value=_response(200, RELEASE)) as get:
            data = updates.check()
        self.assertTrue(data["available"])
        self.assertEqual((data["latest"], data["current"]), ("v1.0.2", "v1.0.1"))
        self.assertNotIn("Authorization", get.call_args.kwargs["headers"])

    def test_private_repo_needs_token(self):
        with patch("web.updates.requests.get", return_value=_response(404)):
            data = updates.check()
        self.assertFalse(data["available"])
        self.assertIn("clave de GitHub", data["error"])
        with patch.object(Settings, "UPDATE_GITHUB_TOKEN", "github_pat_x"), \
                patch("web.updates.requests.get", return_value=_response(200, RELEASE)) as get:
            self.assertTrue(updates.check()["available"])
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer github_pat_x")

    def test_install_only_in_packaged_app(self):
        with patch("web.updates.requests.get", return_value=_response(200, RELEASE)):
            updates.check()
        with self.assertRaises(RuntimeError):
            updates.install()  # ejecutándose desde el código

    @unittest.skipUnless(sys.platform == "win32", "actualizador de Windows")
    def test_windows_updater_waits_copies_and_relaunches(self):
        work = Path(self.tmp.name)
        command = updates._updater_script(work, work / "nueva" / "Biblioteca", work / "app")
        script = (work / "actualizar.cmd").read_text(encoding="utf-8")
        self.assertEqual(command[:2], ["cmd", "/c"])
        self.assertIn(f'PID eq {os.getpid()}', script)
        self.assertIn("robocopy", script)
        self.assertIn("Biblioteca.exe", script)

    def test_api_masks_token_and_rechecks(self):
        client = TestClient(create_app(JobManager({}), init_db=False))
        try:
            with patch("web.updates.requests.get", return_value=_response(200, RELEASE)):
                r = client.put("/api/settings/updates", json={"token": "github_pat_abcdef1234"})
            data = r.json()
            self.assertEqual(data["token"], "••••••1234")
            self.assertTrue(data["available"])
            self.assertFalse(data["can_install"])
            self.assertEqual(client.post("/api/updates/install").status_code, 409)
            self.assertEqual(Settings.UPDATE_GITHUB_TOKEN, "github_pat_abcdef1234")
        finally:
            client.close()
            Settings.UPDATE_GITHUB_TOKEN = ""


if __name__ == "__main__":
    unittest.main()
