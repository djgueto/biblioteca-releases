"""Actualizaciones: comprobar la última versión publicada en GitHub y, en la
app de escritorio, descargarla e instalarla.

- La comprobación usa la API de releases de UPDATE_REPO. Si el repositorio
  es privado hace falta una clave (UPDATE_GITHUB_TOKEN, un token de GitHub
  con permiso de lectura del repositorio); sin ella se informa de que no se
  puede comprobar.
- La instalación solo existe en la app empaquetada (PyInstaller): descarga el
  paquete de su sistema, lo descomprime aparte y lanza un actualizador que
  espera a que la app se cierre, sustituye sus archivos y la vuelve a abrir.
  Los datos (DATA_DIR) no se tocan.
- En Docker o desde el código solo se avisa, con las instrucciones.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import requests

from config.settings import Settings
from web import runtime

logger = logging.getLogger(__name__)

CHECK_EVERY_SECONDS = 24 * 3600
ASSETS = {"win32": "Biblioteca-windows.zip", "darwin": "Biblioteca-macos.zip", "linux": "Biblioteca-linux.tar.gz"}


def parse_version(text: str | None) -> tuple[int, ...] | None:
    m = re.match(r"^v?(\d+)\.(\d+)(?:\.(\d+))?", (text or "").strip())
    return tuple(int(x or 0) for x in m.groups()) if m else None


def is_newer(latest: str | None, current: str | None) -> bool:
    lv, cv = parse_version(latest), parse_version(current)
    return bool(lv and cv and lv > cv)


def can_self_update() -> bool:
    return bool(getattr(sys, "frozen", False)) and sys.platform in ASSETS


@dataclass
class UpdateState:
    current: str = field(default_factory=lambda: Settings.APP_VERSION)
    latest: str | None = None
    available: bool = False
    notes: str = ""
    url: str = ""  # página de la release
    published_at: str | None = None
    checked_at: str | None = None
    error: str | None = None
    can_install: bool = field(default_factory=can_self_update)
    installing: bool = False
    progress: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        data["mode"] = "desktop" if runtime.is_desktop() else "docker" if runtime.is_docker() else "source"
        data["enabled"] = Settings.UPDATE_CHECK_ENABLED
        data["repo"] = Settings.UPDATE_REPO
        data["token_configured"] = bool(Settings.UPDATE_GITHUB_TOKEN)
        return data


state = UpdateState()
_lock = threading.Lock()
_release: dict | None = None  # última respuesta de la API (para instalar)


def _headers(accept: str = "application/vnd.github+json") -> dict:
    headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "Biblioteca-updater"}
    if Settings.UPDATE_GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {Settings.UPDATE_GITHUB_TOKEN}"
    return headers


def check() -> dict:
    """Consulta la última release. Nunca lanza: el error queda en el estado."""
    global _release
    url = f"https://api.github.com/repos/{Settings.UPDATE_REPO}/releases/latest"
    try:
        r = requests.get(url, headers=_headers(), timeout=15)
        if r.status_code == 404:
            raise RuntimeError("No se encuentra el repositorio. Si es privado, añade una clave de GitHub "
                               "en Sistema → Actualizaciones." if not Settings.UPDATE_GITHUB_TOKEN
                               else "La clave de GitHub no tiene acceso al repositorio")
        if r.status_code in (401, 403):
            raise RuntimeError("GitHub rechaza la clave (caducada o sin permiso de lectura)")
        r.raise_for_status()
        release = r.json()
    except Exception as e:
        with _lock:
            state.error = str(e) if isinstance(e, RuntimeError) else f"No se pudo consultar GitHub: {e}"
            state.checked_at = datetime.now().isoformat(timespec="seconds")
        logger.warning(f"Comprobación de actualizaciones: {state.error}")
        return state.to_dict()

    with _lock:
        _release = release
        state.current = Settings.APP_VERSION
        state.latest = release.get("tag_name")
        state.available = is_newer(state.latest, state.current)
        state.notes = (release.get("body") or "").strip()
        state.url = release.get("html_url") or ""
        state.published_at = release.get("published_at")
        state.checked_at = datetime.now().isoformat(timespec="seconds")
        state.error = None
    if state.available:
        logger.info(f"Hay una versión nueva: {state.latest} (instalada {state.current})")
    return state.to_dict()


def start_background_checks():
    """Comprueba al arrancar y una vez al día (si está activado)."""
    def loop():
        time.sleep(20)  # que el arranque no compita con la red
        while True:
            if Settings.UPDATE_CHECK_ENABLED and parse_version(Settings.APP_VERSION):
                check()
            time.sleep(CHECK_EVERY_SECONDS)

    threading.Thread(target=loop, name="actualizaciones", daemon=True).start()


# --- instalación (app de escritorio) ------------------------------------------
def install_dir() -> Path:
    exe = Path(sys.executable).resolve()
    if sys.platform == "darwin":
        return exe.parents[2]  # Biblioteca.app/Contents/MacOS/Biblioteca -> Biblioteca.app
    return exe.parent


def _asset_url(asset: dict) -> str:
    # Con clave, los recursos de un repositorio privado se piden por la API.
    return asset["url"] if Settings.UPDATE_GITHUB_TOKEN else asset["browser_download_url"]


def _download(asset: dict, dest: Path) -> str:
    """Descarga el paquete y devuelve su SHA-256."""
    digest = hashlib.sha256()
    with requests.get(_asset_url(asset), headers=_headers("application/octet-stream"), stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or asset.get("size") or 0)
        done = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if total:
                    state.progress = f"Descargando… {done * 100 // total}%"
    return digest.hexdigest()


# --- firma de las versiones ---------------------------------------------------
# Cada versión trae SHA256SUMS (huella de cada paquete) y SHA256SUMS.sig, su
# firma Ed25519 hecha en la CI (packaging/desktop/sign_release.py) con una
# clave privada que solo está en los secretos de GitHub. Solo se instala un
# paquete cuya huella aparezca en un SHA256SUMS firmado con esta clave: los
# instaladores son públicos y así nadie puede colar uno modificado.
SIGNING_PUBLIC_KEY = "kP5pUBNUlV2GNTGU0ndmpobXib37x7IjwnjtY7Ak5Xw="
SUMS_ASSET, SIG_ASSET = "SHA256SUMS", "SHA256SUMS.sig"


def verified_sums(sums: bytes, signature: bytes, public_key: str | None = None) -> dict[str, str]:
    """{nombre de paquete: sha256} si la firma es válida; si no, RuntimeError."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key or SIGNING_PUBLIC_KEY))
    try:
        key.verify(base64.b64decode(signature.strip()), sums)
    except (InvalidSignature, ValueError) as e:
        raise RuntimeError("La firma de la versión no es válida: no se instala") from e
    out = {}
    for line in sums.decode().splitlines():
        digest, _, name = line.partition("  ")
        if digest and name:
            out[name.strip()] = digest.strip().lower()
    return out


