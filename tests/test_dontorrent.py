"""
Tests de DonTorrent: análisis de páginas, cliente (PoW y límites del sitio)
y DontorrentSourceProvider (elección de ficha, respaldo, presupuesto e
historial de descargas).
"""

import hashlib
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.database.db_manager import DatabaseManager
from core.download_clients import TORRENT
from core.dontorrent.client import (
    DontorrentClient,
    DontorrentRateLimited,
    EpisodeLink,
    SearchResult,
    parse_episode_links,
    parse_result_text,
    parse_search_results,
    quality_from_label,
    solve_pow,
)
from core.repositories.grab_history_repository import GrabHistoryRepository
from core.repositories.source_offer_repository import SourceOfferRepository
from core.services.providers.dontorrent_provider import DontorrentSourceProvider

TORRENT_BYTES = b"d8:announce4:test4:infod4:name1:xee"

SEARCH_HTML = """
<div><p><a href="/serie/109029/109030/The-Boys-4-Temporada">The Boys - 4ª Temporada</a> <span>(HDTV)</span></p>
<p><a href="/serie/109028/109031/The-Boys-4-Temporada-720p">The Boys - 4ª Temporada [720p]</a> (HDTV-720p)</p>
<p><a href="/pelicula/1078/The-Boys-of-Ghost-Town">The Boys of Ghost Town.</a> (DVDRip) Película</p>
<p><a href="/serie/2111/2112/Los-hijos-de-Dune-Miniserie">Los hijos de Dune - Miniserie.</a> (DVDRip)</p></div>
"""

SERIES_HTML = """
<table><thead><tr><th>Episodios</th><th></th></tr></thead><tbody>
<tr><td>4x01</td><td><a class="protected-download" data-content-id="11" data-tabla="series">Descargar</a></td></tr>
<tr><td>4x02 al 4x03</td><td><a class="protected-download" data-content-id="12" data-tabla="series">Descargar</a></td></tr>
<tr><td>Sin número</td><td><a class="protected-download" data-content-id="13" data-tabla="series">Descargar</a></td></tr>
</tbody></table>
"""


class TestParsing(unittest.TestCase):
    def test_result_text(self):
        self.assertEqual(parse_result_text("Dune: La profecía  - 1ª Temporada [4K] (4K)"), ("Dune: La profecía", 1, "4K"))
        self.assertEqual(parse_result_text("Dune (BDremux-1080p) Película"), ("Dune", None, "BDremux-1080p"))
        self.assertEqual(parse_result_text("Los hijos de Dune - Miniserie. (DVDRip)"), ("Los hijos de Dune", 1, "DVDRip"))

    def test_quality_labels(self):
        self.assertEqual(quality_from_label("HDTV").resolution_height, 480)
        self.assertEqual(quality_from_label("HDTV-720p").resolution_height, 720)
        self.assertEqual(quality_from_label("BDremux-1080p").resolution_height, 1080)
        self.assertEqual(quality_from_label("4K").resolution_height, 2160)

    def test_search_results(self):
        results = parse_search_results(SEARCH_HTML, "https://dontorrent.test/")
        self.assertEqual([(r.kind, r.title, r.season, r.quality.resolution_height) for r in results], [
            ("serie", "The Boys", 4, 480),
            ("serie", "The Boys", 4, 720),
            ("pelicula", "The Boys of Ghost Town", None, 480),
            ("serie", "Los hijos de Dune", 1, 480),
        ])
        self.assertEqual(results[1].url, "https://dontorrent.test/serie/109028/109031/The-Boys-4-Temporada-720p")

    def test_episode_links_with_ranges(self):
        links = parse_episode_links(SERIES_HTML)
        self.assertEqual([(l.season, l.episodes, l.content_id) for l in links], [(4, (1,), 11), (4, (2, 3), 12)])


def _response(status=200, json_data=None, content=b""):
    r = MagicMock(status_code=status, content=content)
    r.json.return_value = json_data or {}
    r.raise_for_status.return_value = None
    return r


