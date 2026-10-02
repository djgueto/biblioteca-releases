from __future__ import annotations

from core.services.providers.base import SourceOffer
from core.services.quality import Quality, hdr_from_name


class SourceOfferRepository:
    """Persistencia genérica de ofertas de proveedor por serie.

    A diferencia de SourceRepository (histórico append-only, hardcodeado a
    provider='hispashare'), esta tabla es un upsert real por (series_id,
    provider) y no asume ningún proveedor concreto.
    """

    def __init__(self, db_manager):
        self.db = db_manager
        self._ensure_table()

    def _ensure_table(self):
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS source_offers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series_id INTEGER NOT NULL,
                provider TEXT NOT NULL,
                source_url TEXT,
                resolution_height INTEGER,
                codec TEXT,
                hdr TEXT DEFAULT 'unknown',
                match_score REAL DEFAULT 0,
                available INTEGER DEFAULT 1,
                last_checked_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(series_id, provider)
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_source_offers_series ON source_offers(series_id)"
        )
        conn.commit()

    def upsert(self, series_id: int, offer: SourceOffer) -> None:
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO source_offers(
                series_id, provider, source_url, resolution_height, codec, hdr,
                match_score, available, last_checked_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(series_id, provider) DO UPDATE SET
                source_url = excluded.source_url,
                resolution_height = excluded.resolution_height,
                codec = excluded.codec,
                hdr = excluded.hdr,
                match_score = excluded.match_score,
                available = 1,
                last_checked_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                series_id,
                offer.provider,
                offer.source_url,
                offer.quality.resolution_height,
                offer.quality.codec,
                offer.quality.hdr.name.lower(),
                offer.match_score,
            ),
        )
        conn.commit()

    def get(self, series_id: int, provider: str) -> SourceOffer | None:
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM source_offers WHERE series_id = ? AND provider = ?",
            (series_id, provider),
        )
        row = cursor.fetchone()
        return self._row_to_offer(row) if row else None

    def get_all_for_series(self, series_id: int) -> list[SourceOffer]:
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM source_offers WHERE series_id = ?",
            (series_id,),
        )
        return [self._row_to_offer(row) for row in cursor.fetchall()]

    def best_for_series(self, series_id: int) -> SourceOffer | None:
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM source_offers WHERE series_id = ? AND available = 1",
            (series_id,),
        )
        offers = [self._row_to_offer(row) for row in cursor.fetchall()]
        if not offers:
            return None
        return max(offers, key=lambda o: o.quality.rank_tuple())

    def mark_unavailable(self, series_id: int, provider: str) -> None:
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE source_offers
            SET available = 0, last_checked_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE series_id = ? AND provider = ?
            """,
            (series_id, provider),
        )
        conn.commit()

    def _row_to_offer(self, row) -> SourceOffer:
        d = dict(row)
        return SourceOffer(
            provider=d['provider'],
            source_url=d.get('source_url') or '',
            quality=Quality(d.get('resolution_height'), d.get('codec'), hdr_from_name(d.get('hdr'))),
            match_score=float(d.get('match_score') or 0),
            available=bool(d.get('available', 1)),
        )
