"""
Tests de las descargas fallidas: un envío que no llega en GRAB_RETRY_DAYS
pasa a la lista negra y esa versión no se vuelve a pedir.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.database.db_manager import DatabaseManager
from core.repositories.grab_history_repository import GrabHistoryRepository


class TestDetectFailed(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")
        self.db.initialize_database()
        self.history = GrabHistoryRepository(self.db)
        conn = self.db.get_connection()
        conn.execute("INSERT INTO series (id, name, folder_path, tmdb_id) VALUES (1, 'Serie', '/s', 9)")
        conn.execute("INSERT INTO missing_episodes (series_id, season, episode) VALUES (1, 1, 1)")
        conn.execute("INSERT INTO missing_episodes (series_id, season, episode) VALUES (1, 1, 2)")
        conn.commit()
        index = MagicMock()
        index.episodes_for.return_value = set()
        patcher = patch("core.importer.pending.downloads_index", return_value=index)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.index = index

    def tearDown(self):
        self.db.close()

    def _grab(self, provider, key, episodes, days_ago):
        self.history.record(provider, key, 1, episodes, release_title=f"rel {key}")
        self.db.get_connection().execute(
            "UPDATE grab_history SET created_at = datetime('now', ?) WHERE item_key = ?", (f"-{days_ago} days", key))
        self.db.get_connection().commit()

    def test_old_grab_still_missing_is_blocklisted(self):
        self._grab("dontorrent", "t:1", [(1, 1)], days_ago=10)
        failed = self.history.detect_failed(7)
        self.assertEqual([f["item_key"] for f in failed], ["t:1"])
        self.assertTrue(self.history.is_blocklisted("dontorrent", "t:1"))
        self.assertEqual(self.history.detect_failed(7), [])  # no se marca dos veces

    def test_recent_grab_is_not_failed(self):
        self._grab("dontorrent", "t:1", [(1, 1)], days_ago=2)
        self.assertEqual(self.history.detect_failed(7), [])

    def test_partly_arrived_pack_is_not_failed(self):
        self._grab("dontorrent", "t:1", [(1, 1), (1, 3)], days_ago=10)  # 1x03 ya está en disco
        self.assertEqual(self.history.detect_failed(7), [])

    def test_waiting_in_downloads_is_not_failed(self):
        self.index.episodes_for.return_value = {(1, 1)}
        self._grab("dontorrent", "t:1", [(1, 1)], days_ago=10)
        self.assertEqual(self.history.detect_failed(7), [])

    def test_hispashare_page_is_never_blocklisted(self):
        self._grab("hispashare", "https://hispashare/serie", [(1, 1)], days_ago=10)
        self.assertEqual(self.history.detect_failed(7), [])

    def test_unblock(self):
        self._grab("dontorrent", "t:1", [(1, 1)], days_ago=10)
        self.history.detect_failed(7)
        self.assertTrue(self.history.unblock("dontorrent", "t:1"))
        self.assertFalse(self.history.is_blocklisted("dontorrent", "t:1"))


class TestDontorrentSkipsBlocklisted(unittest.TestCase):
    def test_uses_the_backup_link(self):
        from core.dontorrent.client import SearchResult
        from core.services.providers.dontorrent_provider import DontorrentSourceProvider
        from core.services.quality import Quality

        db = DatabaseManager(":memory:")
        db.initialize_database()
        conn = db.get_connection()
        conn.execute("INSERT INTO series (id, name, folder_path, tmdb_id) VALUES (1, 'Serie', '/s', 9)")
        conn.execute("INSERT INTO missing_episodes (series_id, season, episode) VALUES (1, 1, 1)")
        conn.commit()

        good, bad = MagicMock(), MagicMock()
        for link, cid in ((bad, 10), (good, 20)):
            link.season, link.episodes, link.tabla, link.content_id, link.label = 1, [1], "series", cid, "1x01"
        client = MagicMock()
        result = MagicMock(spec=SearchResult)
        result.title, result.season, result.kind, result.url = "Serie", 1, "serie", "u"
        result.quality = Quality(resolution_height=1080)
        client.search.return_value = [result]
        client.episodes.return_value = [bad, good]
        client.fetch_torrent.return_value = ("x.torrent", b"d")
        dispatcher = MagicMock()
        dispatcher.can_handle.return_value = True
        dispatcher.grab.return_value = "/watch/x.torrent"

        provider = DontorrentSourceProvider(db, dispatcher, client)
        provider.history.blocklist("dontorrent", "series:10", 1, "mala", "No llegó")
        with patch("core.importer.pending.downloads_index", side_effect=Exception("sin carpetas")):
            handle = provider.resolve_download({"id": 1, "name": "Serie", "content_category": "series"}, None)

        self.assertIsNotNone(handle)
        client.fetch_torrent.assert_called_once_with(20, "series")
        db.close()


if __name__ == "__main__":
    unittest.main()
