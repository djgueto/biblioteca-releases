from __future__ import annotations

import json


class DecisionLogRepository:
    def __init__(self, db_manager):
        self.db = db_manager
        self._ensure_table()

    def _ensure_table(self):
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS decision_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series_id INTEGER NOT NULL,
                decision_type TEXT NOT NULL,
                message TEXT NOT NULL,
                context_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_decision_logs_series_id "
            "ON decision_logs(series_id)"
        )
        conn.commit()

    def append(self, series_id: int, decision_type: str, message: str, context: dict | None = None):
        if not series_id:
            return
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO decision_logs(series_id, decision_type, message, context_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                series_id,
                decision_type,
                message,
                json.dumps(context or {}, ensure_ascii=True),
            ),
        )
        conn.commit()

    def list_for_series(self, series_id: int, limit: int = 100) -> list[dict]:
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, series_id, decision_type, message, context_json, created_at
            FROM decision_logs
            WHERE series_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (series_id, limit),
        )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
