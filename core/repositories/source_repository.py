from __future__ import annotations

from urllib.parse import parse_qs, urlparse


def source_key(url: str) -> str:
    """Forma comparable de una URL de ficha. En Hispashare la identifica su
    `id` (?view=title&id=77), escriba como escriba el resto de la URL."""
    url = (url or "").strip()
    parsed = urlparse(url)
    if "hispashare" in parsed.netloc.lower() or not parsed.netloc:  # también "/?view=title&id=77"
        title_id = (parse_qs(parsed.query).get("id") or [None])[0]
        if title_id:
            return f"hispashare:{title_id}"
    return url.lower().rstrip("/")


class SourceRepository:
    def __init__(self, db_manager):
        self.db = db_manager
        self._ensure_table()

    def _ensure_table(self):
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS source_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series_id INTEGER NOT NULL,
                provider TEXT NOT NULL DEFAULT 'hispashare',
                source_url TEXT,
                source_title TEXT,
                cover_url TEXT,
                match_score REAL DEFAULT 0,
                match_status TEXT DEFAULT 'pending',
                validation_reason TEXT,
                validated_at TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_source_matches_series_id "
            "ON source_matches(series_id)"
        )
        # Fichas que el usuario marcó como incorrectas para un elemento: la
        # búsqueda automática nunca vuelve a elegirlas para él.
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS rejected_sources (
                series_id INTEGER NOT NULL,
                provider TEXT NOT NULL,
                source_key TEXT NOT NULL,
                source_url TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (series_id, provider, source_key)
            )
            """
        )
        conn.commit()

    # --- fichas rechazadas ---------------------------------------------------
    def reject(self, series_id: int, source_url: str, provider: str = "hispashare") -> None:
        conn = self.db.get_connection()
        conn.execute(
            "INSERT OR IGNORE INTO rejected_sources (series_id, provider, source_key, source_url) VALUES (?, ?, ?, ?)",
            (series_id, provider, source_key(source_url), source_url),
        )
        conn.commit()

    def unreject(self, series_id: int, source_url: str, provider: str = "hispashare") -> None:
        conn = self.db.get_connection()
        conn.execute(
            "DELETE FROM rejected_sources WHERE series_id = ? AND provider = ? AND source_key = ?",
            (series_id, provider, source_key(source_url)),
        )
        conn.commit()

    def rejected_keys(self, series_id: int, provider: str = "hispashare") -> set[str]:
        rows = self.db.get_connection().execute(
            "SELECT source_key FROM rejected_sources WHERE series_id = ? AND provider = ?", (series_id, provider)
        ).fetchall()
        return {row[0] for row in rows}

    def is_rejected(self, series_id: int, source_url: str, provider: str = "hispashare") -> bool:
        return bool(source_url) and source_key(source_url) in self.rejected_keys(series_id, provider)

    def list_rejected(self, series_id: int) -> list[dict]:
        rows = self.db.get_connection().execute(
            "SELECT provider, source_url, created_at FROM rejected_sources WHERE series_id = ? ORDER BY created_at DESC",
            (series_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_current(self, series_id: int, provider: str = "hispashare") -> dict | None:
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT *
            FROM source_matches
            WHERE series_id = ? AND provider = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (series_id, provider),
        )
        row = cursor.fetchone()
        if row:
            return dict(row)

        # El fallback a la columna legacy series.hispashare_url solo tiene
        # sentido para Hispashare -- es el único proveedor que llegó a
        # escribir ahí antes de que existiera source_matches.
        if provider != "hispashare":
            return None

        item = self.db.get_series_by_id(series_id)
        if not item:
            return None
        url = (item.get("hispashare_url") or "").strip()
        if not url:
            return None
        return {
            "series_id": series_id,
            "provider": "hispashare",
            "source_url": url,
            "source_title": item.get("tmdb_name") or item.get("name", ""),
            "cover_url": item.get("hispashare_cover_url") or "",
            "match_score": 0,
            "match_status": "legacy",
            "validation_reason": "Importado desde tabla series",
        }

    def get_current_all_providers(self, series_id: int) -> list[dict]:
        """Devuelve la fila 'current' de cada proveedor que tenga algo
        registrado para esta serie (source_matches, o la legacy de
        Hispashare si no hay filas todavía)."""
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT DISTINCT provider FROM source_matches WHERE series_id = ?",
            (series_id,),
        )
        providers = {row["provider"] for row in cursor.fetchall()}
        providers.add("hispashare")

        results = []
        for provider in providers:
            current = self.get_current(series_id, provider)
            if current:
                results.append(current)
        return results

    def _url_claimed_by_another_series(self, series_id: int, source_url: str, provider: str) -> int | None:
        """Devuelve el series_id de OTRA serie que ya tenga esta misma
        source_url confirmada, o None si está libre.

        Dos series distintas confirmando la misma página de un proveedor
        casi nunca es legítimo -- así apareció el caso real de "Kung Fu
        Panda: El caballero del dragón" descargándose repetidamente porque
        su URL de Hispashare también estaba asignada a otros dos spin-offs
        (ver scripts/dedupe_hispashare_matches.py). Mejor negarse a
        duplicar la asignación y dejarla para revisión que contaminar otra
        serie en silencio."""
        if not source_url:
            return None
        conn = self.db.get_connection()
        cursor = conn.cursor()
        # Solo cuenta la fila vigente (la más reciente) de cada serie: una
        # 'confirmed' antigua seguida de 'cleared'/'stale' ya no reclama la URL.
        # Sin esto, una URL liberada con clear_current() nunca podía
        # reasignarse a su serie correcta.
        cursor.execute(
            """
            SELECT m.series_id FROM source_matches m
            WHERE m.provider = ? AND m.source_url = ? AND m.match_status = 'confirmed'
              AND m.series_id != ?
              AND m.id = (
                  SELECT MAX(id) FROM source_matches
                  WHERE series_id = m.series_id AND provider = m.provider
              )
            ORDER BY m.id DESC LIMIT 1
            """,
            (provider, source_url, series_id),
        )
        row = cursor.fetchone()
        if row:
            return row["series_id"]
        if provider == "hispashare":
            cursor.execute(
                "SELECT id FROM series WHERE hispashare_url = ? AND id != ? LIMIT 1",
                (source_url, series_id),
            )
            row = cursor.fetchone()
            if row:
                return row["id"]
        return None

    def save_match(self, series_id: int, payload: dict, provider: str | None = None) -> bool:
        payload = dict(payload or {})
        provider = provider or payload.get("provider", "hispashare")
        source_url = (payload.get("source_url") or "").strip()
        cover_url = payload.get("cover_url") or ""
        match_status = payload.get("match_status", "confirmed")

        if match_status == "confirmed" and self.is_rejected(series_id, source_url, provider):
            # Lo encontrado automáticamente nunca pisa un "esta no es" del
            # usuario (que sí puede volver a ponerla a mano: 'manual').
            return False

        if match_status == "confirmed":
            conflicting_series_id = self._url_claimed_by_another_series(series_id, source_url, provider)
            if conflicting_series_id is not None:
                current = self.get_current(series_id, provider) or {}
                if current.get("match_status") == "conflict" and current.get("source_url") == source_url:
                    # Ya registrado: no acumular una fila idéntica por reintento.
                    return False
                self._append_status_row(
                    series_id,
                    provider=provider,
                    source_url=source_url,
                    source_title=payload.get("source_title", ""),
                    cover_url=cover_url,
                    match_status="conflict",
                    validation_reason=(
                        f"URL ya asignada a otra serie distinta (id={conflicting_series_id}); "
                        "se omite para evitar contaminación cruzada de episodios/descargas"
                    ),
                    match_score=payload.get("match_score", 0),
                )
                return False

        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO source_matches(
                series_id,
                provider,
                source_url,
                source_title,
                cover_url,
                match_score,
                match_status,
                validation_reason,
                validated_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (
                series_id,
                provider,
                source_url,
                payload.get("source_title", ""),
                cover_url,
                payload.get("match_score", 0),
                match_status,
                payload.get("validation_reason", ""),
            ),
        )
        conn.commit()

        # Las columnas legacy series.hispashare_url/hispashare_cover_url solo
        # deben reflejar a Hispashare -- antes de parametrizar por proveedor
        # esto se hacía siempre sin condición, lo cual habría corrompido esas
        # columnas en cuanto algo guardara un match con otro proveedor.
        if provider == "hispashare":
            self.db.update_hispashare_url(series_id, source_url)
            self.db.update_hispashare_cover_url(series_id, cover_url or None)

        return True

    def mark_stale(self, series_id: int, reason: str, provider: str = "hispashare"):
        current = self.get_current(series_id, provider)
        item = self.db.get_series_by_id(series_id) or {}
        legacy_url = item.get("hispashare_url") or "" if provider == "hispashare" else ""
        legacy_cover = item.get("hispashare_cover_url") or "" if provider == "hispashare" else ""
        self._append_status_row(
            series_id,
            provider=provider,
            source_url=(current or {}).get("source_url") or legacy_url,
            source_title=(current or {}).get("source_title") or item.get("tmdb_name") or item.get("name", ""),
            cover_url=(current or {}).get("cover_url") or legacy_cover,
            match_status="stale",
            validation_reason=reason,
            match_score=(current or {}).get("match_score", 0),
        )

    def mark_stale_all_providers(self, series_id: int, reason: str):
        """Invalida la fuente actual de TODOS los proveedores conocidos para
        esta serie, no solo Hispashare -- se usa cuando cambia el TMDB, ya
        que eso invalida por igual lo que tuviera guardado cualquier fuente."""
        for current in self.get_current_all_providers(series_id):
            self.mark_stale(series_id, reason, provider=current.get("provider", "hispashare"))

    def confirm_current(self, series_id: int, reason: str, provider: str = "hispashare"):
        current = self.get_current(series_id, provider)
        item = self.db.get_series_by_id(series_id) or {}
        legacy_url = item.get("hispashare_url") or "" if provider == "hispashare" else ""
        legacy_cover = item.get("hispashare_cover_url") or "" if provider == "hispashare" else ""
        self._append_status_row(
            series_id,
            provider=provider,
            source_url=(current or {}).get("source_url") or legacy_url,
            source_title=(current or {}).get("source_title") or item.get("tmdb_name") or item.get("name", ""),
            cover_url=(current or {}).get("cover_url") or legacy_cover,
            match_status="confirmed",
            validation_reason=reason,
            match_score=(current or {}).get("match_score", 0),
        )

    def clear_current(self, series_id: int, reason: str, provider: str = "hispashare"):
        current = self.get_current(series_id, provider)
        self._append_status_row(
            series_id,
            provider=provider,
            source_url=(current or {}).get("source_url", ""),
            source_title=(current or {}).get("source_title", ""),
            cover_url=(current or {}).get("cover_url", ""),
            match_status="cleared",
            validation_reason=reason,
            match_score=(current or {}).get("match_score", 0),
        )
        if provider == "hispashare":
            self.db.update_hispashare_url(series_id, "")
            self.db.update_hispashare_cover_url(series_id, None)

    def _append_status_row(
        self,
        series_id: int,
        *,
        provider: str,
        source_url: str,
        source_title: str,
        cover_url: str,
        match_status: str,
        validation_reason: str,
        match_score: float = 0,
    ):
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO source_matches(
                series_id,
                provider,
                source_url,
                source_title,
                cover_url,
                match_score,
                match_status,
                validation_reason,
                validated_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (
                series_id,
                provider,
                source_url,
                source_title,
                cover_url,
                match_score,
                match_status,
                validation_reason,
            ),
        )
        conn.commit()
