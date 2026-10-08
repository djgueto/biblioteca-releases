"""
Exportar / importar la base de datos (core/database/transfer.py y su API).
"""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient

import config.settings as settings_module
from config.settings import Settings
from core.database import transfer
from core.database.db_manager import DatabaseManager


def make_db(path: Path, names: list[str]) -> None:
    # En memoria y volcada a un archivo propio: DatabaseManager deja abierta
    # la conexión del hilo para rutas de archivo (bloqueo y datos en el WAL).
    db = DatabaseManager(":memory:")
    db.initialize_database()
    conn = db.get_connection()
    for i, name in enumerate(names, 1):
        conn.execute("INSERT INTO series (id, name, folder_path, content_category) VALUES (?, ?, ?, 'series')",
                     (i, name, f"/media/SERIES/{name}"))
    conn.commit()
    dest = sqlite3.connect(str(path))
    conn.backup(dest)
    dest.close()
    db.close()


def series_names(path: Path) -> list[str]:
    conn = sqlite3.connect(str(path))
    try:
        return [r[0] for r in conn.execute("SELECT name FROM series ORDER BY id")]
    finally:
        conn.close()


class TransferTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = Path(self.tmp.name) / "con espacios #1"  # como "app gestion biblioteca"
        self.dir.mkdir()
        self.db_path = self.dir / "series_manager.db"
        make_db(self.db_path, ["Ahsoka"])
        self.patches = [patch.object(Settings, "BACKUPS_DIR", str(self.dir / "backups"))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_export_is_a_complete_copy(self):
        copy = transfer.export_copy(self.db_path, self.dir)
        self.assertEqual(series_names(copy), ["Ahsoka"])
        self.assertEqual(transfer.inspect(copy)["series"], 1)

    def test_inspect_rejects_other_files(self):
        not_db = self.dir / "foto.db"
        not_db.write_bytes(b"\x89PNG no es una base de datos")
        with self.assertRaises(ValueError):
            transfer.inspect(not_db)
        other_app = self.dir / "otra.db"
        sqlite3.connect(str(other_app)).execute("CREATE TABLE notas (x)").connection.close()
        with self.assertRaisesRegex(ValueError, "faltan tablas"):
            transfer.inspect(other_app)

    def test_import_is_applied_on_next_start_with_backup(self):
        other = self.dir / "desde-el-nas.db"
        make_db(other, ["Dune", "Coco"])
        conn = sqlite3.connect(str(other))
        conn.execute("CREATE TABLE IF NOT EXISTS cycle_lock (name TEXT PRIMARY KEY, owner TEXT, heartbeat TEXT)")
        conn.execute("INSERT INTO cycle_lock VALUES ('scheduler', 'nas:1:x', '2026-10-08')")
        conn.commit()
        conn.close()

        conn = sqlite3.connect(str(other))
        conn.execute("PRAGMA journal_mode=WAL")  # como las BD reales
        conn.close()
        summary = transfer.stage_import(other, self.db_path)
        self.assertEqual(summary["series"], 2)
        leftovers = [p.name for p in self.dir.iterdir() if p.name.endswith(("-wal", "-shm"))]
        self.assertEqual(leftovers, [])
        self.assertEqual(series_names(self.db_path), ["Ahsoka"])  # aún no: hasta el arranque

        self.assertTrue(transfer.apply_pending_import(self.db_path))
        self.assertEqual(series_names(self.db_path), ["Dune", "Coco"])
        self.assertFalse(transfer.pending_path(self.db_path).exists())
        backups = list((self.dir / "backups").glob("*.db"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(series_names(backups[0]), ["Ahsoka"])
        conn = sqlite3.connect(str(self.db_path))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM cycle_lock").fetchone()[0], 0)
        conn.close()
        self.assertFalse(transfer.apply_pending_import(self.db_path))  # nada pendiente

    def test_broken_pending_import_is_discarded(self):
        transfer.pending_path(self.db_path).write_bytes(b"basura")
        self.assertFalse(transfer.apply_pending_import(self.db_path))
        self.assertEqual(series_names(self.db_path), ["Ahsoka"])
        self.assertFalse(transfer.pending_path(self.db_path).exists())


class TransferApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = Path(self.tmp.name)
        self.db_path = self.dir / "series_manager.db"
        make_db(self.db_path, ["Ahsoka"])
        self.patches = [
            patch.object(settings_module, "ENV_PATH", self.dir / "t.env"),
            patch.object(Settings, "DB_PATH", str(self.db_path)),
            patch("web.routes.database.DATA_DIR", self.dir),
            patch("web.routes.database._restart"),
        ]
        for p in self.patches:
            p.start()
        from web.app import create_app
        from web.jobs import JobManager

        self.client = TestClient(create_app(JobManager({}), init_db=False))

    def tearDown(self):
        self.client.close()
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_export_downloads_the_database(self):
        r = self.client.get("/api/system/database/export")
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r.headers["content-disposition"])
        self.assertTrue(r.content.startswith(transfer.SQLITE_HEADER))

    def test_import_validates_stages_and_restarts(self):
        from web.routes import database

        r = self.client.post("/api/system/database/import", content=b"no es una base de datos")
        self.assertEqual(r.status_code, 422)
        database._restart.assert_not_called()

        other = self.dir / "otra.db"
        make_db(other, ["Dune", "Coco"])
        r = self.client.post("/api/system/database/import", content=other.read_bytes())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["series"], 2)
        self.assertTrue(transfer.pending_path(self.db_path).exists())
        database._restart.assert_called_once()
        self.assertEqual([p.name for p in self.dir.glob("biblioteca-import-*")], [])  # sin restos


if __name__ == "__main__":
    unittest.main()
