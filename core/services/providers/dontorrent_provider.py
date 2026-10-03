from __future__ import annotations

import logging
import re
import unicodedata

from core.download_clients import TORRENT, DownloadDispatcher, Release
from config.settings import Settings
from core.dontorrent.client import DontorrentClient, DontorrentRateLimited, MoviePage, SearchResult, quality_from_label
from core.repositories.grab_history_repository import GrabHistoryRepository
from core.services.providers.base import DownloadHandle, SourceOffer
from core.services.quality import Quality, quality_from_local_episode
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
    """Series (faltantes y mejoras) y películas desde DonTorrent (torrent, sin
    navegador).

    DonTorrent publica una ficha por temporada y calidad. Para cada serie se
    busca por título y, para cada temporada, se usa la ficha de mayor
    calidad (con las demás como respaldo si a la mejor le falta algún
    episodio). Las películas se comprueban por año en su ficha. Los .torrent
    se entregan al despachador; el proveedor no sabe adónde van.

    `discovers_by_search`: el scheduler le pide ofertas para las series con
    faltantes que aún no tienen una (no tiene catálogo que rastrear).
    """

    name = "dontorrent"
    discovers_by_search = True
    supports_movies = True

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
        self._page_cache: dict[str, MoviePage | None] = {}
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

    def _candidates(self, item: dict, *, kinds=EPISODIC_KINDS, seasons_only: bool = True) -> list[tuple[SearchResult, float]]:
        names = [n for n in dict.fromkeys([(item.get("tmdb_name") or "").strip(), (item.get("name") or "").strip()]) if n]
        wanted = [_comparable(n) for n in names]
        best: dict[str, tuple[SearchResult, float]] = {}
        for query in search_queries(names):
            for result in self._search(query):
                if result.kind not in kinds or (seasons_only and result.season is None):
                    continue
                score = max(_title_score(_comparable(result.title), w) for w in wanted)
                if score >= self.match_threshold and score > best.get(result.url, (None, 0))[1]:
                    best[result.url] = (result, score)
            if best:
                break
        return list(best.values())

    def _missing(self, item: dict) -> set[tuple[int, int]]:
        """Faltantes que se pueden pedir: sin los enviados hace poco ni los que
        ya están en las carpetas de descargas esperando a la biblioteca."""
        missing = {(m["season"], m["episode"]) for m in self.db.get_missing_episodes_for_series(item["id"])}
        return missing - self.history.blocked_episodes(item, self.retry_days)

    def _local_qualities(self, series_id: int) -> dict[tuple[int, int], Quality]:
        """Calidad medida con MediaInfo de cada episodio en disco (lo deducido
        del nombre del archivo no cuenta)."""
        return {
            (ep["season"], ep["episode"]): quality_from_local_episode(ep)
            for ep in self.db.get_local_episodes(series_id)
            if ep.get("quality_source") == "mediainfo" and ep.get("resolution_height")
        }

    def _by_quality(self, candidates):
        # Mejor calidad según el perfil primero (descartando las que no llegan
        # al mínimo); a igual calidad, mejor coincidencia de título.
        acceptable = [c for c in candidates if self.profile.is_acceptable(c[0].quality)]
        return sorted(acceptable, key=lambda c: (self.profile.rank(c[0].quality), c[1]), reverse=True)

    @staticmethod
    def _is_single_file(item: dict) -> bool:
        return Settings.get_category_content_mode(item.get("content_category") or "series") == "single_file"

    # --- series -------------------------------------------------------------
    def _offer_for(self, item: dict) -> SourceOffer | None:
        candidates = self._candidates(item)
        if not candidates:
            return None
        seasons = {s for s, _ in self._missing(item)} if item.get("id") else set()
        # Solo cuenta una ficha de una temporada que falta: una oferta de
        # otra temporada podría ganar el arbitraje por calidad y dejar sin
        # turno a un proveedor que sí tiene esos episodios. Sin faltantes
        # (mejoras) valen las temporadas que hay en disco.
        if not seasons and item.get("id"):
            seasons = {s for s, _ in self._local_qualities(item["id"])}
        useful = [c for c in candidates if not seasons or c[0].season in seasons]
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

    # --- películas ------------------------------------------------------------
    def _movie_offer(self, item: dict) -> SourceOffer | None:
        kinds = ("pelicula", "documental") if item.get("content_category") == "documentaries" else ("pelicula",)
        candidates = self._by_quality(self._candidates(item, kinds=kinds, seasons_only=False))
        year = item.get("year")
        # El buscador no da el año: se mira en la ficha (como mucho 3) para no
        # confundir "Dune" (1984) con "Dune" (2021).
        for result, score in candidates[:3]:
            page = self._movie_page(result.url)
            if page is None:
                continue
            if year and page.year and abs(page.year - int(year)) > 1:
                continue
            quality = quality_from_label(page.format_label) if page.format_label else result.quality
            if quality.resolution_height is None:
                quality = result.quality
            return SourceOffer(provider=self.name, source_url=result.url,
                               title=f"{result.title} ({page.year or '?'}) [{page.format_label or result.quality_label}]",
                               quality=quality, match_score=score)
        return None

    def _movie_page(self, url: str) -> MoviePage | None:
        if url not in self._page_cache:
            self._page_cache[url] = self.client.movie(url)
        return self._page_cache[url]

    # --- contrato SourceProvider ------------------------------------------
    def find_candidate(self, item: dict, *, content_mode: str = "episodic") -> SourceOffer | None:
        if content_mode == "single_file" or self._is_single_file(item):
            return self._movie_offer(item)
        return self._offer_for(item)

    def refresh_offer(self, item: dict, source_url: str) -> SourceOffer | None:
        # La URL guardada es solo la mejor ficha de la última vez: se
        # recalcula desde la búsqueda (puede haber salido una mejor).
        if self._is_single_file(item):
            return self._movie_offer(item)
        return self._offer_for(item)

    def resolve_download(self, item: dict, offer: SourceOffer, *, missing_only: bool = True) -> DownloadHandle | None:
        if self._blocked or self.dispatcher is None or not self.dispatcher.can_handle(TORRENT):
            return None
        if item.get("id") is None:
            return None
        try:
            if self._is_single_file(item):
                return self._resolve_movie(item, offer, upgrade=not missing_only)
            return self._resolve_episodes(item, missing_only=missing_only)
        except DontorrentRateLimited as e:
            logger.warning(str(e))
            self._blocked = True
            return None

    def _resolve_movie(self, item: dict, offer: SourceOffer | None, *, upgrade: bool) -> DownloadHandle | None:
        series_id = item["id"]
        if self.history.movie_blocked(item, self.retry_days):
            return None
        if offer is None or offer.provider != self.name:
            offer = self._movie_offer(item)
        if offer is None:
            return None
        if upgrade:
            from core.repositories.item_quality_repository import ItemQualityRepository

            local = ItemQualityRepository(self.db).get(series_id)
            if local is None or not self.profile.is_upgrade(local, offer.quality):
                return None
        page = self._movie_page(offer.source_url)
        if page is None or self._downloads >= self.max_downloads:
            return None
        destination = self._grab(page.content_id, page.tabla, offer.title, offer.source_url)
        if destination is None:
            return None
        self.history.record(self.name, f"{page.tabla}:{page.content_id}", series_id, [],
                            release_title=offer.title, destination=destination)
        return self._handle([destination])

    def _resolve_episodes(self, item: dict, *, missing_only: bool) -> DownloadHandle | None:
        """Faltantes: cualquier calidad aceptable (la mejor que haya).
        Mejoras: solo episodios en disco cuya calidad medida mejora la ficha."""
        series_id = item["id"]
        recent = self.history.blocked_episodes(item, self.retry_days)
        if missing_only:
            wanted = {key: None for key in self._missing(item)}
        else:
            wanted = {key: q for key, q in self._local_qualities(series_id).items() if key not in recent}
        if not wanted:
            return None
        candidates = self._candidates(item)
        grabbed = []
        for season in sorted({s for s, _ in wanted}):
            for result, _ in self._by_quality([c for c in candidates if c[0].season == season]):
                pending = {e for (s, e), local in wanted.items() if s == season
                           and (local is None or self.profile.is_upgrade(local, result.quality))}
                if not pending:
                    continue
                for link in self.client.episodes(result.url):
                    covered = [e for e in link.episodes if e in pending]
                    if link.season != season or not covered:
                        continue
                    if self._downloads >= self.max_downloads:
                        return self._handle(grabbed)
                    destination = self._grab(link.content_id, link.tabla, f"{result.title} {link.label}", result.url)
                    if destination is None:
                        continue
                    episodes = [(season, e) for e in covered]
                    self.history.record(self.name, f"{link.tabla}:{link.content_id}", series_id, episodes,
                                        release_title=f"{result.title} {link.label}", destination=destination)
                    for key in episodes:
                        wanted.pop(key, None)
                    pending -= set(covered)
                    grabbed.append(destination)
        return self._handle(grabbed)

    def _grab(self, content_id: int, tabla: str, title: str, source_url: str) -> str | None:
        """Descarga el .torrent y lo entrega al cliente. DontorrentRateLimited
        se propaga: para todo el ciclo de este proveedor."""
        try:
            filename, content = self.client.fetch_torrent(content_id, tabla)
            destination = self.dispatcher.grab(Release(
                protocol=TORRENT, title=title, filename=filename, content=content, source_url=source_url))
        except DontorrentRateLimited:
            raise
        except Exception as e:
            logger.error(f"dontorrent: error con {title}: {e}")
            return None
        self._downloads += 1
        return destination

    def _handle(self, grabbed: list[str]) -> DownloadHandle | None:
        if not grabbed:
            return None
        return DownloadHandle(provider=self.name, kind=TORRENT, payload={"grabbed": grabbed})
