from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import QDate, QItemSelection, QItemSelectionModel, QTimer, QTime, Qt, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QDialog,
    QColorDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QInputDialog,
    QSplitter,
    QTableView,
    QTabWidget,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWidgets import QCompleter
from PySide6.QtCore import QStringListModel

from app.work_log_service import WorkCategory, WorkLogEntry, WorkLogService, WorkStatus, calculate_duration_minutes
from app.ui.widgets.card import Card
from app.ui.work.work_delegates import StatusBadgeDelegate
from app.ui.work.work_filter_proxy import WorkFilterProxy
from app.ui.work.work_table_model import WorkTableModel


class CategoryDialog(QDialog):
    def __init__(self, service: WorkLogService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("Справочники работ")
        self.resize(760, 520)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.category_list = QListWidget()
        self.status_list = QListWidget()
        self.tabs.addTab(self._build_category_tab(), "Категории")
        self.tabs.addTab(self._build_status_tab(), "Статусы")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
        self.reload()

    def _build_category_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(self.category_list, 1)
        row = QHBoxLayout()
        for text, slot in (("Добавить", self.add_category), ("Переименовать", self.rename_category), ("Изменить", self.edit_category), ("Цвет", self.choose_color), ("Включить/отключить", self.toggle_category), ("Удалить", self.delete_category), ("Вверх", lambda: self.move_category(-1)), ("Вниз", lambda: self.move_category(1))):
            button = QPushButton(text)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        return page

    def _build_status_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(self.status_list, 1)
        row = QHBoxLayout()
        for text, slot in (("Добавить", self.add_status), ("Переименовать", self.rename_status), ("Изменить", self.edit_status), ("Цвет", self.choose_status_color), ("Включить/отключить", self.toggle_status), ("По умолчанию", self.make_default_status), ("Удалить", self.delete_status), ("Вверх", lambda: self.move_status(-1)), ("Вниз", lambda: self.move_status(1))):
            button = QPushButton(text)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        return page

    @staticmethod
    def _contrast_text(hex_color: str) -> QColor:
        color = QColor(hex_color)
        luminance = 0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()
        return QColor("#111827" if luminance > 160 else "#ffffff")

    def reload(self) -> None:
        self.category_list.clear()
        for category in self.service.list_categories(active_only=False):
            item = QListWidgetItem(f"{'✓' if category.is_active else '○'} {category.name} — {category.summary_group}")
            item.setData(Qt.ItemDataRole.UserRole, category.name)
            item.setBackground(QColor(category.color))
            item.setForeground(self._contrast_text(category.color))
            self.category_list.addItem(item)
        self.status_list.clear()
        for status in self.service.list_statuses(active_only=False):
            default = " ★" if status.is_default else ""
            item = QListWidgetItem(f"{'✓' if status.is_active else '○'} {status.name}{default}")
            item.setData(Qt.ItemDataRole.UserRole, status.name)
            item.setBackground(QColor(status.color))
            item.setForeground(self._contrast_text(status.color))
            self.status_list.addItem(item)

    def selected_category(self) -> WorkCategory | None:
        item = self.category_list.currentItem()
        return self.service.get_category(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def selected_status(self) -> WorkStatus | None:
        item = self.status_list.currentItem()
        return self.service.get_status(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def add_category(self) -> None:
        name, ok = QInputDialog.getText(self, "Новая категория", "Название")
        if ok and name.strip():
            order = len(self.service.list_categories(active_only=False)) * 10 + 10
            self.service.save_category(WorkCategory(name=name.strip(), sort_order=order, is_active=True))
            self.reload()

    def rename_category(self) -> None:
        category = self.selected_category()
        if not category:
            return
        name, ok = QInputDialog.getText(self, "Переименовать категорию", "Название", text=category.name)
        if ok and name.strip():
            self.service.save_category(WorkCategory(name=name.strip(), color=category.color, account_type=category.account_type, default_payment_status=category.default_payment_status, requires_client=category.requires_client, requires_contact=category.requires_contact, summary_group=category.summary_group, is_active=category.is_active, sort_order=category.sort_order), original_name=category.name)
            self.reload()

    def edit_category(self) -> None:
        category = self.selected_category()
        if not category:
            return
        account_type, ok = QInputDialog.getItem(self, "Тип учета", "Тип учета", ["клиент", "internal", "без задач", "перерыв", "другое"], editable=False)
        if not ok:
            return
        summary_group, ok = QInputDialog.getItem(self, "Итоговая статистика", "Карточка итогов", ["Клиенты", "Внутренняя", "Без задач", "Перерыв", "Другое"], editable=False)
        if not ok:
            return
        payment_status, ok = QInputDialog.getItem(self, "Оплата", "Статус оплаты по умолчанию", ["paid", "free", "internal", "none"], editable=False)
        if not ok:
            return
        category.account_type = account_type
        category.summary_group = summary_group
        category.default_payment_status = payment_status
        category.requires_client = QMessageBox.question(self, "Клиент", "Требовать клиента для этой категории?") == QMessageBox.StandardButton.Yes
        category.requires_contact = QMessageBox.question(self, "Контакт", "Требовать контакт для этой категории?") == QMessageBox.StandardButton.Yes
        self.service.save_category(category)
        self.reload()

    def choose_color(self) -> None:
        category = self.selected_category()
        if not category:
            return
        color = QColorDialog.getColor(QColor(category.color), self, "Цвет категории")
        if color.isValid():
            category.color = color.name()
            self.service.save_category(category)
            self.reload()

    def toggle_category(self) -> None:
        category = self.selected_category()
        if category:
            self.service.set_category_active(category.name, not category.is_active)
            self.reload()

    def delete_category(self) -> None:
        category = self.selected_category()
        if category:
            self.service.delete_category(category.name)
            self.reload()

    def move_category(self, delta: int) -> None:
        current = self.selected_category()
        row = self.category_list.currentRow()
        target_item = self.category_list.item(row + delta)
        if not current or target_item is None:
            return
        other = self.service.get_category(target_item.data(Qt.ItemDataRole.UserRole))
        if not other:
            return
        current.sort_order, other.sort_order = other.sort_order, current.sort_order
        self.service.save_category(current)
        self.service.save_category(other)
        self.reload()
        self.category_list.setCurrentRow(max(0, row + delta))

    def add_status(self) -> None:
        name, ok = QInputDialog.getText(self, "Новый статус", "Название")
        if ok and name.strip():
            order = len(self.service.list_statuses(active_only=False)) * 10 + 10
            self.service.save_status(WorkStatus(name=name.strip(), sort_order=order, is_active=True))
            self.reload()

    def rename_status(self) -> None:
        status = self.selected_status()
        if not status:
            return
        name, ok = QInputDialog.getText(self, "Переименовать статус", "Название", text=status.name)
        if ok and name.strip():
            self.service.save_status(WorkStatus(name=name.strip(), color=status.color, is_default=status.is_default, is_active=status.is_active, sort_order=status.sort_order), original_name=status.name)
            self.reload()

    def edit_status(self) -> None:
        status = self.selected_status()
        if not status:
            return
        status.is_active = QMessageBox.question(self, "Статус", "Статус активен?") == QMessageBox.StandardButton.Yes
        status.is_default = QMessageBox.question(self, "Статус", "Сделать статусом по умолчанию?") == QMessageBox.StandardButton.Yes
        self.service.save_status(status)
        self.reload()

    def choose_status_color(self) -> None:
        status = self.selected_status()
        if not status:
            return
        color = QColorDialog.getColor(QColor(status.color), self, "Цвет статуса")
        if color.isValid():
            status.color = color.name()
            self.service.save_status(status)
            self.reload()

    def toggle_status(self) -> None:
        status = self.selected_status()
        if status:
            self.service.set_status_active(status.name, not status.is_active)
            self.reload()

    def make_default_status(self) -> None:
        status = self.selected_status()
        if status:
            status.is_default = True
            status.is_active = True
            self.service.save_status(status)
            self.reload()

    def delete_status(self) -> None:
        status = self.selected_status()
        if status:
            self.service.delete_status(status.name)
            self.reload()

    def move_status(self, delta: int) -> None:
        current = self.selected_status()
        row = self.status_list.currentRow()
        target_item = self.status_list.item(row + delta)
        if not current or target_item is None:
            return
        other = self.service.get_status(target_item.data(Qt.ItemDataRole.UserRole))
        if not other:
            return
        current.sort_order, other.sort_order = other.sort_order, current.sort_order
        self.service.save_status(current)
        self.service.save_status(other)
        self.reload()
        self.status_list.setCurrentRow(max(0, row + delta))


class WorkPage(QWidget):
    entry_saved = Signal()

    def __init__(self, service: WorkLogService, parent: QWidget | None = None, now_provider=datetime.now) -> None:
        super().__init__(parent)
        self.service = service
        self.now_provider = now_provider
        self.current_work_entry_id: int | None = None
        self.filter_buttons: dict[str, QPushButton] = {}
        self.summary_labels: dict[str, QLabel] = {}
        self.layout_mode = "wide"
        self._last_width = 1400
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(250)
        self.search_timer.timeout.connect(self.refresh_entries)
        self._build_ui()
        self._install_shortcuts()
        self.refresh_categories()
        self.refresh_suggestions()
        self.refresh_entries()
        self.new_entry(confirm=False)

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.splitterMoved.connect(self._remember_splitter_state)
        root.addWidget(self.splitter)
        self.form_card = self._build_form_card()
        self.table_card = self._build_table_card()
        self.splitter.addWidget(self.form_card)
        self.splitter.addWidget(self.table_card)
        self.splitter.setStretchFactor(0, 40)
        self.splitter.setStretchFactor(1, 60)
        self._apply_layout_mode("wide", force=True)

    def _build_form_card(self) -> QWidget:
        outer = Card()
        outer.setMinimumWidth(300)
        outer.layout.setContentsMargins(0, 0, 0, 0)
        self.form_scroll = QScrollArea()
        self.form_scroll.setWidgetResizable(True)
        self.form_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        form = QVBoxLayout(content)
        form.setContentsMargins(16, 14, 16, 8)
        form.setSpacing(9)
        self.mode_title = QLabel("Новая работа")
        self.mode_title.setObjectName("Title")
        self.mode_hint = QLabel("Быстрая фиксация работы за день")
        self.mode_hint.setObjectName("Muted")
        form.addWidget(self.mode_title)
        form.addWidget(self.mode_hint)
        self.client_combo = self._combo()
        self.contact_combo = self._combo()
        self.category_combo = QComboBox()
        for label, widget in (("Клиент", self.client_combo), ("Кто обратился", self.contact_combo)):
            form.addWidget(QLabel(label))
            form.addWidget(widget)
        category_label_row = QHBoxLayout()
        category_label_row.addWidget(QLabel("Категория"))
        category_label_row.addStretch(1)
        self.categories_button = QPushButton("⚙ Категории")
        self.categories_button.clicked.connect(self.open_categories_dialog)
        category_label_row.addWidget(self.categories_button)
        form.addLayout(category_label_row)
        form.addWidget(self.category_combo)
        self.quick_actions = QGridLayout()
        quick_actions = self.quick_actions
        self.quick_idle_button = QPushButton("Без задач 20 мин")
        self.repeat_previous_button = QPushButton("Повторить предыдущую")
        for button, category in ((self.quick_idle_button, "Без задач"),):
            menu = QMenu(button)
            for minutes in (20, 30, 60):
                action = QAction(f"{minutes} минут", button)
                action.triggered.connect(lambda _checked=False, cat=category, mins=minutes: self.quick_save_category(cat, mins))
                menu.addAction(action)
            button.setMenu(menu)
        self.quick_idle_button.clicked.connect(lambda: self.quick_save_category("Без задач", 20))
        self.repeat_previous_button.clicked.connect(self.repeat_previous_entry)
        for button in (self.quick_idle_button, self.repeat_previous_button):
            button.setMinimumWidth(150)
            quick_actions.addWidget(button, 0, 0 if button is self.quick_idle_button else 1)
        form.addLayout(quick_actions)
        form.addWidget(QLabel("Сообщение / причина обращения"))
        self.message_edit = QPlainTextEdit()
        self.message_edit.setTabChangesFocus(True)
        self.message_edit.setMinimumHeight(96)
        form.addWidget(self.message_edit)
        form.addWidget(QLabel("Что сделал"))
        self.result_edit = QPlainTextEdit()
        self.result_edit.setTabChangesFocus(True)
        self.result_edit.setMinimumHeight(150)
        self.result_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        form.addWidget(self.result_edit, 1)
        time_box = QFrame()
        time_box.setObjectName("Card")
        time_layout = QGridLayout(time_box)
        time_layout.setContentsMargins(10, 10, 10, 10)
        time_layout.setHorizontalSpacing(10)
        time_layout.addWidget(QLabel("Время"), 0, 0, 1, 3)
        self.start_time = self._time_edit()
        self.end_time = self._time_edit()
        self.fact_time = QLineEdit("00:00")
        self.fact_time.setReadOnly(True)
        self.task_edit = QLineEdit()
        self.payment_edit = QLineEdit()
        self.task_edit.hide()
        self.payment_edit.hide()
        for col, (label, widget) in enumerate((("Начало", self.start_time), ("Конец", self.end_time), ("Фактически", self.fact_time))):
            time_layout.addWidget(QLabel(label), 1, col)
            widget.setMinimumWidth(90)
            time_layout.addWidget(widget, 2, col)
        form.addWidget(time_box)
        self.validation_label = QLabel("")
        self.validation_label.setStyleSheet("color: #dc2626; font-weight: 600;")
        form.addWidget(self.validation_label)
        self.form_scroll.setWidget(content)
        outer.layout.addWidget(self.form_scroll, 1)
        actions = QFrame()
        self.actions_layout = QGridLayout(actions)
        actions_layout = self.actions_layout
        actions_layout.setContentsMargins(16, 10, 16, 14)
        actions_layout.setSpacing(8)
        self.save_button = QPushButton("Сохранить")
        self.save_button.setObjectName("Primary")
        self.save_next_button = QPushButton("Сохранить и следующая")
        self.new_button = QPushButton("Новая работа")
        self.delete_button = QPushButton("Удалить")
        self.delete_button.setObjectName("Danger")
        for button in (self.save_button, self.save_next_button, self.new_button, self.delete_button):
            button.setMinimumWidth(120)
            actions_layout.addWidget(button, 0, (self.save_button, self.save_next_button, self.new_button, self.delete_button).index(button))
        outer.layout.addWidget(actions)
        self.save_button.clicked.connect(lambda: self.save_entry(clear_after=False))
        self.save_next_button.clicked.connect(lambda: self.save_entry(clear_after=True))
        self.new_button.clicked.connect(self.new_entry)
        self.delete_button.clicked.connect(self.delete_entry)
        self.client_combo.currentTextChanged.connect(self.refresh_suggestions)
        self.start_time.timeChanged.connect(self.update_duration)
        self.end_time.timeChanged.connect(self.update_duration)
        return outer

    def _build_table_card(self) -> QWidget:
        card = Card("Работы за день")
        self.toolbar_layout = QVBoxLayout()
        self.filter_layout = QGridLayout()
        self.search_layout = QGridLayout()
        for col, name in enumerate(("Все", "Требует уточнения", "С оплатой", "Без оплаты")):
            button = QPushButton(name)
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, value=name: self.set_filter(value))
            button.setMinimumWidth(105)
            self.filter_layout.addWidget(button, 0, col)
            self.filter_buttons[name] = button
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Поиск по клиенту, человеку или тексту")
        self.search_edit.textChanged.connect(lambda _text: self.search_timer.start())
        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.date_edit.dateChanged.connect(lambda _date: self.refresh_entries())
        self.search_layout.addWidget(self.search_edit, 0, 0)
        self.search_layout.addWidget(self.date_edit, 0, 1)
        self.search_layout.setColumnStretch(0, 1)
        self.toolbar_layout.addLayout(self.filter_layout)
        self.toolbar_layout.addLayout(self.search_layout)
        card.layout.addLayout(self.toolbar_layout)
        self.model = WorkTableModel()
        self.proxy = WorkFilterProxy()
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(False)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setItemDelegateForColumn(5, StatusBadgeDelegate(self.table))
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        header.setMinimumSectionSize(80)
        self.table.verticalHeader().setDefaultSectionSize(46)
        for index, width in enumerate(getattr(self.service.settings, "work_table_columns", [120, 150, 150, 420, 150, 150])):
            self.table.setColumnWidth(index, width)
        self.table.selectionModel().selectionChanged.connect(self.open_selected_entry)
        self.table.doubleClicked.connect(lambda _index: self.open_selected_entry(self.table.selectionModel().selection(), QItemSelection()))
        card.layout.addWidget(self.table, 1)
        self.empty_label = QLabel("За выбранный день работы не заполнены\nСоздайте первую запись слева")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setObjectName("Muted")
        card.layout.addWidget(self.empty_label)
        self.summary_layout = QGridLayout()
        summary = self.summary_layout
        for name in ("Клиенты", "Внутренняя", "Без задач", "Перерыв", "Всего"):
            frame = QFrame()
            frame.setObjectName("SummaryCard")
            layout = QVBoxLayout(frame)
            title = QLabel(name)
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            value = QLabel("0ч 0м")
            value.setAlignment(Qt.AlignmentFlag.AlignCenter)
            value.setStyleSheet("font-size: 15pt; font-weight: 700;")
            layout.addWidget(title)
            layout.addWidget(value)
            summary.addWidget(frame, 0, len(self.summary_labels))
            self.summary_labels[name] = value
        card.layout.addLayout(summary)
        return card

    def _combo(self) -> QComboBox:
        combo = QComboBox()
        combo.setEditable(True)
        completer = QCompleter(combo)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        combo.setCompleter(completer)
        return combo

    def _time_edit(self) -> QTimeEdit:
        edit = QTimeEdit(QTime.currentTime())
        edit.setDisplayFormat("HH:mm")
        return edit

    def _apply_tab_order(self) -> None:
        QWidget.setTabOrder(self.client_combo, self.contact_combo)
        QWidget.setTabOrder(self.contact_combo, self.category_combo)
        QWidget.setTabOrder(self.category_combo, self.message_edit)
        QWidget.setTabOrder(self.message_edit, self.result_edit)
        QWidget.setTabOrder(self.result_edit, self.start_time)
        QWidget.setTabOrder(self.start_time, self.end_time)
        QWidget.setTabOrder(self.end_time, self.save_button)
        QWidget.setTabOrder(self.save_button, self.save_next_button)
        QWidget.setTabOrder(self.save_next_button, self.new_button)
        QWidget.setTabOrder(self.new_button, self.delete_button)

    def _default_time_range(self) -> tuple[QTime, QTime]:
        end = self.now_provider().replace(second=0, microsecond=0)
        start = end - timedelta(minutes=20)
        return QTime(start.hour, start.minute), QTime(end.hour, end.minute)


    def _install_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=lambda: self.save_entry(clear_after=False))
        QShortcut(QKeySequence("Ctrl+Enter"), self, activated=lambda: self.save_entry(clear_after=False))
        QShortcut(QKeySequence("Ctrl+Shift+Return"), self, activated=lambda: self.save_entry(clear_after=True))
        QShortcut(QKeySequence("Ctrl+N"), self, activated=lambda: self.new_entry(confirm=True))

    def refresh_categories(self) -> None:
        current = self.category_combo.currentText() or "Не определено"
        values = self.service.list_category_names(active_only=True)
        if current and current not in values:
            values.append(current)
        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        self.category_combo.addItems(values)
        self.category_combo.setCurrentText(current if current in values else "Не определено")
        self.category_combo.blockSignals(False)

    def open_categories_dialog(self) -> None:
        dialog = CategoryDialog(self.service, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh_categories()
            self.refresh_entries()

    def quick_save_category(self, category: str, minutes: int) -> None:
        end = self.now_provider().replace(second=0, microsecond=0)
        start = end - timedelta(minutes=minutes)
        entry = WorkLogEntry(
            work_date=self.work_date(),
            category=category,
            start_time=f"{start.hour:02d}:{start.minute:02d}",
            end_time=f"{end.hour:02d}:{end.minute:02d}",
            message="",
            result="",
        )
        saved = self.service.save_entry(entry)
        self.refresh_entries(select_id=saved.id)
        self.validation_label.setText(f"Сохранено: {category}, {minutes} мин")
        self.entry_saved.emit()

    def repeat_previous_entry(self) -> None:
        entries = self.service.list_entries(self.work_date())
        self.new_entry(confirm=True)
        if not entries:
            return
        last = entries[-1]
        self.client_combo.setCurrentText(last.client)
        self.contact_combo.setCurrentText(last.requester)
        self.category_combo.setCurrentText(last.category)

    def set_filter(self, value: str) -> None:
        for name, button in self.filter_buttons.items():
            button.setProperty("active", name == value)
            button.setChecked(name == value)
            button.style().unpolish(button)
            button.style().polish(button)
        self.proxy.set_filter_name(value)

    def refresh_suggestions(self) -> None:
        clients = self.service.list_clients(self.client_combo.currentText())
        contacts = self.service.list_contacts(self.client_combo.currentText(), self.contact_combo.currentText())
        self._set_combo_values(self.client_combo, clients)
        self._set_combo_values(self.contact_combo, contacts)

    def _set_combo_values(self, combo: QComboBox, values: list[str]) -> None:
        text = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(values)
        combo.setCurrentText(text)
        combo.blockSignals(False)
        if isinstance(combo.completer().model(), QStringListModel):
            combo.completer().model().setStringList(values)
        else:
            combo.completer().setModel(QStringListModel(values, combo.completer()))

    def work_date(self) -> str:
        return self.date_edit.date().toString("yyyy-MM-dd")

    def update_duration(self) -> None:
        minutes = calculate_duration_minutes(self.start_time.time().toString("HH:mm"), self.end_time.time().toString("HH:mm"))
        text = f"{minutes // 60:02d}:{minutes % 60:02d}"
        self.fact_time.setText(text)

    def _entry_from_form(self) -> WorkLogEntry:
        return WorkLogEntry(
            id=self.current_work_entry_id,
            work_date=self.work_date(),
            client=self.client_combo.currentText().strip(),
            requester=self.contact_combo.currentText().strip(),
            category=self.category_combo.currentText(),
            message=self.message_edit.toPlainText().strip(),
            result=self.result_edit.toPlainText().strip(),
            start_time=self.start_time.time().toString("HH:mm"),
            end_time=self.end_time.time().toString("HH:mm"),
            lurv_minutes=calculate_duration_minutes(self.start_time.time().toString("HH:mm"), self.end_time.time().toString("HH:mm")),
            billable_minutes=calculate_duration_minutes(self.start_time.time().toString("HH:mm"), self.end_time.time().toString("HH:mm")),
            task_reference=self.task_edit.text().strip(),
            payment_comment=self.payment_edit.text().strip(),
        )

    def validate_form(self) -> bool:
        errors: list[str] = []
        if self.category_combo.currentText() == "Не определено":
            errors.append("Выберите категорию")
        category = self.service.get_category(self.category_combo.currentText())
        requires_client = category.requires_client if category else self.category_combo.currentText().startswith("Клиент")
        if requires_client and not self.client_combo.currentText().strip():
            errors.append("Укажите клиента")
        self.validation_label.setText(" · ".join(errors))
        return not errors

    def save_entry(self, clear_after: bool = False) -> None:
        if not self.validate_form():
            return
        saved = self.service.save_entry(self._entry_from_form())
        self.current_work_entry_id = saved.id
        self.refresh_suggestions()
        self.refresh_entries(select_id=saved.id)
        self.entry_saved.emit()
        if clear_after:
            self.new_entry(confirm=False, start_time=saved.end_time)

    def new_entry(self, confirm: bool = True, start_time: str | None = None) -> None:
        if confirm and (self.message_edit.toPlainText().strip() or self.result_edit.toPlainText().strip()):
            if QMessageBox.question(self, "TimeLog OCR", "Очистить форму?") != QMessageBox.StandardButton.Yes:
                return
        self.current_work_entry_id = None
        self.mode_title.setText("Новая работа")
        self.mode_hint.setText("Быстрая фиксация работы за день")
        self.client_combo.setCurrentText("")
        self.contact_combo.setCurrentText("")
        self.category_combo.setCurrentText("Не определено")
        if start_time:
            start_qt = QTime.fromString(start_time, "HH:mm")
            end_qt = QTime(self.now_provider().hour, self.now_provider().minute)
        else:
            start_qt, end_qt = self._default_time_range()
        self.start_time.setTime(start_qt)
        self.end_time.setTime(end_qt)
        self.task_edit.clear()
        self.payment_edit.clear()
        self.message_edit.clear()
        self.result_edit.clear()
        self.validation_label.clear()
        self.delete_button.setEnabled(False)
        self.table.clearSelection()
        self.client_combo.setFocus()
        self._apply_tab_order()

    def _last_end_time(self) -> str:
        entries = self.service.list_entries(self.work_date())
        return entries[-1].end_time if entries else self.now_provider().strftime("%H:%M")

    def delete_entry(self) -> None:
        if not self.current_work_entry_id:
            return
        if QMessageBox.question(self, "TimeLog OCR", "Удалить выбранную работу?") != QMessageBox.StandardButton.Yes:
            return
        self.service.delete_entry(self.current_work_entry_id)
        self.new_entry(confirm=False)
        self.refresh_entries()

    def open_selected_entry(self, selected: QItemSelection, _deselected: QItemSelection) -> None:
        if not selected.indexes():
            return
        source_index = self.proxy.mapToSource(selected.indexes()[0])
        entry_id = self.model.data(self.model.index(source_index.row(), 0), Qt.ItemDataRole.UserRole)
        entry = self.service.get_entry(int(entry_id))
        if not entry:
            return
        self.current_work_entry_id = entry.id
        self.mode_title.setText("Редактирование работы")
        self.mode_hint.setText(f"{entry.client or 'Без клиента'}, {entry.start_time}-{entry.end_time}")
        self.client_combo.setCurrentText(entry.client)
        self.contact_combo.setCurrentText(entry.requester)
        self.category_combo.setCurrentText(entry.category)
        self.message_edit.setPlainText(entry.message)
        self.result_edit.setPlainText(entry.result)
        self.start_time.setTime(QTime.fromString(entry.start_time, "HH:mm"))
        self.end_time.setTime(QTime.fromString(entry.end_time, "HH:mm"))
        self.task_edit.setText(entry.task_reference)
        self.payment_edit.setText(entry.payment_comment)
        self.delete_button.setEnabled(True)
        self.validation_label.clear()

    def refresh_entries(self, select_id: int | None = None) -> None:
        self.proxy.set_search_text(self.search_edit.text())
        entries = self.service.list_entries(self.work_date())
        categories = self.service.list_categories(active_only=False)
        statuses = self.service.list_statuses(active_only=False)
        self.model.set_categories(categories)
        self.model.set_statuses(statuses)
        self.model.set_entries(entries)
        self._apply_status_button_colors(statuses)
        self.empty_label.setVisible(not entries)
        totals = self.service.day_totals(self.work_date())
        for name, label in self.summary_labels.items():
            minutes = totals.get(name, 0)
            label.setText(f"{minutes // 60}ч {minutes % 60}м")
        if select_id:
            for row, entry in enumerate(self.model.entries):
                if entry.id == select_id:
                    index = self.proxy.mapFromSource(self.model.index(row, 0))
                    self.table.selectionModel().select(index, QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows)
                    self.table.scrollTo(index)
                    break

    def _remember_splitter_state(self) -> None:
        if self.layout_mode == "compact":
            self.service.settings.work_splitter_vertical = self.splitter.sizes()
        else:
            self.service.settings.work_splitter_horizontal = self.splitter.sizes()
        self.service.settings.work_table_columns = [self.table.columnWidth(i) for i in range(self.table.model().columnCount())] if hasattr(self, "table") else self.service.settings.work_table_columns

    def _apply_status_button_colors(self, statuses: list[WorkStatus]) -> None:
        status_map = {status.name: status for status in statuses}
        for name, button in self.filter_buttons.items():
            status = status_map.get(name)
            if not status:
                continue
            color = QColor(status.color)
            fg = "#111827" if (0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()) > 160 else "#ffffff"
            button.setStyleSheet(f"QPushButton {{ border-color: {status.color}; }} QPushButton:checked, QPushButton[active='true'] {{ background: {status.color}; color: {fg}; }}")

    def _relayout_grid(self, grid: QGridLayout, widgets: list[QWidget], columns: int) -> None:
        for i, widget in enumerate(widgets):
            grid.removeWidget(widget)
            grid.addWidget(widget, i // columns, i % columns)

    def _apply_layout_mode(self, mode: str, force: bool = False) -> None:
        if not force and mode == self.layout_mode:
            return
        if self.layout_mode == "compact":
            self.service.settings.work_splitter_vertical = self.splitter.sizes()
        else:
            self.service.settings.work_splitter_horizontal = self.splitter.sizes()
        self.layout_mode = mode
        compact = mode == "compact"
        self.splitter.setOrientation(Qt.Orientation.Vertical if compact else Qt.Orientation.Horizontal)
        self.form_card.setMinimumWidth(0 if compact else 390)
        self.table_card.setMinimumWidth(0 if compact else 650)
        self.form_scroll.setMinimumHeight(300 if compact else 0)
        self.message_edit.setMinimumHeight(72 if compact else 96)
        self.result_edit.setMinimumHeight(96 if compact else 150)
        self._relayout_grid(self.quick_actions, [self.quick_idle_button, self.repeat_previous_button], 1 if compact else 2)
        self._relayout_grid(self.actions_layout, [self.save_button, self.save_next_button, self.new_button, self.delete_button], 2 if compact else 4)
        filter_widgets = [self.filter_buttons[name] for name in ("Все", "Требует уточнения", "С оплатой", "Без оплаты")]
        self._relayout_grid(self.filter_layout, filter_widgets, 2 if compact else 4)
        summary_frames = [self.summary_layout.itemAt(i).widget() for i in range(self.summary_layout.count()) if self.summary_layout.itemAt(i).widget()]
        self._relayout_grid(self.summary_layout, summary_frames, 3 if compact else 5)
        sizes = self.service.settings.work_splitter_vertical if compact else self.service.settings.work_splitter_horizontal
        if not sizes or len(sizes) != 2 or min(sizes) <= 0:
            sizes = [360, 360] if compact else [520, 780]
        self.splitter.setSizes(sizes)

    def _update_layout_for_width(self, width: int) -> None:
        if self.layout_mode != "wide" and width >= 1280:
            self._apply_layout_mode("wide")
        elif self.layout_mode != "compact" and width < 1200:
            self._apply_layout_mode("compact")

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._update_layout_for_width(self.width())
        QWidget.resizeEvent(self, event)
