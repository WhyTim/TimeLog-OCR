from __future__ import annotations

from PySide6.QtCore import QSortFilterProxyModel, QModelIndex, Qt


class WorkFilterProxy(QSortFilterProxyModel):
    def __init__(self) -> None:
        super().__init__()
        self.filter_name = "Все"
        self.search_text = ""
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

    def set_filter_name(self, value: str) -> None:
        self.filter_name = value
        self.invalidateFilter()

    def set_search_text(self, value: str) -> None:
        self.search_text = value.casefold().strip()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        model = self.sourceModel()
        entry = model.entries[source_row]
        if self.filter_name == "Требует уточнения" and entry.status != "Требует уточнения":
            return False
        if self.filter_name == "С оплатой" and "с оплатой" not in entry.category.casefold():
            return False
        if self.filter_name == "Без оплаты" and "без оплаты" not in entry.category.casefold():
            return False
        if self.search_text:
            haystack = " ".join([entry.client, entry.requester, entry.message, entry.result]).casefold()
            return self.search_text in haystack
        return True
