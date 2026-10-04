"""Usuario y contraseña para entrar en la web.

- La contraseña se guarda como hash PBKDF2 (nunca en claro) en el .env.
- La sesión es una cookie firmada con HMAC (WEB_SECRET_KEY, que se genera
  sola la primera vez), válida 30 días.
- Sin contraseña configurada, todo queda abierto como antes.
- En la app de escritorio, las visitas desde el propio PC no la necesitan:
  solo se pide desde otros dispositivos de la red.

El middleware es ASGI puro para cubrir también el WebSocket de eventos.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from http.cookies import SimpleCookie

from config.settings import Settings
from web import runtime

COOKIE = "biblioteca_session"
SESSION_SECONDS = 30 * 24 * 3600
PBKDF2_ITERATIONS = 240_000
# Sin sesión también se puede: la interfaz (para mostrar el login), el
# estado del login y la comprobación de salud de Docker.
PUBLIC_API = ("/api/health", "/api/auth/")


# --- contraseñas -------------------------------------------------------------
def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt, digest = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(candidate.hex(), digest)
    except (ValueError, AttributeError):
        return False


def auth_enabled() -> bool:
    return bool(Settings.WEB_AUTH_USER and Settings.WEB_AUTH_PASSWORD)


def check_credentials(username: str, password: str) -> bool:
    if not auth_enabled():
        return False
    user_ok = hmac.compare_digest((username or "").strip().lower(), Settings.WEB_AUTH_USER.lower())
    return verify_password(password or "", Settings.WEB_AUTH_PASSWORD) and user_ok


# --- sesiones ------------------------------------------------------------------
def _secret() -> bytes:
    if not Settings.WEB_SECRET_KEY:
        Settings.update_env({"WEB_SECRET_KEY": secrets.token_hex(32)})
    return Settings.WEB_SECRET_KEY.encode("utf-8")


def make_session(username: str) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(
        {"u": username, "exp": int(time.time()) + SESSION_SECONDS,
         # Cambiar la contraseña invalida las sesiones abiertas.
         "p": hashlib.sha256(Settings.WEB_AUTH_PASSWORD.encode()).hexdigest()[:16]}).encode()).decode()
    signature = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def read_session(token: str | None) -> str | None:
    """Usuario de una cookie válida, o None."""
    if not token or "." not in token or not auth_enabled():
        return None
    payload, signature = token.rsplit(".", 1)
    expected = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode()))
    except (ValueError, json.JSONDecodeError):
        return None
    if data.get("exp", 0) < time.time():
        return None
    if data.get("p") != hashlib.sha256(Settings.WEB_AUTH_PASSWORD.encode()).hexdigest()[:16]:
        return None
    if (data.get("u") or "").lower() != Settings.WEB_AUTH_USER.lower():
        return None
    return data["u"]


def cookie_from_headers(headers: list[tuple[bytes, bytes]]) -> str | None:
    for name, value in headers:
        if name == b"cookie":
            jar = SimpleCookie()
            try:
                jar.load(value.decode("latin-1"))
            except Exception:
                return None
            if COOKIE in jar:
                return jar[COOKIE].value
    return None


def is_local_client(client) -> bool:
    host = (client[0] if client else "") or ""
    return host in ("127.0.0.1", "::1", "localhost") or host.startswith("127.")


def exempt_without_login(client) -> bool:
    """La app de escritorio no pide contraseña desde el propio PC."""
    return runtime.is_desktop() and is_local_client(client)


def request_allowed(scope) -> bool:
    if not auth_enabled() or exempt_without_login(scope.get("client")):
        return True
    return read_session(cookie_from_headers(scope.get("headers") or [])) is not None


# --- middleware ---------------------------------------------------------------
class AuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        protected = path.startswith("/api/") and not path.startswith(PUBLIC_API)
        if not protected or request_allowed(scope):
            return await self.app(scope, receive, send)

        if scope["type"] == "websocket":
            # Rechazar antes de aceptar la conexión.
            await send({"type": "websocket.close", "code": 4401})
            return
        body = json.dumps({"detail": "Inicia sesión"}).encode()
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
