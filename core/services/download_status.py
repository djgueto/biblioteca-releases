"""En qué punto está cada episodio que falta (como la cola de Sonarr).

No se guarda: se deduce cada vez del registro de envíos (grab_history) y de
lo que hay en las carpetas de descargas, así nunca se queda desfasado.

  buscando     nunca se ha enviado a descargar
  enviado      enviado hace menos de GRAB_RETRY_DAYS y aún no aparece
  descargando  en la carpeta de descargas, a medias
  descargado   en la carpeta de descargas, terminado: falta importarlo
  fallido      se envió hace más de GRAB_RETRY_DAYS y no llegó (solo torrents
               de una versión concreta; ver PER_RELEASE_PROVIDERS)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from core.repositories.grab_history_repository import PER_RELEASE_PROVIDERS

logger = logging.getLogger(__name__)

LABELS = {
    "buscando": "Buscando",
    "enviado": "Enviado",
    "descargando": "Descargando",
    "descargado": "Pendiente de importar",
    "fallido": "No llegó",
}


def _parse(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def missing_states(db, missing: list[dict], *, retry_days: int) -> dict[tuple[int, int, int], dict]:
    """(series_id, temporada, episodio) -> {"state", "provider", "grabbed_at"}
    para cada fila de `missing` (con series_id, season, episode)."""
    if not missing:
        return {}
    conn = db.get_connection()
    series_ids = sorted({m["series_id"] for m in missing})

    latest: dict[tuple[int, int, int], dict] = {}
    try:
        rows = conn.execute(
            "SELECT series_id, season, episode, provider, created_at FROM grab_history "
            "WHERE season IS NOT NULL ORDER BY created_at, id"
        ).fetchall()
    except Exception:
        rows = []  # sin tabla todavía: nunca se ha enviado nada
    for row in rows:
        latest[(row[0], row[1], row[2])] = {"provider": row[3], "grabbed_at": row[4]}

    progress: dict[int, dict[tuple[int, int], bool]] = {}
    try:
        from core.importer.pending import downloads_index

        index = downloads_index(db)
        names = {r[0]: {"id": r[0], "name": r[1], "tmdb_name": r[2]} for r in conn.execute(
            f"SELECT id, name, tmdb_name FROM series WHERE id IN ({','.join('?' for _ in series_ids)})",
            series_ids,
        ).fetchall()}
        for series_id in series_ids:
            if series_id in names:
                progress[series_id] = index.episode_progress(names[series_id])
    except Exception as e:
        logger.debug(f"Sin índice de descargas para el estado de faltantes: {e}")

    cutoff = datetime.utcnow() - timedelta(days=retry_days)  # created_at va en UTC (CURRENT_TIMESTAMP)
    states = {}
    for m in missing:
        key = (m["series_id"], m["season"], m["episode"])
        grab = latest.get(key) or {}
        finished = progress.get(m["series_id"], {}).get((m["season"], m["episode"]))
        if finished is not None:
            state = "descargado" if finished else "descargando"
        elif grab:
            grabbed = _parse(grab["grabbed_at"])
            # eMule (Hispashare) puede tardar semanas y se le reenvía solo:
            # "no llegó" solo para envíos de una versión concreta.
            expired = grabbed is not None and grabbed < cutoff
            state = "fallido" if expired and grab["provider"] in PER_RELEASE_PROVIDERS else "enviado"
        else:
            state = "buscando"
        states[key] = {"state": state, "provider": grab.get("provider"), "grabbed_at": grab.get("grabbed_at")}
    return states
