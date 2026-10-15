"""
Caso Ted Lasso: se descargó en 720p (DonTorrent) teniendo 1080p en Hispashare.
- La oferta de Hispashare apuntaba a una ficha distinta de la de la serie y,
  al revisarla, se marcaba "no disponible" para siempre.
- Faltaba un suelo: no bajar de la calidad que la serie ya tiene en disco.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.database.db_manager import DatabaseManager
from core.repositories import SourceOfferRepository
from core.services import SourceArbitrationService
from core.services.providers.base import DownloadHandle, SourceOffer
from core.services.quality import Quality

SERIES_URL = "https://www.hispashare.org/?view=title&id=35471"
STALE_URL = "https://www.hispashare.org/?view=title&id=35472"


class TestHispashareOfferFollowsSeriesUrl(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")
        self.db.initialize_database()
        self.db.get_connection().execute(
            "INSERT INTO series (id, name, folder_path, hispashare_url) VALUES (1, 'Ted Lasso', '/s', ?)",
            (SERIES_URL,))
        self.offers = SourceOfferRepository(self.db)
        self.offers.upsert(1, SourceOffer(provider="hispashare", source_url=STALE_URL, quality=Quality()))
        self.offers.mark_unavailable(1, "hispashare")

    def tearDown(self):
        self.db.close()

    def test_stale_offer_is_replaced_by_series_url(self):
        provider = MagicMock()
        provider.refresh_offer.side_effect = lambda item, url: SourceOffer(
            provider="hispashare", source_url=url, quality=Quality(resolution_height=1080)) if url == SERIES_URL else None
        service = SourceArbitrationService(self.db, self.offers, {"hispashare": provider})

        service.refresh_all_offers(1, {"id": 1, "name": "Ted Lasso", "hispashare_url": SERIES_URL})

        provider.refresh_offer.assert_called_once()
        self.assertEqual(provider.refresh_offer.call_args[0][1], SERIES_URL)
        offer = self.offers.get(1, "hispashare")
        self.assertEqual((offer.source_url, offer.available, offer.quality.resolution_height),
                         (SERIES_URL, True, 1080))


class _Provider:
    def __init__(self, name):
        self.name = name
        self.calls = []

    def resolve_download(self, item, offer, *, missing_only=True):
        self.calls.append(item.get("min_resolution"))
        return DownloadHandle(provider=self.name, kind="torrent", payload={"grabbed": ["/w/x.torrent"]})


class TestQualityFloor(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")
        self.db.initialize_database()
        with patch("core.scheduler.DatabaseManager", return_value=self.db):
            from core.scheduler import Scheduler

            self.scheduler = Scheduler()
        self.scheduler.running = True
        conn = self.db.get_connection()
        conn.execute("INSERT INTO series (id, name, folder_path) VALUES (1, 'Ted Lasso', '/s')")
        conn.executemany(
            "INSERT INTO local_episodes (series_id, season, episode, filename, file_path, resolution_height, "
            "quality_source) VALUES (1, ?, ?, 'f', ?, ?, 'mediainfo')",
            [(1, 1, "/s/a", 720), (3, 1, "/s/b", 1080), (3, 2, "/s/c", 1080)])
        conn.execute("INSERT INTO missing_episodes (series_id, season, episode) VALUES (1, 4, 1)")
        conn.commit()
        patcher = patch("core.importer.pending.downloads_index", side_effect=Exception("sin carpetas"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.db.close()

    def test_floor_is_last_season_quality(self):
        self.assertEqual(self.scheduler._quality_floor(1), 1080)  # la T1 en 720p no cuenta

    def test_lower_offer_is_skipped_and_floor_passed_down(self):
        low, good = _Provider("dontorrent"), _Provider("hispashare")
        self.scheduler.providers.update({"dontorrent": low, "hispashare": good})
        offers = [SourceOffer(provider="dontorrent", source_url="u", quality=Quality(resolution_height=720)),
                  SourceOffer(provider="hispashare", source_url="h", quality=Quality())]  # calidad desconocida: vale
        total = self.scheduler._download_from_offers({"id": 1, "name": "Ted Lasso"}, offers)
        self.assertEqual(low.calls, [])
        self.assertEqual(good.calls, [1080])
        self.assertEqual(total, 1)


if __name__ == "__main__":
    unittest.main()
