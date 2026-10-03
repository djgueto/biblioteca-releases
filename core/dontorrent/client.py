"""Cliente HTTP de DonTorrent (sin navegador).

Flujo del sitio (2026-10):
  - Búsqueda: POST /buscar {valor}. Cada resultado es una ficha por
    temporada y calidad: "The Boys - 4ª Temporada [720p] (HDTV-720p)".
  - Ficha de serie: tabla de episodios ("1x01", "1x01 al 1x03") con un botón
    `.protected-download` que lleva data-content-id y data-tabla.
  - Descarga: POST /api_validate_pow.php {action: generate, content_id, tabla}
    → challenge; prueba de trabajo sha256(challenge + nonce) con 3 ceros
    iniciales; {action: validate, challenge, nonce} → download_url del
    .torrent. Si se descarga mucho, el sitio responde 429/limit_exceeded
    (espera de N minutos) o pide un captcha: en ambos casos se para.

El dominio cambia a menudo: la URL base es configurable (DONTORRENT_URL).
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from core.services.providers.base import ProviderUnavailable
from core.services.quality import Quality, parse_quality_text

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
POW_DIFFICULTY = 3

_RESULT_HREF_RE = re.compile(r"^/(serie|documental|pelicula)/\d+")
_SEASON_RE = re.compile(r"\s*-?\s*(\d{1,2})\s*[ªº°]?\s*Temporada\b", re.IGNORECASE)
_EPISODE_RE = re.compile(r"(\d{1,2})x(\d{1,3})(?:\s*al\s*(?:\d{1,2}x)?(\d{1,3}))?", re.IGNORECASE)
_TRAILING_QUALITY_RE = re.compile(r"\s*\(([^()]*)\)\s*(?:Pel[ií]cula|Serie|Documental)?\s*$", re.IGNORECASE)
_LOW_QUALITY_RE = re.compile(r"hdtv|dvdrip|vhsrip|tvrip|xvid|screener", re.IGNORECASE)


class DontorrentRateLimited(ProviderUnavailable):
    """El sitio ha limitado las búsquedas o las descargas (espera o captcha)."""


@dataclass(frozen=True)
class SearchResult:
    url: str
    kind: str  # 'serie' | 'documental' | 'pelicula'
    title: str  # sin temporada ni calidad: "The Boys"
    season: int | None
    quality_label: str  # "HDTV-720p", "4K", "BluRay-1080p"...
    quality: Quality = field(default_factory=Quality)


@dataclass(frozen=True)
class EpisodeLink:
    season: int
    episodes: tuple[int, ...]
    content_id: int
    tabla: str
    label: str


def quality_from_label(label: str) -> Quality:
    quality = parse_quality_text(label)
    if quality.resolution_height is None and _LOW_QUALITY_RE.search(label or ""):
        # "HDTV" o "DVDRip" sin resolución en DonTorrent son SD.
        quality = Quality(480, quality.codec, quality.hdr)
    return quality


def parse_result_text(text: str) -> tuple[str, int | None, str]:
    """'Dune: La profecía  - 1ª Temporada [4K] (4K)' → ('Dune: La profecía', 1, '4K')."""
    text = re.sub(r"\s+", " ", text or "").strip()
    quality_label = ""
    m = _TRAILING_QUALITY_RE.search(text)
    if m:
        quality_label = m.group(1).strip()
        text = text[: m.start()]
    season = None
    m = _SEASON_RE.search(text)
    if m:
        season = int(m.group(1))
        text = text[: m.start()]
    elif re.search(r"\bMiniserie\b", text, re.IGNORECASE):
        season = 1
        text = re.sub(r"\s*-?\s*Miniserie\.?", "", text, flags=re.IGNORECASE)
    title = re.sub(r"\s*\[[^\]]*\]", "", text).strip(" .-:")
    return title, season, quality_label


_THROTTLE_RE = re.compile(r"Reintentar en\s+(\d+)\s+segundos", re.IGNORECASE)


def search_throttle_seconds(html: str) -> int | None:
    """Segundos de espera si la página es el aviso "Estás buscando demasiado
    rápido" (límite: 15 búsquedas por minuto); None si es una búsqueda normal."""
    if "demasiado r" not in html and "Reintentar en" not in html:
        return None
    m = _THROTTLE_RE.search(html)
    return int(m.group(1)) if m else 60


def parse_search_results(html: str, base_url: str) -> list[SearchResult]:
    soup = BeautifulSoup(html, "html.parser")
    results, seen = [], set()
    for a in soup.find_all("a", href=True):
        m = _RESULT_HREF_RE.match(a["href"])
        if not m or a["href"] in seen:
            continue
        seen.add(a["href"])
        title, season, label = parse_result_text(a.parent.get_text(" ", strip=True))
        if not title:
            continue
        results.append(SearchResult(
            url=urljoin(base_url, a["href"]),
            kind=m.group(1),
            title=title,
            season=season,
            quality_label=label,
            quality=quality_from_label(label),
        ))
    return results


def parse_episode_links(html: str) -> list[EpisodeLink]:
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for row in soup.select("table tr"):
        button = row.select_one(".protected-download[data-content-id][data-tabla]")
        cells = row.find_all("td")
        if not button or not cells:
            continue
        label = cells[0].get_text(" ", strip=True)
        m = _EPISODE_RE.search(label)
        if not m:
            continue
        season, first = int(m.group(1)), int(m.group(2))
        last = int(m.group(3)) if m.group(3) else first
        links.append(EpisodeLink(
            season=season,
            episodes=tuple(range(first, max(first, last) + 1)),
            content_id=int(button["data-content-id"]),
            tabla=button["data-tabla"],
            label=label,
        ))
    return links


