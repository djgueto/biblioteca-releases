"""
Tests de películas en HispashareSourceProvider (sin navegador: se simula la
lectura de la ficha).
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.database.db_manager import DatabaseManager
from core.repositories.grab_history_repository import GrabHistoryRepository
from core.repositories.item_quality_repository import ItemQualityRepository
from core.services.providers.base import SourceOffer
from core.services.quality import Quality

URL = "https://www.hispashare.org/?view=title&id=9"
LINKS = ["ed2k://|file|Dune.2021.2160p.mkv|123|ABC|/"]


class TestHispashareMovies(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")
        self.db.initialize_database()
        self.db.get_connection().execute(
            "INSERT INTO series (id, name, folder_path, content_category, hispashare_url) VALUES (9, 'Dune', '/m/Dune.mkv', 'movies', ?)",
            (URL,))
        with patch("core.services.providers.hispashare_provider.HispashareSearcher"), \
                patch("core.services.providers.hispashare_provider.HispashareDownloader"):
            from core.services.providers.hispashare_provider import HispashareSourceProvider

            self.provider = HispashareSourceProvider(MagicMock(), self.db)
        self.item = {"id": 9, "name": "Dune", "content_category": "movies", "hispashare_url": URL}
        self.scanner = patch("core.hispashare.content_scanner.ContentScanner")
        scanner_cls = self.scanner.start()
        scanner_cls.return_value._process_content_item.return_value = LINKS
        self.process = scanner_cls.return_value._process_content_item

    def tearDown(self):
        self.scanner.stop()
        self.db.close()

    def test_download_movie_and_not_twice(self):
        handle = self.provider.resolve_download(self.item, SourceOffer("hispashare", URL, quality=Quality(2160)))
        self.assertEqual(handle.payload["ed2k_links"], LINKS)
        self.assertEqual(self.process.call_args.args[0]["genre"], "Películas")  # aplica el filtro de tamaño
        self.provider._downloader._save_ed2k_links.assert_called_once_with(9, LINKS, kind="missing")
        self.assertTrue(GrabHistoryRepository(self.db).recently_grabbed_item(9, 7))
        self.assertIsNone(self.provider.resolve_download(self.item, SourceOffer("hispashare", URL, quality=Quality(2160))))

    def test_upgrade_only_if_better_than_measured_file(self):
        repo = ItemQualityRepository(self.db)
        repo.save(9, "/m/Dune.mkv", file_path=None, quality={"resolution_height": 2160, "codec": "x265"}, source="mediainfo")
        offer = SourceOffer("hispashare", URL, quality=Quality(1080, "x265"))
        self.assertIsNone(self.provider.resolve_download(self.item, offer, missing_only=False))
        self.process.assert_not_called()
        repo.save(9, "/m/Dune.mkv", file_path=None, quality={"resolution_height": 720, "codec": "x264"}, source="mediainfo")
        self.assertIsNotNone(self.provider.resolve_download(self.item, offer, missing_only=False))
        self.provider._downloader._save_ed2k_links.assert_called_with(9, LINKS, kind="upgrade")

    def test_episodes_still_use_the_downloader(self):
        series = {"id": 9, "name": "Serie", "content_category": "series"}
        self.provider._downloader.download_missing_episodes.return_value = LINKS
        self.assertEqual(self.provider.resolve_download(series, None).payload["ed2k_links"], LINKS)
        self.process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
