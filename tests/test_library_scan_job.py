"""
"Escanear biblioteca": disco -> BD -> TMDB, sin Hispashare.
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
from core.events import TASK_FINISHED, TASK_LOG, TMDB_MATCHED
from core.library.scan_job import LibraryScanJob


class LibraryScanJobTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        base = Path(self.tmp.name)
        (base / "SERIES" / "Mi Serie" / "Temporada 1").mkdir(parents=True)
        (base / "SERIES" / "Mi Serie" / "Temporada 1" / "Mi.Serie.S01E01.mkv").write_bytes(b"x")
        (base / "PELIS" / "Una Peli (2020)").mkdir(parents=True)
        (base / "PELIS" / "Una Peli (2020)" / "Una.Peli.2020.mkv").write_bytes(b"x" * 10)
        paths = {"series": [str(base / "SERIES")], "movies": [str(base / "PELIS")]}
        self.patches = [
            patch.object(Settings, "DB_PATH", os.path.join(self.tmp.name, "t.db")),
            patch.object(Settings, "get_paths_for_category", lambda c: paths.get(c, [])),
            patch.object(Settings, "TMDB_API_KEY", "clave"),
            patch.object(Settings, "MINIMUM_MOVIE_SIZE_GB", 0, create=True),
        ]
        for p in self.patches:
            p.start()
        DatabaseManager().initialize_database()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_scans_disk_and_matches_new_items(self):
        def fake_match(_matcher, item):
            return {"tmdb_id": 100 + item["id"], "name": item["name"], "year": 2020,
                    "confidence_score": 0.95, "needs_review": False}

        job = LibraryScanJob()
        finished, matched = [], []
        job.event_bus.subscribe(TASK_FINISHED, finished.append)
        job.event_bus.subscribe(TMDB_MATCHED, matched.append)
        with patch("core.tmdb.auto_matcher.TMDBAutoMatcher.match_content", fake_match), \
                patch("core.services.missing_episodes_service.MissingEpisodesService.rebuild_for_categories",
                      return_value={"total_missing": 3}):
            job.run()

        rows = {r["name"]: r for r in DatabaseManager().get_all_series()}
        self.assertIn("Mi Serie", rows)
        self.assertEqual(rows["Mi Serie"]["content_category"], "series")
        self.assertTrue(all(r["tmdb_id"] for r in rows.values()))
        result = finished[0]["result"]
        self.assertEqual((result["series"], result["matched"], result["total_missing"]),
                         (1, len(rows), 3))
        self.assertEqual(len(matched), len(rows))


class LooseEpisodesTest(LibraryScanJobTest):
    def test_loose_episode_in_movies_is_not_a_movie(self):
        pelis = Path(self.tmp.name) / "PELIS"
        loose = pelis / "Trigger point Fuera de control S02E01 - Episodio 1.mp4"
        loose.write_bytes(b"x")
        (pelis / "2001 Una odisea del espacio (1968).mkv").write_bytes(b"x")  # año, no episodio
        db = DatabaseManager()
        db.insert_or_update_series({"name": loose.stem, "folder_path": str(loose), "content_category": "movies"})

        job = LibraryScanJob()
        logs = []
        job.event_bus.subscribe(TASK_LOG, lambda p: logs.append(p["message"]))
        with patch("core.tmdb.auto_matcher.TMDBAutoMatcher.match_content", return_value=None), \
                patch("core.services.missing_episodes_service.MissingEpisodesService.rebuild_for_categories",
                      return_value={"total_missing": 0}):
            job.run()

        names = {r["name"] for r in DatabaseManager().get_all_series()}
        self.assertNotIn(loose.stem, names)
        self.assertIn("2001 Una odisea del espacio (1968)", names)
        self.assertTrue(any("episodio(s) suelto(s) en Películas" in m for m in logs))
        self.assertTrue(any("1 ficha(s) de película" in m for m in logs))

    def test_scans_disk_and_matches_new_items(self):
        pass  # ya cubierto en la clase base


if __name__ == "__main__":
    unittest.main()