class TestSearchQueries(unittest.TestCase):
    def test_queries(self):
        from core.services.providers.dontorrent_provider import search_queries

        self.assertEqual(search_queries(["Dune: La profecía"]), ["Dune", "Dune La profecía"])
        self.assertEqual(search_queries(["Fast & Furious: Espías a todo gas"]), ["Fast", "Fast Furious Espías a todo gas"])
        self.assertEqual(search_queries(["The Boys", "the boys"]), ["The Boys"])
        self.assertEqual(search_queries(["Doc"]), ["Doc"])

    def test_accent_insensitive_match(self):
        from core.services.providers.dontorrent_provider import _comparable
        from core.wolfmax.category_scanner import _title_score

        self.assertEqual(_title_score(_comparable("Fisica o quimica La Nueva Generacion"),
                                      _comparable("Física o química: La nueva generación")), 1.0)


class TestClient(unittest.TestCase):
    def setUp(self):
        self.session = MagicMock()
        self.session.headers = {}
        self.client = DontorrentClient("https://dontorrent.test", delay=0, session=self.session)

    def test_solve_pow(self):
        nonce = solve_pow("abc")
        self.assertTrue(hashlib.sha256(f"abc{nonce}".encode()).hexdigest().startswith("000"))

    def test_fetch_torrent(self):
        self.session.post.side_effect = [
            _response(json_data={"success": True, "challenge": "abc"}),
            _response(json_data={"success": True, "download_url": "//dontorrent.test/torrents/series/Serie-4x01%5BHD%5D.torrent"}),
        ]
        self.session.get.return_value = _response(content=TORRENT_BYTES)
        name, data = self.client.fetch_torrent(11, "series")
        self.assertEqual((name, data), ("Serie-4x01[HD].torrent", TORRENT_BYTES))
        self.assertEqual(self.session.get.call_args.args[0], "https://dontorrent.test/torrents/series/Serie-4x01%5BHD%5D.torrent")
        self.assertEqual(self.session.post.call_args_list[0].kwargs["json"], {"action": "generate", "content_id": 11, "tabla": "series"})

    def test_search_waits_on_throttle_page_then_retries(self):
        throttled = "<p>Estás buscando demasiado rápido. Reintentar en 37 segundos. Límite: 15 búsquedas por minuto</p>"
        sleeps = []
        client = DontorrentClient("https://dontorrent.test", delay=0, session=self.session, sleep=sleeps.append)
        self.session.post.side_effect = [MagicMock(text=throttled), MagicMock(text=SEARCH_HTML)]
        self.assertEqual(len(client.search("The Boys")), 4)
        self.assertEqual(sleeps, [39])

    def test_search_gives_up_if_still_throttled(self):
        throttled = "<p>Estás buscando demasiado rápido. Reintentar en 5 segundos</p>"
        client = DontorrentClient("https://dontorrent.test", delay=0, session=self.session, sleep=lambda s: None)
        self.session.post.return_value = MagicMock(text=throttled)
        with self.assertRaises(DontorrentRateLimited):
            client.search("The Boys")

    def test_rate_limit_and_captcha(self):
        for validate in (_response(429, {"status": "limit_exceeded", "wait_minutes": 30}),
                         _response(200, {"status": "captcha_required"})):
            self.session.post.side_effect = [_response(json_data={"success": True, "challenge": "abc"}), validate]
            with self.assertRaises(DontorrentRateLimited):
                self.client.fetch_torrent(11, "series")
        self.session.get.assert_not_called()


def _result(season, height, url):
    from core.dontorrent.client import quality_from_label
    label = {480: "HDTV", 720: "HDTV-720p", 2160: "4K"}[height]
    return SearchResult(url=url, kind="serie", title="The Boys", season=season, quality_label=label,
                        quality=quality_from_label(label))


