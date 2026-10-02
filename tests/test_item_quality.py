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

    def test_filename_is_not_trusted(self):
        # Aunque el nombre diga "2160p HDR x265", si MediaInfo no puede leer
        # el archivo no se da por buena esa calidad.
        f = self.base / "Dune (2021) 2160p HDR x265.mkv"
        f.write_bytes(b"no es un video")
        self.assertEqual(detect_item_quality(str(f))[1:], (None, None))

    def test_mediainfo_result_is_used(self):
        f = self.base / "Dune (2021) 720p.mkv"  # el nombre miente
        f.write_bytes(b"x")
        with patch("core.library.item_quality.detect_resolution_from_file",
                   return_value={"resolution_height": 2160, "codec": "x265", "hdr": None, "quality_rank": 4}):
            self.assertEqual(detect_item_quality(str(f))[1:],
                             ({"resolution_height": 2160, "codec": "x265", "hdr": "sdr"}, "mediainfo"))

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
            INSERT INTO local_episodes (series_id, season, episode, filename, file_path, resolution_height, quality_source) VALUES
              (2, 1, 1, 'e1', '/s/Serie/e1.mkv', 2160, 'mediainfo'),
              (2, 1, 2, 'e2', '/s/Serie/e2.mkv', 1080, 'mediainfo');
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
        with patch("core.library.quality_scan.mediainfo_available", return_value=True),                 patch("core.library.quality_scan.detect_item_quality",
                      return_value=("/m/Coco.mkv", {"resolution_height": 720, "codec": "x264", "hdr": "sdr"}, "mediainfo")):
            self.scheduler.scan_item_qualities()
        self.assertEqual(self.scheduler.item_quality_repository.get(3), Quality(720, "x264", Hdr.SDR))

    def test_series_quality_from_filename_is_ignored(self):
        self.db.get_connection().execute("UPDATE local_episodes SET quality_source = 'filename'")
        self.scheduler.process_upgrades(driver=None)
        upgraded = [c.args[0]["id"] for c in self.provider.resolve_download.call_args_list]
        self.assertEqual(upgraded, [1])  # solo la película (medida); la serie no tiene nada medido


class TestQualityScanJob(unittest.TestCase):
    def test_runs_batches_until_done_and_reports(self):
        from core.library import quality_scan_job

        episode_results = iter([{"measured": 200, "unreadable": 0, "pending": True},
                                {"measured": 50, "unreadable": 3, "pending": False}])
        item_results = iter([{"measured": 10, "unreadable": 1, "pending": False}])
        events = []
        with patch.object(quality_scan_job, "DatabaseManager"),                 patch.object(quality_scan_job, "measure_episodes", side_effect=lambda db, **kw: next(episode_results)),                 patch.object(quality_scan_job, "measure_items", side_effect=lambda db, **kw: next(item_results)),                 patch.object(quality_scan_job, "coverage", return_value={"episodes": {"measured": 5, "total": 10},
                                                                          "items": {"measured": 5, "total": 10}}):
            job = quality_scan_job.QualityScanJob()
            job.event_bus.subscribe("TASK_FINISHED", events.append)
            job.run()
        self.assertEqual(events[0]["result"], {"measured": 260, "unreadable": 4})

    def test_mediainfo_missing_is_reported_not_marked_unreadable(self):
        from core.library.quality_scan import MediaInfoUnavailable, measure_episodes

        db = MagicMock()
        with patch("core.library.quality_scan.mediainfo_available", return_value=False):
            with self.assertRaises(MediaInfoUnavailable):
                measure_episodes(db, limit=10)
        db.mark_episode_resolution_unreadable.assert_not_called()


if __name__ == "__main__":
    unittest.main()
