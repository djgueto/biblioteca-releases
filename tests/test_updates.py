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


class SigningTest(unittest.TestCase):
    """Firma en la CI (sign_release.py) y comprobación en la app."""

    def setUp(self):
        import base64
        import importlib.util

        from cryptography.hazmat.primitives import serialization as s
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        spec = importlib.util.spec_from_file_location(
            "sign_release", PROJECT_ROOT / "packaging" / "desktop" / "sign_release.py")
        self.sign_release = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.sign_release)
        key = Ed25519PrivateKey.generate()
        self.private = base64.b64encode(key.private_bytes(s.Encoding.Raw, s.PrivateFormat.Raw,
                                                          s.NoEncryption())).decode()
        self.public = base64.b64encode(key.public_key().public_bytes(s.Encoding.Raw, s.PublicFormat.Raw)).decode()
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = Path(self.tmp.name)
        (self.dir / "Biblioteca-windows.zip").write_bytes(b"paquete de windows")
        (self.dir / "Biblioteca-linux.tar.gz").write_bytes(b"paquete de linux")
        with patch.dict(os.environ, {"UPDATE_SIGNING_KEY": self.private}):
            self.sign_release.main(str(self.dir))
        self.sums = (self.dir / "SHA256SUMS").read_bytes()
        self.sig = (self.dir / "SHA256SUMS.sig").read_bytes()

    def tearDown(self):
        self.tmp.cleanup()

    def test_signed_sums_are_accepted(self):
        import hashlib

        sums = updates.verified_sums(self.sums, self.sig, self.public)
        self.assertEqual(sums["Biblioteca-windows.zip"], hashlib.sha256(b"paquete de windows").hexdigest())
        self.assertEqual(set(sums), {"Biblioteca-windows.zip", "Biblioteca-linux.tar.gz"})

    def test_tampered_sums_or_other_key_are_rejected(self):
        tampered = self.sums.replace(self.sums[:4], b"0000")
        with self.assertRaises(RuntimeError):
            updates.verified_sums(tampered, self.sig, self.public)
        with self.assertRaises(RuntimeError):
            updates.verified_sums(self.sums, self.sig)  # clave real de la app: no es la de este test
        with self.assertRaises(RuntimeError):
            updates.verified_sums(self.sums, b"no es una firma", self.public)

    def test_signing_without_key_fails(self):
        with patch.dict(os.environ, {"UPDATE_SIGNING_KEY": ""}), self.assertRaises(SystemExit):
            self.sign_release.main(str(self.dir))

    def test_unsigned_release_is_not_installed(self):
        with self.assertRaises(RuntimeError) as ctx:
            updates._expected_sha256(RELEASE, "Biblioteca-windows.zip")
        self.assertIn("no está firmada", str(ctx.exception))

    def test_install_checks_downloaded_package(self):
        assets = [{"name": n, "url": f"https://api/{n}", "browser_download_url": f"https://dl/{n}"}
                  for n in ("Biblioteca-windows.zip", "SHA256SUMS", "SHA256SUMS.sig")]
        release = {"tag_name": "v9.0.0", "assets": assets}
        files = {"https://dl/SHA256SUMS": self.sums, "https://dl/SHA256SUMS.sig": self.sig}

        def fake_get(url, **kw):
            return MagicMock(content=files[url], raise_for_status=lambda: None)

        with patch.object(Settings, "UPDATE_GITHUB_TOKEN", ""), patch("web.updates.requests.get", fake_get), \
                patch.object(updates, "SIGNING_PUBLIC_KEY", self.public):
            import hashlib

            self.assertEqual(updates._expected_sha256(release, "Biblioteca-windows.zip"),
                             hashlib.sha256(b"paquete de windows").hexdigest())
            with self.assertRaises(RuntimeError):
                updates._expected_sha256(release, "Biblioteca-macos.zip")


if __name__ == "__main__":
    unittest.main()
