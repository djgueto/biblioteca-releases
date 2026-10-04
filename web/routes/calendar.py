"""Calendario de emisiones."""

from __future__ import annotations

from fastapi import APIRouter

from core.agenda.service import AgendaService
from core.database.db_manager import DatabaseManager
from core.tmdb.metadata import image_url


def build(_ctx) -> APIRouter:
    router = APIRouter()

    @router.get("/api/calendar")
    def calendar(start: str | None = None, end: str | None = None):
        from datetime import date, timedelta

        today = date.today()
        start = start or (today - timedelta(days=7)).isoformat()
        end = end or (today + timedelta(days=30)).isoformat()
        entries = AgendaService(DatabaseManager()).entries(start, end)
        for entry in entries:
            if entry.get("poster_path"):
                entry["poster_url"] = image_url(entry.pop("poster_path"), "w185")
        return {"today": today.isoformat(), "start": start, "end": end, "entries": entries}

    return router
