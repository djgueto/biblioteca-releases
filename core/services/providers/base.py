from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from core.services.quality import Quality


@dataclass(frozen=True)
class SourceOffer:
    provider: str
    source_url: str
    title: str = ""
    quality: Quality = field(default_factory=Quality)
    match_score: float = 0.0
    cover_url: str | None = None
    available: bool = True


@dataclass(frozen=True)
class DownloadHandle:
    provider: str
    kind: str  # protocolo: 'ed2k' | 'torrent' (ver core/download_clients)
    payload: dict


class SourceProvider(Protocol):
    """Contrato que implementa cada fuente de descarga (Hispashare, Wolfmax,
    y las que se añadan en el futuro). Añadir un proveedor nuevo = una clase
    que implemente estos 3 métodos + una entrada en providers/registry.py;
    nada de la orquestación/arbitraje tiene que cambiar."""

    name: str

    def find_candidate(self, item: dict, *, content_mode: str) -> SourceOffer | None:
        """Fase de búsqueda. La calidad puede venir desconocida (Hispashare
        es dos fases: primero se encuentra la URL, la calidad solo se sabe
        al visitar la página de descarga)."""
        ...

    def refresh_offer(self, item: dict, source_url: str) -> SourceOffer | None:
        """Revisita una URL ya conocida y devuelve su calidad/disponibilidad
        actual. Se llama cada ciclo para todo proveedor con URL conocida,
        gane o no el arbitraje -- es el mecanismo concreto de 'seguir
        comprobando esta fuente aunque no sea la que se está usando ahora'.
        Devuelve None si la fuente ya no está disponible."""
        ...

    def resolve_download(
        self, item: dict, offer: SourceOffer, *, missing_only: bool = True
    ) -> DownloadHandle | None:
        """Ejecuta (o prepara) la descarga de los episodios faltantes/objetivo
        de upgrade para este item usando esta oferta."""
        ...
