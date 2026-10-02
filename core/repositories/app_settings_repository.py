from __future__ import annotations

import json


class AppSettingsRepository:
    """Ajustes editables desde la web, guardados como JSON en la BD (viajan
    con ella al NAS/Docker, a diferencia del .env)."""

    def __init__(self, db_manager):
        self.db = db_manager
        conn = self.db.get_connection()
        conn.execute('''
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()

    def get(self, key: str, default=None):
        row = self.db.get_connection().execute("SELECT value_json FROM app_settings WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key: str, value) -> None:
        conn = self.db.get_connection()
        conn.execute(
            "INSERT INTO app_settings (key, value_json, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = CURRENT_TIMESTAMP",
            (key, json.dumps(value)),
        )
        conn.commit()
