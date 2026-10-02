"""
Tests de la caché de metadatos de TMDB (core/tmdb/metadata.py).
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import Settings
from core.database.db_manager import DatabaseManager
from core.tmdb.metadata import TmdbMetadataService, media_type_for_category, preferred_poster

DETAIL = {
    "id": 1663026, "title": "Beckham", "original_title": "Beckham", "release_date": "2026-05-01",
    "poster_path": "/checo.jpg", "backdrop_path": "/fondo.jpg", "overview": "Documental", "vote_average": 7.5,
    "vote_count": 120, "imdb_id": "tt1234567", "genres": [{"id": 99, "name": "Documental"}],
    "images": {"posters": [
        {"iso_639_1": "cs", "file_path": "/checo.jpg", "vote_average": 9},
        {"iso_639_1": "en", "file_path": "/ingles.jpg", "vote_average": 5},
        {"iso_639_1": "en", "file_path": "/ingles2.jpg", "vote_average": 6},
    ]},
}


class TestMetadata(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")
        self.db.initialize_database()
        self.client = MagicMock()
        self.client._make_request.return_value = DETAIL
        self.key = patch.object(Settings, "TMDB_API_KEY", "k")
        self.key.start()

    def tearDown(self):
        self.key.stop()
        self.db.close()

    def test_preferred_poster(self):
        self.assertEqual(preferred_poster(DETAIL), "/ingles2.jpg")
        self.assertEqual(preferred_poster({"images": {"posters": [{"iso_639_1": "es", "file_path": "/es.jpg"}, *DETAIL["images"]["posters"]]}}), "/es.jpg")
        self.assertIsNone(preferred_poster({}))

    def test_fetch_once_then_cache(self):
        service = TmdbMetadataService(self.db, client=self.client)
        meta = service.get(1663026, "movie")
        self.assertEqual((meta["poster_path"], meta["imdb_id"], meta["genre_ids"]), ("/ingles2.jpg", "tt1234567", [99]))
        service.get(1663026, "movie")
        self.assertEqual(self.client._make_request.call_count, 1)
        self.assertEqual(self.client._make_request.call_args.args[0], "movie/1663026")

    def test_poster_only_in_other_language_is_kept_apart(self):
        self.client._make_request.return_value = {**DETAIL, "images": {"posters": [DETAIL["images"]["posters"][0]]}}
        meta = TmdbMetadataService(self.db, client=self.client).get(1663026, "movie")
        self.assertEqual((meta["poster_path"], meta["poster_other_language"]), (None, "/checo.jpg"))

    def test_without_api_key_uses_only_cache(self):
        with patch.object(Settings, "TMDB_API_KEY", ""):
            self.assertIsNone(TmdbMetadataService(self.db, client=self.client).get(1, "tv"))
        self.client._make_request.assert_not_called()

    def test_media_type_by_category(self):
        self.assertEqual(media_type_for_category("series"), "tv")
        self.assertEqual(media_type_for_category("docuseries"), "tv")
        self.assertEqual(media_type_for_category("movies"), "movie")
        self.assertEqual(media_type_for_category("documentaries"), "movie")


if __name__ == "__main__":
    unittest.main()
