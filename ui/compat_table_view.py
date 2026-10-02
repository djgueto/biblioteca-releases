from __future__ import annotations

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, pyqtSignal
from PyQt6.QtGui import QBrush
from PyQt6.QtWidgets import QTableView, QTableWidgetItem


class CompatTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._column_count = 0
        self._headers: list[str] = []
        self._items: list[list[QTableWidgetItem | None]] = []
        self._bulk_update_depth = 0
        self._pending_change_top_left: tuple[int, int] | None = None
        self._pending_change_bottom_right: tuple[int, int] | None = None

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self._items)

    def columnCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return self._column_count

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None

        item = self.item(index.row(), index.column())
        if item is None:
            return None

        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return item.text()
        if role == Qt.ItemDataRole.ForegroundRole:
            brush = item.foreground()
            return brush if isinstance(brush, QBrush) else None
        if role == Qt.ItemDataRole.BackgroundRole:
            brush = item.background()
            return brush if isinstance(brush, QBrush) else None
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(item.textAlignment())
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self._headers):
            return self._headers[section]
        return super().headerData(section, orientation, role)

    def setColumnCount(self, column_count: int):
        column_count = max(0, int(column_count))
        if column_count == self._column_count:
            return
        self.beginResetModel()
        self._column_count = column_count
        self._headers = self._headers[:column_count]
        if len(self._headers) < column_count:
            self._headers.extend([""] * (column_count - len(self._headers)))
        self._items = [
            row[:column_count] + [None] * max(0, column_count - len(row))
            for row in self._items
        ]
        self._clear_pending_changes()
        self.endResetModel()

    def setHorizontalHeaderLabels(self, labels: list[str]):
        self._headers = list(labels)
        if len(self._headers) > self._column_count:
            self.setColumnCount(len(self._headers))
            return
        if self._column_count:
            self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, self._column_count - 1)

    def setRowCount(self, row_count: int):
        row_count = max(0, int(row_count))
        current = len(self._items)
        if row_count == current:
            return

        if row_count > current:
            self.beginInsertRows(QModelIndex(), current, row_count - 1)
            for _ in range(row_count - current):
                self._items.append([None] * self._column_count)
            self.endInsertRows()
            return

        self.beginRemoveRows(QModelIndex(), row_count, current - 1)
        del self._items[row_count:]
        self.endRemoveRows()

    def setItem(self, row: int, column: int, item: QTableWidgetItem | None):
        if row < 0 or column < 0 or column >= self._column_count:
            return
        if row >= len(self._items):
            self.setRowCount(row + 1)
        if len(self._items[row]) < self._column_count:
            self._items[row].extend([None] * (self._column_count - len(self._items[row])))
        self._items[row][column] = item
        self._notify_item_changed(row, column)

    def item(self, row: int, column: int):
        if row < 0 or column < 0:
            return None
        if row >= len(self._items) or column >= self._column_count:
            return None
        row_items = self._items[row]
        if column >= len(row_items):
            return None
        return row_items[column]

    def beginBulkUpdate(self):
        self._bulk_update_depth += 1

    def endBulkUpdate(self):
        if self._bulk_update_depth <= 0:
            return
        self._bulk_update_depth -= 1
        if self._bulk_update_depth == 0:
            self._flush_pending_changes()

    def _notify_item_changed(self, row: int, column: int):
        if self._bulk_update_depth > 0:
            self._merge_pending_change(row, column)
            return

        model_index = self.index(row, column)
        self.dataChanged.emit(model_index, model_index)

    def _merge_pending_change(self, row: int, column: int):
        if self._pending_change_top_left is None:
            self._pending_change_top_left = (row, column)
            self._pending_change_bottom_right = (row, column)
            return

        top_row, left_col = self._pending_change_top_left
        bottom_row, right_col = self._pending_change_bottom_right
        self._pending_change_top_left = (min(top_row, row), min(left_col, column))
        self._pending_change_bottom_right = (max(bottom_row, row), max(right_col, column))

    def _flush_pending_changes(self):
        if self._pending_change_top_left is None or self._pending_change_bottom_right is None:
            return

        top_row, left_col = self._pending_change_top_left
        bottom_row, right_col = self._pending_change_bottom_right
        self._clear_pending_changes()
        self.dataChanged.emit(self.index(top_row, left_col), self.index(bottom_row, right_col))

    def _clear_pending_changes(self):
        self._pending_change_top_left = None
        self._pending_change_bottom_right = None


class CompatTableView(QTableView):
    itemDoubleClicked = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._compat_model = CompatTableModel(self)
        self.setModel(self._compat_model)
        self.doubleClicked.connect(self.itemDoubleClicked.emit)

    def setColumnCount(self, column_count: int):
        self._compat_model.setColumnCount(column_count)

    def setHorizontalHeaderLabels(self, labels: list[str]):
        self._compat_model.setHorizontalHeaderLabels(labels)

    def setRowCount(self, row_count: int):
        self._compat_model.setRowCount(row_count)

    def rowCount(self):
        return self._compat_model.rowCount()

    def setItem(self, row: int, column: int, item: QTableWidgetItem | None):
        self._compat_model.setItem(row, column, item)

    def item(self, row: int, column: int):
        return self._compat_model.item(row, column)

    def currentRow(self):
        return self.currentIndex().row()

    def setCurrentCell(self, row: int, column: int):
        index = self.model().index(row, column)
        if index.isValid():
            self.setCurrentIndex(index)
            self.selectRow(row)

    def itemAt(self, position):
        index = self.indexAt(position)
        return index if index.isValid() else None

    def setCellWidget(self, row: int, column: int, widget):
        index = self.model().index(row, column)
        if index.isValid():
            self.setIndexWidget(index, widget)

    def cellWidget(self, row: int, column: int):
        index = self.model().index(row, column)
        if not index.isValid():
            return None
        return self.indexWidget(index)

    def removeCellWidget(self, row: int, column: int):
        index = self.model().index(row, column)
        if not index.isValid():
            return None
        widget = self.indexWidget(index)
        if widget is not None:
            self.setIndexWidget(index, None)
        return widget

    def beginBulkUpdate(self):
        self._compat_model.beginBulkUpdate()

    def endBulkUpdate(self):
        self._compat_model.endBulkUpdate()
