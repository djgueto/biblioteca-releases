"""
Tests de las novedades (core/discovery) y de las notas de IMDb (core/imdb).
"""

import gzip
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import Settings
from core.database.db_manager import DatabaseManager
from core.discovery.repository import WantedRepository
from core.discovery.rules import (
    APPROVED, EXPIRED, IN_LIBRARY, REJECTED, WAITING, DiscoverySettings, classify_category, evaluate,
)
from core.discovery.service import DiscoveryService
from core.imdb.ratings import ImdbRatings

TODAY = date(2026, 10, 3)


class TestRules(unittest.TestCase):
    def setUp(self):
        self.s = DiscoverySettings(min_tmdb=6.0, min_imdb=6.0, min_tmdb_votes=50, min_imdb_votes=500,
                                   imdb_grace_days=30, max_wait_days=180)

    def ev(self, **kw):
        item = {"release_date": "2026-09-01", "tmdb_rating": 7.0, "tmdb_votes": 300, "imdb_rating": 7.2, "imdb_votes": 2000}
        return evaluate({**item, **kw}, self.s, TODAY)[0]

    def test_statuses(self):
        self.assertEqual(self.ev(), APPROVED)
        self.assertEqual(self.ev(release_date="2026-12-01"), WAITING)  # sin estrenar
        self.assertEqual(self.ev(tmdb_rating=5.4), REJECTED)
        self.assertEqual(self.ev(imdb_rating=5.9), REJECTED)
        self.assertEqual(self.ev(tmdb_rating=5.0, tmdb_votes=10), APPROVED)  # pocos votos TMDB, pero ya pasó la gracia con IMDb
        self.assertEqual(self.ev(release_date="2026-09-25", tmdb_votes=10), WAITING)  # recién estrenada, pocos votos
        self.assertEqual(self.ev(imdb_rating=None, imdb_votes=None, release_date="2026-09-25"), WAITING)
        self.assertEqual(self.ev(imdb_rating=None, imdb_votes=None), APPROVED)  # >30 días: basta TMDB
        self.assertEqual(self.ev(release_date="2026-01-01", tmdb_votes=3, imdb_votes=10), EXPIRED)

    def test_exactly_the_minimum_passes(self):
        self.assertEqual(self.ev(tmdb_rating=6.0, imdb_rating=6.0), APPROVED)

    def test_categories(self):
        self.assertEqual(classify_category("movie", [28]), "movies")
        self.assertEqual(classify_category("movie", [16, 10751]), "animated_movies")
        self.assertEqual(classify_category("movie", [99]), "documentaries")
        self.assertEqual(classify_category("tv", [18]), "series")
        self.assertEqual(classify_category("tv", [16]), "cartoons")
        self.assertEqual(classify_category("tv", [10762]), "cartoons")
        self.assertEqual(classify_category("tv", [99]), "docuseries")


def _discover_page(results):
    return {"results": results, "total_pages": 1}


MOVIE = {"id": 101, "title": "Película Buena", "release_date": "2026-09-01", "genre_ids": [28], "vote_average": 7.5, "vote_count": 400}
MOVIE_BAD = {"id": 102, "title": "Película Mala", "release_date": "2026-09-01", "genre_ids": [35], "vote_average": 4.0, "vote_count": 400}
SERIES = {"id": 201, "name": "Serie Nueva", "first_air_date": "2026-08-20", "genre_ids": [18], "vote_average": 8.0, "vote_count": 150}
OWNED = {"id": 301, "title": "Ya la tengo", "release_date": "2026-09-10", "genre_ids": [28], "vote_average": 8.0, "vote_count": 999}


