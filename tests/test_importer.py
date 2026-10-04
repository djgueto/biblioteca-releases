"""
Tests de la importación automática (core/importer) sobre carpetas temporales.
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import Settings
from core.database.db_manager import DatabaseManager
from core.importer.service import ImportService, ImportSettings

OLD = time.time() - 3600  # archivos "terminados" hace una hora


def _video(path: Path, size=1000, mtime=OLD) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    os.utime(path, (mtime, mtime))
    return path


class TestImporter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        base = Path(self.tmp.name).resolve()
        self.downloads = base / "descargas"
        self.series_root = base / "SERIES"
        self.movies_root = base / "PELICULAS"
        self.recycle = base / "reemplazados"
        self.downloads.mkdir()
        self.roots = {"series": [str(self.series_root)], "movies": [str(self.movies_root)]}
        self.patches = [
            patch.object(Settings, "get_paths_for_category", lambda c: self.roots.get(c, [])),
            patch.object(Settings, "TORRENT_WATCH_DIR", str(self.downloads / "watch")),
        ]
        for p in self.patches:
            p.start()
        self.db = DatabaseManager(":memory:")
        self.db.initialize_database()
        conn = self.db.get_connection()
        self.ted = self.series_root / "Ted Lasso"
        self.flat = self.series_root / "Sherwood"
        from core.discovery.repository import WantedRepository
        from core.repositories.grab_history_repository import GrabHistoryRepository

        WantedRepository(self.db)
        GrabHistoryRepository(self.db)
        conn.executemany(
            "INSERT INTO series (id, name, tmdb_name, folder_path, content_category, year, placeholder) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(1, "Ted Lasso", "Ted Lasso", str(self.ted), "series", 2020, 0),
             (2, "Sherwood", "Sherwood", str(self.flat), "series", 2022, 0),
             (3, "Primetime (2026)", "Primetime", str(self.movies_root / "Primetime (2026)"), "movies", 2026, 1),
             (4, "Dune", "Dune", str(self.movies_root / "Dune (2021).mkv"), "movies", 2021, 0)])
        conn.executemany("INSERT INTO local_episodes (series_id, season, episode, filename, file_path) VALUES (?, ?, ?, ?, ?)",
                         [(1, 4, 1, "a", str(self.ted / "Season 04" / "Ted Lasso S04E01 - Uno.mkv")),
                          (2, 2, 1, "b", str(self.flat / "Sherwood S02E01 - Uno.mkv"))])
        conn.executemany("INSERT INTO missing_episodes (series_id, season, episode, name, status) VALUES (?, ?, ?, ?, 'missing')",
                         [(1, 4, 7, "La final"), (2, 2, 3, None)])
        conn.execute("INSERT INTO wanted_items (tmdb_id, media_type, title, status, series_id) VALUES (900, 'movie', 'Primetime', 'approved', 3)")
        conn.commit()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.db.close()
        self.tmp.cleanup()

    def _service(self, **kw):
        settings = ImportSettings(enabled=True, source_dirs=[str(self.downloads)], recycle_dir=str(self.recycle), **kw)
        return ImportService(self.db, settings=settings)

    def test_missing_episode_goes_to_season_folder_with_library_name(self):
        src = _video(self.downloads / "Ted.Lasso.S04E07.2160p.WEB-DL" / "Ted.Lasso.S04E07.2160p.WEB-DL.mkv")
        result = self._service().run()
        self.assertEqual(result["imported"], 1)
        self.assertTrue((self.ted / "Season 04" / "Ted Lasso S04E07 - La final.mkv").exists())
        self.assertTrue(src.exists())  # modo copiar: sigue compartiéndose

    def test_flat_series_keeps_flat_layout_and_move_mode(self):
        src = _video(self.downloads / "Sherwood 2x03 [1080p].mkv")
        self._service(mode="move").run()
        self.assertTrue((self.flat / "Sherwood S02E03 - Episodio 3.mkv").exists())
        self.assertFalse(src.exists())

    def test_movie_in_camino_becomes_real_item(self):
        _video(self.downloads / "Primetime.2026.1080p.BluRay.x264.mkv")
        self._service().run()
        dest = self.movies_root / "Primetime (2026).mkv"
        self.assertTrue(dest.exists())
        row = self.db.get_connection().execute("SELECT placeholder, folder_path FROM series WHERE id = 3").fetchone()
        self.assertEqual((row[0], Path(row[1])), (0, dest))
        self.assertEqual(self.db.get_connection().execute(
            "SELECT status FROM wanted_items WHERE series_id = 3").fetchone()[0], "in_library")

    def test_never_touches_what_the_app_did_not_ask_for(self):
        others = [
            _video(self.downloads / "Otra.Serie.S01E01.mkv"),          # serie que no es nuestra
            _video(self.downloads / "Ted.Lasso.S04E01.mkv"),           # ya lo tienes y no se pidió mejora
            _video(self.downloads / "Pelicula.Cualquiera.2024.mkv"),   # película no pedida
            _video(self.downloads / "Dune.2021.2160p.mkv"),            # Dune existe pero no se pidió mejora
        ]
        result = self._service().run()
        self.assertEqual(result, {"imported": 0, "upgraded": 0, "skipped": 0})
        self.assertTrue(all(p.exists() for p in others))
        self.assertFalse((self.movies_root / "Dune (2021).mkv").exists())

    def test_unfinished_and_partial_files_are_ignored(self):
        _video(self.downloads / "Ted.Lasso.S04E07.mkv", mtime=time.time())  # aún cambiando
        _video(self.downloads / "Ted.Lasso.S04E07.mkv.part")
        _video(self.downloads / "watch" / "Ted.Lasso.S04E07.mkv")          # carpeta vigilada
        self.assertEqual(self._service().run()["imported"], 0)

    def test_disabled_does_nothing(self):
        _video(self.downloads / "Ted.Lasso.S04E07.mkv")
        service = ImportService(self.db, settings=ImportSettings(enabled=False, source_dirs=[str(self.downloads)]))
        self.assertEqual(service.run()["imported"], 0)
        self.assertFalse((self.ted / "Season 04").exists())

    def test_imported_once(self):
        _video(self.downloads / "Sherwood 2x03.mkv")
        service = self._service()
        self.assertEqual(service.run()["imported"], 1)
        self.db.get_connection().execute("DELETE FROM missing_episodes")  # como tras el escaneo
        self.assertEqual(service.run()["imported"], 0)

    def test_upgrade_replaces_only_if_mediainfo_says_better_and_keeps_old(self):
        old = _video(self.movies_root / "Dune (2021).mkv")
        self.db.get_connection().execute(
            "INSERT INTO grab_history (provider, item_key, series_id, season, episode) VALUES ('dontorrent', 'x', 4, NULL, NULL)")
        new = _video(self.downloads / "Dune.2021.2160p.UHD.mkv", size=5000)
        qualities = {str(new): {"resolution_height": 2160, "codec": "x265", "hdr": "hdr10"},
                     str(old): {"resolution_height": 1080, "codec": "x264", "hdr": None}}
        with patch("core.importer.service.detect_resolution_from_file", side_effect=lambda p: qualities.get(p)):
            self.assertEqual(self._service().run()["upgraded"], 1)
        self.assertEqual((self.movies_root / "Dune (2021).mkv").stat().st_size, 5000)
        self.assertTrue((self.recycle / "Dune (2021).mkv").exists())  # el viejo no se borra

    def test_failed_copy_puts_the_old_file_back(self):
        old = _video(self.movies_root / "Dune (2021).mkv")
        self.db.get_connection().execute(
            "INSERT INTO grab_history (provider, item_key, series_id, season, episode) VALUES ('dontorrent', 'x', 4, NULL, NULL)")
        new = _video(self.downloads / "Dune.2021.2160p.mkv", size=5000)
        qualities = {str(new): {"resolution_height": 2160, "codec": "x265", "hdr": None},
                     str(old): {"resolution_height": 1080, "codec": "x264", "hdr": None}}
        with patch("core.importer.service.detect_resolution_from_file", side_effect=lambda p: qualities.get(p)), \
                patch("core.importer.service.shutil.copy2", side_effect=OSError("disco lleno")):
            self._service().run()
        self.assertEqual(old.stat().st_size, 1000)  # el original sigue en su sitio
        self.assertFalse((self.recycle / "Dune (2021).mkv").exists())

    def test_upgrade_that_is_not_better_is_skipped(self):
        _video(self.movies_root / "Dune (2021).mkv")
        self.db.get_connection().execute(
            "INSERT INTO grab_history (provider, item_key, series_id, season, episode) VALUES ('dontorrent', 'x', 4, NULL, NULL)")
        _video(self.downloads / "Dune.2021.1080p.mkv")
        same = {"resolution_height": 1080, "codec": "x264", "hdr": None}
        with patch("core.importer.service.detect_resolution_from_file", return_value=same):
            self.assertEqual(self._service().run()["skipped"], 1)
        self.assertFalse(self.recycle.exists())


if __name__ == "__main__":
    unittest.main()
