from __future__ import annotations

import logging

from config.settings import Settings
from core.download_clients import TORRENT, DownloadDispatcher, Release
from core.repositories.grab_history_repository import GrabHistoryRepository
from core.services.providers.base import DownloadHandle, SourceOffer
from core.services.quality import Quality
from core.wolfmax.downloader import (
    WolfmaxDownloader,
    WolfmaxRateLimited,
    WolfmaxVerificationRequired,
    is_current_url,
)
from core.wolfmax.searcher import WolfmaxSearcher

logger = logging.getLogger(__name__)


class WolfmaxSourceProvider:
    """Envuelve WolfmaxSearcher/WolfmaxDownloader (que ya gestionan su propio
    driver Selenium por llamada) para exponerlos como SourceProvider."""

    name = "wolfmax"
    # Sabe buscar y descargar películas sueltas (las novedades aprobadas).
    supports_movies = True

    def __init__(self, db_manager, dispatcher: DownloadDispatcher | None = None):
        self.db = db_manager
        self.dispatcher = dispatcher
        self._searcher = WolfmaxSearcher()
        self._downloader = WolfmaxDownloader()
        # El sitio exige verificación de persona para descargar: no se
        # reintenta en lo que queda de ciclo (cada intento abre un Chrome y
        # tarda ~12 s; con cientos de episodios eran horas perdidas).
        self.verification_required = False

    def find_candidate(self, item: dict, *, content_mode: str = "episodic") -> SourceOffer | None:
        title = (item.get("tmdb_name") or item.get("name") or "").strip()
        if not title:
            return None
        app_category = item.get("content_category") or "movies"
        result = self._searcher.search(title, item.get("year"), app_category=app_category)
        if not result:
            return None
        url, score, quality_height = result
        return SourceOffer(
            provider=self.name,
            source_url=url,
            title=title,
            quality=Quality(resolution_height=quality_height),
            match_score=float(score or 0),
        )

    def refresh_offer(self, item: dict, source_url: str) -> SourceOffer | None:
        # URLs del diseño antiguo del sitio (/movie/123, /series/4k-2160p/x)
        # ya no existen: marcarlas no disponibles hasta que
        # scan_wolfmax_categories guarde la URL nueva.
        if not source_url or not is_current_url(source_url):
            return None
        # Wolfmax no tiene una forma barata de "revisitar" una única URL sin
        # re-escanear la categoría (eso ya lo hace scan_wolfmax_categories
        # cada ciclo, que además alimenta source_offers vía el dual-write en
        # DatabaseManager.save_wolfmax_source). Las URLs nuevas no llevan la
        # calidad en el path, así que se reutiliza la guardada en el escaneo.
        height = None
        stored = self.db.get_wolfmax_source_for_series(item["id"]) if item.get("id") else None
        if stored and stored.get("source_url") == source_url and stored.get("match_score"):
            height = round(float(stored["match_score"]) * 2160)
        return SourceOffer(
            provider=self.name,
            source_url=source_url,
            title=item.get("tmdb_name") or item.get("name", ""),
            quality=Quality(resolution_height=height),
            match_score=1.0,
        )

    def resolve_download(
        self, item: dict, offer: SourceOffer, *, missing_only: bool = True
    ) -> DownloadHandle | None:
        if self.dispatcher is None or not self.dispatcher.can_handle(TORRENT):
            return None
        if self.verification_required:
            return None

        series_id = item.get("id")
        episodic_cats = self.db.get_episodic_categories()
        is_episodic = item.get("content_category") in episodic_cats
        history = GrabHistoryRepository(self.db)
        episodes_by_url: dict[str, list[tuple[int, int]]] = {}

        if is_episodic:
            episodes = self._downloader.get_episodes(offer.source_url)
            if missing_only and series_id:
                # El sitio publica fichas por temporada con todos sus
                # episodios: descargar solo los que faltan en la biblioteca y
                # no se han pedido ya (ni están esperando en descargas).
                missing = {
                    (m.get("season"), m.get("episode"))
                    for m in self.db.get_missing_episodes_for_series(series_id)
                } - history.blocked_episodes(item, Settings.GRAB_RETRY_DAYS)
                episodes = [
                    ep for ep in episodes
                    if any((ep["season"], n) in missing for n in ep["episodes"])
                ]
            episodes_by_url = {ep["url"]: [(ep["season"], n) for n in ep["episodes"]] for ep in episodes}
            episode_urls = [ep["url"] for ep in episodes]
            if not episode_urls:
                return None
            new_urls = self.db.filter_new_wolfmax_urls(episode_urls)
        else:
            if series_id and history.movie_blocked(item, Settings.GRAB_RETRY_DAYS):
                return None
            new_urls = self.db.filter_new_wolfmax_urls([offer.source_url])

        if not new_urls:
            return None

        grabbed = []
        for ep_url in new_urls:
            try:
                filename, content = self._downloader.fetch_torrent(ep_url)
                destination = self.dispatcher.grab(Release(
                    protocol=TORRENT,
                    title=item.get("tmdb_name") or item.get("name", ""),
                    filename=filename,
                    content=content,
                    source_url=ep_url,
                ))
                # Solo se marca tras entregarlo: si el cliente falla, la URL
                # se reintenta en el siguiente ciclo.
                self.db.mark_wolfmax_urls_done([ep_url], series_id=series_id)
                if series_id:
                    # Registro común de envíos: así ninguna otra fuente vuelve a
                    # pedir estos episodios mientras se descargan.
                    history.record(self.name, ep_url, series_id, episodes_by_url.get(ep_url, []),
                                   release_title=filename, destination=destination)
                grabbed.append(destination)
            except WolfmaxRateLimited as e:
                logger.warning(str(e))
                break
            except WolfmaxVerificationRequired as e:
                logger.warning(f"{e}: descargas de Wolfmax en pausa en este ciclo")
                self.verification_required = True
                break
            except Exception as e:
                logger.error(f"wolfmax resolve_download error {ep_url}: {e}")

        if not grabbed:
            return None
        return DownloadHandle(
            provider=self.name,
            kind=TORRENT,
            payload={"grabbed": grabbed},
        )
