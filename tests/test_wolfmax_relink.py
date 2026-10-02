"""
Tests de Scheduler.relink_wolfmax: buscar en Wolfmax por turnos lo que su
catálogo reciente no renueva.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.database.db_manager import DatabaseManager
from core.services.providers.base import SourceOffer
from core.services.quality import Quality


class TestWolfmaxRelink(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")
        self.db.initialize_database()
        conn = self.db.get_connection()
        conn.executescript("""
            INSERT INTO series (id, name, folder_path, content_category) VALUES
              (1, 'Caducada', '/s/1', 'series'),
              (2, 'Con faltantes', '/s/2', 'series'),
              (3, 'Peli 1080', '/m/3.mkv', 'movies'),
              (4, 'Peli 4K', '/m/4.mkv', 'movies'),
              (5, 'Al dia', '/s/5', 'series');
            INSERT INTO source_offers (series_id, provider, source_url, available) VALUES
              (1, 'wolfmax', 'https://wolfmax4k.com/series/1080p/caducada', 1),
              (5, 'wolfmax', 'https://wolfmax4k.com/serie/abc123', 1);
            INSERT INTO missing_episodes (series_id, season, episode, status) VALUES (2, 1, 1, 'missing');
        """)
        conn.commit()
        with patch("core.scheduler.DatabaseManager", return_value=self.db):
            from core.scheduler import Scheduler

            self.scheduler = Scheduler()
        repo = self.scheduler.item_quality_repository
        repo.save(3, "/m/3.mkv", file_path=None, quality={"resolution_height": 1080}, source="mediainfo")
        repo.save(4, "/m/4.mkv", file_path=None, quality={"resolution_height": 2160}, source="mediainfo")
        self.provider = MagicMock()
        self.provider.find_candidate.side_effect = lambda item, content_mode: (
            SourceOffer("wolfmax", f"https://wolfmax4k.com/pelicula/n{item['id']}", quality=Quality(2160))
            if item["id"] != 2 else None)
        self.scheduler.providers["wolfmax"] = self.provider

    def tearDown(self):
        self.db.close()

    def _searched(self):
        return [c.args[0]["id"] for c in self.provider.find_candidate.call_args_list]

    def test_priority_order_and_saves_links(self):
        self.scheduler.relink_wolfmax()
        # Caducada, faltantes y la peli 1080p; ni la 4K ni la que tiene enlace vigente.
        self.assertEqual(self._searched(), [1, 2, 3])
        urls = dict(self.db.get_connection().execute(
            "SELECT series_id, source_url FROM source_offers WHERE provider = 'wolfmax'").fetchall())
        self.assertEqual(urls[1], "https://wolfmax4k.com/pelicula/n1")
        self.assertEqual(urls[3], "https://wolfmax4k.com/pelicula/n3")
        self.assertNotIn(2, urls)

    def test_limit_per_cycle_and_no_repeat(self):
        self.scheduler.relink_wolfmax(limit=1)
        self.assertEqual(self._searched(), [1])
        self.scheduler.relink_wolfmax(limit=5)
        self.assertEqual(self._searched(), [1, 2, 3])
        self.scheduler.relink_wolfmax(limit=5)  # 2 no se encontró: no se repite antes de 14 días
        self.assertEqual(self._searched(), [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
