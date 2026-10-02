from __future__ import annotations

from core.download_clients.base import ED2K, Release
from core.emule.client import EmuleClient


class EmuleDownloadClient:
    """Envía releases ed2k a la cola de eMule/aMule por su interfaz web."""

    protocol = ED2K

    def __init__(self, emule_client: EmuleClient | None = None):
        self._client = emule_client or EmuleClient()

    def is_configured(self) -> bool:
        return self._client.is_configured()

    def send(self, release: Release) -> str:
        if not release.link or not release.link.startswith("ed2k://"):
            raise ValueError(f"Release ed2k sin enlace válido: '{release.title}'")
        result = self._client.add_ed2k_link(release.link)
        if not result.get("success"):
            raise RuntimeError(result.get("error") or result.get("message") or "eMule rechazó el enlace")
        return "cola de eMule"
