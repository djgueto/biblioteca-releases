"""Reglas de las novedades: a qué biblioteca va cada título y si cumple las
notas mínimas para descargarse."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date

from config.settings import Settings

SETTINGS_KEY = "discovery"

# Géneros de TMDB
DOCUMENTARY, ANIMATION, KIDS, NEWS, REALITY, TALK = 99, 16, 10762, 10763, 10764, 10767
EXCLUDED_TV_GENRES = (NEWS, REALITY, TALK)

WAITING, APPROVED, REJECTED, EXPIRED, IN_LIBRARY, DISMISSED = (
    "waiting", "approved", "rejected", "expired", "in_library", "dismissed")


@dataclass
class DiscoverySettings:
    enabled: bool = True
    min_tmdb: float = 6.0
    min_imdb: float = 6.0
    # Con pocos votos la nota no es fiable (los primeros días la ponen fans).
    min_tmdb_votes: int = 50
    min_imdb_votes: int = 500
    categories: list[str] = field(default_factory=lambda: list(Settings.get_library_categories()))
    days_back: int = 60       # estrenos de los últimos N días
    days_ahead: int = 90      # y próximos estrenos (quedan en espera)
    pages: int = 3            # páginas de TMDB por lista (20 títulos cada una)
    imdb_grace_days: int = 30  # tras N días, decidir solo con la nota que haya
    max_wait_days: int = 180   # sin votos suficientes tras N días: se descarta

    @classmethod
    def load(cls, db_manager) -> "DiscoverySettings":
        from core.repositories.app_settings_repository import AppSettingsRepository

        data = AppSettingsRepository(db_manager).get(SETTINGS_KEY) or {}
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def save(self, db_manager) -> None:
        from core.repositories.app_settings_repository import AppSettingsRepository

        AppSettingsRepository(db_manager).set(SETTINGS_KEY, asdict(self))

    def to_dict(self) -> dict:
        return asdict(self)


def classify_category(media_type: str, genre_ids: list[int]) -> str:
    genres = set(genre_ids or [])
    if media_type == "movie":
        if DOCUMENTARY in genres:
            return "documentaries"
        if ANIMATION in genres:
            return "animated_movies"
        return "movies"
    if DOCUMENTARY in genres:
        return "docuseries"
    if ANIMATION in genres or KIDS in genres:
        return "cartoons"
    return "series"


def evaluate(item: dict, settings: DiscoverySettings, today: date | None = None) -> tuple[str, str]:
    """→ (estado, motivo legible). `item` lleva release_date, tmdb_rating,
    tmdb_votes, imdb_rating, imdb_votes."""
    today = today or date.today()
    release = _parse_date(item.get("release_date"))
    if release is None or release > today:
        return WAITING, f"Se estrena el {release.isoformat()}" if release else "Sin fecha de estreno"
    days = (today - release).days

    tmdb_rating, tmdb_votes = item.get("tmdb_rating") or 0, item.get("tmdb_votes") or 0
    imdb_rating, imdb_votes = item.get("imdb_rating"), item.get("imdb_votes") or 0
    tmdb_ok_votes = tmdb_votes >= settings.min_tmdb_votes
    imdb_ok_votes = imdb_rating is not None and imdb_votes >= settings.min_imdb_votes

    if tmdb_ok_votes and tmdb_rating < settings.min_tmdb:
        return REJECTED, f"TMDB {tmdb_rating:.1f} < {settings.min_tmdb:.1f}"
    if imdb_ok_votes and imdb_rating < settings.min_imdb:
        return REJECTED, f"IMDb {imdb_rating:.1f} < {settings.min_imdb:.1f}"
    if tmdb_ok_votes and imdb_ok_votes:
        return APPROVED, f"TMDB {tmdb_rating:.1f} · IMDb {imdb_rating:.1f}"
    if days >= settings.imdb_grace_days and (tmdb_ok_votes or imdb_ok_votes):
        if tmdb_ok_votes:
            return APPROVED, f"TMDB {tmdb_rating:.1f} (IMDb sin votos suficientes)"
        return APPROVED, f"IMDb {imdb_rating:.1f} (TMDB sin votos suficientes)"
    if days > settings.max_wait_days:
        return EXPIRED, f"Sin votos suficientes tras {settings.max_wait_days} días"
    return WAITING, "Esperando votos suficientes para fiarse de la nota"


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
