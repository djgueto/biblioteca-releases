from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QObject, QTimer


class ChunkedTableRenderer(QObject):
    def __init__(
        self,
        table,
        *,
        row_height: int,
        chunk_size: int,
        populate_row: Callable[[int, dict], None],
        should_abort: Callable[[], bool] | None = None,
        before_render: Callable[[list[dict], int], None] | None = None,
        before_chunk: Callable[[int, list[dict], int], None] | None = None,
        after_chunk: Callable[[int, list[dict], int, int], None] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.table = table
        self.row_height = row_height
        self.chunk_size = chunk_size
        self.populate_row = populate_row
        self.should_abort = should_abort or (lambda: False)
        self.before_render = before_render
        self.before_chunk = before_chunk
        self.after_chunk = after_chunk
        self._render_token = 0

    @property
    def render_token(self) -> int:
        return self._render_token

    def render(self, items: list[dict] | None) -> int:
        items = list(items or [])
        self._render_token += 1
        token = self._render_token
        self.table.setRowCount(len(items))
        if self.before_render:
            self.before_render(items, token)
        QTimer.singleShot(0, lambda: self._render_chunk(token, items, 0))
        return token

    def _render_chunk(self, token: int, items: list[dict], start: int) -> None:
        if self.should_abort() or token != self._render_token:
            return

        if self.before_chunk:
            self.before_chunk(token, items, start)

        end = min(start + self.chunk_size, len(items))
        self.table.setUpdatesEnabled(False)
        begin_bulk_update = getattr(self.table, "beginBulkUpdate", None)
        end_bulk_update = getattr(self.table, "endBulkUpdate", None)
        if callable(begin_bulk_update):
            begin_bulk_update()
        try:
            for row in range(start, end):
                # setRowHeight es redundante: setDefaultSectionSize ya garantiza
                # la altura correcta para nuevas filas y genera 500 resizeSection()
                # innecesarios. Solo ajustar si difiere del valor esperado.
                if self.table.rowHeight(row) != self.row_height:
                    self.table.setRowHeight(row, self.row_height)
                self.populate_row(row, items[row])
        finally:
            if callable(end_bulk_update):
                end_bulk_update()
            self.table.setUpdatesEnabled(True)
            self.table.viewport().update()

        if self.after_chunk:
            self.after_chunk(token, items, start, end)

        if end < len(items):
            QTimer.singleShot(0, lambda: self._render_chunk(token, items, end))
