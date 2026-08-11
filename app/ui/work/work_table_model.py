from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from app.work_log_service import WorkCategory, WorkLogEntry, WorkStatus, first_line

HEADERS = ["Время", "Клиент", "Кто обратился", "Тема / краткое описание", "Категория", "Статус"]
STATUS_BG_ROLE = Qt.ItemDataRole.UserRole + 10
STATUS_FG_ROLE = Qt.ItemDataRole.UserRole + 11


def contrast_color(hex_color: str) -> QColor:
    color = QColor(hex_color)
    if not color.isValid():
        return QColor("#111827")
    luminance = 0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()
    return QColor("#111827" if luminance > 160 else "#ffffff")


class WorkTableModel(QAbstractTableModel):
    def __init__(self) -> None:
        super().__init__()
        self.entries: list[WorkLogEntry] = []
        self.categories: dict[str, WorkCategory] = {}
        self.statuses: dict[str, WorkStatus] = {}

    def set_categories(self, categories: list[WorkCategory]) -> None:
        self.categories = {category.name: category for category in categories}

    def set_statuses(self, statuses: list[WorkStatus]) -> None:
        self.statuses = {status.name: status for status in statuses}

    def set_entries(self, entries: list[WorkLogEntry]) -> None:
        self.beginResetModel()
        self.entries = entries
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.entries)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(HEADERS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        entry = self.entries[index.row()]
        label = self.status_label(entry)
        status = self.statuses.get(label) or self.statuses.get(entry.status)
        if role == Qt.ItemDataRole.UserRole:
            return entry.id
        if role == STATUS_BG_ROLE and status:
            return QColor(status.color)
        if role == STATUS_FG_ROLE and status:
            return contrast_color(status.color)
        if role == Qt.ItemDataRole.ToolTipRole:
            values = [entry.start_time, entry.client, entry.requester, first_line(entry.message, entry.result), entry.category, label]
            return values[index.column()]
        if role == Qt.ItemDataRole.BackgroundRole and index.column() == 4:
            category = self.categories.get(entry.category)
            return QColor(category.color) if category else None
        if role == Qt.ItemDataRole.ForegroundRole and index.column() == 4:
            category = self.categories.get(entry.category)
            return contrast_color(category.color) if category else None
        if role == Qt.ItemDataRole.ForegroundRole and index.column() == 5:
            return contrast_color(status.color) if status else QColor("#15803d")
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        hours, minutes = divmod(entry.duration_minutes, 60)
        values = [
            f"{entry.start_time}–{entry.end_time} ({hours:02d}:{minutes:02d})",
            entry.client,
            entry.requester,
            first_line(entry.message, entry.result)[:100],
            entry.category,
            label,
        ]
        return values[index.column()]

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return HEADERS[section]
        return None

    @staticmethod
    def status_label(entry: WorkLogEntry) -> str:
        if entry.status == "Требует уточнения":
            return "Требует уточнения"
        if entry.category == "Перерыв":
            return "Перерыв"
        if "без оплаты" in entry.category.casefold():
            return "Без оплаты"
        if "внутрен" in entry.category.casefold() or entry.category in {"Обучение", "Обучение ТВБ"}:
            return "Внутренняя"
        return "С оплатой" if "с оплатой" in entry.category.casefold() else "Готово"
