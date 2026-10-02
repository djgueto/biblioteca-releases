from __future__ import annotations

from core.events.event_types import SOURCE_OFFER_UPDATED
from core.services.providers.base import ProviderUnavailable, SourceOffer, SourceProvider
from core.services.quality import Quality
from core.services.quality_profile import QualityProfile


class SourceArbitrationService:
    """Decide qué proveedor tiene, ahora mismo, la mejor oferta para un
    item, e implementa el mecanismo de 'seguir comprobando todas las fuentes
    activas aunque no sean la que se está usando actualmente'."""

    def __init__(
        self,
        db_manager,
        source_offer_repository,
        providers: dict[str, SourceProvider],
        event_bus=None,
        profile: QualityProfile | None = None,
    ):
        self.db = db_manager
        self.source_offer_repository = source_offer_repository
        self.providers = providers
        self.event_bus = event_bus
        self._profile = profile

    def refresh_all_offers(self, series_id: int, item: dict) -> list[SourceOffer]:
        """Revisita, para cada proveedor con una URL conocida (ya sea en
        source_offers o en la columna legacy series.hispashare_url), su
        oferta actual y la persiste. No decide nada sobre descargas."""
        known: dict[str, SourceOffer] = {
            o.provider: o for o in self.source_offer_repository.get_all_for_series(series_id)
        }

        legacy_url = (item.get("hispashare_url") or "").strip()
        if legacy_url and "hispashare" not in known:
            known["hispashare"] = SourceOffer(
                provider="hispashare", source_url=legacy_url, quality=Quality()
            )

        refreshed: list[SourceOffer] = []
        for provider_name, known_offer in known.items():
            provider = self.providers.get(provider_name)
            if provider is None or not known_offer.source_url:
                continue
            try:
                fresh = provider.refresh_offer(item, known_offer.source_url)
            except ProviderUnavailable:
                continue  # se conserva la oferta conocida tal cual
            except Exception:
                fresh = None

            if fresh is None:
                self.source_offer_repository.mark_unavailable(series_id, provider_name)
                continue

            self.source_offer_repository.upsert(series_id, fresh)
            refreshed.append(fresh)
            if self.event_bus is not None:
                self.event_bus.publish(
                    SOURCE_OFFER_UPDATED, {"series_id": series_id, "provider": provider_name}
                )

        return refreshed

    @property
    def profile(self) -> QualityProfile:
        # Se lee en cada uso: los cambios hechos desde la web aplican sin
        # reiniciar el ciclo.
        if self._profile is not None:
            return self._profile
        return QualityProfile.load(self.db)

    def best_offer(self, series_id: int) -> SourceOffer | None:
        """La mejor oferta disponible según el perfil de calidad, venga del
        proveedor que venga (None si ninguna llega al mínimo)."""
        offers = [o for o in self.source_offer_repository.get_all_for_series(series_id) if o.available]
        return self.profile.best(offers)

    def should_upgrade(self, series_id: int, local: Quality) -> tuple[bool, SourceOffer | None]:
        best = self.best_offer(series_id)
        if best is None:
            return False, None
        return self.profile.is_upgrade(local, best.quality), best
