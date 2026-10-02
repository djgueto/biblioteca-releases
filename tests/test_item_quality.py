"""
Tests de la calidad de películas/documentales (item_quality) y de las
mejoras de calidad para todo tipo de contenido en el scheduler.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.database.db_manager import DatabaseManager
from core.library.item_quality import detect_item_quality, main_video_file
from core.repositories.item_quality_repository import ItemQualityRepository
from core.repositories.source_offer_repository import SourceOfferRepository
from core.services.providers.base import DownloadHandle, SourceOffer
from core.services.quality import Hdr, Quality


class TestDetection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_main_video_file_picks_largest_in_folder(self):
        folder = self.base / "Dune (2021)"
        (folder / "extras").mkdir(parents=True)
        (folder / "extras" / "trailer.mkv").write_bytes(b"x" * 10)
        (folder / "Dune.2021.2160p.mkv").write_bytes(b"x" * 1000)
        (folder / "poster.jpg").write_bytes(b"x" * 5000)
        self.assertEqual(main_video_file(str(folder)).name, "Dune.2021.2160p.mkv")
        self.assertIsNone(main_video_file(str(self.base / "no_existe")))

    def test_falls_back_to_filename_when_mediainfo_cannot_read(self):
        f = self.base / "Dune (2021) 2160p HDR x265.mkv"
        f.write_bytes(b"no es un video")
        path, quality, source = detect_item_quality(str(f))
        self.assertEqual((quality, source), ({"resolution_height": 2160, "codec": "x265", "hdr": "hdr10"}, "filename"))

    def test_unreadable_without_quality_in_name(self):
        f = self.base / "Dune (2021).mkv"
        f.write_bytes(b"no es un video")
        self.assertEqual(detect_item_quality(str(f))[1:], (None, None))


class TestRepository(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")
        self.db.initialize_database()
        conn = self.db.get_connection()
        conn.executescript("""
            INSERT INTO series (id, name, folder_path, content_category, hispashare_url) VALUES
              (1, 'Dune', '/m/Dune.mkv', 'movies', 'https://h/?id=1'),
              (2, 'Coco', '/m/Coco.mkv', 'animated_movies', NULL),
              (3, 'Serie', '/s/Serie', 'series', NULL);
        """)
        conn.commit()
        self.repo = ItemQualityRepository(self.db)

    def tearDown(self):
        self.db.close()

    def test_pending_and_remeasure_on_path_change(self):
        cats = ["movies", "animated_movies"]
        self.assertEqual([i["id"] for i in self.repo.pending(cats)], [1, 2])
        self.repo.save(1, "/m/Dune.mkv", file_path="/m/Dune.mkv", quality={"resolution_height": 1080, "codec": "x264"}, source="mediainfo")
        self.repo.save(2, "/m/Coco.mkv", file_path="/m/Coco.mkv", quality=None, source=None)
        self.assertEqual(self.repo.pending(cats), [])
        self.assertEqual(self.repo.get(1), Quality(1080, "x264"))
        self.assertIsNone(self.repo.get(2))  # ilegible
        self.db.get_connection().execute("UPDATE series SET folder_path = '/m/Dune 2160p.mkv' WHERE id = 1")
        self.assertEqual([i["id"] for i in self.repo.pending(cats)], [1])

    def test_upgrade_candidates_need_quality_and_a_source(self):
        cats = ["movies", "animated_movies"]
        self.repo.save(1, "/m/Dune.mkv", file_path=None, quality={"resolution_height": 1080}, source="mediainfo")
        self.repo.save(2, "/m/Coco.mkv", file_path=None, quality={"resolution_height": 1080}, source="mediainfo")
        self.assertEqual([c["id"] for c in self.repo.upgrade_candidates(cats)], [1])  # Coco sin fuente
        SourceOfferRepository(self.db).upsert(2, SourceOffer("wolfmax", "w", quality=Quality(2160)))
        self.assertEqual([c["id"] for c in self.repo.upgrade_candidates(cats)], [2, 1])


class TestSchedulerUpgrades(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")
        self.db.initialize_database()
        conn = self.db.get_connection()
        conn.executescript("""
            INSERT INTO series (id, name, folder_path, content_category) VALUES
              (1, 'Dune', '/m/Dune.mkv', 'movies'),
              (2, 'Serie', '/s/Serie', 'series');
            INSERT INTO local_episodes (series_id, season, episode, filename, file_path, resolution_height) VALUES
              (2, 1, 1, 'e1', '/s/Serie/e1.mkv', 2160),
              (2, 1, 2, 'e2', '/s/Serie/e2.mkv', 1080);
        """)
        conn.commit()
        with patch("core.scheduler.DatabaseManager", return_value=self.db):
            from core.scheduler import Scheduler

            self.scheduler = Scheduler()
        self.scheduler.item_quality_repository.save(1, "/m/Dune.mkv", file_path=None,
                                                    quality={"resolution_height": 1080, "codec": "x264"}, source="mediainfo")
        offers = SourceOfferRepository(self.db)
        offers.upsert(1, SourceOffer("wolfmax", "https://wolfmax4k.com/pelicula/abc", quality=Quality(2160, "x265", Hdr.HDR10)))
        offers.upsert(2, SourceOffer("wolfmax", "https://wolfmax4k.com/serie/xyz", quality=Quality(2160)))
        self.provider = MagicMock()
        self.provider.refresh_offer.side_effect = lambda item, url: None if False else SourceOffer(
            "wolfmax", url, quality=Quality(2160, "x265", Hdr.HDR10) if "pelicula" in url else Quality(2160))
        self.provider.resolve_download.return_value = DownloadHandle("wolfmax", "torrent", {"grabbed": ["/w/x.torrent"]})
        self.scheduler.providers["wolfmax"] = self.provider

    def tearDown(self):
        self.db.close()

    def test_movies_and_series_with_worse_episode_are_upgraded(self):
        self.scheduler.process_upgrades(driver=None)
        upgraded = sorted(c.args[0]["id"] for c in self.provider.resolve_download.call_args_list)
        # La película (1080p → 4K HDR) y la serie (su peor episodio es 1080p).
        self.assertEqual(upgraded, [1, 2])
        for call in self.provider.resolve_download.call_args_list:
            self.assertFalse(call.kwargs["missing_only"])

    def test_already_best_quality_is_left_alone(self):
        self.scheduler.item_quality_repository.save(1, "/m/Dune.mkv", file_path=None,
                                                    quality={"resolution_height": 2160, "codec": "x265", "hdr": "hdr10"},
                                                    source="mediainfo")
        self.db.get_connection().execute("UPDATE local_episodes SET resolution_height = 2160")
        self.scheduler.process_upgrades(driver=None)
        self.provider.resolve_download.assert_not_called()

    def test_scan_item_qualities(self):
        self.db.get_connection().execute("INSERT INTO series (id, name, folder_path, content_category) VALUES (3, 'Coco', '/m/Coco.mkv', 'movies')")
        with patch("core.scheduler.detect_item_quality", return_value=("/m/Coco.mkv", {"resolution_height": 720}, "filename")):
            self.scheduler.scan_item_qualities()
        self.assertEqual(self.scheduler.item_quality_repository.get(3), Quality(720))


if __name__ == "__main__":
    unittest.main()
