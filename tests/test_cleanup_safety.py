"""
Tests de las protecciones de DatabaseManager.cleanup_deleted_series y de
LibraryRepository.path_health: una ruta de biblioteca inaccesible (NAS caído)
o una BD con rutas de otro sistema (Windows -> Docker) no debe vaciar la
biblioteca.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import Settings
from core.database.db_manager import DatabaseManager
from core.repositories import LibraryRepository


class CleanupSafetyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name) / "SERIES"
        self.root.mkdir()
        self.db = DatabaseManager(":memory:")
        self.db.initialize_database()
        self.paths = patch.object(Settings, "get_paths_for_category", lambda category: [str(self.root)])
        self.paths.start()

    def tearDown(self):
        self.paths.stop()
        self.db.close()
        self.tmp.cleanup()

    def _add(self, folder: Path | str, name="x"):
        conn = self.db.get_connection()
        conn.execute("INSERT INTO series (name, folder_path, content_category) VALUES (?, ?, 'series')", (name, str(folder)))
        conn.commit()

    def _count(self):
        return self.db.get_connection().execute("SELECT COUNT(*) FROM series").fetchone()[0]

    def test_deletes_folder_really_gone_from_live_root(self):
        kept = self.root / "Sigue"
        kept.mkdir()
        self._add(kept, "sigue")
        self._add(self.root / "Borrada", "borrada")
        self.assertEqual(self.db.cleanup_deleted_series(), 1)
        self.assertEqual(self._count(), 1)

    def test_keeps_series_when_root_unreachable(self):
        offline_root = Path(self.tmp.name) / "NAS_CAIDO"
        with patch.object(Settings, "get_paths_for_category", lambda category: [str(offline_root)]):
            self._add(offline_root / "Serie", "serie")
            self.assertEqual(self.db.cleanup_deleted_series(), 0)
        self.assertEqual(self._count(), 1)

    def test_keeps_paths_from_another_system(self):
        # BD creada en Windows usada en Docker: la ruta no existe y no está
        # bajo ninguna raíz configurada -> no se toca.
        self._add(r"\\192.168.1.101\Multimedia\SERIES\Serie", "serie")
        self.assertEqual(self.db.cleanup_deleted_series(), 0)
        self.assertEqual(self._count(), 1)

    def test_mass_deletion_is_cancelled(self):
        for i in range(30):
            self._add(self.root / f"Borrada{i}", f"b{i}")
        self.assertEqual(self.db.cleanup_deleted_series(), 0)
        self.assertEqual(self._count(), 30)

    def test_path_health(self):
        self._add(self.root / "A", "a")
        self.assertTrue(LibraryRepository(self.db).path_health()["ok"])
        for i in range(3):
            self._add(rf"\\192.168.1.101\Multimedia\SERIES\S{i}", f"s{i}")
        health = LibraryRepository(self.db).path_health()
        self.assertEqual((health["total"], health["in_configured_roots"], health["ok"]), (4, 1, False))


if __name__ == "__main__":
    unittest.main()