class TestProvider(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")
        self.db.initialize_database()
        conn = self.db.get_connection()
        conn.execute("INSERT INTO series (id, name, folder_path, content_category, tmdb_name) VALUES (1, 'the boys', '/s/tb', 'series', 'The Boys')")
        conn.executemany("INSERT INTO missing_episodes (series_id, season, episode, status) VALUES (1, ?, ?, 'missing')",
                         [(4, 1), (4, 2), (4, 3), (4, 4)])
        conn.commit()

        self.client = MagicMock()
        self.client.search.return_value = [
            _result(4, 480, "sd"), _result(4, 720, "hd"), _result(3, 2160, "t3-4k"),
            SearchResult(url="otra", kind="serie", title="The Office", season=4, quality_label="4K",
                         quality=quality_from_label("4K")),
        ]
        pages = {
            "hd": [EpisodeLink(4, (1,), 21, "series", "4x01"), EpisodeLink(4, (2, 3), 22, "series", "4x02 al 4x03")],
            "sd": [EpisodeLink(4, (1,), 11, "series", "4x01"), EpisodeLink(4, (4,), 14, "series", "4x04")],
        }
        self.client.episodes.side_effect = lambda url: pages[url]
        self.client.fetch_torrent.side_effect = lambda cid, tabla: (f"{cid}.torrent", TORRENT_BYTES)
        self.dispatcher = MagicMock()
        self.dispatcher.can_handle.return_value = True
        self.dispatcher.grab.side_effect = lambda release: f"/watch/{release.filename}"
        self.item = {"id": 1, "name": "the boys", "tmdb_name": "The Boys", "content_category": "series"}

    def tearDown(self):
        self.db.close()

    def _provider(self, **kw):
        return DontorrentSourceProvider(self.db, self.dispatcher, self.client, **kw)

    def test_offer_is_best_quality_for_seasons_with_missing(self):
        offer = self._provider().find_candidate(self.item)
        # T3 en 4K no cuenta: los faltantes son de la T4. "The Office" no casa.
        self.assertEqual((offer.provider, offer.source_url, offer.quality.resolution_height), ("dontorrent", "hd", 720))

    def test_no_offer_when_missing_season_is_not_published(self):
        self.client.search.return_value = [_result(3, 2160, "t3-4k")]
        self.assertIsNone(self._provider().find_candidate(self.item))
        self.assertIsNone(self._provider().refresh_offer(self.item, "t3-4k"))

    def test_grabs_best_page_and_falls_back_for_the_rest(self):
        handle = self._provider().resolve_download(self.item, None)
        grabbed_ids = [c.args[0] for c in self.client.fetch_torrent.call_args_list]
        self.assertEqual(grabbed_ids, [21, 22, 14])  # 720p para 1-3, SD solo para el 4
        self.assertEqual(handle.kind, TORRENT)
        self.assertEqual(len(handle.payload["grabbed"]), 3)
        grabbed = GrabHistoryRepository(self.db).recently_grabbed_episodes(1, 7)
        self.assertEqual(grabbed, {(4, 1), (4, 2), (4, 3), (4, 4)})

    def test_does_not_grab_twice(self):
        self._provider().resolve_download(self.item, None)
        self.client.fetch_torrent.reset_mock()
        self.assertIsNone(self._provider().resolve_download(self.item, None))
        self.client.fetch_torrent.assert_not_called()

    def test_budget_per_cycle(self):
        provider = self._provider(max_downloads_per_cycle=1)
        provider.resolve_download(self.item, None)
        self.assertEqual(self.client.fetch_torrent.call_count, 1)

    def test_rate_limit_stops_the_cycle(self):
        self.client.fetch_torrent.side_effect = DontorrentRateLimited("límite")
        provider = self._provider()
        self.assertIsNone(provider.resolve_download(self.item, None))
        self.assertEqual(self.client.fetch_torrent.call_count, 1)
        self.assertIsNone(provider.resolve_download(self.item, None))
        self.assertEqual(self.client.fetch_torrent.call_count, 1)

    def test_client_failure_keeps_episode_pending(self):
        self.dispatcher.grab.side_effect = OSError("disco lleno")
        self.assertIsNone(self._provider().resolve_download(self.item, None))
        self.assertEqual(GrabHistoryRepository(self.db).recently_grabbed_episodes(1, 7), set())

    def test_no_torrent_client_does_nothing(self):
        self.dispatcher.can_handle.return_value = False
        self.assertIsNone(self._provider().resolve_download(self.item, None))
        self.client.search.assert_not_called()

    def test_blocked_search_keeps_known_offer(self):
        from core.services.source_arbitration_service import SourceArbitrationService

        repo = SourceOfferRepository(self.db)
        provider = self._provider()
        repo.upsert(1, provider.find_candidate(self.item))
        self.client.search.side_effect = DontorrentRateLimited("límite")
        provider = self._provider()
        service = SourceArbitrationService(self.db, repo, {"dontorrent": provider})
        service.refresh_all_offers(1, self.item)
        offer = repo.get(1, "dontorrent")
        self.assertTrue(offer.available)
        self.assertEqual(offer.quality.resolution_height, 720)
        self.assertIsNone(provider.resolve_download(self.item, offer))

    def test_upgrades_only_episodes_below_the_page_quality(self):
        conn = self.db.get_connection()
        conn.execute("DELETE FROM missing_episodes")
        conn.executemany(
            "INSERT INTO local_episodes (series_id, season, episode, filename, file_path, resolution_height, codec, "
            "quality_source) VALUES (1, 4, ?, 'f', ?, ?, 'x264', ?)",
            [(1, "/e1", 480, "mediainfo"), (2, "/e2", 720, "mediainfo"), (3, "/e3", 480, "filename")])
        conn.commit()
        handle = self._provider().resolve_download(self.item, None, missing_only=False)
        # Página 720p: mejora el 1 (SD medido). El 2 ya es 720p y el 3 no está
        # medido con MediaInfo: no se tocan.
        self.assertEqual([c.args[0] for c in self.client.fetch_torrent.call_args_list], [21])
        self.assertEqual(len(handle.payload["grabbed"]), 1)

    def test_series_missing_without_offer(self):
        repo = SourceOfferRepository(self.db)
        self.assertEqual([s["id"] for s in repo.series_missing_without_offer("dontorrent", ["series"])], [1])
        repo.upsert(1, self._provider().find_candidate(self.item))
        self.assertEqual(repo.series_missing_without_offer("dontorrent", ["series"]), [])


if __name__ == "__main__":
    unittest.main()


class TestMovies(unittest.TestCase):
    def setUp(self):
        from core.dontorrent.client import MoviePage

        self.db = DatabaseManager(":memory:")
        self.db.initialize_database()
        self.db.get_connection().execute(
            "INSERT INTO series (id, name, folder_path, content_category, tmdb_name, year) "
            "VALUES (7, 'dune', '/m/Dune.mkv', 'movies', 'Dune', 2021)")
        self.client = MagicMock()
        self.client.search.return_value = [
            SearchResult("u-1984-4k", "pelicula", "Dune", None, "4K", quality_from_label("4K")),
            SearchResult("u-2021-1080", "pelicula", "Dune", None, "BDremux-1080p", quality_from_label("BDremux-1080p")),
            SearchResult("u-serie", "serie", "Dune", 1, "HDTV", quality_from_label("HDTV")),
        ]
        pages = {"u-1984-4k": MoviePage(1984, "4K", 501, "peliculas"),
                 "u-2021-1080": MoviePage(2021, "BDremux-1080p", 502, "peliculas")}
        self.client.movie.side_effect = lambda url: pages[url]
        self.client.fetch_torrent.side_effect = lambda cid, tabla: (f"{cid}.torrent", TORRENT_BYTES)
        self.dispatcher = MagicMock()
        self.dispatcher.can_handle.return_value = True
        self.dispatcher.grab.side_effect = lambda r: f"/watch/{r.filename}"
        self.item = {"id": 7, "name": "dune", "tmdb_name": "Dune", "year": 2021, "content_category": "movies"}

    def tearDown(self):
        self.db.close()

    def _provider(self):
        return DontorrentSourceProvider(self.db, self.dispatcher, self.client)

    def test_movie_offer_checks_year(self):
        offer = self._provider().find_candidate(self.item, content_mode="single_file")
        # El 4K es la de 1984: se descarta aunque sea mejor calidad.
        self.assertEqual((offer.source_url, offer.quality.resolution_height), ("u-2021-1080", 1080))

    def test_download_movie_once(self):
        provider = self._provider()
        offer = provider.find_candidate(self.item, content_mode="single_file")
        handle = provider.resolve_download(self.item, offer, missing_only=True)
        self.assertEqual(handle.payload["grabbed"], ["/watch/502.torrent"])
        self.assertTrue(GrabHistoryRepository(self.db).recently_grabbed_item(7, 7))
        self.assertIsNone(self._provider().resolve_download(self.item, offer, missing_only=True))

    def test_movie_upgrade_only_if_better_than_measured_file(self):
        from core.repositories.item_quality_repository import ItemQualityRepository

        repo = ItemQualityRepository(self.db)
        repo.save(7, "/m/Dune.mkv", file_path=None, quality={"resolution_height": 1080, "codec": "x264", "hdr": "sdr"},
                  source="mediainfo")
        provider = self._provider()
        offer = provider.find_candidate(self.item, content_mode="single_file")  # BDremux-1080p, códec desconocido
        self.assertIsNone(provider.resolve_download(self.item, offer, missing_only=False))
        repo.save(7, "/m/Dune.mkv", file_path=None, quality={"resolution_height": 720, "codec": "x264", "hdr": "sdr"},
                  source="mediainfo")
        self.assertIsNotNone(provider.resolve_download(self.item, offer, missing_only=False))
