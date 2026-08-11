from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem

from app.ui.work.work_table_model import STATUS_BG_ROLE, STATUS_FG_ROLE


class StatusBadgeDelegate(QStyledItemDelegate):
    COLORS = {
        "С оплатой": ("#dcfce7", "#15803d"),
        "Готово": ("#dcfce7", "#15803d"),
        "Без оплаты": ("#f1f5f9", "#475569"),
        "Требует уточнения": ("#ffedd5", "#c2410c"),
        "Внутренняя": ("#ede9fe", "#7c3aed"),
        "Перерыв": ("#dbeafe", "#1d4ed8"),
    }

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        text = str(index.data() or "")
        bg = index.data(STATUS_BG_ROLE)
        fg = index.data(STATUS_FG_ROLE)
        if not isinstance(bg, QColor) or not bg.isValid():
            default_bg, default_fg = self.COLORS.get(text, ("#f1f5f9", "#475569"))
            bg = QColor(default_bg)
            fg = QColor(default_fg)
        if not isinstance(fg, QColor) or not fg.isValid():
            fg = QColor("#111827")
        painter.save()
        rect = option.rect.adjusted(8, 10, -8, -10)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(bg)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, 6, 6)
        painter.setPen(fg)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index):
        text = str(index.data() or "")
        metrics = option.fontMetrics
        width = metrics.horizontalAdvance(text) + 36
        height = max(metrics.height() + 18, 42)
        return QSize(width, height)
