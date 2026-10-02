"""
Tests para core/download_clients (carpeta vigilada de torrents, despachador)
y para la entrega de Wolfmax a través de ellos.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.download_clients import (
    ED2K,
    TORRENT,
    DownloadDispatcher,
    NoDownloadClient,
    Release,
)
from core.download_clients.torrent_blackhole import TorrentBlackholeClient
from core.services.providers.base import SourceOffer

TORRENT_BYTES = b"d8:announce4:test4:infod4:name1:xee"


class TestTorrentBlackhole(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.watch = Path(self.tmp.name) / "watch"
        self.client = TorrentBlackholeClient(str(self.watch))

    def tearDown(self):
        self.tmp.cleanup()

    def test_writes_torrent_file_creating_dir(self):
        dest = self.client.send(Release(TORRENT, "Serie", filename="Serie 1x01.torrent", content=TORRENT_BYTES))
        self.assertEqual(Path(dest), self.watch / "Serie 1x01.torrent")
        self.assertEqual(Path(dest).read_bytes(), TORRENT_BYTES)

    def test_sanitizes_name_and_adds_suffix(self):
        dest = self.client.send(Release(TORRENT, "a", filename='Peli: "x"?', content=TORRENT_BYTES))
        self.assertEqual(Path(dest).name, "Peli_ _x__.torrent")

    def test_magnet_link_written_as_magnet_file(self):
        dest = self.client.send(Release(TORRENT, "Peli 2024", link="magnet:?xt=urn:btih:abc"))
        self.assertEqual(Path(dest).name, "Peli 2024.magnet")
        self.assertEqual(Path(dest).read_text(encoding="utf-8"), "magnet:?xt=urn:btih:abc")

    def test_rejects_non_torrent_content(self):
        with self.assertRaises(ValueError):
            self.client.send(Release(TORRENT, "x", filename="x.torrent", content=b"<html>"))
        self.assertFalse(self.watch.exists())

    def test_unconfigured(self):
        self.assertFalse(TorrentBlackholeClient("  ").is_configured())


class TestDispatcher(unittest.TestCase):
    def test_routes_by_protocol(self):
        torrent, ed2k = MagicMock(protocol=TORRENT), MagicMock(protocol=ED2K)
        torrent.send.return_value = "watch/x.torrent"
        dispatcher = DownloadDispatcher({TORRENT: torrent, ED2K: ed2k})
        self.assertEqual(dispatcher.grab(Release(TORRENT, "x", content=TORRENT_BYTES)), "watch/x.torrent")
        ed2k.send.assert_not_called()

    def test_unconfigured_client_raises(self):
        client = MagicMock(protocol=TORRENT)
        client.is_configured.return_value = False
        dispatcher = DownloadDispatcher({TORRENT: client})
        self.assertFalse(dispatcher.can_handle(TORRENT))
        with self.assertRaises(NoDownloadClient):
            dispatcher.grab(Release(TORRENT, "x"))
        with self.assertRaises(NoDownloadClient):
            dispatcher.grab(Release(ED2K, "x"))


class TestWolfmaxGrab(unittest.TestCase):
    def _provider(self, dispatcher):
        with patch("core.services.providers.wolfmax_provider.WolfmaxSearcher"), \
                patch("core.services.providers.wolfmax_provider.WolfmaxDownloader"):
            from core.services.providers.wolfmax_provider import WolfmaxSourceProvider

            db = MagicMock()
            db.get_episodic_categories.return_value = ["series"]
            db.filter_new_wolfmax_urls.side_effect = lambda urls: urls
            provider = WolfmaxSourceProvider(db, dispatcher)
        provider._downloader.fetch_torrent.return_value = ("Peli.torrent", TORRENT_BYTES)
        return provider, db

    def _movie(self):
        item = {"id": 5, "name": "Peli", "content_category": "movies"}
        offer = SourceOffer(provider="wolfmax", source_url="https://wolfmax4k.com/pelicula/abc")
        return item, offer

    def test_grabbed_torrent_goes_to_dispatcher_and_is_marked_done(self):
        dispatcher = MagicMock()
        dispatcher.can_handle.return_value = True
        dispatcher.grab.return_value = "W:/watch/Peli.torrent"
        provider, db = self._provider(dispatcher)
        item, offer = self._movie()

        handle = provider.resolve_download(item, offer)

        release = dispatcher.grab.call_args.args[0]
        self.assertEqual((release.protocol, release.filename, release.content), (TORRENT, "Peli.torrent", TORRENT_BYTES))
        db.mark_wolfmax_urls_done.assert_called_once_with([offer.source_url], series_id=5)
        self.assertEqual((handle.kind, handle.payload), (TORRENT, {"grabbed": ["W:/watch/Peli.torrent"]}))

    def test_client_failure_leaves_url_pending(self):
        dispatcher = MagicMock()
        dispatcher.can_handle.return_value = True
        dispatcher.grab.side_effect = OSError("disco lleno")
        provider, db = self._provider(dispatcher)
        item, offer = self._movie()

        self.assertIsNone(provider.resolve_download(item, offer))
        db.mark_wolfmax_urls_done.assert_not_called()

    def test_no_torrent_client_skips_without_touching_site(self):
        dispatcher = MagicMock()
        dispatcher.can_handle.return_value = False
        provider, _ = self._provider(dispatcher)
        item, offer = self._movie()

        self.assertIsNone(provider.resolve_download(item, offer))
        provider._downloader.fetch_torrent.assert_not_called()


class TestWatchDirSetting(unittest.TestCase):
    def test_falls_back_to_legacy_wolfmax_staging_dir(self):
        from config.settings import Settings

        with patch.object(Settings, "TORRENT_WATCH_DIR", ""), patch.object(Settings, "WOLFMAX_STAGING_DIR", "D:/staging"):
            self.assertEqual(Settings.get_torrent_watch_dir(), "D:/staging")
        with patch.object(Settings, "TORRENT_WATCH_DIR", "D:/watch"), patch.object(Settings, "WOLFMAX_STAGING_DIR", "D:/staging"):
            self.assertEqual(Settings.get_torrent_watch_dir(), "D:/watch")


if __name__ == "__main__":
    unittest.main()
