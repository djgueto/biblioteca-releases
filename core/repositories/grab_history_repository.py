from __future__ import annotations


class GrabHistoryRepository:
    """Qué episodios se han entregado ya a un cliente de descarga.

    Común a todos los proveedores: evita pedir otra vez el mismo episodio
    (aunque sea en otra calidad o de otro proveedor) mientras el cliente lo
    está descargando y el escaneo de la biblioteca aún no lo ve en disco.
    """

    def __init__(self, db_manager):
        self.db = db_manager
        self._ensure_table()

    def _ensure_table(self):
        conn = self.db.get_connection()
        conn.execute('''
            CREATE TABLE IF NOT EXISTS grab_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                item_key TEXT NOT NULL,
                series_id INTEGER,
                season INTEGER,
                episode INTEGER,
                release_title TEXT,
                destination TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_grab_history_episode ON grab_history(series_id, season, episode)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_grab_history_item ON grab_history(provider, item_key)')
        conn.commit()

    def record(self, provider: str, item_key: str, series_id: int | None, episodes: list[tuple[int, int]],
               release_title: str = "", destination: str = ""):
        rows = [(provider, item_key, series_id, s, e, release_title, destination) for s, e in episodes] or \
               [(provider, item_key, series_id, None, None, release_title, destination)]
        conn = self.db.get_connection()
        conn.executemany(
            "INSERT INTO grab_history (provider, item_key, series_id, season, episode, release_title, destination) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()

    def recently_grabbed_episodes(self, series_id: int, days: int) -> set[tuple[int, int]]:
        rows = self.db.get_connection().execute(
            "SELECT season, episode FROM grab_history "
            "WHERE series_id = ? AND season IS NOT NULL AND created_at >= datetime('now', ?)",
            (series_id, f"-{int(days)} days"),
        ).fetchall()
        return {(r[0], r[1]) for r in rows}

    def recently_grabbed_item(self, series_id: int, days: int) -> bool:
        """¿Se envió algo de esta ficha (p. ej. la película entera) hace poco?"""
        row = self.db.get_connection().execute(
            "SELECT 1 FROM grab_history WHERE series_id = ? AND created_at >= datetime('now', ?) LIMIT 1",
            (series_id, f"-{int(days)} days"),
        ).fetchone()
        return row is not None

    def list_for_series(self, series_id: int, limit: int = 50) -> list[dict]:
        rows = self.db.get_connection().execute(
            "SELECT provider, season, episode, release_title, destination, created_at FROM grab_history "
            "WHERE series_id = ? ORDER BY id DESC LIMIT ?",
            (series_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
