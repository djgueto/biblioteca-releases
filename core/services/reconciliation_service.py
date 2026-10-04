from __future__ import annotations

from config.settings import Settings
from core.events.event_types import (
    MISSING_EPISODES_REBUILT,
    SERIES_UPDATED,
    SOURCE_MATCH_CHANGED,
    TMDB_CHANGED,
)


class ReconciliationService:
    def __init__(
        self,
        series_repository,
        source_repository,
        missing_episodes_service,
        source_match_service,
        decision_log_repository,
        event_bus=None,
        source_offer_repository=None,
    ):
        self.series_repository = series_repository
        self.source_repository = source_repository
        self.missing_episodes_service = missing_episodes_service
        self.source_match_service = source_match_service
        self.decision_log_repository = decision_log_repository
        self.event_bus = event_bus
        # Opcional para no romper llamadores antiguos que no lo pasen; sin él
        # simplemente no se invalidan las ofertas de source_offers (solo las
        # de source_matches vía mark_stale_all_providers).
        self.source_offer_repository = source_offer_repository

    def on_tmdb_changed(
        self,
        series_id: int,
        previous: dict,
        current: dict,
        *,
        is_library: bool = False,
        rebuild_missing: bool = True,
    ) -> dict:
        previous_tmdb_id = previous.get("tmdb_id")
        current_tmdb_id = current.get("tmdb_id")
        tmdb_changed = bool(current_tmdb_id) and previous_tmdb_id != current_tmdb_id
        if not tmdb_changed:
            return {
                "recalculated_missing": False,
                "missing_count": 0,
                "reset_hispashare_url": False,
                "recheck_hispashare_url": False,
                "previous_hispashare_url": "",
            }

        previous_url = (previous.get("hispashare_url") or "").strip()
        self.series_repository.mark_search_enabled(series_id)

        known_offers = (
            self.source_offer_repository.get_all_for_series(series_id)
            if self.source_offer_repository is not None
            else []
        )
        if previous_url or known_offers:
            # Un cambio de TMDB invalida lo que tuviera guardado CUALQUIER
            # proveedor, no solo Hispashare -- una serie que ya se estaba
            # descargando de Wolfmax también necesita revalidarse si ahora
            # apunta a otro título.
            reason = "La fuente quedó obsoleta porque se cambió el TMDB manualmente"
            self.source_repository.mark_stale_all_providers(series_id, reason)
            if self.source_offer_repository is not None:
                for offer in known_offers:
                    self.source_offer_repository.mark_unavailable(series_id, offer.provider)

        self.decision_log_repository.append(
            series_id,
            "tmdb_changed",
            "Se cambió la identidad TMDB y se invalidó la reconciliación dependiente",
            {
                "previous_tmdb_id": previous_tmdb_id,
                "current_tmdb_id": current_tmdb_id,
                "previous_hispashare_url": previous_url,
            },
        )

        recalculated_missing = False
        missing_count = 0
        if (
            rebuild_missing
            and Settings.get_category_content_mode(current.get("content_category", "series")) == "episodic"
        ):
            missing_result = self.missing_episodes_service.rebuild_for_series(series_id)
            recalculated_missing = bool(missing_result.get("recalculated_missing"))
            missing_count = int(missing_result.get("missing_count", 0))
            self._publish(
                MISSING_EPISODES_REBUILT,
                {"series_id": series_id, "missing_count": missing_count},
            )

        self._publish(
            TMDB_CHANGED,
            {
                "series_id": series_id,
                "previous_tmdb_id": previous_tmdb_id,
                "current_tmdb_id": current_tmdb_id,
            },
        )
        self._publish(
            SOURCE_MATCH_CHANGED,
            {
                "series_id": series_id,
                "status": "stale" if previous_url else "pending",
            },
        )
        self._publish(SERIES_UPDATED, {"series_id": series_id})
        return {
            "recalculated_missing": recalculated_missing,
            "missing_count": missing_count,
            "reset_hispashare_url": False,
            "recheck_hispashare_url": bool(current_tmdb_id),
            "previous_hispashare_url": previous_url,
        }

    def on_manual_update(
        self,
        series_id: int,
        previous: dict,
        current: dict,
        changes: dict,
        *,
        is_library: bool = False,
    ) -> dict:
        if "hispashare_url" in changes:
            manual_url = (changes.get("hispashare_url") or "").strip()
            if manual_url:
                # Ponerla a mano anula un "esta no es" anterior.
                self.source_repository.unreject(series_id, manual_url)
                if self.source_offer_repository is not None:
                    current_offer = self.source_offer_repository.get(series_id, "hispashare")
                    if current_offer and current_offer.source_url != manual_url:
                        # La oferta era de la ficha anterior.
                        self.source_offer_repository.delete(series_id, "hispashare")
                cover_url = self.series_repository.db.derive_hispashare_cover_url(manual_url) or ""
                self.source_repository.save_match(
                    series_id,
                    {
                        "source_url": manual_url,
                        "source_title": current.get("tmdb_name") or current.get("name", ""),
                        "cover_url": cover_url,
                        "match_score": 1.0,
                        "match_status": "manual",
                        "validation_reason": "URL establecida manualmente por el usuario",
                    },
                )
                self.decision_log_repository.append(
                    series_id,
                    "source_set_manual",
                    "Se asignó manualmente una URL Hispashare",
                    {"source_url": manual_url},
                )
            else:
                self.source_repository.clear_current(
                    series_id,
                    "La URL Hispashare se limpió manualmente",
                )
                if self.source_offer_repository is not None:
                    # Si no, el ciclo revisaría la URL de la oferta y la reviviría.
                    self.source_offer_repository.delete(series_id, "hispashare")
                self.decision_log_repository.append(
                    series_id,
                    "source_cleared_manual",
                    "Se limpió manualmente la URL Hispashare",
                    {},
                )

        result = self.on_tmdb_changed(
            series_id,
            previous,
            current,
            is_library=is_library,
        )
        if result.get("recheck_hispashare_url"):
            return result

        self._publish(SERIES_UPDATED, {"series_id": series_id})
        return {
            "recalculated_missing": False,
            "missing_count": 0,
            "reset_hispashare_url": False,
            "recheck_hispashare_url": False,
            "previous_hispashare_url": "",
        }

    def reject_source(self, series_id: int, provider: str = "hispashare") -> str | None:
        """El usuario dice que la ficha asignada no es la de este elemento:
        se quita, se olvida su oferta, nunca se vuelve a elegir sola y se
        vuelve a buscar la correcta. Devuelve la URL rechazada."""
        current = self.source_repository.get_current(series_id, provider) or {}
        url = (current.get("source_url") or "").strip() if current.get("match_status") != "cleared" else ""
        if not url and self.source_offer_repository is not None:
            offer = self.source_offer_repository.get(series_id, provider)
            url = (offer.source_url or "").strip() if offer else ""
        if not url:
            return None
        self.source_repository.reject(series_id, url, provider)
        self.source_repository.clear_current(series_id, "Marcada como incorrecta por el usuario", provider=provider)
        if self.source_offer_repository is not None:
            self.source_offer_repository.delete(series_id, provider)
        if provider == "hispashare":
            self.series_repository.mark_search_enabled(series_id)
        self.decision_log_repository.append(
            series_id,
            "source_rejected",
            f"Ficha de {provider} marcada como incorrecta: no se volverá a usar",
            {"provider": provider, "source_url": url},
        )
        self._publish(SERIES_UPDATED, {"series_id": series_id})
        return url

    def _publish(self, event_type: str, payload: dict):
        if self.event_bus is not None:
            self.event_bus.publish(event_type, payload)
