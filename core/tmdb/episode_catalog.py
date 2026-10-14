"""Lista de episodios de cada serie de TMDB, en caché.

El recálculo de faltantes pedía a TMDB, en cada ciclo y para cada serie, la
ficha y todas sus temporadas, aunque la serie terminara hace años. Ahora se
guarda la lista completa (también los episodios aún por emitir) y se vuelve a
pedir solo cuando caduca, según si la serie sigue en emisión. El filtro de
"ya emitido" se aplica al leer: un episodio que se estrena hoy cuenta como
faltante sin volver a preguntar a TMDB.

Si TMDB no responde (caído, límite de peticiones) se usa la copia guardada
aunque esté caducada. Sin copia se lanza TMDBUnavailable: una lista vacía o a
medias haría creer que la serie está completa y borraría sus faltantes.
"""

from __future__ import annotations

import json
import logging
from datetime import date

logger = logging.getLogger(__name__)

ENDED_STATUSES = {"Ended", "Canceled"}
AIRING_STATUSES = {"Returning Series", "In Production", "Planned", "Pilot"}
MAX_AGE_DAYS_AIRING = 2
MAX_AGE_DAYS_ENDED = 30
MAX_AGE_DAYS_OTHER = 7


class TMDBUnavailable(Exception):
    """TMDB no dio la lista completa y no hay copia guardada."""


def max_age_days(status: str | None) -> int:
    if status in ENDED_STATUSES:
        return MAX_AGE_DAYS_ENDED
    if status in AIRING_STATUSES:
        return MAX_AGE_DAYS_AIRING
    return MAX_AGE_DAYS_OTHER


class EpisodeCatalog:
    def __init__(self, db_manager, client=None, *, today: date | None = None):
        self.db = db_manager
        self._client = client
        self.today = today
        conn = self.db.get_connection()
        conn.execute('''
            CREATE TABLE IF NOT EXISTS tmdb_episode_cache (
                tmdb_id INTEGER PRIMARY KEY,
                status TEXT,
                episodes_json TEXT NOT NULL,
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()

    @property
    def client(self):
        if self._client is None:
            from config.settings import Settings
            from core.tmdb.api_client import TMDBClient

            self._client = TMDBClient(Settings.TMDB_API_KEY)
        return self._client

    # --- caché -------------------------------------------------------------
    def _cached(self, tmdb_id: int) -> dict | None:
        row = self.db.get_connection().execute(
            "SELECT status, episodes_json, "
            "julianday('now') - julianday(fetched_at) AS age_days FROM tmdb_episode_cache WHERE tmdb_id = ?",
            (tmdb_id,),
        ).fetchone()
        if row is None:
            return None
        return {"status": row[0], "episodes": json.loads(row[1]), "age_days": row[2] or 0}

    def _save(self, tmdb_id: int, status: str | None, episodes: list[dict]) -> None:
        conn = self.db.get_connection()
        conn.execute(
            "INSERT INTO tmdb_episode_cache (tmdb_id, status, episodes_json, fetched_at) "
            "VALUES (?, ?, ?, CURRENT_TIMESTAMP) ON CONFLICT(tmdb_id) DO UPDATE SET "
            "status = excluded.status, episodes_json = excluded.episodes_json, fetched_at = CURRENT_TIMESTAMP",
            (tmdb_id, status, json.dumps(episodes, ensure_ascii=False)),
        )
        conn.commit()

    def invalidate(self, tmdb_id: int | None = None) -> None:
        conn = self.db.get_connection()
        if tmdb_id is None:
            conn.execute("DELETE FROM tmdb_episode_cache")
        else:
            conn.execute("DELETE FROM tmdb_episode_cache WHERE tmdb_id = ?", (tmdb_id,))
        conn.commit()

    # --- TMDB --------------------------------------------------------------
    def _fetch(self, tmdb_id: int) -> tuple[str | None, list[dict]]:
        """Ficha y todas las temporadas, o TMDBUnavailable si falta algo."""
        details = self.client.get_series_details(tmdb_id)
        if not details:
            raise TMDBUnavailable(f"TMDB no devolvió la serie {tmdb_id}")
        episodes = []
        for season in range(1, int(details.get("number_of_seasons") or 0) + 1):
            data = self.client.get_season_details(tmdb_id, season)
            if not data or "episodes" not in data:
                raise TMDBUnavailable(f"TMDB no devolvió la temporada {season} de la serie {tmdb_id}")
            for ep in data["episodes"]:
                episodes.append({
                    "season_number": season,
                    "episode_number": ep["episode_number"],
                    "name": ep.get("name") or "",
                    "air_date": ep.get("air_date") or "",
                    "overview": ep.get("overview") or "",
                })
        return details.get("status"), episodes

    def series(self, tmdb_id: int, *, refresh: bool = False) -> tuple[str | None, list[dict]]:
        """(estado de la serie en TMDB, todos sus episodios)."""
        cached = None if refresh else self._cached(tmdb_id)
        if cached is not None and cached["age_days"] < max_age_days(cached["status"]):
            return cached["status"], cached["episodes"]
        try:
            status, episodes = self._fetch(tmdb_id)
        except TMDBUnavailable:
            stale = cached or self._cached(tmdb_id)
            if stale is None:
                raise
            logger.warning(f"TMDB no responde para {tmdb_id}: se usa la lista guardada")
            return stale["status"], stale["episodes"]
        self._save(tmdb_id, status, episodes)
        return status, episodes

    def episodes(self, tmdb_id: int, *, only_aired: bool = True, refresh: bool = False) -> list[dict]:
        _, episodes = self.series(tmdb_id, refresh=refresh)
        if not only_aired:
            return list(episodes)
        today = (self.today or date.today()).isoformat()
        # Sin fecha de emisión = aún no anunciada: no cuenta como faltante.
        return [ep for ep in episodes if ep.get("air_date") and ep["air_date"] <= today]
