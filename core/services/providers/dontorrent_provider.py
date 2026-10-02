from __future__ import annotations

import logging
import re
import unicodedata

from core.download_clients import TORRENT, DownloadDispatcher, Release
from core.dontorrent.client import DontorrentClient, DontorrentRateLimited, SearchResult
from core.repositories.grab_history_repository import GrabHistoryRepository
from core.services.providers.base import DownloadHandle, SourceOffer
from core.services.quality_profile import QualityProfile
from core.wolfmax.category_scanner import _normalize, _title_score

logger = logging.getLogger(__name__)

EPISODIC_KINDS = ("serie", "documental")
_QUERY_CUT_RE = re.compile(r"[:&(\[–—]| - ")


def _comparable(title: str) -> str:
    """Título normalizado y sin acentos (el sitio mezcla 'Física' y 'Fisica')."""
    stripped = unicodedata.normalize("NFKD", title or "")
    return _normalize("".join(c for c in stripped if not unicodedata.combining(c)))


def search_queries(names: list[str]) -> list[str]:
    """Consultas para el buscador de DonTorrent, que busca la frase literal:
    'Dune: La profecía' no encuentra 'Dune La profecia'. Se busca primero por
    la parte anterior a los dos puntos ('Dune'), más amplia, y se filtra
    después comparando con el título completo."""
    queries = []
    for name in names:
        prefix = _QUERY_CUT_RE.split(name, maxsplit=1)[0].strip(" .,-")
        for query in (prefix if len(prefix) >= 4 else "", name):
            query = re.sub(r"\s+", " ", re.sub(r"[^\w\s']", " ", query)).strip()
            if len(query) >= 2 and query.lower() not in (q.lower() for q in queries):
                queries.append(query)
    return queries


