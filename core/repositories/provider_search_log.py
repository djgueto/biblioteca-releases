from __future__ import annotations


class ProviderSearchLog:
    """Cuándo se buscó por última vez cada ficha en cada proveedor y si se
    encontró. Sirve para buscar por turnos sin repetir cada ciclo lo que no
    está (las webs limitan las búsquedas y algunas tardan segundos)."""

    def __init__(self, db_manager):
        self.db = db_manager
        conn = self.db.get_connection()
        conn.execute('''
            CREATE TABLE IF NOT EXISTS provider_search_log (
                series_id INTEGER NOT NULL,
                provider TEXT NOT NULL,
                searched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                found INTEGER DEFAULT 0,
                PRIMARY KEY (series_id, provider)
            )
        ''')
        conn.commit()

    def record(self, series_id: int, provider: str, found: bool) -> None:
        conn = self.db.get_connection()
        conn.execute(
            "INSERT INTO provider_search_log (series_id, provider, searched_at, found) VALUES (?, ?, CURRENT_TIMESTAMP, ?) "
            "ON CONFLICT(series_id, provider) DO UPDATE SET searched_at = CURRENT_TIMESTAMP, found = excluded.found",
            (series_id, provider, 1 if found else 0),
        )
        conn.commit()

    def searched_recently(self, provider: str, days: int) -> set[int]:
        rows = self.db.get_connection().execute(
            "SELECT series_id FROM provider_search_log WHERE provider = ? AND searched_at >= datetime('now', ?)",
            (provider, f"-{int(days)} days"),
        ).fetchall()
        return {r[0] for r in rows}
