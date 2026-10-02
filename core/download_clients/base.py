from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

# Protocolos de entrega. Un proveedor (indexador) produce Releases de uno de
# estos protocolos; el cliente de descarga registrado para ese protocolo es
# quien sabe qué hacer con ellas.
TORRENT = "torrent"
ED2K = "ed2k"


@dataclass(frozen=True)
class Release:
    """Algo descargable que un proveedor ha encontrado.

    Lleva el contenido (`content`, p. ej. los bytes de un .torrent que solo se
    pueden obtener con la sesión del proveedor) o un enlace (`link`: magnet,
    ed2k). El proveedor no sabe adónde va: eso lo decide el cliente.
    """

    protocol: str
    title: str
    filename: str = ""
    content: bytes | None = None
    link: str | None = None
    source_url: str = ""
    extra: dict = field(default_factory=dict)


class DownloadClient(Protocol):
    protocol: str

    def is_configured(self) -> bool:
        ...

    def send(self, release: Release) -> str:
        """Entrega la release al cliente. Devuelve una descripción legible de
        dónde ha ido (ruta, cola...). Lanza excepción si no se pudo entregar."""
        ...


class NoDownloadClient(RuntimeError):
    """No hay cliente configurado para el protocolo de una release."""