def _fetch_small(asset: dict) -> bytes:
    r = requests.get(_asset_url(asset), headers=_headers("application/octet-stream"), timeout=30)
    r.raise_for_status()
    return r.content


def _expected_sha256(release: dict, package: str) -> str:
    assets = {a.get("name"): a for a in release.get("assets") or []}
    if SUMS_ASSET not in assets or SIG_ASSET not in assets:
        raise RuntimeError(f"La versión {release.get('tag_name')} no está firmada: no se instala")
    sums = verified_sums(_fetch_small(assets[SUMS_ASSET]), _fetch_small(assets[SIG_ASSET]))
    if package not in sums:
        raise RuntimeError(f"{package} no aparece en la lista firmada de la versión: no se instala")
    return sums[package]


def _extract(archive: Path, target: Path) -> Path:
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as z:
            z.extractall(target)
    else:
        with tarfile.open(archive) as t:
            t.extractall(target)
    entries = [p for p in target.iterdir()]
    return entries[0] if len(entries) == 1 else target


def _updater_script(work: Path, new_app: Path, dest: Path) -> list[str]:
    pid = os.getpid()
    if sys.platform == "win32":
        script = work / "actualizar.cmd"
        exe = dest / "Biblioteca.exe"
        # Saltos "\n": write_text ya los convierte en CRLF en Windows (con
        # "\r\n" quedaba "\r\r\n" y cmd no encontraba la etiqueta :esperar).
        # ping en vez de timeout: timeout falla en un proceso sin consola.
        # Sin tubería "tasklist | find": una vez se quedó colgado el find
        # con la app ya cerrada. Además la espera tiene tope (~2 min) y
        # robocopy reintenta si algún archivo sigue bloqueado.
        procs = work / "procesos.txt"
        script.write_text(
            "@echo off\n"
            "set intentos=0\n"
            ":esperar\n"
            "set /a intentos+=1\n"
            "if %intentos% gtr 60 goto copiar\n"
            f'tasklist /FI "PID eq {pid}" /FI "IMAGENAME eq {Path(sys.executable).name}" /NH >"{procs}" 2>nul\n'
            f'find "{pid}" "{procs}" >nul 2>nul && (ping -n 2 127.0.0.1 >nul & goto esperar)\n'
            ":copiar\n"
            f'robocopy "{new_app}" "{dest}" /MIR /R:30 /W:1 /NFL /NDL /NJH /NJS /NP >nul\n'
            f'start "" "{exe}"\n'
            f'rmdir /s /q "{work}"\n', encoding="utf-8")
        return ["cmd", "/c", str(script)]
    script = work / "actualizar.sh"
    launch = f'open "{dest}"' if sys.platform == "darwin" else f'nohup "{dest}/Biblioteca" >/dev/null 2>&1 &'
    script.write_text(
        "#!/bin/sh\n"
        f"while kill -0 {pid} 2>/dev/null; do sleep 1; done\n"
        f'rm -rf "{dest}.old" && mv "{dest}" "{dest}.old" && mv "{new_app}" "{dest}" && rm -rf "{dest}.old"\n'
        + ('xattr -dr com.apple.quarantine "{}" 2>/dev/null\n'.format(dest) if sys.platform == "darwin" else "")
        + f"{launch}\n"
        f'rm -rf "{work}"\n', encoding="utf-8")
    script.chmod(0o755)
    return ["/bin/sh", str(script)]