class TestService(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.roots = {"movies": [str(Path(self.tmp.name) / "PELICULAS")], "series": [str(Path(self.tmp.name) / "SERIES")]}
        self.patches = [
            patch.object(Settings, "TMDB_API_KEY", "k"),
            patch.object(Settings, "get_paths_for_category", lambda c: self.roots.get(c, [])),
        ]
        for p in self.patches:
            p.start()
        self.db = DatabaseManager(":memory:")
        self.db.initialize_database()
        self.db.get_connection().execute(
            "INSERT INTO series (id, name, folder_path, content_category, tmdb_id) VALUES (1, 'Ya', '/x', 'movies', 301)")
        self.db.recalculate_missing_episodes_for_series = MagicMock(return_value=8)

        def request(endpoint, params=None):
            if endpoint == "discover/movie":
                return _discover_page([MOVIE, MOVIE_BAD, OWNED])
            if endpoint == "discover/tv":
                return _discover_page([SERIES])
            media, tmdb_id = endpoint.split("/")
            src = {101: MOVIE, 102: MOVIE_BAD, 201: SERIES}[int(tmdb_id)]
            return {**src, "imdb_id": f"tt{tmdb_id}", "images": {"posters": []}}

        self.client = MagicMock()
        self.client._make_request.side_effect = request
        self.imdb = MagicMock()
        self.imdb.get.side_effect = lambda imdb_id: {"tt101": (7.1, 5000), "tt102": (4.2, 5000), "tt201": (8.3, 900)}.get(imdb_id)
        self.service = DiscoveryService(self.db, tmdb_client=self.client, imdb=self.imdb, today=TODAY,
                                        settings=DiscoverySettings(pages=1))

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.db.close()
        self.tmp.cleanup()

    def _status(self, tmdb_id, media):
        return self.service.repo.find(tmdb_id, media)["status"]

    def test_run_adds_evaluates_and_creates_placeholders(self):
        result = self.service.run()
        self.assertEqual(result["added"], 3)  # la que ya está en la biblioteca no entra
        self.assertEqual(self._status(101, "movie"), APPROVED)
        self.assertEqual(self._status(102, "movie"), REJECTED)
        self.assertEqual(self._status(201, "tv"), APPROVED)
        rows = {r["tmdb_id"]: dict(r) for r in self.db.get_connection().execute(
            "SELECT tmdb_id, folder_path, content_category, placeholder, monitored FROM series WHERE placeholder = 1")}
        self.assertEqual(set(rows), {101, 201})
        self.assertEqual(Path(rows[101]["folder_path"]), Path(self.roots["movies"][0]) / "Película Buena (2026)")
        self.assertEqual(rows[201]["content_category"], "series")
        self.db.recalculate_missing_episodes_for_series.assert_called_once()  # solo la serie
        # Una segunda pasada no duplica nada.
        self.assertEqual(self.service.run()["added"], 0)
        self.assertEqual(self.db.get_connection().execute("SELECT COUNT(*) FROM series WHERE placeholder = 1").fetchone()[0], 2)

    def test_cleanup_never_deletes_placeholders(self):
        Path(self.roots["movies"][0]).mkdir(parents=True)
        self.service.run()
        self.db.cleanup_deleted_series()
        self.assertEqual(self.db.get_connection().execute("SELECT COUNT(*) FROM series WHERE placeholder = 1").fetchone()[0], 2)

    def test_reconcile_when_real_file_arrives_with_other_name(self):
        self.service.run()
        placeholder_id = self.service.repo.find(101, "movie")["series_id"]
        conn = self.db.get_connection()
        conn.execute("CREATE TABLE IF NOT EXISTS grab_history (id INTEGER PRIMARY KEY, series_id INTEGER)")
        conn.execute("INSERT INTO grab_history (series_id) VALUES (?)", (placeholder_id,))
        conn.execute("INSERT INTO series (id, name, folder_path, content_category, tmdb_id) VALUES (50, 'Pelicula.Buena.2026.2160p', '/p/x.mkv', 'movies', 101)")
        self.assertEqual(self.service.reconcile_placeholders(), 1)
        self.assertIsNone(conn.execute("SELECT 1 FROM series WHERE id = ?", (placeholder_id,)).fetchone())
        self.assertEqual(conn.execute("SELECT series_id FROM grab_history").fetchone()[0], 50)
        self.assertEqual(self._status(101, "movie"), IN_LIBRARY)

    def test_reconcile_when_folder_appears_with_planned_name(self):
        self.service.run()
        series_id = self.service.repo.find(201, "tv")["series_id"]
        folder = self.db.get_connection().execute("SELECT folder_path FROM series WHERE id = ?", (series_id,)).fetchone()[0]
        Path(folder).mkdir(parents=True)
        self.service.reconcile_placeholders()
        self.assertEqual(self.db.get_connection().execute("SELECT placeholder FROM series WHERE id = ?", (series_id,)).fetchone()[0], 0)

    def test_manual_approve_and_dismiss(self):
        self.service.run()
        bad = self.service.repo.find(102, "movie")
        self.assertIsNotNone(self.service.approve(bad["id"]))
        self.assertEqual(self._status(102, "movie"), APPROVED)
        self.service.dismiss(bad["id"])
        self.assertEqual(self.db.get_connection().execute("SELECT COUNT(*) FROM series WHERE tmdb_id = 102").fetchone()[0], 0)
        # Descartar nunca borra una ficha real.
        self.service.repo.update(bad["id"], series_id=1)
        self.service.dismiss(bad["id"])
        self.assertIsNotNone(self.db.get_connection().execute("SELECT 1 FROM series WHERE id = 1").fetchone())

    def test_disabled(self):
        self.service.settings.enabled = False
        self.assertEqual(self.service.run(), {"enabled": False})
        self.client._make_request.assert_not_called()

    def test_wanted_settings_persist(self):
        DiscoverySettings(min_tmdb=7.0, categories=["movies"]).save(self.db)
        loaded = DiscoverySettings.load(self.db)
        self.assertEqual((loaded.min_tmdb, loaded.categories), (7.0, ["movies"]))


class TestImdbRatings(unittest.TestCase):
    def test_load_dataset_and_lookup(self):
        tsv = "tconst\taverageRating\tnumVotes\ntt0000001\t5.7\t2100\ntt0133093\t8.7\t2200000\n"
        response = MagicMock(content=gzip.compress(tsv.encode()))
        response.raise_for_status.return_value = None
        session = MagicMock()
        session.get.return_value = response
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            ratings = ImdbRatings(Path(tmp), omdb_api_key="", session=session)
            self.assertTrue(ratings.ensure_dataset())
            self.assertEqual(ratings.get("tt0133093"), (8.7, 2200000))
            self.assertIsNone(ratings.get("tt9999999"))
            ratings.ensure_dataset()  # recién descargado: no vuelve a bajarlo
            self.assertEqual(session.get.call_count, 1)

    def test_omdb_fallback(self):
        session = MagicMock()
        session.get.return_value.json.return_value = {"Response": "True", "imdbRating": "7.4", "imdbVotes": "12,345"}
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            ratings = ImdbRatings(Path(tmp), omdb_api_key="key", session=session)
            self.assertEqual(ratings.get("tt1"), (7.4, 12345))


if __name__ == "__main__":
    unittest.main()
