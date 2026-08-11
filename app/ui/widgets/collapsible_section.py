from __future__ import annotations

from PySide6.QtWidgets import QToolButton, QWidget, QVBoxLayout
from PySide6.QtCore import Qt


class CollapsibleSection(QWidget):
    def __init__(self, title: str, content: QWidget) -> None:
        super().__init__()
        self.toggle = QToolButton(text=title, checkable=True, checked=False)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.content = content
        self.content.setVisible(False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.toggle)
        layout.addWidget(self.content)
        self.toggle.toggled.connect(self.content.setVisible)
