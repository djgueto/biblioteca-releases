"""Inicio de sesión y ajustes de acceso (red local y contraseña)."""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from config.settings import Settings
from web import auth, runtime

router = APIRouter()


class Login(BaseModel):
    username: str
    password: str


class AccessUpdate(BaseModel):
    allow_lan: bool | None = None
    username: str | None = None
    password: str | None = None  # nueva contraseña ("" = quitarla)
    current_password: str | None = None


def _logged_user(request: Request) -> str | None:
    return auth.read_session(request.cookies.get(auth.COOKIE))


def _can_manage(request: Request) -> bool:
    """Cambiar el acceso: con sesión iniciada, sin contraseña aún, o desde el
    propio PC en la app de escritorio."""
    return (not auth.auth_enabled() or _logged_user(request) is not None
            or auth.exempt_without_login(request.client and (request.client.host, request.client.port)))


def _port() -> int:
    return int(os.getenv("WEB_PORT", "9797"))


def _bound_host() -> str:
    return os.getenv("BIBLIOTECA_BOUND_HOST") or os.getenv("WEB_HOST", "0.0.0.0")


@router.get("/api/auth/status")
def auth_status(request: Request):
    client = request.client and (request.client.host, request.client.port)
    return {
        "auth_enabled": auth.auth_enabled(),
        "logged_in": _logged_user(request) is not None,
        "required": auth.auth_enabled() and not auth.request_allowed(request.scope),
        "user": _logged_user(request) or (Settings.WEB_AUTH_USER if auth.exempt_without_login(client) else None),
    }


@router.post("/api/auth/login")
def login(body: Login, response: Response):
    if not auth.check_credentials(body.username, body.password):
        raise HTTPException(401, "Usuario o contraseña incorrectos")
    response.set_cookie(auth.COOKIE, auth.make_session(Settings.WEB_AUTH_USER), max_age=auth.SESSION_SECONDS,
                        httponly=True, samesite="lax")
    return {"ok": True, "user": Settings.WEB_AUTH_USER}


@router.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE)
    return {"ok": True}


def _access_settings() -> dict:
    host = _bound_host()
    on_network = runtime.listening_on_network(host)
    return {
        "desktop": runtime.is_desktop(),
        "docker": runtime.is_docker(),
        "allow_lan": Settings.WEB_ALLOW_LAN if runtime.is_desktop() else on_network,
        "listening_on_network": on_network,
        # El ajuste cambió pero la app aún escucha como antes.
        "restart_needed": runtime.is_desktop() and Settings.WEB_ALLOW_LAN != on_network,
        "lan_urls": [f"http://{ip}:{_port()}" for ip in runtime.lan_addresses()] if on_network else [],
        "auth_enabled": auth.auth_enabled(),
        "username": Settings.WEB_AUTH_USER,
    }


@router.get("/api/settings/access")
def get_access():
    return _access_settings()


@router.put("/api/settings/access")
def put_access(body: AccessUpdate, request: Request):
    if not _can_manage(request):
        raise HTTPException(401, "Inicia sesión")
    values: dict = {}
    if body.allow_lan is not None:
        if not runtime.is_desktop():
            raise HTTPException(422, "Solo se puede cambiar en la app de escritorio")
        values["WEB_ALLOW_LAN"] = body.allow_lan

    if body.password is not None:
        if auth.auth_enabled() and not auth.exempt_without_login(request.client and (request.client.host, request.client.port)):
            if not auth.verify_password(body.current_password or "", Settings.WEB_AUTH_PASSWORD):
                raise HTTPException(403, "La contraseña actual no es correcta")
        if body.password == "":
            values["WEB_AUTH_USER"] = ""
            values["WEB_AUTH_PASSWORD"] = ""
        else:
            username = (body.username or Settings.WEB_AUTH_USER or "").strip()
            if not username:
                raise HTTPException(422, "Indica un nombre de usuario")
            if len(body.password) < 6:
                raise HTTPException(422, "La contraseña debe tener al menos 6 caracteres")
            values["WEB_AUTH_USER"] = username
            values["WEB_AUTH_PASSWORD"] = auth.hash_password(body.password)
    elif body.username is not None and auth.auth_enabled():
        if not body.username.strip():
            raise HTTPException(422, "Indica un nombre de usuario")
        values["WEB_AUTH_USER"] = body.username.strip()

    if values.get("WEB_ALLOW_LAN") and not (values.get("WEB_AUTH_PASSWORD") or auth.auth_enabled()):
        raise HTTPException(422, "Para abrir el acceso a la red, crea antes un usuario y contraseña")
    if values:
        Settings.update_env(values)
    response = _access_settings()
    if "WEB_AUTH_PASSWORD" in values and values["WEB_AUTH_PASSWORD"]:
        # Quien la acaba de poner sigue dentro sin tener que iniciar sesión.
        from fastapi.responses import JSONResponse

        out = JSONResponse(response)
        out.set_cookie(auth.COOKIE, auth.make_session(values["WEB_AUTH_USER"]), max_age=auth.SESSION_SECONDS,
                       httponly=True, samesite="lax")
        return out
    return response


@router.post("/api/system/restart")
def restart(request: Request):
    if not runtime.is_desktop():
        raise HTTPException(422, "Reinicia el contenedor o el servicio para aplicar los cambios")
    if not _can_manage(request):
        raise HTTPException(401, "Inicia sesión")
    runtime.restart()
    return {"ok": True}