class DontorrentSourceProvider:
    """Episodios faltantes de series desde DonTorrent (torrent, sin navegador).

    DonTorrent publica una ficha por temporada y calidad. Para cada serie se
    busca por título y, para cada temporada con faltantes, se usa la ficha
    de mayor calidad (con las demás como respaldo si a la mejor le falta
    algún episodio). Los .torrent se entregan al despachador; el proveedor
    no sabe adónde van.

    `discovers_by_search`: el scheduler le pide ofertas para las series con
    faltantes que aún no tienen una (no tiene catálogo que rastrear).
    """

    name = "dontorrent"
    discovers_by_search = True

    def __init__(
        self,
        db_manager,
        dispatcher: DownloadDispatcher | None,
        client: DontorrentClient,
        *,
        match_threshold: float = 0.8,
        max_downloads_per_cycle: int = 10,
        retry_days: int = 7,
        profile: QualityProfile | None = None,
    ):
        self.db = db_manager
        self.dispatcher = dispatcher
        self.client = client
        self.match_threshold = match_threshold
        self.max_downloads = max_downloads_per_cycle
        self.retry_days = retry_days
        self.history = GrabHistoryRepository(db_manager)
        self.profile = profile or QualityProfile()
        self._search_cache: dict[str, list[SearchResult]] = {}
        self._downloads = 0
        self._blocked = False

    # --- búsqueda -------------------------------------------------------
    def _search(self, query: str) -> list[SearchResult]:
        if self._blocked:
            raise DontorrentRateLimited("DonTorrent limitado en este ciclo")
        key = query.strip().lower()
        if key not in self._search_cache:
            try:
                self._search_cache[key] = self.client.search(query)
            except DontorrentRateLimited:
                self._blocked = True
                raise
        return self._search_cache[key]

    def _candidates(self, item: dict) -> list[tuple[SearchResult, float]]:
        names = [n for n in dict.fromkeys([(item.get("tmdb_name") or "").strip(), (item.get("name") or "").strip()]) if n]
        wanted = [_comparable(n) for n in names]
        best: dict[str, tuple[SearchResult, float]] = {}
        for query in search_queries(names):
            for result in self._search(query):
                if result.kind not in EPISODIC_KINDS or result.season is None:
                    continue
                score = max(_title_score(_comparable(result.title), w) for w in wanted)
                if score >= self.match_threshold and score > best.get(result.url, (None, 0))[1]:
                    best[result.url] = (result, score)
            if best:
                break
        return list(best.values())

    def _missing(self, series_id: int) -> set[tuple[int, int]]:
        missing = {(m["season"], m["episode"]) for m in self.db.get_missing_episodes_for_series(series_id)}
        return missing - self.history.recently_grabbed_episodes(series_id, self.retry_days)

    def _by_quality(self, candidates):
        # Mejor calidad según el perfil primero (descartando las que no llegan
        # al mínimo); a igual calidad, mejor coincidencia de título.
        acceptable = [c for c in candidates if self.profile.is_acceptable(c[0].quality)]
        return sorted(acceptable, key=lambda c: (self.profile.rank(c[0].quality), c[1]), reverse=True)

    def _offer_for(self, item: dict) -> SourceOffer | None:
        candidates = self._candidates(item)
        if not candidates:
            return None
        seasons = {s for s, _ in self._missing(item["id"])} if item.get("id") else set()
        # Solo cuenta una ficha de una temporada que falta: una oferta de
        # otra temporada podría ganar el arbitraje por calidad y dejar sin
        # turno a un proveedor que sí tiene esos episodios.
        useful = [c for c in candidates if not seasons or c[0].season in seasons]
        if not useful:
            return None
        ranked = self._by_quality(useful)
        if not ranked:
            return None
        result, score = ranked[0]
        return SourceOffer(
            provider=self.name,
            source_url=result.url,
            title=f"{result.title} T{result.season} ({result.quality_label})",
            quality=result.quality,
            match_score=score,
        )

    # --- contrato SourceProvider ------------------------------------------
    def find_candidate(self, item: dict, *, content_mode: str = "episodic") -> SourceOffer | None:
        if content_mode != "episodic":
            return None
        return self._offer_for(item)

    def refresh_offer(self, item: dict, source_url: str) -> SourceOffer | None:
        # La URL guardada es solo la mejor ficha de la última vez: cada
        # temporada puede tener otra, así que se recalcula desde la búsqueda.
        return self._offer_for(item)

    def resolve_download(self, item: dict, offer: SourceOffer, *, missing_only: bool = True) -> DownloadHandle | None:
        # Por ahora solo faltantes: los upgrades de calidad siguen en
        # Hispashare/Wolfmax.
        if not missing_only or self._blocked:
            return None
        if self.dispatcher is None or not self.dispatcher.can_handle(TORRENT):
            return None
        series_id = item.get("id")
        if series_id is None:
            return None

        missing = self._missing(series_id)
        if not missing:
            return None
        try:
            candidates = self._candidates(item)
        except DontorrentRateLimited as e:
            logger.warning(str(e))
            return None
        grabbed = []
        for season in sorted({s for s, _ in missing}):
            pages = self._by_quality([c for c in candidates if c[0].season == season])
            for result, _ in pages:
                pending = {e for s, e in missing if s == season}
                if not pending:
                    break
                for link in self.client.episodes(result.url):
                    covered = [e for e in link.episodes if (season, e) in missing]
                    if link.season != season or not covered:
                        continue
                    if self._downloads >= self.max_downloads:
                        return self._handle(grabbed)
                    try:
                        filename, content = self.client.fetch_torrent(link.content_id, link.tabla)
                        destination = self.dispatcher.grab(Release(
                            protocol=TORRENT,
                            title=f"{result.title} {link.label}",
                            filename=filename,
                            content=content,
                            source_url=result.url,
                        ))
                    except DontorrentRateLimited as e:
                        logger.warning(str(e))
                        self._blocked = True
                        return self._handle(grabbed)
                    except Exception as e:
                        logger.error(f"dontorrent: error con {result.title} {link.label}: {e}")
                        continue
                    self._downloads += 1
                    episodes = [(season, e) for e in covered]
                    self.history.record(self.name, f"{link.tabla}:{link.content_id}", series_id, episodes,
                                        release_title=filename, destination=destination)
                    missing -= set(episodes)
                    grabbed.append(destination)
        return self._handle(grabbed)

    def _handle(self, grabbed: list[str]) -> DownloadHandle | None:
        if not grabbed:
            return None
        return DownloadHandle(provider=self.name, kind=TORRENT, payload={"grabbed": grabbed})
