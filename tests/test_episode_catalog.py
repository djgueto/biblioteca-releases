"""
Tests de core/tmdb/episode_catalog.py: lista de episodios de TMDB en caché.
"""

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.database.db_manager import DatabaseManager
from core.tmdb.episode_catalog import EpisodeCatalog, TMDBUnavailable


def _client(status="Ended"):
    client = MagicMock()
    client.get_series_details.return_value = {"number_of_seasons": 2, "status": status}
    client.get_season_details.side_effect = lambda tmdb_id, season: {"episodes": [
        {"episode_number": 1, "name": f"T{season}E1", "air_date": f"202{season}-01-01"},
        {"episode_number": 2, "name": f"T{season}E2", "air_date": "2030-01-01"},
        {"episode_number": 3, "name": "Sin anunciar", "air_date": None},
    ]}
    return client


class TestEpisodeCatalog(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")
        self.db.initialize_database()

    def tearDown(self):
        self.db.close()

    def _age(self, days):
        self.db.get_connection().execute(
            "UPDATE tmdb_episode_cache SET fetched_at = datetime('now', ?)", (f"-{days} days",))
        self.db.get_connection().commit()

    def test_only_aired_filter_and_cache(self):
        client = _client()
        catalog = EpisodeCatalog(self.db, client, today=date(2026, 6, 1))
        aired = catalog.episodes(5)
        self.assertEqual([(e["season_number"], e["episode_number"]) for e in aired], [(1, 1), (2, 1)])
        self.assertEqual(len(catalog.episodes(5, only_aired=False)), 6)
        self.assertEqual(client.get_series_details.call_count, 1)  # la segunda vez, de la caché

    def test_ended_series_kept_a_month_airing_two_days(self):
        ended = _client("Ended")
        EpisodeCatalog(self.db, ended).episodes(5)
        self._age(10)
        EpisodeCatalog(self.db, ended).episodes(5)
        self.assertEqual(ended.get_series_details.call_count, 1)

        airing = _client("Returning Series")
        EpisodeCatalog(self.db, airing).episodes(6)
        self._age(3)
        EpisodeCatalog(self.db, airing).episodes(6)
        self.assertEqual(airing.get_series_details.call_count, 2)

    def test_episode_airing_today_counts_without_refetch(self):
        client = _client("Returning Series")
        EpisodeCatalog(self.db, client, today=date(2026, 1, 1)).episodes(5)
        later = EpisodeCatalog(self.db, client, today=date(2030, 1, 1)).episodes(5)
        self.assertEqual(len(later), 4)
        self.assertEqual(client.get_series_details.call_count, 1)

    def test_tmdb_down_uses_stale_copy(self):
        EpisodeCatalog(self.db, _client()).episodes(5)
        self._age(100)
        down = MagicMock()
        down.get_series_details.return_value = None
        self.assertEqual(len(EpisodeCatalog(self.db, down, today=date(2026, 6, 1)).episodes(5)), 2)

    def test_tmdb_down_without_copy_raises(self):
        down = MagicMock()
        down.get_series_details.return_value = None
        with self.assertRaises(TMDBUnavailable):
            EpisodeCatalog(self.db, down).episodes(5)

    def test_missing_season_is_not_saved_half(self):
        client = _client()
        client.get_season_details.side_effect = lambda tmdb_id, season: None if season == 2 else {"episodes": []}
        with self.assertRaises(TMDBUnavailable):
            EpisodeCatalog(self.db, client).episodes(5)
        self.assertIsNone(EpisodeCatalog(self.db, client)._cached(5))

    def test_refresh_ignores_cache(self):
        client = _client()
        EpisodeCatalog(self.db, client).episodes(5)
        EpisodeCatalog(self.db, client).episodes(5, refresh=True)
        self.assertEqual(client.get_series_details.call_count, 2)


if __name__ == "__main__":
    unittest.main()
