"""
Tests de MissingEpisodesService y del recálculo de faltantes: una serie que
falla no impide recalcular las demás, y TMDB se consulta sin transacción
abierta (si no, la BD queda bloqueada mientras dura la llamada).
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.services.missing_episodes_service import MissingEpisodesService


class TestRebuildForCategories(unittest.TestCase):
    def test_failing_series_does_not_stop_the_rest(self):
        db = MagicMock()
        db.get_series_with_tmdb.return_value = [
            {"id": 1, "name": "Rota"},
            {"id": 2, "name": "Buena"},
        ]

        def recalc(series_id):
            if series_id == 1:
                raise Exception("database is locked")
            return 3

        db.recalculate_missing_episodes_for_series.side_effect = recalc
        repo = MagicMock()
        repo.get_by_id.side_effect = lambda sid: {"id": sid, "name": str(sid)}
        service = MissingEpisodesService(db, repo, MagicMock())

        result = service.rebuild_for_categories(["series"])

        self.assertEqual(result["total_missing"], 3)
        self.assertEqual(result["recalculated_series"], 1)
        self.assertEqual(len(result["failed"]), 1)
        self.assertEqual(result["failed"][0]["series_name"], "Rota")
        self.assertIn("locked", result["failed"][0]["error"])


class TestRecalculateMissingTransaction(unittest.TestCase):
    def setUp(self):
        from core.database.db_manager import DatabaseManager

        self.db = DatabaseManager(":memory:")
        self.db.initialize_database()
        conn = self.db.get_connection()
        conn.execute("INSERT INTO series (id, name, folder_path, tmdb_id) VALUES (1, 'Serie', '/s', 99)")
        conn.execute("INSERT INTO missing_episodes (series_id, season, episode) VALUES (1, 1, 5)")
        conn.commit()

    def tearDown(self):
        self.db.close()

    def test_tmdb_is_called_without_open_transaction(self):
        conn = self.db.get_connection()
        seen = {}

        def get_all_episodes(tmdb_id, only_aired=True):
            seen["in_transaction"] = conn.in_transaction
            return [{"season_number": 1, "episode_number": 1, "name": "Uno", "air_date": "2020-01-01"}]

        client = MagicMock()
        client.get_all_episodes.side_effect = get_all_episodes
        with patch("core.tmdb.api_client.TMDBClient", return_value=client):
            count = self.db.recalculate_missing_episodes_for_series(1)

        self.assertFalse(seen["in_transaction"])
        self.assertEqual(count, 1)
        rows = conn.execute("SELECT season, episode FROM missing_episodes WHERE series_id = 1").fetchall()
        self.assertEqual([tuple(r) for r in rows], [(1, 1)])

    def test_tmdb_failure_keeps_previous_missing(self):
        client = MagicMock()
        client.get_all_episodes.side_effect = Exception("TMDB caído")
        with patch("core.tmdb.api_client.TMDBClient", return_value=client):
            with self.assertRaises(Exception):
                self.db.recalculate_missing_episodes_for_series(1)
        rows = self.db.get_connection().execute("SELECT episode FROM missing_episodes").fetchall()
        self.assertEqual([r[0] for r in rows], [5])


if __name__ == "__main__":
    unittest.main()
