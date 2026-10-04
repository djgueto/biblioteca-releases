"""Actualizaciones: estado, comprobar ahora, instalar y clave de GitHub."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config.settings import Settings
from web import updates
from web.routes.setup import mask

router = APIRouter()


class UpdateSettings(BaseModel):
    enabled: bool | None = None
    token: str | None = None


def _status() -> dict:
    return {**updates.state.to_dict(), "token": mask(Settings.UPDATE_GITHUB_TOKEN)}


@router.get("/api/updates")
def get_updates():
    return _status()


@router.post("/api/updates/check")
def check_now():
    updates.check()
    return _status()


@router.post("/api/updates/install")
def install():
    try:
        updates.install()
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return _status()


@router.put("/api/settings/updates")
def put_settings(body: UpdateSettings):
    values = {}
    if body.enabled is not None:
        values["UPDATE_CHECK_ENABLED"] = body.enabled
    if body.token is not None and body.token.strip() != mask(Settings.UPDATE_GITHUB_TOKEN):
        values["UPDATE_GITHUB_TOKEN"] = body.token.strip()
    if values:
        Settings.update_env(values)
    if "UPDATE_GITHUB_TOKEN" in values:
        updates.check()  # comprobar ya que la clave funciona
    return _status()
