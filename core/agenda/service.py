"""Calendario de emisiones y estrenos (como el de Sonarr/Radarr).

- Episodios: para cada serie vigilada que sigue en emisión se guardan las
  fechas de emisión de TMDB en una ventana (de 2 semanas atrás a 4 meses
  vista). Solo se piden a TMDB las temporadas que caen en esa ventana.
- Películas: fechas de estreno de las novedades (lista de seguimiento).

Los episodios solo se buscan en las fuentes una vez emitidos: los
faltantes se calculan con only_aired=True (ver recalculate_missing_episodes
_for_series); el calendario muestra lo que viene y en qué estado está.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from config.settings import Settings

logger = logging.getLogger(__name__)

ENDED_STATUSES = {"Ended", "Canceled"}
DAYS_BACK = 14
DAYS_AHEAD = 120


class AgendaRepository:
    def __init__(self, db_manager):
        self.db = db_manager
        conn = self.db.get_connection()
        conn.execute('''
            CREATE TABLE IF NOT EXISTS agenda_episodes (
                series_id INTEGER NOT NULL,
                season INTEGER NOT NULL,
                episode INTEGER NOT NULL,
                title TEXT,
                air_date TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (series_id, season, episode)
            )
        ''')
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agenda_air_date ON agenda_episodes(air_date)")
        conn.commit()

    def replace_for_series(self, series_id: int, episodes: list[dict]) -> None:
        conn = self.db.get_connection()
        conn.execute("DELETE FROM agenda_episodes WHERE series_id = ?", (series_id,))
        conn.executemany(
            "INSERT OR REPLACE INTO agenda_episodes (series_id, season, episode, title, air_date) VALUES (?, ?, ?, ?, ?)",
            [(series_id, e["season"], e["episode"], e.get("title"), e["air_date"]) for e in episodes],
        )
        conn.commit()

    def prune(self, keep_series_ids: set[int], oldest: str) -> None:
        conn = self.db.get_connection()
        conn.execute("DELETE FROM agenda_episodes WHERE air_date < ?", (oldest,))
        if keep_series_ids:
            placeholders = ",".join("?" for _ in keep_series_ids)
            conn.execute(f"DELETE FROM agenda_episodes WHERE series_id NOT IN ({placeholders})", tuple(keep_series_ids))
        else:
            conn.execute("DELETE FROM agenda_episodes")
        conn.commit()


class AgendaService:
    def __init__(self, db_manager, *, tmdb_client=None, today: date | None = None, log=None):
        self.db = db_manager
        self.repo = AgendaRepository(db_manager)
        # Tablas que cruza entries(): se crean si aún no existen.
        from core.discovery.repository import WantedRepository
        from core.repositories.grab_history_repository import GrabHistoryRepository

        GrabHistoryRepository(db_manager)
        WantedRepository(db_manager)
        self._client = tmdb_client
        self.today = today or date.today()
        self.log = log or (lambda message: logger.info(message))

    @property
    def client(self):
        if self._client is None:
            from core.tmdb.api_client import TMDBClient

            self._client = TMDBClient(Settings.TMDB_API_KEY)
        return self._client

    # --- construcción --------------------------------------------------------
    def rebuild(self, *, days_back: int = DAYS_BACK, days_ahead: int = DAYS_AHEAD) -> dict:
        if not Settings.TMDB_API_KEY:
            return {"series": 0, "episodes": 0}
        start = (self.today - timedelta(days=days_back)).isoformat()
        end = (self.today + timedelta(days=days_ahead)).isoformat()
        categories = Settings.get_episodic_categories()
        rows = self.db.get_connection().execute(
            f"""SELECT id, tmdb_id FROM series WHERE tmdb_id IS NOT NULL AND COALESCE(monitored, 1) = 1
                AND COALESCE(needs_review, 0) = 0 AND content_category IN ({','.join('?' for _ in categories)})""",
            categories,
        ).fetchall()
        kept, total = set(), 0
        for row in rows:
            try:
                episodes = self._episodes_in_window(row["tmdb_id"], start, end)
            except Exception as e:
                logger.warning(f"Calendario: error con tmdb {row['tmdb_id']}: {e}")
                continue
            if episodes is None:
                continue  # serie terminada fuera de la ventana: no sale en el calendario
            kept.add(row["id"])
            self.repo.replace_for_series(row["id"], episodes)
            total += len(episodes)
        self.repo.prune(kept, start)
        return {"series": len(kept), "episodes": total}

    def _episodes_in_window(self, tmdb_id: int, start: str, end: str) -> list[dict] | None:
        details = self.client.get_series_details(tmdb_id) or {}
        last = (details.get("last_episode_to_air") or {})
        upcoming = (details.get("next_episode_to_air") or {})
        if details.get("status") in ENDED_STATUSES and (last.get("air_date") or "") < start:
            return None
        seasons = {s for s in (last.get("season_number"), upcoming.get("season_number")) if s}
        if not seasons and not details.get("number_of_seasons"):
            return None
        seasons = seasons or {details["number_of_seasons"]}
        episodes = []
        for season in sorted(seasons):
            data = self.client.get_season_details(tmdb_id, season) or {}
            for ep in data.get("episodes") or []:
                air_date = ep.get("air_date") or ""
                if start <= air_date <= end:
                    episodes.append({"season": season, "episode": ep["episode_number"],
                                     "title": ep.get("name"), "air_date": air_date})
        return episodes

    # --- consulta ---------------------------------------------------------------
    def entries(self, start: str, end: str) -> list[dict]:
        """Episodios y estrenos entre dos fechas, con su estado."""
        conn = self.db.get_connection()
        today = self.today.isoformat()
        rows = conn.execute(
            """
            SELECT a.series_id, a.season, a.episode, a.title, a.air_date,
                   COALESCE(NULLIF(s.tmdb_name, ''), s.name) AS series_name, s.content_category,
                   EXISTS (SELECT 1 FROM local_episodes le WHERE le.series_id = a.series_id
                           AND le.season = a.season AND le.episode = a.episode) AS have,
                   EXISTS (SELECT 1 FROM grab_history g WHERE g.series_id = a.series_id
                           AND g.season = a.season AND g.episode = a.episode) AS grabbed
            FROM agenda_episodes a JOIN series s ON s.id = a.series_id
            WHERE a.air_date BETWEEN ? AND ?
            ORDER BY a.air_date, series_name, a.season, a.episode
            """,
            (start, end),
        ).fetchall()
        entries = []
        for r in rows:
            entries.append({
                "kind": "episode", "date": r["air_date"], "series_id": r["series_id"],
                "title": r["series_name"], "subtitle": f"{r['season']}x{r['episode']:02d}" + (f" · {r['title']}" if r["title"] else ""),
                "category_label": Settings.get_category_label(r["content_category"]),
                "status": _status(r["air_date"], today, bool(r["have"]), bool(r["grabbed"])),
            })
        for w in conn.execute(
            """SELECT w.tmdb_id, w.title, w.release_date, w.category, w.status AS wanted_status, w.series_id,
                      w.poster_path
               FROM wanted_items w
               WHERE w.media_type = 'movie' AND w.release_date BETWEEN ? AND ?
                 AND w.status IN ('waiting', 'approved', 'in_library')""",
            (start, end),
        ).fetchall():
            have = w["wanted_status"] == "in_library"
            entries.append({
                "kind": "movie", "date": w["release_date"], "series_id": w["series_id"],
                "title": w["title"], "subtitle": "Estreno" + ("" if w["wanted_status"] != "waiting" else " · en espera de notas"),
                "category_label": Settings.get_category_label(w["category"]),
                "poster_path": w["poster_path"],
                "status": _status(w["release_date"], today, have, False),
            })
        entries.sort(key=lambda e: (e["date"], e["title"]))
        return entries


def _status(air_date: str, today: str, have: bool, grabbed: bool) -> str:
    if have:
        return "have"
    if air_date > today:
        return "upcoming"
    return "grabbed" if grabbed else "missing"

