from __future__ import annotations

import re
from pathlib import Path

from core.download_clients.base import TORRENT, Release

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*]')


class TorrentBlackholeClient:
    """Deja las releases torrent en una carpeta vigilada por el cliente
    torrent (qBittorrent, Transmission, ...), que es quien las descarga.

    - .torrent (content) → <watch_dir>/<filename>.torrent
    - magnet (link)      → <watch_dir>/<title>.magnet (qBittorrent lo acepta)
    """

    protocol = TORRENT

    def __init__(self, watch_dir: str):
        self.watch_dir = (watch_dir or "").strip()

    def is_configured(self) -> bool:
        return bool(self.watch_dir)

    def send(self, release: Release) -> str:
        if not self.is_configured():
            raise RuntimeError("Carpeta vigilada de torrents no configurada")
        if release.content is not None:
            if not release.content.startswith(b"d"):
                raise ValueError(f"'{release.title}' no es un archivo .torrent válido")
            name, data = self._filename(release.filename or release.title, ".torrent"), release.content
        elif release.link and release.link.startswith("magnet:"):
            name, data = self._filename(release.title, ".magnet"), release.link.encode("utf-8")
        else:
            raise ValueError(f"Release torrent sin contenido ni magnet: '{release.title}'")

        out_dir = Path(self.watch_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / name
        path.write_bytes(data)
        return str(path)

    @staticmethod
    def _filename(name: str, suffix: str) -> str:
        name = _INVALID_FILENAME_CHARS.sub("_", name).strip() or "descarga"
        return name if name.lower().endswith(suffix) else name + suffix
