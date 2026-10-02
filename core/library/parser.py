import re
import logging

try:
    from pymediainfo import MediaInfo as _MediaInfo
    _MEDIAINFO_AVAILABLE = True
except ImportError:
    _MEDIAINFO_AVAILABLE = False


def mediainfo_available() -> bool:
    """pymediainfo instalado y con la librería MediaInfo del sistema."""
    if not _MEDIAINFO_AVAILABLE:
        return False
    try:
        return bool(_MediaInfo.can_parse())
    except Exception:
        return False


def _detect_hdr_from_track(track) -> str | None:
    """Inspecciona los atributos hdr_format* que expone pymediainfo (cuando
    el fichero MediaInfo los detecta) para clasificar el HDR real del
    archivo. Devuelve 'dolby_vision' | 'hdr10' | None (SDR/desconocido)."""
    fields = ' '.join(
        str(getattr(track, attr, '') or '')
        for attr in ('hdr_format', 'hdr_format_compatibility', 'hdr_format_string', 'transfer_characteristics')
    ).lower()
    if 'dolby vision' in fields or 'dolby_vision' in fields:
        return 'dolby_vision'
    if 'hdr10' in fields or 'hdr' in fields or 'pq' in fields or 'smpte st 2086' in fields:
        return 'hdr10'
    return None


def detect_resolution_from_file(file_path: str) -> dict | None:
    """
    Lee los metadatos del archivo de vídeo con pymediainfo para obtener
    resolución real (independiente del nombre del fichero).
    Devuelve {resolution_height, codec, quality_rank, hdr} o None si falla.
    """
    if not _MEDIAINFO_AVAILABLE:
        return None
    try:
        info = _MediaInfo.parse(file_path)
        for track in info.tracks:
            if track.track_type != 'Video':
                continue
            width = int(track.width or 0)
            height_raw = int(track.height or 0)
            # Clasificar por ancho (1920x800 = Full HD, no 720p)
            if width >= 3840 or height_raw >= 2160:
                res_h = 2160
            elif width >= 1920 or height_raw >= 1080:
                res_h = 1080
            elif width >= 1280 or height_raw >= 720:
                res_h = 720
            elif width > 0 or height_raw > 0:
                res_h = 480
            else:
                return None
            # quality_rank: 4=4K, 3=1080p, 2=720p, 1=480p
            rank_map = {2160: 4, 1080: 3, 720: 2, 480: 1}
            quality_rank = rank_map.get(res_h, 0)
            codec_id = (track.codec_id or '').lower()
            if 'hvc' in codec_id or 'hev' in codec_id or 'h265' in codec_id or 'hevc' in codec_id:
                codec = 'x265'
            elif 'avc' in codec_id or 'h264' in codec_id:
                codec = 'x264'
            else:
                codec = codec_id or None
            return {
                'resolution_height': res_h,
                'codec': codec,
                'quality_rank': quality_rank,
                'hdr': _detect_hdr_from_track(track),
            }
    except Exception:
        pass
    return None


