"""
Tests del calendario de emisiones (core/agenda).
"""

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import Settings
from core.agenda.service import AgendaService
from core.database.db_manager import DatabaseManager

TODAY = date(2026, 10, 3)

SERIES_DETAILS = {
    10: {"status": "Returning Series", "number_of_seasons": 2,
         "last_episode_to_air": {"season_number": 2, "air_date": "2026-09-30"},
         "next_episode_to_air": {"season_number": 2, "air_date": "2026-10-07"}},
    20: {"status": "Ended", "number_of_seasons": 3,
         "last_episode_to_air": {"season_number": 3, "air_date": "2020-01-01"}},
}
SEASONS = {
    (10, 2): {"episodes": [
        {"episode_number": 1, "name": "Uno", "air_date": "2026-09-23"},
        {"episode_number": 2, "name": "Dos", "air_date": "2026-09-30"},
        {"episode_number": 3, "name": "Tres", "air_date": "2026-10-07"},
        {"episode_number": 4, "name": "Cuatro", "air_date": "2027-06-01"},  # fuera de la ventana
    ]},
}


class TestAgenda(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")
        self.db.initialize_database()
        conn = self.db.get_connection()
        conn.executescript("""
            INSERT INTO series (id, name, folder_path, content_category, tmdb_id, monitored) VALUES
              (1, 'En emision', '/s/1', 'series', 10, 1),
              (2, 'Terminada', '/s/2', 'series', 20, 1),
              (3, 'No vigilada', '/s/3', 'series', 10, 0);
            INSERT INTO local_episodes (series_id, season, episode, filename, file_path) VALUES
              (1, 2, 1, 'e1', '/s/1/e1.mkv');
        """)
        conn.commit()
        client = MagicMock()
        client.get_series_details.side_effect = lambda tmdb_id: SERIES_DETAILS[tmdb_id]
        client.get_season_details.side_effect = lambda tmdb_id, season: SEASONS.get((tmdb_id, season), {"episodes": []})
        self.client = client
        self.key = patch.object(Settings, "TMDB_API_KEY", "k")
        self.key.start()
        self.service = AgendaService(self.db, tmdb_client=client, today=TODAY)

    def tearDown(self):
        self.key.stop()
        self.db.close()

    def test_rebuild_only_window_and_active_series(self):
        result = self.service.rebuild()
        self.assertEqual(result, {"series": 1, "episodes": 3})
        calls = self.client.get_series_details.call_count
        # La lista queda en caché (la comparte el recálculo de faltantes).
        self.assertEqual(self.service.rebuild(), result)
        self.assertEqual(self.client.get_series_details.call_count, calls)

    def test_entries_status(self):
        self.service.rebuild()
        self.db.get_connection().execute(
            "INSERT INTO grab_history (provider, item_key, series_id, season, episode) VALUES ('dontorrent', 'x', 1, 2, 2)")
        entries = self.service.entries("2026-09-20", "2026-10-31")
        self.assertEqual([(e["subtitle"].split(" ")[0], e["status"]) for e in entries],
                         [("2x01", "have"), ("2x02", "grabbed"), ("2x03", "upcoming")])

    def test_movies_from_novedades(self):
        from core.discovery.repository import WantedRepository

        WantedRepository(self.db).add({"tmdb_id": 99, "media_type": "movie", "title": "Estreno", "release_date": "2026-10-15",
                                       "category": "movies", "status": "approved"})
        entries = self.service.entries("2026-10-01", "2026-10-31")
        movie = [e for e in entries if e["kind"] == "movie"][0]
        self.assertEqual((movie["title"], movie["status"]), ("Estreno", "upcoming"))


if __name__ == "__main__":
    unittest.main()
