"""
Emparejado automático de archivos únicos con series de TMDB
(core/tmdb/auto_matcher.py): un documental puede ser una miniserie.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.tmdb.auto_matcher import TMDBAutoMatcher
from core.tmdb.metadata import allowed_media_types, media_type_for_item

SPACEY = {"id": 252727, "name": "Kevin Spacey al descubierto", "first_air_date": "2024-09-09"}


def _api(movies=(), multi=(), series=()):
    api = MagicMock()
    api.search_movies.return_value = list(movies)
    api.search_multi.return_value = list(multi)
    api.search_series.return_value = list(series)
    return api


class TestSingleFileMediaType(unittest.TestCase):
    def test_documentary_falls_back_to_tv(self):
        matcher = TMDBAutoMatcher(_api(series=[SPACEY]))
        match = matcher.match_content({"name": "Kevin Spacey al descubierto", "content_category": "documentaries"})
        self.assertEqual((match["tmdb_id"], match["media_type"]), (252727, "tv"))

    def test_documentary_prefers_good_movie(self):
        film = {"id": 5, "title": "Kevin Spacey al descubierto", "release_date": "2024-01-01"}
        api = _api(movies=[film], series=[SPACEY])
        match = TMDBAutoMatcher(api).match_content(
            {"name": "Kevin Spacey al descubierto", "content_category": "documentaries"})
        self.assertEqual((match["tmdb_id"], match["media_type"]), (5, "movie"))
        api.search_series.assert_not_called()

    def test_movies_never_match_tv(self):
        api = _api(multi=[{**SPACEY, "media_type": "tv"}], series=[SPACEY])
        match = TMDBAutoMatcher(api).match_content(
            {"name": "Kevin Spacey al descubierto", "content_category": "movies"})
        self.assertIsNone(match)
        api.search_series.assert_not_called()

    def test_series_match_is_tv(self):
        match = TMDBAutoMatcher(_api(series=[SPACEY])).match_content(
            {"name": "Kevin Spacey al descubierto", "content_category": "series"})
        self.assertEqual(match["media_type"], "tv")

    def test_media_type_helpers(self):
        self.assertEqual(allowed_media_types("documentaries"), ("movie", "tv"))
        self.assertEqual(allowed_media_types("movies"), ("movie",))
        self.assertEqual(allowed_media_types("series"), ("tv",))
        self.assertEqual(media_type_for_item({"content_category": "documentaries"}), "movie")
        self.assertEqual(media_type_for_item({"content_category": "documentaries", "tmdb_media_type": "tv"}), "tv")


if __name__ == "__main__":
    unittest.main()
