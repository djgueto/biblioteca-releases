from __future__ import annotations

from config.settings import Settings
from core.auth.hispashare_login import HispashareAuth
from core.hispashare.searcher import HispashareSearcher


class SourceMatchService:
    def __init__(self, db_manager, source_repository, decision_log_repository):
        self.db = db_manager
        self.source_repository = source_repository
        self.decision_log_repository = decision_log_repository

    def search_new_source(self, series_id: int, *, log_callback=None) -> dict:
        return self.revalidate_or_search(series_id, force_recheck=False, log_callback=log_callback)

    def revalidate_current_source(self, series_id: int, *, log_callback=None) -> dict:
        return self.revalidate_or_search(series_id, force_recheck=True, log_callback=log_callback)

    def revalidate_or_search(self, series_id: int, *, force_recheck: bool = False, log_callback=None) -> dict:
        auth = None
        item = self.db.get_series_by_id(series_id)
        if not item:
            raise RuntimeError("Elemento no encontrado en la base de datos")

        existing_url = (item.get("hispashare_url") or "").strip()
        if existing_url and not force_recheck:
            self.source_repository.confirm_current(
                series_id,
                "Se conserva la URL existente porque no se pidió revalidación forzada",
            )
            self.decision_log_repository.append(
                series_id,
                "source_confirmed",
                "Se mantiene la URL Hispashare existente",
                {
                    "source_url": existing_url,
                    "force_recheck": False,
                },
            )
            return {
                "series_id": series_id,
                "found": True,
                "skipped": True,
                "url": existing_url,
                "updated_item": item,
                "force_recheck": False,
                "existing_url": existing_url,
                "matched_title": "",
                "kept_existing": True,
                "validated_existing": True,
                "replaced_existing": False,
            }

        query = (item.get("tmdb_name") or item.get("name") or "").strip()
        if not query:
            raise RuntimeError("No hay un nombre válido para buscar en Hispashare")
        if not Settings.HISPASHARE_USER or not Settings.HISPASHARE_PASS:
            raise RuntimeError("Credenciales de Hispashare no configuradas")

        content_mode = Settings.get_category_content_mode(item.get("content_category", "series"))
        year = item.get("year")

        self._emit(log_callback, f"🔐 Iniciando sesión en Hispashare para buscar '{query}'...")
        auth = HispashareAuth(
            Settings.HISPASHARE_USER,
            Settings.HISPASHARE_PASS,
            headless=Settings.SELENIUM_HEADLESS,
        )
        if not auth.login() or auth.driver is None:
            raise RuntimeError("No se pudo iniciar sesión en Hispashare")

        if force_recheck and existing_url:
            self._emit(log_callback, f"🔁 Revalidando URL de Hispashare con el nuevo TMDB para '{query}'...")
        else:
            self._emit(log_callback, f"🔎 Buscando URL de Hispashare para '{query}'...")

        searcher = HispashareSearcher(auth.driver, self.db)
        best_result = searcher.search_best_result(
            query,
            year,
            series_id,
            content_mode=content_mode,
        )
        url = (best_result or {}).get("url") or ""
        matched_title = (best_result or {}).get("title") or ""
        score = float((best_result or {}).get("score") or 0)

        kept_existing = False
        replaced_existing = False
        validated_existing = False

        try:
            if url:
                if existing_url and url == existing_url:
                    kept_existing = True
                    validated_existing = True
                    self.source_repository.confirm_current(
                        series_id,
                        "La URL existente sigue siendo la mejor coincidencia con el TMDB actual",
                    )
                    self.decision_log_repository.append(
                        series_id,
                        "source_confirmed",
                        "La URL Hispashare actual sigue siendo válida",
                        {
                            "source_url": url,
                            "matched_title": matched_title,
                            "match_score": score,
                        },
                    )
                    self._emit(
                        log_callback,
                        f"✅ La URL actual de Hispashare sigue siendo válida para '{query}'",
                    )
                else:
                    cover_url = self.db.derive_hispashare_cover_url(url) or ""
                    saved = self.source_repository.save_match(
                        series_id,
                        {
                            "source_url": url,
                            "source_title": matched_title or query,
                            "cover_url": cover_url,
                            "match_score": score,
                            "match_status": "confirmed",
                            "validation_reason": (
                                "URL reemplazada tras revalidación"
                                if existing_url
                                else "URL encontrada en búsqueda inmediata"
                            ),
                        },
                    )
                    if saved:
                        replaced_existing = bool(existing_url)
                        self.decision_log_repository.append(
                            series_id,
                            "source_replaced" if existing_url else "source_selected",
                            "Se guardó una URL Hispashare para el elemento",
                            {
                                "previous_url": existing_url,
                                "source_url": url,
                                "matched_title": matched_title,
                                "match_score": score,
                            },
                        )
                        self._emit(log_callback, f"✅ URL de Hispashare encontrada para '{query}'")
                    else:
                        self._emit(
                            log_callback,
                            f"⚠️ URL encontrada para '{query}' ya pertenece a otra serie; se omite",
                        )
            else:
                if existing_url and force_recheck:
                    kept_existing = True
                    self.source_repository.mark_stale(
                        series_id,
                        "No apareció una URL mejor tras revalidar con el nuevo TMDB",
                    )
                    self.decision_log_repository.append(
                        series_id,
                        "source_kept_stale",
                        "Se conserva la URL existente porque no apareció una mejor coincidencia",
                        {
                            "source_url": existing_url,
                            "query": query,
                        },
                    )
                    self._emit(
                        log_callback,
                        f"ℹ️ No apareció una URL mejor para '{query}'. Se conserva la existente.",
                    )
                else:
                    self.decision_log_repository.append(
                        series_id,
                        "source_not_found",
                        "No se encontró URL Hispashare para el elemento",
                        {
                            "query": query,
                            "force_recheck": force_recheck,
                        },
                    )
                    self._emit(log_callback, f"ℹ️ No se encontró URL de Hispashare para '{query}'")
        finally:
            if auth is not None:
                auth.close()

        updated_item = self.db.get_series_by_id(series_id)
        return {
            "series_id": series_id,
            "found": bool(url),
            "skipped": False,
            "url": url or "",
            "updated_item": updated_item,
            "force_recheck": force_recheck,
            "existing_url": existing_url,
            "matched_title": matched_title,
            "kept_existing": kept_existing,
            "validated_existing": validated_existing,
            "replaced_existing": replaced_existing,
        }

    def _emit(self, log_callback, message: str):
        if callable(log_callback):
            log_callback(message)
