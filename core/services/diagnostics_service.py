from __future__ import annotations

import json


class DiagnosticsService:
    def __init__(self, db_manager, decision_log_repository):
        self.db = db_manager
        self.decision_log_repository = decision_log_repository

    def build_item_diagnostics_text(self, series_id: int, *, limit: int = 50) -> str:
        item = self.db.get_series_by_id(series_id) or {}
        decision_logs = self.decision_log_repository.list_for_series(series_id, limit=limit)

        lines = [
            f"ID: {item.get('id', series_id)}",
            f"Nombre: {item.get('name', '')}",
            f"Categoria: {item.get('content_category', '')}",
            f"TMDB ID: {item.get('tmdb_id', '')}",
            f"TMDB Nombre: {item.get('tmdb_name', '')}",
            f"TMDB Confianza: {self._format_confidence(item.get('confidence_score'))}",
            f"URL Hispashare: {(item.get('hispashare_url') or '').strip() or 'No asignada'}",
            f"Cover URL: {(item.get('hispashare_cover_url') or '').strip() or 'No asignada'}",
            f"Busqueda deshabilitada: {'Si' if item.get('search_disabled') else 'No'}",
            f"Fallos de busqueda: {item.get('search_failures', 0)}",
            f"Episodios faltantes: {item.get('missing_count', 0)}",
            "",
            "Decision Logs:",
        ]

        if not decision_logs:
            lines.append("  - Sin trazas registradas todavia")
            return "\n".join(lines)

        for entry in decision_logs:
            lines.append("")
            lines.append(
                f"[{entry.get('created_at', '')}] {entry.get('decision_type', 'decision')}"
            )
            lines.append(f"  {entry.get('message', '')}")
            context_text = self._format_context(entry.get("context_json"))
            if context_text:
                for context_line in context_text.splitlines():
                    lines.append(f"  {context_line}")

        return "\n".join(lines)

    def _format_confidence(self, value) -> str:
        if value is None:
            return "N/A"
        try:
            return f"{float(value):.1%}"
        except Exception:
            return str(value)

    def _format_context(self, context_json) -> str:
        if not context_json:
            return ""
        try:
            context = json.loads(context_json)
        except Exception:
            return str(context_json)
        if not context:
            return ""
        return json.dumps(context, ensure_ascii=True, indent=2, sort_keys=True)
