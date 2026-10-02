"""Perfil de calidad: la única regla que decide qué se descarga y qué se mejora.

Cada calidad cae en un nivel (4K Dolby Vision, 4K HDR, 4K, 1080p x265...).
El perfil es el orden de esos niveles (el primero es el mejor) más una
calidad mínima aceptable. Da igual el proveedor: gana la oferta cuyo nivel
esté más arriba; dentro del mismo nivel, desempata compare_quality
(resolución > HDR > codec).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.services.quality import Hdr, Quality

TIERS = {
    "2160p_dv": "4K Dolby Vision",
    "2160p_hdr": "4K HDR",
    "2160p": "4K",
    "1080p_x265": "1080p x265",
    "1080p": "1080p x264",
    "720p": "720p",
    "sd": "SD",
}
DEFAULT_ORDER = list(TIERS)
UNKNOWN = "unknown"
SETTINGS_KEY = "quality_profile"


def tier_of(quality: Quality | None) -> str:
    height = (quality.resolution_height if quality else None) or 0
    if height >= 2000:
        if quality.hdr == Hdr.DOLBY_VISION:
            return "2160p_dv"
        if quality.hdr == Hdr.HDR10:
            return "2160p_hdr"
        return "2160p"
    if height >= 1000:
        return "1080p_x265" if quality.codec == "x265" else "1080p"
    if height >= 700:
        return "720p"
    if height > 0:
        return "sd"
    return UNKNOWN


def _same_resolution(a: Quality, b: Quality) -> bool:
    return tier_of(Quality(a.resolution_height))[:4] == tier_of(Quality(b.resolution_height))[:4]


@dataclass
class QualityProfile:
    order: list[str] = field(default_factory=lambda: list(DEFAULT_ORDER))
    minimum: str = "sd"

    def __post_init__(self):
        # Tolerar perfiles guardados con niveles que ya no existen o faltan.
        order = [t for t in self.order if t in TIERS]
        self.order = order + [t for t in DEFAULT_ORDER if t not in order]
        if self.minimum not in TIERS:
            self.minimum = "sd"

    # --- comparación ------------------------------------------------------
    def rank(self, quality: Quality | None) -> tuple:
        """Mayor es mejor. Calidad desconocida queda por debajo de todo."""
        tier = tier_of(quality)
        position = len(self.order) - self.order.index(tier) if tier in self.order else 0
        return (position, *(quality.rank_tuple() if quality else (0, 0, 0)))

    def compare(self, a: Quality | None, b: Quality | None) -> int:
        ra, rb = self.rank(a), self.rank(b)
        return (ra > rb) - (ra < rb)

    def is_acceptable(self, quality: Quality | None) -> bool:
        """¿Llega al mínimo? La calidad desconocida se acepta: Hispashare no la
        sabe hasta visitar la ficha, y descartarla dejaría sin descargar."""
        tier = tier_of(quality)
        if tier == UNKNOWN:
            return True
        return self.order.index(tier) <= self.order.index(self.minimum)

    def is_upgrade(self, current: Quality | None, candidate: Quality | None) -> bool:
        """Solo es mejora si sube de nivel en el perfil y la calidad actual es
        conocida (si no, no hay con qué comparar y se descargaría a ciegas)."""
        if tier_of(current) == UNKNOWN or tier_of(candidate) == UNKNOWN:
            return False
        if _same_resolution(current, candidate) and not current.codec and current.hdr == Hdr.UNKNOWN:
            # Misma resolución y no sabemos el codec/HDR del archivo local:
            # podría ser ya x265/HDR, y "mejorarlo" sería volver a bajar lo mismo.
            return False
        return self.is_acceptable(candidate) and self.rank(candidate)[0] > self.rank(current)[0]

    def best(self, offers):
        """La mejor oferta aceptable de una lista (objetos con .quality)."""
        acceptable = [o for o in offers if self.is_acceptable(o.quality)]
        return max(acceptable, key=lambda o: self.rank(o.quality), default=None)

    # --- persistencia -----------------------------------------------------
    def to_dict(self) -> dict:
        return {"order": self.order, "minimum": self.minimum}

    @classmethod
    def load(cls, db_manager) -> "QualityProfile":
        from core.repositories.app_settings_repository import AppSettingsRepository

        data = AppSettingsRepository(db_manager).get(SETTINGS_KEY) or {}
        return cls(order=data.get("order") or list(DEFAULT_ORDER), minimum=data.get("minimum") or "sd")

    def save(self, db_manager) -> None:
        from core.repositories.app_settings_repository import AppSettingsRepository

        AppSettingsRepository(db_manager).set(SETTINGS_KEY, self.to_dict())