def install() -> None:
    """Descarga e instala la última versión y reinicia la app (en otro hilo)."""
    if not can_self_update():
        raise RuntimeError("Esta instalación no se puede actualizar sola")
    if not state.available or _release is None:
        raise RuntimeError("No hay ninguna actualización pendiente")
    if state.installing:
        raise RuntimeError("La actualización ya está en marcha")
    asset = next((a for a in _release.get("assets") or [] if a.get("name") == ASSETS[sys.platform]), None)
    if asset is None:
        raise RuntimeError(f"La versión {state.latest} no trae paquete para este sistema")
    dest = install_dir()
    if sys.platform == "win32" and not (dest / "Biblioteca.exe").exists():
        raise RuntimeError(f"No reconozco la carpeta de la app: {dest}")

    def _run():
        state.installing, state.error = True, None
        try:
            state.progress = "Comprobando la firma…"
            expected = _expected_sha256(_release, asset["name"])
            work = Path(tempfile.mkdtemp(prefix="biblioteca-update-"))
            archive = work / asset["name"]
            if _download(asset, archive) != expected:
                raise RuntimeError("El paquete descargado no coincide con la huella firmada: no se instala")
            state.progress = "Preparando…"
            new_app = _extract(archive, work / "nueva")
            archive.unlink(missing_ok=True)
            command = _updater_script(work, new_app, dest)
            state.progress = "Reiniciando con la versión nueva…"
            flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) \
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen(command, close_fds=True, creationflags=flags, start_new_session=sys.platform != "win32",
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            logger.info(f"Actualizando a {state.latest}: la app se cierra para sustituir sus archivos")
            time.sleep(1.5)  # que el navegador reciba el último estado
            os._exit(0)
        except Exception as e:
            logger.exception("La actualización falló")
            state.error = f"La actualización falló: {e}"
            state.installing = False
            state.progress = ""
            shutil.rmtree(locals().get("work", ""), ignore_errors=True)

    threading.Thread(target=_run, name="actualizar", daemon=True).start()
