from __future__ import annotations

import json

from core.discovery.rules import APPROVED, DISMISSED, IN_LIBRARY, WAITING


def ensure_placeholder_column(db_manager) -> None:
    """series.placeholder = 1: ficha "en camino" de una novedad aprobada que
    aún no está en disco. La limpieza no debe borrarla por no tener carpeta."""
    conn = db_manager.get_connection()
    columns = {r[1] for r in conn.execute("PRAGMA table_info(series)")}
    if "placeholder" not in columns:
        conn.execute("ALTER TABLE series ADD COLUMN placeholder INTEGER DEFAULT 0")
        conn.commit()


class WantedRepository:
    """Lista de seguimiento: novedades de TMDB con su estado y sus notas."""

    def __init__(self, db_manager):
        self.db = db_manager
        conn = self.db.get_connection()
        conn.execute('''
            CREATE TABLE IF NOT EXISTS wanted_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tmdb_id INTEGER NOT NULL,
                media_type TEXT NOT NULL,
                title TEXT,
                original_title TEXT,
                year INTEGER,
                release_date TEXT,
                category TEXT,
                poster_path TEXT,
                overview TEXT,
                genre_ids TEXT,
                tmdb_rating REAL,
                tmdb_votes INTEGER,
                imdb_id TEXT,
                imdb_rating REAL,
                imdb_votes INTEGER,
                status TEXT NOT NULL DEFAULT 'waiting',
                reason TEXT,
                source TEXT DEFAULT 'tmdb',
                series_id INTEGER,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                evaluated_at TIMESTAMP,
                UNIQUE (tmdb_id, media_type)
            )
        ''')
        conn.execute("CREATE INDEX IF NOT EXISTS idx_wanted_status ON wanted_items(status)")
        conn.commit()
        ensure_placeholder_column(db_manager)

    def get(self, wanted_id: int) -> dict | None:
        row = self.db.get_connection().execute("SELECT * FROM wanted_items WHERE id = ?", (wanted_id,)).fetchone()
        return self._row(row)

    def find(self, tmdb_id: int, media_type: str) -> dict | None:
        row = self.db.get_connection().execute(
            "SELECT * FROM wanted_items WHERE tmdb_id = ? AND media_type = ?", (tmdb_id, media_type)).fetchone()
        return self._row(row)

    def add(self, item: dict) -> int | None:
        """Añade si no estaba. Devuelve el id nuevo, o None si ya existía."""
        conn = self.db.get_connection()
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO wanted_items (tmdb_id, media_type, title, original_title, year, release_date,
                category, poster_path, overview, genre_ids, tmdb_rating, tmdb_votes, status, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (item["tmdb_id"], item["media_type"], item.get("title"), item.get("original_title"), item.get("year"),
             item.get("release_date"), item.get("category"), item.get("poster_path"), item.get("overview"),
             json.dumps(item.get("genre_ids") or []), item.get("tmdb_rating"), item.get("tmdb_votes"),
             item.get("status", WAITING), item.get("source", "tmdb")),
        )
        conn.commit()
        return cursor.lastrowid if cursor.rowcount else None

    def update(self, wanted_id: int, **fields) -> None:
        if not fields:
            return
        if "genre_ids" in fields:
            fields["genre_ids"] = json.dumps(fields["genre_ids"] or [])
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn = self.db.get_connection()
        conn.execute(f"UPDATE wanted_items SET {sets} WHERE id = ?", (*fields.values(), wanted_id))
        conn.commit()

    def list(self, statuses: list[str] | None = None, limit: int = 500) -> list[dict]:
        query, params = "SELECT * FROM wanted_items", []
        if statuses:
            query += f" WHERE status IN ({','.join('?' for _ in statuses)})"
            params = list(statuses)
        query += " ORDER BY COALESCE(release_date, '9999') DESC, id DESC LIMIT ?"
        rows = self.db.get_connection().execute(query, (*params, limit)).fetchall()
        return [self._row(r) for r in rows]

    def to_evaluate(self) -> list[dict]:
        return self.list([WAITING], limit=5000)

    def counts(self) -> dict:
        rows = self.db.get_connection().execute("SELECT status, COUNT(*) FROM wanted_items GROUP BY status").fetchall()
        return {r[0]: r[1] for r in rows}

    def approved_with_placeholder(self) -> list[dict]:
        return self.list([APPROVED], limit=5000)

    @staticmethod
    def _row(row) -> dict | None:
        if row is None:
            return None
        item = dict(row)
        item["genre_ids"] = json.loads(item.get("genre_ids") or "[]")
        return item


__all__ = ["WantedRepository", "ensure_placeholder_column", "APPROVED", "DISMISSED", "IN_LIBRARY", "WAITING"]
