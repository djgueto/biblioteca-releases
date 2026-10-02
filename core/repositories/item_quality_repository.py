from __future__ import annotations

from core.services.quality import Quality, hdr_from_name


class ItemQualityRepository:
    """Calidad real del archivo de las bibliotecas de archivo único
    (películas, documentales, conciertos...). Los episodios la guardan en
    local_episodes; sin esto, una película nunca podría mejorarse porque no
    habría con qué comparar.

    `item_path` es la ruta de la ficha cuando se midió: si cambia (archivo
    sustituido por otro, biblioteca reubicada), se vuelve a medir.
    """

    def __init__(self, db_manager):
        self.db = db_manager
        conn = self.db.get_connection()
        conn.execute('''
            CREATE TABLE IF NOT EXISTS item_quality (
                series_id INTEGER PRIMARY KEY,
                item_path TEXT NOT NULL,
                file_path TEXT,
                resolution_height INTEGER,
                codec TEXT,
                hdr TEXT,
                source TEXT,
                unreadable INTEGER DEFAULT 0,
                checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (series_id) REFERENCES series(id) ON DELETE CASCADE
            )
        ''')
        conn.commit()

    def pending(self, categories: list[str], limit: int = 300) -> list[dict]:
        if not categories:
            return []
        placeholders = ",".join("?" for _ in categories)
        rows = self.db.get_connection().execute(
            f"""
            SELECT s.id, s.name, s.folder_path
            FROM series s
            LEFT JOIN item_quality iq ON iq.series_id = s.id
            WHERE s.content_category IN ({placeholders})
              AND COALESCE(s.placeholder, 0) = 0
              AND (iq.series_id IS NULL
                   OR iq.item_path != s.folder_path
                   OR COALESCE(iq.source, '') != 'mediainfo' AND iq.unreadable = 0
                   OR iq.unreadable = 1 AND iq.checked_at < datetime('now', '-7 days'))
            ORDER BY iq.series_id IS NOT NULL, s.id
            LIMIT ?
            """,
            (*categories, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def save(self, series_id: int, item_path: str, *, file_path: str | None, quality: dict | None, source: str | None):
        quality = quality or {}
        conn = self.db.get_connection()
        conn.execute(
            """
            INSERT INTO item_quality (series_id, item_path, file_path, resolution_height, codec, hdr, source, unreadable, checked_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(series_id) DO UPDATE SET
                item_path = excluded.item_path, file_path = excluded.file_path,
                resolution_height = excluded.resolution_height, codec = excluded.codec, hdr = excluded.hdr,
                source = excluded.source, unreadable = excluded.unreadable, checked_at = CURRENT_TIMESTAMP
            """,
            (series_id, item_path, file_path, quality.get("resolution_height"), quality.get("codec"),
             quality.get("hdr"), source, 0 if quality.get("resolution_height") else 1),
        )
        conn.commit()

    def counts(self, categories: list[str]) -> dict:
        if not categories:
            return {"total": 0, "measured": 0, "unreadable": 0}
        placeholders = ",".join("?" for _ in categories)
        row = self.db.get_connection().execute(
            f"""
            SELECT COUNT(*), SUM(iq.source = 'mediainfo' AND iq.unreadable = 0), SUM(iq.unreadable = 1)
            FROM series s LEFT JOIN item_quality iq ON iq.series_id = s.id
            WHERE s.content_category IN ({placeholders}) AND COALESCE(s.placeholder, 0) = 0
            """,
            categories,
        ).fetchone()
        return {"total": row[0] or 0, "measured": row[1] or 0, "unreadable": row[2] or 0}

    def get(self, series_id: int) -> Quality | None:
        row = self.db.get_connection().execute(
            "SELECT resolution_height, codec, hdr FROM item_quality WHERE series_id = ? AND unreadable = 0",
            (series_id,),
        ).fetchone()
        if not row:
            return None
        return Quality(row[0], row[1], hdr_from_name(row[2]))

    def upgrade_candidates(self, categories: list[str]) -> list[dict]:
        """Elementos vigilados con calidad conocida y alguna oferta."""
        if not categories:
            return []
        placeholders = ",".join("?" for _ in categories)
        rows = self.db.get_connection().execute(
            f"""
            SELECT s.id, s.name, s.tmdb_name, s.year, s.hispashare_url, s.tmdb_id, s.content_category, s.folder_path,
                   iq.resolution_height, iq.codec, iq.hdr
            FROM series s
            JOIN item_quality iq ON iq.series_id = s.id AND iq.unreadable = 0 AND iq.source = 'mediainfo'
            WHERE COALESCE(s.monitored, 1) = 1
              AND s.content_category IN ({placeholders})
              AND (
                  (s.hispashare_url IS NOT NULL AND s.hispashare_url != '')
                  OR EXISTS (SELECT 1 FROM source_offers so WHERE so.series_id = s.id AND so.available = 1)
              )
            ORDER BY s.name
            """,
            categories,
        ).fetchall()
        return [dict(r) for r in rows]