class EpisodeParser:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        
        # Patrones para detectar temporada y episodio
        self.patterns = [
            # S01.E01, S01_E01, S01 - E01, S1 E1 (formatos comunes en releases)
            re.compile(r'(?<!\w)[Ss](\d{1,2})\s*[\.\-_ ]*\s*[Ee](\d{1,3})(?!\w)', re.IGNORECASE),
            # T01E01, T01.E01 (formato común en títulos en español)
            re.compile(r'(?<!\w)[Tt](\d{1,2})\s*[\.\-_ ]*\s*[Ee](\d{1,3})(?!\w)', re.IGNORECASE),
            # S01E01, S1E1
            re.compile(r'[Ss](\d{1,2})[Ee](\d{1,3})', re.IGNORECASE),
            # 1x01, 1x1
            re.compile(r'(\d{1,2})[xX](\d{1,3})'),
            # 1X01, 2X05 (formato alternativo con X mayúscula)
            re.compile(r'(\d{1,2})X(\d{1,3})'),
            # Temporada 2 Episodio 3 / Temp 2 Cap 3 / Season 2 Ep 3
            re.compile(r'(?i)(?:temporada|temp|season)\s*(\d{1,2}).{0,24}?(?:episodio|cap(?:itulo)?|ep)\s*(\d{1,3})'),
            # 101 (temporada 1, episodio 1)
            re.compile(r'(?:^|[\s\._-])(\d)(\d{2})(?:[\s\._-]|$)'),
        ]
        
        # Patrones para carpetas de temporadas
        self.season_patterns = [
            re.compile(r'[Ss]eason[\s\._-]?(\d{1,2})', re.IGNORECASE),
            re.compile(r'[Tt]emporada[\s\._-]?(\d{1,2})', re.IGNORECASE),
            re.compile(r'[Ss](\d{1,2})$', re.IGNORECASE),
        ]
    
    def parse_episode(self, filename, default_season=None):
        """
        Parsear nombre de archivo para extraer temporada y episodio
        
        Args:
            filename: Nombre del archivo
            default_season: Temporada por defecto si no se encuentra en el nombre
        
        Returns:
            dict con season, episode o None
        """
        for pattern in self.patterns:
            match = pattern.search(filename)
            if match:
                season = int(match.group(1))
                episode = int(match.group(2))
                
                return {
                    'season': season,
                    'episode': episode,
                    'filename': filename
                }
        
        # Si no se encontró patrón pero hay temporada por defecto
        if default_season is not None:
            # Intentar extraer solo el número de episodio (admite "Capítulo"/"Cap"/
            # "Episodio", habitual en nomenclatura en español, además de "E01")
            ep_match = re.search(
                r'(?:[Ee]|cap(?:itulo|ítulo)?|episodio|ep)\.?\s*(\d{1,3})',
                filename,
                re.IGNORECASE,
            )
            if ep_match:
                return {
                    'season': default_season,
                    'episode': int(ep_match.group(1)),
                    'filename': filename
                }
        
        self.logger.warning(f"No se pudo parsear: {filename}")
        return None
    
    def parse_season_folder(self, folder_name):
        """
        Parsear nombre de carpeta para extraer número de temporada
        
        Returns:
            int con número de temporada o None
        """
        for pattern in self.season_patterns:
            match = pattern.search(folder_name)
            if match:
                return int(match.group(1))
        
        return None

    def parse_quality(self, filename):
        text = (filename or "").lower()

        resolution_height = None
        if re.search(r'(?<!\d)2160p(?!\d)', text) or re.search(r'(?<!\d)4k(?!\w)', text) or 'uhd' in text:
            resolution_height = 2160
        elif re.search(r'(?<!\d)1080p(?!\d)', text):
            resolution_height = 1080
        elif re.search(r'(?<!\d)720p(?!\d)', text):
            resolution_height = 720
        elif re.search(r'(?<!\d)(576p|480p)(?!\d)', text):
            resolution_height = 480

        codec = None
        if 'hevc' in text or re.search(r'(?<!\w)(x265|h265)(?!\w)', text):
            codec = 'x265'
        elif re.search(r'(?<!\w)(x264|h264|avc)(?!\w)', text):
            codec = 'x264'

        source = None
        if re.search(r'(?<!\w)(blu[- ]?ray|bdrip|brip)(?!\w)', text):
            source = 'bluray'
        elif re.search(r'(?<!\w)(web[- ]?dl|webdl)(?!\w)', text):
            source = 'web-dl'
        elif re.search(r'(?<!\w)webrip(?!\w)', text):
            source = 'webrip'
        elif re.search(r'(?<!\w)hdtv(?!\w)', text):
            source = 'hdtv'

        hdr = None
        if re.search(r'(?<!\w)(dolby[\s.-]?vision|dovi|dv)(?!\w)', text):
            hdr = 'dolby_vision'
        elif re.search(r'(?<!\w)hdr10\+?(?!\w)', text) or re.search(r'(?<!\w)hdr(?!\w)', text):
            hdr = 'hdr10'

        quality_rank = 0
        if resolution_height == 2160:
            quality_rank = 4
        elif resolution_height == 1080:
            quality_rank = 3
        elif resolution_height == 720:
            quality_rank = 2
        elif resolution_height == 480:
            quality_rank = 1

        return {
            'resolution_height': resolution_height,
            'codec': codec,
            'source': source,
            'quality_rank': quality_rank,
            'hdr': hdr,
        }
