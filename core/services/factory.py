"""Construcción de los servicios de dominio, compartida por la app de
escritorio (ui/workers) y la web: así ambas aplican los cambios manuales
(TMDB, URL de Hispashare...) con exactamente la misma lógica."""

from __future__ import annotations

from core.events import EventBus
from core.repositories import (
    DecisionLogRepository,
    SeriesRepository,
    SourceOfferRepository,
    SourceRepository,
)
from core.services.missing_episodes_service import MissingEpisodesService
from core.services.reconciliation_service import ReconciliationService
from core.services.source_match_service import SourceMatchService


def build_domain_services(db, event_bus: EventBus | None = None) -> dict:
    series_repository = SeriesRepository(db)
    source_repository = SourceRepository(db)
    source_offer_repository = SourceOfferRepository(db)
    decision_log_repository = DecisionLogRepository(db)
    missing_episodes_service = MissingEpisodesService(db, series_repository, decision_log_repository)
    source_match_service = SourceMatchService(db, source_repository, decision_log_repository)
    reconciliation_service = ReconciliationService(
        series_repository,
        source_repository,
        missing_episodes_service,
        source_match_service,
        decision_log_repository,
        event_bus=event_bus or EventBus(),
        source_offer_repository=source_offer_repository,
    )
    return {
        "series_repository": series_repository,
        "source_repository": source_repository,
        "source_offer_repository": source_offer_repository,
        "decision_log_repository": decision_log_repository,
        "missing_episodes_service": missing_episodes_service,
        "source_match_service": source_match_service,
        "reconciliation_service": reconciliation_service,
    }
