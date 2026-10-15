"""
Wolfmax exige Cloudflare Turnstile para descargar (desde 2026-10-10): se
detecta, no se intenta saltar, y el proveedor deja de reintentar en el ciclo.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.services.providers.base import SourceOffer
from core.services.quality import Quality
from core.wolfmax import downloader as w

# Respuesta real de /api/descargas validate (2026-10-16).
TURNSTILE = {"status": 200, "data": {"status": "turnstile_required", "success": False, "verification": {
    "action": "download", "provider": "turnstile", "sitekey": "0x4AAAAAAFQKUllCFjdAL2eL"}}}


class TestWolfmaxVerification(unittest.TestCase):
    def test_turnstile_response_raises_verification_required(self):
        driver = MagicMock()
        driver.execute_async_script.return_value = TURNSTILE
        with self.assertRaises(w.WolfmaxVerificationRequired):
            w._api_post(driver, {"action": "validate"})

    def test_provider_stops_after_first_verification(self):
        from core.services.providers.wolfmax_provider import WolfmaxSourceProvider

        db = MagicMock()
        db.get_episodic_categories.return_value = ["series"]
        db.get_missing_episodes_for_series.return_value = [{"season": 4, "episode": n} for n in (1, 2, 3)]
        db.filter_new_wolfmax_urls.side_effect = lambda urls: urls
        dispatcher = MagicMock()
        dispatcher.can_handle.return_value = True
        with patch("core.services.providers.wolfmax_provider.WolfmaxSearcher"), \
                patch("core.services.providers.wolfmax_provider.WolfmaxDownloader"), \
                patch("core.services.providers.wolfmax_provider.GrabHistoryRepository") as history:
            history.return_value.blocked_episodes.return_value = set()
            provider = WolfmaxSourceProvider(db, dispatcher)
            provider._downloader.get_episodes.return_value = [
                {"url": f"https://wolfmax4k.com/serie/episodio/e{n}", "season": 4, "episodes": [n]} for n in (1, 2, 3)]
            provider._downloader.fetch_torrent.side_effect = w.WolfmaxVerificationRequired()
            item = {"id": 1, "name": "Ted Lasso", "content_category": "series"}
            offer = SourceOffer(provider="wolfmax", source_url="https://wolfmax4k.com/serie/x",
                                quality=Quality(resolution_height=2160))

            self.assertIsNone(provider.resolve_download(item, offer))
            self.assertIsNone(provider.resolve_download(item, offer))  # segunda serie: ni lo intenta

        self.assertTrue(provider.verification_required)
        self.assertEqual(provider._downloader.fetch_torrent.call_count, 1)


if __name__ == "__main__":
    unittest.main()
