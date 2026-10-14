from __future__ import annotations

# Proveedores cuyo envío (item_key) es una versión concreta y se puede vetar
# sin vetar la fuente entera.
PER_RELEASE_PROVIDERS = ("dontorrent", "wolfmax")
# Envíos más antiguos no se juzgan: a saber qué pasó entonces.
FAILED_LOOKBACK_DAYS = 60


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
        conn.execute('''
            CREATE TABLE IF NOT EXISTS release_blocklist (
                provider TEXT NOT NULL,
                item_key TEXT NOT NULL,
                series_id INTEGER,
                release_title TEXT,
                reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, item_key)
            )
        ''')
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

    def blocked_episodes(self, series: dict, days: int) -> set[tuple[int, int]]:
        """Episodios que no se deben volver a pedir: enviados hace poco a un
        cliente, o ya presentes en las carpetas de descargas (descargados o
        descargándose) aunque aún no estén en la biblioteca."""
        from core.importer.pending import downloads_index

        blocked = self.recently_grabbed_episodes(series["id"], days)
        try:
            blocked |= downloads_index(self.db).episodes_for(series)
        except Exception:
            pass  # sin índice de descargas: se queda en la regla de los días
        return blocked

    def movie_blocked(self, series: dict, days: int) -> bool:
        from core.importer.pending import downloads_index

        if self.recently_grabbed_item(series["id"], days):
            return True
        try:
            return downloads_index(self.db).has_movie(series)
        except Exception:
            return False

    def recently_grabbed_item(self, series_id: int, days: int) -> bool:
        """¿Se envió algo de esta ficha (p. ej. la película entera) hace poco?"""
        row = self.db.get_connection().execute(
            "SELECT 1 FROM grab_history WHERE series_id = ? AND created_at >= datetime('now', ?) LIMIT 1",
            (series_id, f"-{int(days)} days"),
        ).fetchone()
        return row is not None

    # --- descargas fallidas y lista negra --------------------------------------
    def is_blocklisted(self, provider: str, item_key: str) -> bool:
        return self.db.get_connection().execute(
            "SELECT 1 FROM release_blocklist WHERE provider = ? AND item_key = ?", (provider, item_key)
        ).fetchone() is not None

    def blocklist(self, provider: str, item_key: str, series_id: int | None, release_title: str = "",
                  reason: str = "") -> None:
        conn = self.db.get_connection()
        conn.execute(
            "INSERT OR REPLACE INTO release_blocklist (provider, item_key, series_id, release_title, reason) "
            "VALUES (?, ?, ?, ?, ?)",
            (provider, item_key, series_id, release_title, reason),
        )
        conn.commit()

    def unblock(self, provider: str, item_key: str) -> bool:
        conn = self.db.get_connection()
        cursor = conn.execute("DELETE FROM release_blocklist WHERE provider = ? AND item_key = ?",
                              (provider, item_key))
        conn.commit()
        return cursor.rowcount > 0

    def blocklist_for_series(self, series_id: int) -> list[dict]:
        rows = self.db.get_connection().execute(
            "SELECT provider, item_key, release_title, reason, created_at FROM release_blocklist "
            "WHERE series_id = ? ORDER BY created_at DESC",
            (series_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def detect_failed(self, days: int, *, max_age_days: int = FAILED_LOOKBACK_DAYS) -> list[dict]:
        """Envíos que no llegaron: hace más de `days` días, lo que pedían sigue
        faltando y no aparece en las carpetas de descargas. Cada uno pasa a la
        lista negra para que no se vuelva a pedir esa misma versión (se buscará
        otra u otra fuente). Solo proveedores cuyo envío es una versión
        concreta: el de Hispashare es la ficha entera y bloquearlo dejaría la
        serie sin esa fuente. Devuelve los que se marcan ahora."""
        from core.importer.pending import downloads_index

        conn = self.db.get_connection()
        marks = ",".join("?" for _ in PER_RELEASE_PROVIDERS)
        grabs = conn.execute(
            f"""SELECT g.provider, g.item_key, g.series_id, MAX(g.release_title) AS release_title,
                       MAX(g.created_at) AS last_grab
                FROM grab_history g
                WHERE g.provider IN ({marks}) AND g.series_id IS NOT NULL
                  AND NOT EXISTS (SELECT 1 FROM release_blocklist b
                                  WHERE b.provider = g.provider AND b.item_key = g.item_key)
                GROUP BY g.provider, g.item_key, g.series_id
                HAVING MAX(g.created_at) < datetime('now', ?) AND MAX(g.created_at) >= datetime('now', ?)""",
            (*PER_RELEASE_PROVIDERS, f"-{int(days)} days", f"-{int(max_age_days)} days"),
        ).fetchall()
        if not grabs:
            return []
        try:
            index = downloads_index(self.db)
        except Exception:
            index = None

        failed = []
        for grab in grabs:
            series = conn.execute(
                "SELECT id, name, tmdb_name, year, COALESCE(placeholder, 0) AS placeholder FROM series WHERE id = ?",
                (grab["series_id"],),
            ).fetchone()
            if series is None:
                continue
            series = dict(series)
            episodes = {(r[0], r[1]) for r in conn.execute(
                "SELECT season, episode FROM grab_history WHERE provider = ? AND item_key = ? AND season IS NOT NULL",
                (grab["provider"], grab["item_key"]),
            ).fetchall()}
            if episodes:
                missing = {(r[0], r[1]) for r in conn.execute(
                    "SELECT season, episode FROM missing_episodes WHERE series_id = ? AND status = 'missing'",
                    (series["id"],),
                ).fetchall()}
                still = episodes & missing
                # Llegó algo (o todo): no es un envío fallido.
                if len(still) < len(episodes):
                    continue
                if index is not None and still & index.episodes_for(series):
                    continue
            else:
                # Película: sigue "en camino" y no está en descargas.
                if not series["placeholder"] or (index is not None and index.has_movie(series)):
                    continue
            reason = f"No llegó en {int(days)} días"
            self.blocklist(grab["provider"], grab["item_key"], series["id"], grab["release_title"] or "", reason)
            failed.append({**dict(grab), "series_name": series.get("tmdb_name") or series["name"],
                           "episodes": sorted(episodes), "reason": reason})
        return failed

    def list_for_series(self, series_id: int, limit: int = 50) -> list[dict]:
        rows = self.db.get_connection().execute(
            "SELECT provider, season, episode, release_title, destination, created_at FROM grab_history "
            "WHERE series_id = ? ORDER BY id DESC LIMIT ?",
            (series_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
