from __future__ import annotations

from config.settings import Settings
from core.download_clients.base import DownloadClient, NoDownloadClient, Release


class DownloadDispatcher:
    """Entrega cada Release al cliente registrado para su protocolo.

    Es lo único que un proveedor recibe para "descargar": así un plugin solo
    encuentra contenido y no sabe si acaba en una carpeta vigilada, en eMule
    o en otro cliente.
    """

    def __init__(self, clients: dict[str, DownloadClient]):
        self.clients = clients

    def can_handle(self, protocol: str) -> bool:
        client = self.clients.get(protocol)
        return client is not None and client.is_configured()

    def grab(self, release: Release) -> str:
        if not self.can_handle(release.protocol):
            raise NoDownloadClient(f"Sin cliente de descarga configurado para '{release.protocol}'")
        return self.clients[release.protocol].send(release)


def build_download_dispatcher() -> DownloadDispatcher:
    from core.download_clients.emule import EmuleDownloadClient
    from core.download_clients.torrent_blackhole import TorrentBlackholeClient

    clients: list[DownloadClient] = [
        TorrentBlackholeClient(Settings.get_torrent_watch_dir()),
        EmuleDownloadClient(),
    ]
    return DownloadDispatcher({client.protocol: client for client in clients})
