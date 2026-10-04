"""Un solo ciclo a la vez aunque haya varios procesos (p. ej. dos servidores sobre la misma BD).

Si dos servidores arrancan su ciclo sobre la misma base de datos, sin esto los dos
ciclos buscarían y descargarían lo mismo a la vez. El ciclo que tiene el
bloqueo renueva su "señal de vida" (heartbeat) mientras trabaja; si el
proceso muere, la señal caduca y otro puede tomar el relevo.
"""

from __future__ import annotations

import os
import socket
import uuid

STALE_MINUTES = 30


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
        row = self.db.get_connection().execute(
            "SELECT owner FROM cycle_lock WHERE name = ? AND heartbeat >= datetime('now', ?)",
            (self.name, f"-{self.stale_minutes} minutes"),
        ).fetchone()
        return row[0] if row else None

    def acquire(self) -> bool:
        conn = self.db.get_connection()
        try:
            # BEGIN IMMEDIATE: nadie más puede escribir entre la comprobación
            # y la toma del bloqueo.
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT owner FROM cycle_lock WHERE name = ? AND heartbeat >= datetime('now', ?)",
                (self.name, f"-{self.stale_minutes} minutes"),
            ).fetchone()
            if row and row[0] != self.owner:
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
