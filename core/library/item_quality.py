"""Calidad real de una película/documental/concierto en disco."""

from __future__ import annotations

from pathlib import Path

from core.library.parser import detect_resolution_from_file

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".ts", ".m2ts", ".mpg", ".mpeg", ".webm", ".iso"}


def main_video_file(item_path: str) -> Path | None:
    """El propio archivo, o el vídeo más grande si la ficha es una carpeta
    (los extras y muestras pesan mucho menos que la película)."""
    path = Path(item_path)
    try:
        if path.is_file():
            return path
        if not path.is_dir():
            return None
        videos = [p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS]
    except OSError:
        return None
    return max(videos, key=lambda p: p.stat().st_size, default=None)


def detect_item_quality(item_path: str) -> tuple[str | None, dict | None, str | None]:
    """→ (archivo medido, {resolution_height, codec, hdr}, 'mediainfo').

    Solo cuenta lo que MediaInfo lee del propio archivo: lo que diga el
    nombre no es fiable. Si no se puede leer: (archivo, None, None).
    """
    video = main_video_file(item_path)
    if video is None:
        return None, None, None
    detected = detect_resolution_from_file(str(video))
    if detected and detected.get("resolution_height"):
        return str(video), {
            "resolution_height": detected["resolution_height"],
            "codec": detected.get("codec"),
            "hdr": detected.get("hdr") or "sdr",
        }, "mediainfo"
    return str(video), None, None
