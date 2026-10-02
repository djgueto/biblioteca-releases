"""Calidad real de una película/documental/concierto en disco."""

from __future__ import annotations

from pathlib import Path

from core.library.parser import detect_resolution_from_file
from core.services.quality import parse_quality_text

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
    """→ (archivo medido, {resolution_height, codec, hdr}, origen 'mediainfo'|'filename').

    MediaInfo da la calidad real; si no puede leer el archivo se usa lo que
    diga el nombre ("Dune 2160p HDR x265.mkv"). Sin resolución: (archivo, None, None).
    """
    video = main_video_file(item_path)
    if video is None:
        return None, None, None
    detected = detect_resolution_from_file(str(video))
    if detected and detected.get("resolution_height"):
        codec = detected.get("codec")
        return str(video), {
            "resolution_height": detected["resolution_height"],
            "codec": codec if codec in ("x264", "x265") else None,
            "hdr": detected.get("hdr"),
        }, "mediainfo"
    parsed = parse_quality_text(video.name)
    if parsed.resolution_height:
        hdr = parsed.hdr.name.lower() if parsed.hdr.value >= 2 else None
        return str(video), {"resolution_height": parsed.resolution_height, "codec": parsed.codec, "hdr": hdr}, "filename"
    return str(video), None, None
