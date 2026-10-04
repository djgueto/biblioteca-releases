"""Cómo se está ejecutando la app: escritorio, Docker o desde el código."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time


def is_desktop() -> bool:
    """App de escritorio (packaging/desktop/launcher.py)."""
    return os.getenv("BIBLIOTECA_DESKTOP") == "1"


def is_docker() -> bool:
    return os.path.exists("/.dockerenv") or os.getenv("APP_DATA_DIR") == "/config"


def lan_addresses() -> list[str]:
    """IPs de este equipo en la red local (la principal primero)."""
    found = []
    try:
        # No envía nada: solo pregunta al sistema qué interfaz usaría.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in found and not ip.startswith("127."):
                found.append(ip)
    except OSError:
        pass
    return [ip for ip in found if not ip.startswith(("127.", "169.254."))]


def listening_on_network(host: str) -> bool:
    return host not in ("127.0.0.1", "localhost", "::1")


def restart(delay: float = 0.8):
    """Reinicia la app (para aplicar el acceso desde la red).

    Lanza una copia nueva del mismo programa y termina este proceso; la
    nueva espera a que el puerto quede libre (ver wait_port_free).
    """
    def _do():
        time.sleep(delay)  # que la respuesta HTTP llegue al navegador
        if getattr(sys, "frozen", False):
            args = [sys.executable] + sys.argv[1:]
        elif sys.argv and sys.argv[0].endswith("__main__.py"):
            args = [sys.executable, "-m", "web"] + sys.argv[1:]  # python -m web
        else:
            args = [sys.executable] + sys.argv
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        env = {**os.environ, "BIBLIOTECA_RESTARTED": "1"}
        subprocess.Popen(args, close_fds=True, creationflags=flags, env=env)
        os._exit(0)

    threading.Thread(target=_do, name="reinicio", daemon=True).start()


def wait_port_free(port: int, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", port))
                return
            except OSError:
                time.sleep(0.3)
