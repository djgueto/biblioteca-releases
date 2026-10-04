"""
Contraseña de la web, sesiones y acceso desde la red local.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import config.settings as settings_module
from config.settings import Settings
from core.database.db_manager import DatabaseManager
from web import auth
from web.app import create_app
from web.jobs import JobManager


class WebAuthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        base = Path(self.tmp.name)
        self.env = base / "test.env"
        self.patches = [
            patch.object(settings_module, "ENV_PATH", self.env),
            patch.dict(os.environ, {"BIBLIOTECA_DESKTOP": "", "BIBLIOTECA_BOUND_HOST": "127.0.0.1"}),
            patch.object(Settings, "DB_PATH", str(base / "t.db")),
            patch.object(Settings, "TMDB_API_KEY", ""),
        ] + [patch.object(Settings, k, v) for k, v in {
            "WEB_AUTH_USER": "", "WEB_AUTH_PASSWORD": "", "WEB_SECRET_KEY": "", "WEB_ALLOW_LAN": False}.items()]
        for p in self.patches:
            p.start()
        DatabaseManager().initialize_database()
        self.app = create_app(JobManager({}), init_db=False)
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def _set_password(self, client=None):
        r = (client or self.client).put("/api/settings/access", json={"username": "Rafa", "password": "secreto1"})
        self.assertEqual(r.status_code, 200, r.text)
        return r

    def test_hash_never_stores_plain_password(self):
        stored = auth.hash_password("secreto1")
        self.assertNotIn("secreto1", stored)
        self.assertTrue(auth.verify_password("secreto1", stored))
        self.assertFalse(auth.verify_password("otra", stored))
        self.assertFalse(auth.verify_password("secreto1", "basura"))

    def test_without_password_everything_is_open(self):
        self.assertEqual(self.client.get("/api/library").status_code, 200)
        self.assertFalse(self.client.get("/api/auth/status").json()["required"])

    def test_password_protects_api_but_not_login_or_ui(self):
        self._set_password()
        self.assertIn("WEB_AUTH_PASSWORD=pbkdf2_sha256$", self.env.read_text(encoding="utf-8"))
        self.assertEqual(self.client.get("/api/library").status_code, 200)  # quien la puso sigue dentro

        other = TestClient(self.app)
        self.assertEqual(other.get("/api/library").status_code, 401)
        self.assertTrue(other.get("/api/auth/status").json()["required"])
        self.assertEqual(other.get("/api/health").status_code, 200)
        self.assertEqual(other.get("/").status_code, 200)
        self.assertEqual(other.post("/api/auth/login", json={"username": "rafa", "password": "mal"}).status_code, 401)
        self.assertEqual(other.post("/api/auth/login", json={"username": "rafa", "password": "secreto1"}).status_code, 200)
        self.assertEqual(other.get("/api/library").status_code, 200)
        other.post("/api/auth/logout")
        self.assertEqual(other.get("/api/library").status_code, 401)

    def test_websocket_needs_session(self):
        self._set_password()
        other = TestClient(self.app)
        with self.assertRaises(WebSocketDisconnect):
            with other.websocket_connect("/api/ws") as ws:
                ws.receive_json()
        with self.client.websocket_connect("/api/ws") as ws:
            self.assertEqual(ws.receive_json()["type"], "hello")

    def test_changing_password_needs_current_and_logs_out_others(self):
        self._set_password()
        other = TestClient(self.app)
        other.post("/api/auth/login", json={"username": "Rafa", "password": "secreto1"})
        r = self.client.put("/api/settings/access", json={"password": "nueva123", "current_password": "mal"})
        self.assertEqual(r.status_code, 403)
        r = self.client.put("/api/settings/access", json={"password": "nueva123", "current_password": "secreto1"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(other.get("/api/library").status_code, 401)  # su sesión era de la contraseña antigua
        self.assertEqual(self.client.get("/api/library").status_code, 200)
        self.assertEqual(TestClient(self.app).put("/api/settings/access", json={"password": ""}).status_code, 401)

    def test_short_password_rejected(self):
        r = self.client.put("/api/settings/access", json={"username": "Rafa", "password": "123"})
        self.assertEqual(r.status_code, 422)

    def test_desktop_skips_password_on_this_pc_only(self):
        self._set_password()
        with patch.dict(os.environ, {"BIBLIOTECA_DESKTOP": "1"}):
            self.assertTrue(auth.request_allowed({"client": ("127.0.0.1", 5000), "headers": []}))
            self.assertFalse(auth.request_allowed({"client": ("192.168.1.20", 5000), "headers": []}))
        self.assertFalse(auth.request_allowed({"client": ("127.0.0.1", 5000), "headers": []}))  # Docker/código

    def test_allow_lan_only_in_desktop_and_with_password(self):
        self.assertEqual(self.client.put("/api/settings/access", json={"allow_lan": True}).status_code, 422)
        with patch.dict(os.environ, {"BIBLIOTECA_DESKTOP": "1"}):
            r = self.client.put("/api/settings/access", json={"allow_lan": True})
            self.assertEqual(r.status_code, 422)
            self.assertIn("contraseña", r.json()["detail"])
            r = self.client.put("/api/settings/access", json={"allow_lan": True, "username": "Rafa", "password": "secreto1"})
            self.assertEqual(r.status_code, 200)
            data = r.json()
            self.assertTrue(data["allow_lan"])
            self.assertTrue(data["restart_needed"])  # escucha aún solo en 127.0.0.1
            self.assertIn("WEB_ALLOW_LAN=True", self.env.read_text(encoding="utf-8"))

    def test_lan_urls_when_listening_on_network(self):
        with patch.dict(os.environ, {"BIBLIOTECA_BOUND_HOST": "0.0.0.0", "WEB_PORT": "9797"}), \
                patch("web.runtime.lan_addresses", return_value=["192.168.1.30"]):
            data = self.client.get("/api/settings/access").json()
        self.assertEqual(data["lan_urls"], ["http://192.168.1.30:9797"])


if __name__ == "__main__":
    unittest.main()
