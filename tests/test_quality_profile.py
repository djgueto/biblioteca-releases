"""
Tests del perfil de calidad (core/services/quality_profile.py) y de su uso en
el arbitraje entre proveedores.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.database.db_manager import DatabaseManager
from core.repositories.source_offer_repository import SourceOfferRepository
from core.services.providers.base import SourceOffer
from core.services.quality import Hdr, Quality
from core.services.quality_profile import QualityProfile, tier_of
from core.services.source_arbitration_service import SourceArbitrationService

Q4K_DV = Quality(2160, "x265", Hdr.DOLBY_VISION)
Q4K = Quality(2160, "x265")
Q1080_265 = Quality(1080, "x265")
Q1080_264 = Quality(1080, "x264")
Q720 = Quality(720, "x264")
QSD = Quality(480)
UNKNOWN = Quality()


class TestTiers(unittest.TestCase):
    def test_tier_of(self):
        self.assertEqual([tier_of(q) for q in (Q4K_DV, Q4K, Q1080_265, Q1080_264, Q720, QSD, UNKNOWN)],
                         ["2160p_dv", "2160p", "1080p_x265", "1080p", "720p", "sd", "unknown"])
        self.assertEqual(tier_of(Quality(2160, None, Hdr.HDR10)), "2160p_hdr")
        self.assertEqual(tier_of(Quality(1080)), "1080p")  # codec desconocido = x264


class TestProfile(unittest.TestCase):
    def test_default_order_matches_user_preference(self):
        p = QualityProfile()
        ordered = sorted([QSD, Q1080_264, Q4K, Q720, Q1080_265, Q4K_DV, UNKNOWN], key=p.rank, reverse=True)
        self.assertEqual(ordered, [Q4K_DV, Q4K, Q1080_265, Q1080_264, Q720, QSD, UNKNOWN])

    def test_custom_order(self):
        # Alguien que prefiere 1080p x265 a 4K (por espacio).
        p = QualityProfile(order=["1080p_x265", "2160p", "1080p"])
        self.assertEqual(p.compare(Q1080_265, Q4K), 1)
        # Los niveles que faltan se añaden al final en el orden por defecto.
        self.assertEqual(p.order[:4], ["1080p_x265", "2160p", "1080p", "2160p_dv"])

    def test_minimum(self):
        p = QualityProfile(minimum="1080p")
        self.assertTrue(p.is_acceptable(Q1080_264))
        self.assertFalse(p.is_acceptable(Q720))
        self.assertTrue(p.is_acceptable(UNKNOWN))  # Hispashare antes de visitar la ficha

    def test_upgrades(self):
        p = QualityProfile()
        self.assertTrue(p.is_upgrade(Q1080_264, Q4K))
        self.assertTrue(p.is_upgrade(Q1080_264, Q1080_265))
        self.assertFalse(p.is_upgrade(Q4K, Q1080_265))
        self.assertFalse(p.is_upgrade(Q1080_265, Quality(1080, "x265", Hdr.SDR)))  # mismo nivel
        self.assertFalse(p.is_upgrade(UNKNOWN, Q4K))  # sin calidad local no se compara
        self.assertFalse(p.is_upgrade(Q4K, UNKNOWN))

    def test_unknown_local_codec_is_not_upgraded_by_codec_alone(self):
        p = QualityProfile()
        local = Quality(1080)  # no sabemos si ya es x265
        self.assertFalse(p.is_upgrade(local, Q1080_265))
        self.assertTrue(p.is_upgrade(local, Q4K))

    def test_minimum_blocks_upgrade_targets(self):
        p = QualityProfile(order=["1080p_x265", "2160p"], minimum="1080p_x265")
        self.assertFalse(p.is_upgrade(Q720, Q1080_264))

    def test_persistence(self):
        db = DatabaseManager(":memory:")
        db.initialize_database()
        self.assertEqual(QualityProfile.load(db).to_dict(), QualityProfile().to_dict())
        QualityProfile(order=["1080p_x265"], minimum="720p").save(db)
        loaded = QualityProfile.load(db)
        self.assertEqual((loaded.order[0], loaded.minimum), ("1080p_x265", "720p"))
        db.close()


class TestArbitrationUsesProfile(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")
        self.db.initialize_database()
        self.repo = SourceOfferRepository(self.db)
        self.repo.upsert(1, SourceOffer("hispashare", "h", quality=Q1080_265))
        self.repo.upsert(1, SourceOffer("wolfmax", "w", quality=Q4K))
        self.repo.upsert(1, SourceOffer("dontorrent", "d", quality=Q720))

    def tearDown(self):
        self.db.close()

    def test_quality_wins_regardless_of_provider(self):
        service = SourceArbitrationService(self.db, self.repo, {})
        self.assertEqual(service.best_offer(1).provider, "wolfmax")
        service = SourceArbitrationService(self.db, self.repo, {}, profile=QualityProfile(order=["1080p_x265"]))
        self.assertEqual(service.best_offer(1).provider, "hispashare")

    def test_profile_saved_from_web_applies_without_restart(self):
        service = SourceArbitrationService(self.db, self.repo, {})
        QualityProfile(order=["720p"]).save(self.db)
        self.assertEqual(service.best_offer(1).provider, "dontorrent")

    def test_minimum_excludes_offers(self):
        service = SourceArbitrationService(self.db, self.repo, {}, profile=QualityProfile(minimum="2160p_dv"))
        self.assertIsNone(service.best_offer(1))

    def test_should_upgrade(self):
        service = SourceArbitrationService(self.db, self.repo, {})
        self.assertEqual(service.should_upgrade(1, Q1080_264), (True, service.best_offer(1)))
        self.assertFalse(service.should_upgrade(1, Q4K)[0])


if __name__ == "__main__":
    unittest.main()
