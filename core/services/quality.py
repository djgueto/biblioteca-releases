from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum


class Hdr(IntEnum):
    UNKNOWN = 0
    SDR = 1
    HDR10 = 2
    DOLBY_VISION = 3


# x265 solo se prefiere como desempate débil (tamaño/compatibilidad), nunca
# por delante de resolución u HDR, que tienen mucho más impacto perceptual.
CODEC_RANK = {None: 0, "": 0, "x264": 1, "x265": 2}


@dataclass(frozen=True)
class Quality:
    resolution_height: int | None = None
    codec: str | None = None
    hdr: Hdr = Hdr.UNKNOWN

    def rank_tuple(self) -> tuple[int, int, int]:
        return (
            self.resolution_height or 0,
            int(self.hdr),
            CODEC_RANK.get(self.codec, 0),
        )


def compare_quality(a: Quality, b: Quality) -> int:
    """-1 si a<b, 0 si son iguales, 1 si a>b.

    Orden: resolución (mayor impacto perceptual) > HDR (elección de
    mastering, gran impacto en pantallas compatibles) > codec (x265 vs
    x264, principalmente tamaño/compatibilidad, desempate más débil).
    """
    at, bt = a.rank_tuple(), b.rank_tuple()
    return (at > bt) - (at < bt)


def is_upgrade(current: Quality, candidate: Quality) -> bool:
    return compare_quality(candidate, current) > 0


_RES_2160_RE = re.compile(r'(?<!\d)2160p(?!\d)')
_RES_4K_RE = re.compile(r'(?<!\d)4k(?!\w)')
_RES_1080_RE = re.compile(r'(?<!\d)1080p(?!\d)')
_RES_720_RE = re.compile(r'(?<!\d)720p(?!\d)')
_RES_SD_RE = re.compile(r'(?<!\d)(576p|480p)(?!\d)')

_CODEC_X265_RE = re.compile(r'(?<!\w)(x265|h265|hevc)(?!\w)')
_CODEC_X264_RE = re.compile(r'(?<!\w)(x264|h264|avc)(?!\w)')

_HDR_DOLBY_RE = re.compile(r'(?<!\w)(dolby[\s.-]?vision|dovi|\bdv\b)(?!\w)')
_HDR_HDR10_RE = re.compile(r'(?<!\w)(hdr10\+?|hdr)(?!\w)')
_SDR_RE = re.compile(r'(?<!\w)sdr(?!\w)')


def parse_quality_text(text: str) -> Quality:
    """Generaliza `_quality_rank_from_text` (core/hispashare/downloader.py) y
    las regex de resolución/codec de `core/library/parser.py::parse_quality`
    en un único parser reutilizable sobre cualquier texto libre: la sección
    de calidad de Hispashare, el título crudo de una card de Wolfmax, o un
    nombre de fichero."""
    value = (text or "").lower()

    resolution_height = None
    if _RES_2160_RE.search(value) or _RES_4K_RE.search(value) or 'uhd' in value or 'ultra' in value:
        resolution_height = 2160
    elif _RES_1080_RE.search(value):
        resolution_height = 1080
    elif _RES_720_RE.search(value):
        resolution_height = 720
    elif _RES_SD_RE.search(value):
        resolution_height = 480

    codec = None
    if _CODEC_X265_RE.search(value):
        codec = 'x265'
    elif _CODEC_X264_RE.search(value):
        codec = 'x264'

    if _HDR_DOLBY_RE.search(value):
        hdr = Hdr.DOLBY_VISION
    elif _HDR_HDR10_RE.search(value):
        hdr = Hdr.HDR10
    elif _SDR_RE.search(value):
        hdr = Hdr.SDR
    else:
        hdr = Hdr.UNKNOWN

    return Quality(resolution_height=resolution_height, codec=codec, hdr=hdr)


def quality_from_local_episode(row: dict) -> Quality:
    """Adaptador desde una fila de `local_episodes` (columnas resolution_height/
    codec/hdr, esta última poblada por pymediainfo o por el nombre de
    fichero -- ver core/library/parser.py)."""
    row = row or {}
    return Quality(
        resolution_height=row.get('resolution_height'),
        codec=row.get('codec'),
        hdr=hdr_from_name(row.get('hdr')),
    )


def hdr_from_name(name: str | None) -> Hdr:
    """Convierte el valor de texto guardado en BD (p. ej. 'dolby_vision',
    'hdr10', None) al enum Hdr correspondiente."""
    try:
        return Hdr[(name or 'unknown').upper()]
    except KeyError:
        return Hdr.UNKNOWN
