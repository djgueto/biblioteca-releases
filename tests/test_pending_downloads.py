"""
Tests: no volver a pedir lo que ya está descargado (o descargándose) en las
carpetas de descargas aunque aún no esté en la biblioteca.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.database.db_manager import DatabaseManager
from core.importer import pending
from core.importer.pending import DownloadsIndex
from core.importer.service import ImportSettings
from core.repositories.grab_history_repository import GrabHistoryRepository

TED = {"id": 1, "name": "Ted Lasso", "tmdb_name": "Ted Lasso", "year": 2020}
DUNE = {"id": 2, "name": "Dune", "tmdb_name": "Dune", "year": 2021}


class TestDownloadsIndex(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = Path(self.tmp.name)
        for name in ("Ted.Lasso.S04E07.2160p.mkv",                 # terminado
                     "Ted Lasso 4x08 [1080p].mkv.part",            # a medias
                     "Otra.Serie.S04E09.mkv",
                     "Dune.1984.1080p.mkv",                        # otra Dune
                     "notas.txt"):
            (self.dir / name).write_bytes(b"x" * 10)
        (self.dir / "sub").mkdir()
        (self.dir / "sub" / "La Venganza Será Terrible 1x01.mkv").write_bytes(b"x" * 1234)

    def tearDown(self):
        self.tmp.cleanup()

    def test_episodes_and_movies(self):
        index = DownloadsIndex([str(self.dir)])
        self.assertEqual(index.episodes_for(TED), {(4, 7), (4, 8)})
        self.assertFalse(index.has_movie(DUNE))  # la de 1984 no es la de 2021
        self.assertTrue(index.has_movie({**DUNE, "year": 1984}))

    def test_ed2k_link_by_name_or_size(self):
        index = DownloadsIndex([str(self.dir)])
        self.assertTrue(index.has_ed2k_file("ed2k://|file|Ted.Lasso.S04E07.2160p.mkv|999|H|/"))
        self.assertTrue(index.has_ed2k_file("ed2k://|file|otro_nombre.mkv|1234|H|/"))
        self.assertFalse(index.has_ed2k_file("ed2k://|file|nada.mkv|55|H|/"))


class TestBlockedEpisodes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        (Path(self.tmp.name) / "Ted.Lasso.S04E07.mkv").write_bytes(b"x")
        self.db = DatabaseManager(":memory:")
        self.db.initialize_database()
        ImportSettings(source_dirs=[self.tmp.name]).save(self.db)  # importación desactivada: da igual
        pending.clear_cache()
        self.history = GrabHistoryRepository(self.db)

    def tearDown(self):
        pending.clear_cache()
        self.db.close()
        self.tmp.cleanup()

    def test_in_downloads_blocks_even_after_retry_window(self):
        self.history.record("wolfmax", "u", 1, [(4, 7)])
        self.db.get_connection().execute("UPDATE grab_history SET created_at = datetime('now', '-30 days')")
        self.assertEqual(self.history.blocked_episodes(TED, 7), {(4, 7)})

    def test_recent_grab_blocks_even_if_not_in_downloads_yet(self):
        self.history.record("dontorrent", "x", 1, [(4, 9)])
        self.assertEqual(self.history.blocked_episodes(TED, 7), {(4, 7), (4, 9)})

    def test_old_grab_not_in_downloads_can_be_requested_again(self):
        self.history.record("dontorrent", "x", 1, [(4, 9)])
        self.db.get_connection().execute("UPDATE grab_history SET created_at = datetime('now', '-30 days')")
        self.assertNotIn((4, 9), self.history.blocked_episodes(TED, 7))


class TestHispashare(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")
        self.db.initialize_database()
        self.db.get_connection().execute(
            "INSERT INTO series (id, name, folder_path, content_category, hispashare_url) VALUES (1, 'Ted Lasso', '/s', 'series', 'h')")
        with patch("core.services.providers.hispashare_provider.HispashareSearcher"), \
                patch("core.services.providers.hispashare_provider.HispashareDownloader"):
            from core.services.providers.hispashare_provider import HispashareSourceProvider

            self.provider = HispashareSourceProvider(MagicMock(), self.db)
        pending.clear_cache()

    def tearDown(self):
        self.db.close()

    def test_excludes_blocked_and_records_sent_episodes(self):
        history = GrabHistoryRepository(self.db)
        history.record("wolfmax", "u", 1, [(4, 7)])
        self.provider._downloader.download_missing_episodes.return_value = [
            "ed2k://|file|Ted.Lasso.S04E08.mkv|1|A|/", "ed2k://|file|Ted.Lasso.S04E09.mkv|2|B|/"]
        item = {"id": 1, "name": "Ted Lasso", "content_category": "series", "hispashare_url": "h"}
        self.provider.resolve_download(item, None)
        self.assertEqual(self.provider._downloader.download_missing_episodes.call_args.kwargs["exclude"], {(4, 7)})
        self.assertEqual(history.recently_grabbed_episodes(1, 7), {(4, 7), (4, 8), (4, 9)})


class TestStaleEd2kRetry(unittest.TestCase):
    def test_finished_download_is_not_retried(self):
        from core.hispashare.downloader import HispashareDownloader

        downloader = HispashareDownloader.__new__(HispashareDownloader)
        downloader.settings = MagicMock(ED2K_RETRY_ENABLED=True, ED2K_RETRY_AFTER_DAYS=3)
        downloader.db_manager = MagicMock()
        downloader.logger = MagicMock()
        finished = "ed2k://|file|Ted.Lasso.S04E07.mkv|10|A|/"
        stuck = "ed2k://|file|Ted.Lasso.S04E08.mkv|20|B|/"
        downloader.db_manager.get_stale_ed2k_links.return_value = [finished, stuck]
        downloader.emule_client = MagicMock()
        downloader.emule_client.get_active_download_page.return_value = "<cola vacía>"
        downloader.emule_client.is_link_active_in_queue.return_value = False
        index = MagicMock()
        index.has_ed2k_file.side_effect = lambda link: link == finished
        with patch("core.importer.pending.downloads_index", return_value=index):
            self.assertEqual(downloader._get_retryable_stale_links([finished, stuck], []), [stuck])


if __name__ == "__main__":
    unittest.main()
