from __future__ import annotations

from PySide6.QtWidgets import QFrame, QVBoxLayout, QLabel, QWidget


class Card(QFrame):
    def __init__(self, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(16, 14, 16, 14)
        self.layout.setSpacing(10)
        if title:
            label = QLabel(title)
            label.setObjectName("Title")
            self.layout.addWidget(label)
