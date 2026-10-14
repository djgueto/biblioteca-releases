"""
Tests de core/services/download_status.py: en qué punto está cada faltante.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.database.db_manager import DatabaseManager
from core.repositories.grab_history_repository import GrabHistoryRepository
from core.services.download_status import missing_states


class TestMissingStates(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")
        self.db.initialize_database()
        self.db.get_connection().execute(
            "INSERT INTO series (id, name, folder_path, tmdb_id) VALUES (1, 'Serie', '/s', 9)")
        self.history = GrabHistoryRepository(self.db)
        self.index = MagicMock()
        self.index.episode_progress.return_value = {}
        patcher = patch("core.importer.pending.downloads_index", return_value=self.index)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.db.close()

    def _state(self, episode):
        rows = [{"series_id": 1, "season": 1, "episode": episode}]
        return missing_states(self.db, rows, retry_days=7)[(1, 1, episode)]["state"]

    def _grab(self, provider, episode, days_ago):
        self.history.record(provider, f"k{episode}", 1, [(1, episode)])
        conn = self.db.get_connection()
        conn.execute("UPDATE grab_history SET created_at = datetime('now', ?) WHERE item_key = ?",
                     (f"-{days_ago} days", f"k{episode}"))
        conn.commit()

    def test_states(self):
        self._grab("dontorrent", 2, days_ago=1)
        self._grab("dontorrent", 3, days_ago=10)
        self._grab("hispashare", 4, days_ago=10)
        self.index.episode_progress.return_value = {(1, 5): False, (1, 6): True}
        self.assertEqual(self._state(1), "buscando")
        self.assertEqual(self._state(2), "enviado")
        self.assertEqual(self._state(3), "fallido")
        self.assertEqual(self._state(4), "enviado")  # eMule: puede tardar semanas
        self.assertEqual(self._state(5), "descargando")
        self.assertEqual(self._state(6), "descargado")


class TestDownloadsIndexProgress(unittest.TestCase):
    def test_partial_and_finished(self):
        import os
        import tempfile
        import time

        from core.importer.pending import DownloadsIndex

        with tempfile.TemporaryDirectory() as tmp:
            old = time.time() - 3600
            for name in ("Serie.S01E01.mkv", "Serie.S01E02.mkv.part", "Serie.S01E03.mkv"):
                Path(tmp, name).write_bytes(b"x")
            os.utime(Path(tmp, "Serie.S01E01.mkv"), (old, old))
            # S01E03 se acaba de escribir: aún se está descargando.
            progress = DownloadsIndex([tmp]).episode_progress({"name": "Serie"})
        self.assertEqual(progress, {(1, 1): True, (1, 2): False, (1, 3): False})


if __name__ == "__main__":
    unittest.main()