@dataclass(frozen=True)
class MoviePage:
    year: int | None
    format_label: str
    content_id: int
    tabla: str


_YEAR_RE = re.compile(r"A[ñn]o:?\s*((?:19|20)\d{2})")
_FORMAT_RE = re.compile(r"Formato:\s*(\S+)")


def parse_movie_page(html: str) -> MoviePage | None:
    """Ficha de película: año (en el título "Descargar X Año 2026 Torrent"),
    formato ("BluRay-1080p", "4K"...) y el botón de descarga."""
    soup = BeautifulSoup(html, "html.parser")
    button = soup.select_one(".protected-download[data-content-id][data-tabla]")
    if button is None:
        return None
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    text = " ".join(soup.get_text(" ", strip=True).split())
    year_match = _YEAR_RE.search(title) or _YEAR_RE.search(text)
    format_match = _FORMAT_RE.search(text)
    return MoviePage(
        year=int(year_match.group(1)) if year_match else None,
        format_label=format_match.group(1).strip() if format_match else "",
        content_id=int(button["data-content-id"]),
        tabla=button["data-tabla"],
    )


def solve_pow(challenge: str, difficulty: int = POW_DIFFICULTY) -> int:
    target = "0" * difficulty
    nonce = 0
    while not hashlib.sha256(f"{challenge}{nonce}".encode()).hexdigest().startswith(target):
        nonce += 1
    return nonce


class DontorrentClient:
    # El buscador admite 15 búsquedas por minuto: 4,2 s entre peticiones.
    def __init__(self, base_url: str, *, timeout: float = 20, delay: float = 4.2, session: requests.Session | None = None,
                 sleep=time.sleep):
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout = timeout
        self.delay = delay
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)
        self._sleep = sleep
        self._last_request = 0.0

    def _throttle(self):
        wait = self.delay - (time.monotonic() - self._last_request)
        if wait > 0:
            self._sleep(wait)
        self._last_request = time.monotonic()

    def _url(self, path: str) -> str:
        return urljoin(self.base_url, path.lstrip("/"))

    def search(self, query: str, *, retries: int = 2) -> list[SearchResult]:
        for _ in range(retries + 1):
            self._throttle()
            response = self.session.post(self._url("/buscar"), data={"valor": query, "Buscar": "Buscar"}, timeout=self.timeout)
            response.raise_for_status()
            wait = search_throttle_seconds(response.text)
            if wait is None:
                return parse_search_results(response.text, self.base_url)
            # Esta página de aviso no trae resultados: tomarla por "no
            # encontrado" haría creer que la serie no está publicada.
            logger.info(f"DonTorrent limita las búsquedas: esperando {wait} s")
            self._sleep(wait + 2)
        raise DontorrentRateLimited("DonTorrent sigue limitando las búsquedas")

    def episodes(self, page_url: str) -> list[EpisodeLink]:
        self._throttle()
        response = self.session.get(page_url, timeout=self.timeout)
        response.raise_for_status()
        return parse_episode_links(response.text)

    def movie(self, page_url: str) -> MoviePage | None:
        self._throttle()
        response = self.session.get(page_url, timeout=self.timeout)
        response.raise_for_status()
        return parse_movie_page(response.text)

    def fetch_torrent(self, content_id: int, tabla: str) -> tuple[str, bytes]:
        """Resuelve la prueba de trabajo y descarga el .torrent → (nombre, bytes)."""
        api = self._url("/api_validate_pow.php")
        self._throttle()
        generated = self.session.post(api, json={"action": "generate", "content_id": int(content_id), "tabla": tabla}, timeout=self.timeout)
        data = generated.json()
        if not data.get("success"):
            raise RuntimeError(f"DonTorrent no generó el desafío: {data.get('error') or generated.status_code}")

        challenge = data["challenge"]
        nonce = solve_pow(challenge)
        validated = self.session.post(api, json={"action": "validate", "challenge": challenge, "nonce": nonce}, timeout=self.timeout)
        try:
            result = validated.json()
        except ValueError:
            result = {}
        if validated.status_code == 429 or result.get("status") == "limit_exceeded":
            raise DontorrentRateLimited(f"DonTorrent: límite de descargas, esperar {result.get('wait_minutes', 60)} min")
        if result.get("status") == "captcha_required":
            raise DontorrentRateLimited("DonTorrent pide captcha: se reintentará en el próximo ciclo")
        if not result.get("success") or not result.get("download_url"):
            raise RuntimeError(f"DonTorrent rechazó la descarga: {result.get('error') or validated.status_code}")

        torrent_url = result["download_url"]
        if torrent_url.startswith("//"):
            torrent_url = f"{urlparse(self.base_url).scheme}:{torrent_url}"
        torrent_url = urljoin(self.base_url, torrent_url)
        response = self.session.get(torrent_url, timeout=self.timeout * 2)
        response.raise_for_status()
        if not response.content.startswith(b"d"):
            raise RuntimeError("La respuesta de DonTorrent no es un archivo .torrent")
        filename = unquote(urlparse(torrent_url).path.rsplit("/", 1)[-1]) or f"dontorrent-{content_id}.torrent"
        return filename, response.content
