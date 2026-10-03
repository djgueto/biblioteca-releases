from __future__ import annotations

from config.settings import Settings


class LibraryRepository:
    """Consultas de solo lectura para las vistas de biblioteca (web/API).

    Agrega en una sola consulta lo que la vista necesita por elemento
    (faltantes, episodios locales, mejor resolución local, proveedores con
    oferta) para no hacer N consultas sobre una BD que suele estar en red.
    """

    def __init__(self, db_manager):
        self.db = db_manager
        from core.repositories.item_quality_repository import ItemQualityRepository

        ItemQualityRepository(db_manager)  # crea la tabla si aún no existe

    def list_items(self) -> list[dict]:
        cursor = self.db.get_connection().cursor()
        cursor.execute('''
            SELECT s.id, s.name, s.tmdb_name, s.tmdb_id, s.year, s.content_category,
                   s.folder_path, s.monitored, s.needs_review, s.confidence_score,
                   s.search_disabled, s.hispashare_url, s.hispashare_cover_url,
                   s.updated_at, COALESCE(s.placeholder, 0) AS placeholder,
                   COALESCE(me.missing_count, 0) AS missing_count,
                   COALESCE(le.local_count, 0) AS local_count,
                   COALESCE(le.max_resolution, iq.resolution_height) AS max_resolution,
                   iq.codec AS file_codec, iq.hdr AS file_hdr,
                   so.providers
            FROM series s
            LEFT JOIN (
                SELECT series_id, COUNT(*) AS missing_count
                FROM missing_episodes WHERE status = 'missing'
                GROUP BY series_id
            ) me ON me.series_id = s.id
            LEFT JOIN (
                SELECT series_id, COUNT(*) AS local_count,
                       -- solo la calidad leída del archivo: el nombre no es fiable
                       MAX(CASE WHEN quality_source = 'mediainfo' THEN resolution_height END) AS max_resolution
                FROM local_episodes GROUP BY series_id
            ) le ON le.series_id = s.id
            LEFT JOIN (
                SELECT series_id, GROUP_CONCAT(provider) AS providers
                FROM source_offers WHERE available = 1
                GROUP BY series_id
            ) so ON so.series_id = s.id
            LEFT JOIN item_quality iq ON iq.series_id = s.id AND iq.unreadable = 0 AND iq.source = 'mediainfo'
            ORDER BY COALESCE(NULLIF(s.tmdb_name, ''), s.name) COLLATE NOCASE
        ''')
        return [self._present(dict(row)) for row in cursor.fetchall()]

    def get_item(self, series_id: int) -> dict | None:
        item = self.db.get_series_by_id(series_id)
        if not item:
            return None
        item = dict(item)
        item["category_label"] = Settings.get_category_label(item.get("content_category"))
        item["content_mode"] = Settings.get_category_content_mode(item.get("content_category"))
        item["cover_url"] = self.cover_url(item)
        # Misma regla que la lista (_present): hay carátula si hay TMDB
        # (cartel) o Hispashare. Antes la ficha solo miraba Hispashare.
        item["has_cover"] = bool(item.get("tmdb_id")) or bool(item["cover_url"])
        row = self.db.get_connection().execute(
            "SELECT file_path, resolution_height, codec, hdr, source FROM item_quality WHERE series_id = ? AND unreadable = 0",
            (series_id,),
        ).fetchone()
        item["file_quality"] = dict(row) if row else None
        return item

    def list_missing(self, limit: int = 10000) -> list[dict]:
        cursor = self.db.get_connection().cursor()
        cursor.execute('''
            SELECT me.id, me.series_id, COALESCE(NULLIF(s.tmdb_name, ''), s.name, me.series_name) AS series_name,
                   s.content_category, me.season, me.episode, me.name, me.air_date
            FROM missing_episodes me
            JOIN series s ON s.id = me.series_id
            WHERE me.status = 'missing' AND COALESCE(s.monitored, 1) = 1
            ORDER BY me.air_date DESC, series_name, me.season, me.episode
            LIMIT ?
        ''', (limit,))
        return [dict(row) for row in cursor.fetchall()]

    def path_health(self) -> dict:
        """Cuántos elementos de la BD están dentro de alguna ruta configurada.

        Si casi ninguno lo está, la BD viene de otro sistema (p. ej. rutas
        UNC de Windows usadas desde Docker, donde la biblioteca es /media):
        un ciclo de escaneo crearía duplicados de toda la biblioteca.
        """
        from core.library.roots import LibraryRoots, canon

        roots = {alias for root in LibraryRoots(self.db).configured() for alias in root.aliases}

        cursor = self.db.get_connection().cursor()
        cursor.execute("SELECT folder_path FROM series")
        paths = [canon(row[0]) for row in cursor.fetchall()]
        matched = sum(1 for p in paths if any(p == r or p.startswith(r + "/") for r in roots))
        total = len(paths)
        return {
            "total": total,
            "in_configured_roots": matched,
            "roots": sorted(roots),
            "ok": total == 0 or matched / total >= 0.5,
        }

    def cover_url(self, item: dict) -> str:
        cover = (item.get("hispashare_cover_url") or "").strip()
        if cover:
            return cover
        return self.db.derive_hispashare_cover_url(item.get("hispashare_url") or "") or ""

    def _present(self, item: dict) -> dict:
        category = item.get("content_category")
        item["category_label"] = Settings.get_category_label(category)
        item["content_mode"] = Settings.get_category_content_mode(category)
        item["has_cover"] = bool(item.get("tmdb_id")) or bool(self.cover_url(item))
        item["providers"] = sorted(set((item.get("providers") or "").split(",")) - {""})
        if item.get("hispashare_url") and "hispashare" not in item["providers"]:
            item["providers"].append("hispashare")
        item["placeholder"] = bool(item.get("placeholder"))
        for key in ("monitored", "needs_review", "search_disabled"):
            item[key] = bool(item.get(key)) if item.get(key) is not None else key == "monitored"
        item.pop("hispashare_cover_url", None)
        return item
