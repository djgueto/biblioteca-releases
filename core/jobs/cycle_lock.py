"""Un solo ciclo a la vez aunque haya varios procesos (p. ej. dos servidores sobre la misma BD).

Si dos servidores arrancan su ciclo sobre la misma base de datos, sin esto los dos
ciclos buscarían y descargarían lo mismo a la vez. El ciclo que tiene el
bloqueo renueva su "señal de vida" (heartbeat) mientras trabaja; si el
proceso muere, la señal caduca y otro puede tomar el relevo. Si el dueño es de
este mismo equipo y su proceso ya no existe (p. ej. la app se reinició en mitad
de un ciclo), el relevo es inmediato.
"""

from __future__ import annotations

import os
import socket
import sys
import uuid

STALE_MINUTES = 30


def _pid_alive(pid: int) -> bool:
    """¿Sigue vivo el proceso? Ante la duda, sí (se espera a que caduque)."""
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return kernel32.GetLastError() != 87  # ERROR_INVALID_PARAMETER: no existe
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _owner_is_dead(owner: str) -> bool:
    """Dueño de este equipo cuyo proceso ya no existe."""
    host, _, rest = owner.rpartition(":")[0].rpartition(":")
    try:
        pid = int(rest)
    except ValueError:
        return False
    return host == socket.gethostname() and pid != os.getpid() and not _pid_alive(pid)


class CycleLock:
    def __init__(self, db_manager, name: str = "scheduler", *, stale_minutes: int = STALE_MINUTES):
        self.db = db_manager
        self.name = name
        self.stale_minutes = stale_minutes
        self.owner = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        conn = self.db.get_connection()
        conn.execute('''
            CREATE TABLE IF NOT EXISTS cycle_lock (
                name TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                heartbeat TIMESTAMP NOT NULL
            )
        ''')
        conn.commit()

    def holder(self) -> str | None:
        """Quién tiene el bloqueo ahora (None si nadie o si caducó)."""
        return self._live_owner(self.db.get_connection())

    def _live_owner(self, conn) -> str | None:
        row = conn.execute(
            "SELECT owner FROM cycle_lock WHERE name = ? AND heartbeat >= datetime('now', ?)",
            (self.name, f"-{self.stale_minutes} minutes"),
        ).fetchone()
        if not row or _owner_is_dead(row[0]):
            return None
        return row[0]

    def acquire(self) -> bool:
        conn = self.db.get_connection()
        try:
            # BEGIN IMMEDIATE: nadie más puede escribir entre la comprobación
            # y la toma del bloqueo.
            conn.execute("BEGIN IMMEDIATE")
            owner = self._live_owner(conn)
            if owner and owner != self.owner:
                conn.execute("ROLLBACK")
                return False
            conn.execute(
                "INSERT INTO cycle_lock (name, owner, heartbeat) VALUES (?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(name) DO UPDATE SET owner = excluded.owner, heartbeat = CURRENT_TIMESTAMP",
                (self.name, self.owner),
            )
            conn.execute("COMMIT")
            return True
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise

    def heartbeat(self) -> None:
        conn = self.db.get_connection()
        conn.execute("UPDATE cycle_lock SET heartbeat = CURRENT_TIMESTAMP WHERE name = ? AND owner = ?",
                     (self.name, self.owner))
        conn.commit()

    def release(self) -> None:
        conn = self.db.get_connection()
        conn.execute("DELETE FROM cycle_lock WHERE name = ? AND owner = ?", (self.name, self.owner))
        conn.commit()
