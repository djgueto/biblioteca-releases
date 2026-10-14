"""
Tests del respaldo entre proveedores en las descargas del ciclo: si la mejor
oferta no puede dar un episodio, se pide a la siguiente; si ya lo dio, no.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.database.db_manager import DatabaseManager
from core.services.providers.base import DownloadHandle, SourceOffer
from core.services.quality import Quality


class _Provider:
    def __init__(self, name, scheduler, episodes):
        self.name = name
        self.scheduler = scheduler
        self.episodes = episodes  # lo que este proveedor tiene
        self.calls = 0

    def resolve_download(self, item, offer, *, missing_only=True):
        self.calls += 1
        pending = self.scheduler._pending_missing(item) & self.episodes
        if not pending:
            return None
        self.scheduler.grab_history_repository.record(self.name, f"{self.name}:x", item["id"], sorted(pending))
        return DownloadHandle(provider=self.name, kind="torrent", payload={"grabbed": [f"/w/{self.name}.torrent"]})


class TestDownloadFallback(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")
        self.db.initialize_database()
        with patch("core.scheduler.DatabaseManager", return_value=self.db):
            from core.scheduler import Scheduler

            self.scheduler = Scheduler()
        self.scheduler.running = True
        conn = self.db.get_connection()
        conn.execute("INSERT INTO series (id, name, folder_path, tmdb_id) VALUES (1, 'Serie', '/s', 9)")
        conn.executemany("INSERT INTO missing_episodes (series_id, season, episode) VALUES (1, 1, ?)", [(1,), (2,)])
        conn.commit()
        self.series = {"id": 1, "name": "Serie"}
        self.offers = [
            SourceOffer(provider="best", source_url="u1", quality=Quality(resolution_height=2160)),
            SourceOffer(provider="backup", source_url="u2", quality=Quality(resolution_height=1080)),
        ]
        patcher = patch("core.importer.pending.downloads_index", side_effect=Exception("sin carpetas"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.db.close()

    def _providers(self, best_has, backup_has):
        best = _Provider("best", self.scheduler, best_has)
        backup = _Provider("backup", self.scheduler, backup_has)
        self.scheduler.providers.update({"best": best, "backup": backup})
        return best, backup

    def test_backup_gets_what_the_best_lacks(self):
        best, backup = self._providers({(1, 1)}, {(1, 1), (1, 2)})
        total = self.scheduler._download_from_offers(self.series, self.offers)
        self.assertEqual(total, 2)
        grabbed = self.db.get_connection().execute(
            "SELECT provider, episode FROM grab_history ORDER BY episode").fetchall()
        self.assertEqual([tuple(r) for r in grabbed], [("best", 1), ("backup", 2)])

    def test_backup_not_asked_when_best_covers_everything(self):
        best, backup = self._providers({(1, 1), (1, 2)}, {(1, 1), (1, 2)})
        self.scheduler._download_from_offers(self.series, self.offers)
        self.assertEqual(backup.calls, 0)

    def test_failing_provider_does_not_stop_the_backup(self):
        best, backup = self._providers(set(), {(1, 1), (1, 2)})
        best.resolve_download = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("caído"))
        self.assertEqual(self.scheduler._download_from_offers(self.series, self.offers), 1)
        self.assertEqual(backup.calls, 1)


if __name__ == "__main__":
    unittest.main()
