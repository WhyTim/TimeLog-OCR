from __future__ import annotations

import json
import logging
import queue
import shutil
import sys
from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path
from threading import Event

from PySide6.QtCore import QDate, QObject, QPoint, QRect, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QKeySequence, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.archive_service import archive_day
from app.capture_service import CaptureService
from app.scheduler import Scheduler, next_schedule_start
from app.secret_vault import SecretVault
from app.paths import resource_path, user_data_root, user_path
from app.ocr_service import check_tesseract as validate_tesseract
from app.settings import SETTINGS_PATH, AppSettings, ensure_default_report_template, save_settings, validate_settings
from app.startup import set_startup_enabled
from app.system_info import get_runtime_info
from app.transcription_service import DEFAULT_TRANSCRIBER_DIR, find_transcriber_entry, normalize_transcription_result, transcribe_media_file
from app.ui.widgets.collapsible_section import CollapsibleSection
from tools.local_call_transcriber_v3.transcribe import TranscriptionCancelled, ensure_model, model_download_requirements, model_is_installed
from app.update_service import check_for_updates
from app.version import APP_NAME, APP_VERSION
from app.work_log_service import WorkLogService
from app.ui.pages.work_page import WorkPage

LOGGER = logging.getLogger(__name__)
SECRET_CATEGORY_LABELS = {
    "credential": "Логин или пароль",
    "email": "Электронная почта",
    "phone": "Телефон",
    "authorization": "Токен авторизации",
    "jwt": "JWT-токен",
    "github_token": "GitHub-токен",
    "openai_api_key": "OpenAI API-ключ",
    "aws_access_key": "AWS access key",
    "private_key": "Приватный ключ",
    "connection_string_password": "Пароль подключения",
    "high_entropy_secret": "Возможный секрет",
}


def read_log_tail(log_dir: Path = Path("logs"), limit: int = 4000) -> str:
    logs = sorted(log_dir.glob("*.log"))
    if not logs:
        return "Логов пока нет."
    return logs[-1].read_text(encoding="utf-8", errors="replace")[-limit:]


def open_path(path: Path) -> None:
    path = path.resolve()
    if path.is_file():
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
    else:
        path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


class TranscriptionWorker(QObject):
    progress = Signal(int, str)
    message = Signal(str)
    finished = Signal(bool)

    def __init__(self, paths: list[Path], settings: AppSettings) -> None:
        super().__init__()
        self.paths = paths
        self.settings = settings
        self.cancel_event = Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        try:
            total = max(len(self.paths), 1)
            for index, path in enumerate(self.paths, start=1):
                if self.cancel_event.is_set():
                    self.message.emit("Операция отменена пользователем.")
                    self.finished.emit(False)
                    return
                self.progress.emit(int((index - 1) / total * 100), f"Обработка: {path.name}")
                def report(value: float, message: str) -> None:
                    overall = ((index - 1) + max(0.0, min(1.0, value))) / total
                    self.progress.emit(int(overall * 100), message)

                result = transcribe_media_file(
                    path,
                    self.settings,
                    progress_callback=report,
                    cancel_event=self.cancel_event,
                )
                normalized = normalize_transcription_result(result, source_path=path)
                self.message.emit(f"{path.name}: {result.message}\n{normalized.text}".strip())
                if not result.success:
                    self.finished.emit(False)
                    return
                self.progress.emit(int(index / total * 100), f"Готово: {path.name}")
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("Transcription queue failed")
            self.message.emit(f"Ошибка транскрибации: {exc}")
            self.finished.emit(False)
        else:
            self.finished.emit(True)


class ModelDownloadWorker(QObject):
    progress = Signal(int, str)
    finished = Signal(bool, str)

    def __init__(self, model_name: str, models_dir: str) -> None:
        super().__init__()
        self.model_name = model_name
        self.models_dir = models_dir
        self.cancel_event = Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        try:
            path = ensure_model(
                self.model_name,
                self.models_dir,
                lambda value, message: self.progress.emit(int(max(0.0, min(1.0, value)) * 100), message),
                self.cancel_event,
            )
            self.finished.emit(True, f"Модель готова: {path}")
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, TranscriptionCancelled):
                LOGGER.info("Model download cancelled")
            else:
                LOGGER.exception("Model download failed")
            self.finished.emit(False, str(exc))


SHOW_JOURNAL_PAGE = False
SHOW_WORK_PAGE = False


class SchedulerBridge(QObject):
    """Marshal scheduler callbacks from its worker thread onto the Qt thread."""

    start_requested = Signal()
    stop_requested = Signal()


class MainWindow(QMainWindow):
    PAGE_NAMES = ["Главная", "Настройки", "Транскрибация", "Логи", "Информация"]

    def __init__(self, settings: AppSettings, start_minimized: bool = False, settings_path: Path | None = None) -> None:
        super().__init__()
        self.settings = settings
        self.settings_path = settings_path
        self.events: queue.Queue[dict[str, object]] = queue.Queue()
        self.service = CaptureService(settings, self.events)
        self.scheduler_bridge = SchedulerBridge(self)
        self.scheduler_bridge.start_requested.connect(lambda: self.start_capture(automatic=True))
        self.scheduler_bridge.stop_requested.connect(lambda: self.stop_capture_and_archive(automatic=True))
        self.scheduler = Scheduler(
            settings,
            self.scheduler_bridge.start_requested.emit,
            self.scheduler_bridge.stop_requested.emit,
        )
        self.work_service = WorkLogService(settings)
        self.transcription_thread: QThread | None = None
        self.transcription_worker: TranscriptionWorker | None = None
        self.model_download_thread: QThread | None = None
        self.model_download_worker: ModelDownloadWorker | None = None
        self._exit_pending = False
        self._tray_error = ""
        self.home_values: dict[str, QLabel] = {}
        self.settings_widgets: dict[str, object] = {}
        self.app_icon = QIcon(str(resource_path("assets/app_icon.ico")))
        self.setWindowIcon(self.app_icon)
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.setMinimumSize(1100, 700)
        self._restore_window_geometry()
        self.nav_buttons: list[QPushButton] = []
        self._build_ui()
        self._install_shortcuts()
        self._build_tray()
        self.scheduler.start()
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(500)
        self.poll_timer.timeout.connect(self.poll_events)
        self.poll_timer.start()
        self._refresh_runtime_labels()
        if start_minimized or settings.start_minimized:
            QTimer.singleShot(0, self.hide)
        if settings.start_capture_on_app_launch:
            QTimer.singleShot(0, lambda: self.start_capture(automatic=True))

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)
        header = QHBoxLayout()
        title = QLabel(f"{APP_NAME}\nv{APP_VERSION}")
        title.setObjectName("Title")
        header.addWidget(title)
        header.addStretch(1)
        status_panel = QGroupBox("Состояние")
        status_layout = QHBoxLayout(status_panel)
        self.status_label = QLabel("Остановлено")
        self.duration_label = QLabel("00:00:00")
        self.next_schedule_label = QLabel("Следующий запуск: —")
        for widget in (self.status_label, self.duration_label, self.next_schedule_label):
            widget.setMinimumWidth(110)
            status_layout.addWidget(widget)
        header.addWidget(status_panel)
        root.addLayout(header)
        nav = QHBoxLayout()
        self.stack = QStackedWidget()
        self.PAGE_NAMES = list(type(self).PAGE_NAMES)
        self.pages = {
            "Главная": self._home_page(),
            "Настройки": self._settings_page(),
            "Транскрибация": self._transcription_page(),
            "Логи": self._logs_page(),
            "Информация": self._info_page(),
        }
        if SHOW_WORK_PAGE:
            self.PAGE_NAMES.insert(2, "Работы")
            self.pages["Работы"] = WorkPage(self.work_service)
        if SHOW_JOURNAL_PAGE:
            self.PAGE_NAMES.insert(2, "Журнал")
            self.pages["Журнал"] = self._journal_page()
        for index, name in enumerate(self.PAGE_NAMES):
            button = QPushButton(name)
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, i=index: self.set_page(i))
            nav.addWidget(button)
            self.nav_buttons.append(button)
            self.stack.addWidget(self.pages[name])
        nav.addStretch(1)
        root.addLayout(nav)
        root.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self.set_page(0)

    def _install_shortcuts(self) -> None:
        shortcuts = {
            "Ctrl+R": self.start_capture,
            "Ctrl+P": self.pause_capture,
            "Ctrl+S": self.stop_capture_and_archive,
            "Ctrl+L": lambda: self.set_page(self.PAGE_NAMES.index("Логи")),
            "F1": lambda: self.set_page(self.PAGE_NAMES.index("Информация")),
        }
        self.shortcut_actions: dict[str, QAction] = {}
        for sequence, callback in shortcuts.items():
            action = QAction(self)
            action.setShortcut(QKeySequence(sequence))
            action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
            action.triggered.connect(callback)
            self.addAction(action)
            self.shortcut_actions[sequence] = action

    def _home_page(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page); layout.setSpacing(12)
        controls = QGroupBox("OCR и архив")
        row = QHBoxLayout(controls)
        for text, slot in (("Запустить OCR", self.start_capture), ("Пауза", self.pause_capture), ("Остановить и архив", self.stop_capture_and_archive), ("Создать архив", lambda: self.create_archive(None)), ("Открыть папку данных", self.open_data_folder)):
            btn = QPushButton(text); btn.clicked.connect(slot); row.addWidget(btn)
        row.addStretch(1); layout.addWidget(controls)
        grid_box = QGroupBox("Текущее состояние")
        grid = QGridLayout(grid_box)
        labels = ["Статус OCR", "Время работы", "Следующий запуск", "Файл журнала", "Записей сегодня", "Последнее сообщение"]
        for r, label in enumerate(labels):
            grid.addWidget(QLabel(label), r, 0)
            value = QLabel("—"); value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.home_values[label] = value; grid.addWidget(value, r, 1)
        layout.addWidget(grid_box)
        privacy_box = QGroupBox("Защита конфиденциальных данных"); privacy_row = QHBoxLayout(privacy_box)
        self.vault_home_status = QLabel(); self.vault_home_status.setWordWrap(True); privacy_row.addWidget(self.vault_home_status, 1)
        vault_button = QPushButton("Открыть защищённые данные"); vault_button.clicked.connect(self.open_vault_window); privacy_row.addWidget(vault_button)
        layout.addWidget(privacy_box)
        QTimer.singleShot(0, self.refresh_vault_status)
        layout.addStretch(1); return page

    def _settings_page(self) -> QWidget:
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        page = QWidget(); layout = QVBoxLayout(page)
        general = QGroupBox("Основные настройки"); form = QFormLayout(general)
        self._spin_setting(form, "interval_seconds", "Интервал OCR, сек", 5, 3600)
        self._line_setting(form, "ocr_language", "Язык OCR")
        self._combo_setting(form, "theme", "Тема", ["light", "dark"])
        form.addRow("Распознавание текста", QLabel("Встроено в приложение" if Path(self.settings.tesseract_path).exists() else "Требуется восстановление приложения"))
        self._path_setting(form, "base_directory", "Папка хранения", file_mode=False)
        self._path_setting(form, "archive_directory", "Папка архивов", file_mode=False)
        self._path_setting(form, "report_template_path", "Шаблон отчета", file_mode=True)
        layout.addWidget(general)
        schedule = QGroupBox("Расписание и запуск"); sform = QFormLayout(schedule)
        self._check_setting(sform, "scheduled_capture_enabled", "Включить расписание")
        self._line_setting(sform, "scheduled_start_time", "Начало HH:MM")
        self._line_setting(sform, "scheduled_stop_time", "Окончание HH:MM")
        weekday_row = QHBoxLayout(); self.weekday_checks: list[QCheckBox] = []
        for day, label in enumerate(("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")):
            check = QCheckBox(label); check.setChecked(day in self.settings.scheduled_workdays)
            self.weekday_checks.append(check); weekday_row.addWidget(check)
        weekday_row.addStretch(1); sform.addRow("Дни недели", weekday_row)
        self._check_setting(sform, "schedule_manual_pause", "Ручная пауза расписания")
        self._check_setting(sform, "start_with_windows", "Автозапуск Windows")
        self._check_setting(sform, "start_minimized", "Запускать свернутым")
        self._check_setting(sform, "start_capture_on_app_launch", "Запускать OCR при старте")
        layout.addWidget(schedule)
        flags = QGroupBox("Скриншоты, архив и приватность"); fform = QFormLayout(flags)
        for name, label in (("save_monitor_screenshots", "Сохранять отдельный снимок каждого выбранного экрана"), ("save_all_screenshot", "Сохранять общий снимок всех экранов"), ("archive_on_stop", "Создавать архив при остановке"), ("delete_screenshots_after_archive", "Удалять скриншоты после архива")):
            self._check_setting(fform, name, label)
        self.monitor_checks: list[QCheckBox] = []
        monitor_row = QVBoxLayout()
        selected_monitors = set(self.settings.selected_monitor_indices)
        for index, screen in enumerate(QApplication.screens(), start=1):
            geometry = screen.geometry()
            primary = " · основной" if screen is QApplication.primaryScreen() else ""
            check = QCheckBox(f"Экран {index}: {geometry.width()}×{geometry.height()}, координаты {geometry.x()},{geometry.y()}{primary}")
            check.setChecked(not selected_monitors or index in selected_monitors)
            self.monitor_checks.append(check); monitor_row.addWidget(check)
        monitor_hint = QLabel("OCR выполняется для выбранных экранов. Снимки сохраняются только при включённом флажке выше; экраны с найденными секретами по умолчанию не сохраняются.")
        monitor_hint.setWordWrap(True); monitor_row.addWidget(monitor_hint)
        fform.addRow("Экраны", monitor_row)
        privacy_combo = QComboBox()
        for label, value in (
            ("Маскировать", "redact"),
            ("Не сохранять кадр и текст", "drop"),
            ("Сохранять в защищённое хранилище", "vault"),
            ("Маскировать и предупреждать", "warn"),
        ):
            privacy_combo.addItem(label, value)
        privacy_combo.setCurrentIndex(max(0, privacy_combo.findData(self.settings.privacy_handling)))
        self.settings_widgets["privacy_handling"] = privacy_combo; fform.addRow("Конфиденциальные данные", privacy_combo)
        self._check_setting(fform, "privacy_skip_screenshots_on_detection", "Не сохранять скриншоты с секретами")
        self._path_setting(fform, "secret_vault_path", "Защищённое хранилище", file_mode=True)
        fform.addRow("Защищённые данные", QLabel("Просмотр и управление доступны по кнопке на вкладке «Главная»"))
        layout.addWidget(flags)
        trans = QGroupBox("Транскрибация"); tform = QFormLayout(trans)
        self._check_setting(tform, "transcription_enabled", "Включить транскрибацию")
        self._spin_setting(tform, "transcriber_timeout_seconds", "Таймаут, сек", 30, 86400)
        tform.addRow("Модель", QLabel("Выбирается на вкладке «Транскрибация»"))
        layout.addWidget(trans)
        developer_content = QWidget(); developer_form = QFormLayout(developer_content)
        developer_note = QLabel("Обычному пользователю менять эти пути не требуется. Приложение использует встроенный компонент и локальную папку моделей автоматически.")
        developer_note.setWordWrap(True); developer_form.addRow(developer_note)
        self._path_setting(developer_form, "transcriber_script_path", "Компонент транскрибации", file_mode=False)
        self._path_setting(developer_form, "transcriber_models_dir", "Хранилище моделей", file_mode=False)
        restore_transcriber = QPushButton("Восстановить стандартные настройки транскрибации")
        restore_transcriber.clicked.connect(self.restore_transcriber_defaults)
        developer_form.addRow(restore_transcriber)
        layout.addWidget(CollapsibleSection("Дополнительные настройки для разработчиков", developer_content))
        checks = QHBoxLayout()
        for text, slot in (("Проверить распознавание текста", self.check_tesseract_action), ("Открыть папку данных", self.open_data_folder), ("Открыть папку архивов", lambda: open_path(Path(self.settings.archive_directory))), ("Открыть папку моделей", lambda: open_path(Path(self.settings.transcriber_models_dir))), ("Сбросить геометрию", self.reset_ui_geometry)):
            btn = QPushButton(text); btn.clicked.connect(slot); checks.addWidget(btn)
        checks.addStretch(1); layout.addLayout(checks)
        save = QPushButton("Сохранить настройки"); save.setObjectName("Primary"); save.clicked.connect(self.save_settings_from_page)
        layout.addWidget(save); layout.addStretch(1); scroll.setWidget(page); return scroll

    def _line_setting(self, form: QFormLayout, name: str, label: str) -> None:
        edit = QLineEdit(str(getattr(self.settings, name))); self.settings_widgets[name] = edit; form.addRow(label, edit)

    def _path_setting(self, form: QFormLayout, name: str, label: str, file_mode: bool) -> None:
        edit = QLineEdit(str(getattr(self.settings, name)))
        self.settings_widgets[name] = edit
        row = QHBoxLayout(); row.addWidget(edit, 1)
        browse = QPushButton("…"); browse.setFixedWidth(34); browse.clicked.connect(lambda _=False, n=name, f=file_mode: self.choose_path(n, f))
        row.addWidget(browse)
        form.addRow(label, row)

    def _spin_setting(self, form: QFormLayout, name: str, label: str, minimum: int, maximum: int) -> None:
        spin = QSpinBox(); spin.setRange(minimum, maximum); spin.setValue(int(getattr(self.settings, name))); self.settings_widgets[name] = spin; form.addRow(label, spin)

    def _check_setting(self, form: QFormLayout, name: str, label: str) -> None:
        check = QCheckBox(label); check.setChecked(bool(getattr(self.settings, name))); self.settings_widgets[name] = check; form.addRow("", check)

    def _combo_setting(self, form: QFormLayout, name: str, label: str, values: list[str]) -> None:
        combo = QComboBox()
        combo.addItems(values)
        combo.setCurrentText(str(getattr(self.settings, name)))
        self.settings_widgets[name] = combo
        form.addRow(label, combo)

    def _journal_page(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page)
        controls = QHBoxLayout(); self.journal_date = QDateEdit(QDate.currentDate()); self.journal_date.setCalendarPopup(True)
        self.journal_search = QLineEdit(); self.journal_search.setPlaceholderText("Поиск по приложению, окну или тексту")
        refresh = QPushButton("Обновить"); open_file = QPushButton("Открыть файл"); open_folder = QPushButton("Открыть папку")
        for w in (QLabel("Дата"), self.journal_date, self.journal_search, refresh, open_file, open_folder): controls.addWidget(w)
        layout.addLayout(controls)
        self.journal_table = QTableWidget(0, 4); self.journal_table.setHorizontalHeaderLabels(["Время", "Приложение", "Окно", "Текст"])
        self.journal_table.horizontalHeader().setStretchLastSection(True); layout.addWidget(self.journal_table, 1)
        self.journal_hint = QLabel(""); layout.addWidget(self.journal_hint)
        refresh.clicked.connect(self.refresh_journal); self.journal_search.textChanged.connect(lambda _t: self.refresh_journal())
        self.journal_date.dateChanged.connect(lambda _d: self.refresh_journal()); open_file.clicked.connect(self.open_journal_file); open_folder.clicked.connect(self.open_data_folder)
        QTimer.singleShot(0, self.refresh_journal); return page

    def _transcription_page(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page); controls = QHBoxLayout()
        self.transcription_model = QComboBox()
        for name, label in (("tiny", "Tiny — максимально быстро"), ("base", "Base — быстро для коротких записей"), ("small", "Small — рекомендуется"), ("medium", "Medium — высокая точность"), ("large-v3", "Large v3 — максимум точности")):
            self.transcription_model.addItem(label, name)
        self.transcription_model.setCurrentIndex(max(0, self.transcription_model.findData(self.settings.transcriber_model_name)))
        add = QPushButton("Добавить аудио/видео"); remove = QPushButton("Удалить из списка")
        self.transcription_start_button = QPushButton("Запустить транскрибацию")
        self.model_download_button = QPushButton("Скачать модель")
        self.model_delete_button = QPushButton("Удалить модель")
        for w in (QLabel("Качество распознавания"), self.transcription_model, add, remove, self.transcription_start_button, self.model_download_button, self.model_delete_button): controls.addWidget(w)
        controls.addStretch(1); layout.addLayout(controls)
        self.model_description = QLabel(); self.model_description.setWordWrap(True); layout.addWidget(self.model_description)
        self.transcription_files = QListWidget(); layout.addWidget(self.transcription_files, 1)
        self.transcription_status = QLabel("Готово к работе")
        self.transcription_status.setWordWrap(True); layout.addWidget(self.transcription_status)
        self.transcription_progress = QProgressBar(); layout.addWidget(self.transcription_progress)
        self.transcription_text = QTextEdit(readOnly=True); layout.addWidget(self.transcription_text, 2)
        bottom = QHBoxLayout(); copy = QPushButton("Копировать текст"); save = QPushButton("Сохранить результат")
        self.transcription_cancel_button = QPushButton("Отменить"); self.transcription_cancel_button.setEnabled(False)
        retry = QPushButton("Повторить"); open_models = QPushButton("Открыть папку моделей")
        for button in (copy, save, self.transcription_cancel_button, retry, open_models): bottom.addWidget(button)
        bottom.addStretch(1); layout.addLayout(bottom)
        add.clicked.connect(self.transcribe_media_action); remove.clicked.connect(self.remove_selected_transcription_file)
        self.transcription_start_button.clicked.connect(self.start_transcription_queue)
        self.model_download_button.clicked.connect(self.download_transcriber_model_action)
        self.model_delete_button.clicked.connect(self.delete_selected_model)
        self.transcription_model.currentIndexChanged.connect(self._model_selection_changed)
        self.transcription_cancel_button.clicked.connect(self.cancel_transcription_operation)
        retry.clicked.connect(self.download_transcriber_model_action)
        open_models.clicked.connect(lambda: open_path(Path(self.settings.transcriber_models_dir)))
        copy.clicked.connect(self.copy_transcription_result); save.clicked.connect(self.save_transcription_result)
        self._refresh_model_description()
        return page

    def _vault_page(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page)
        explanation = QLabel("Здесь отображаются конфиденциальные данные, обнаруженные в OCR. Полные значения сохраняются только в выбранном режиме и шифруются локально средствами Windows DPAPI.")
        explanation.setWordWrap(True); layout.addWidget(explanation)
        self.vault_mode_status = QLabel(); self.vault_mode_status.setWordWrap(True); layout.addWidget(self.vault_mode_status)
        self.vault_enable_button = QPushButton("Включить защищённое хранилище")
        self.vault_enable_button.clicked.connect(self.enable_secret_vault)
        layout.addWidget(self.vault_enable_button)
        self.vault_table = QTableWidget(0, 6)
        self.vault_table.setHorizontalHeaderLabels(["Тип", "Первое обнаружение", "Последнее обнаружение", "Маска", "Повторы", "Безопасный контекст"])
        header = self.vault_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.vault_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.vault_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.vault_table, 1)
        self.vault_revealed_value = QLabel("Значение скрыто")
        self.vault_revealed_value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.vault_revealed_value.setWordWrap(True); layout.addWidget(self.vault_revealed_value)
        actions = QGridLayout()
        action_items = (("Обновить", self.refresh_vault_table), ("Показать выбранное значение", self.reveal_selected_vault), ("Скрыть сейчас", self.hide_revealed_vault), ("Скопировать значение", self.copy_selected_vault), ("Удалить выбранную запись", self.delete_selected_vault), ("Очистить хранилище", self.clear_secret_vault), ("Открыть расположение хранилища", self.open_vault_location))
        for index, (text, slot) in enumerate(action_items):
            button = QPushButton(text); button.clicked.connect(slot); actions.addWidget(button, index // 4, index % 4)
        layout.addLayout(actions)
        self.vault_reveal_timer = QTimer(self); self.vault_reveal_timer.setSingleShot(True); self.vault_reveal_timer.timeout.connect(self.hide_revealed_vault)
        QTimer.singleShot(0, self.refresh_vault_table)
        return page

    def open_vault_window(self) -> None:
        dialog = getattr(self, "vault_dialog", None)
        if dialog is None:
            dialog = QDialog(self)
            dialog.setWindowTitle("Защищённые данные")
            dialog.resize(1200, 700)
            dialog.setLayout(QVBoxLayout())
            dialog.layout().addWidget(self._vault_page())
            dialog.finished.connect(self.hide_revealed_vault)
            self.vault_dialog = dialog
        self.refresh_vault_table()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _logs_page(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page); controls = QHBoxLayout()
        self.log_level = QComboBox(); self.log_level.addItems(["ALL", "INFO", "WARNING", "ERROR"])
        refresh = QPushButton("Обновить"); clear = QPushButton("Очистить отображение"); open_log = QPushButton("Открыть файл лога"); copy = QPushButton("Копировать")
        for w in (QLabel("Уровень"), self.log_level, refresh, clear, open_log, copy): controls.addWidget(w)
        controls.addStretch(1); layout.addLayout(controls)
        self.logs_text = QTextEdit(readOnly=True); layout.addWidget(self.logs_text, 1)
        refresh.clicked.connect(self.refresh_logs); clear.clicked.connect(self.logs_text.clear); open_log.clicked.connect(self.open_log_file); copy.clicked.connect(lambda: QApplication.clipboard().setText(self.logs_text.toPlainText()))
        QTimer.singleShot(0, self.refresh_logs); return page

    def _info_page(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page)
        self.info_text = QTextEdit(readOnly=True); layout.addWidget(self.info_text, 1)
        updates = QPushButton("Проверить обновления"); updates.clicked.connect(self.check_for_updates_action); layout.addWidget(updates)
        btn = QPushButton("Открыть папку данных"); btn.clicked.connect(lambda: open_path(user_data_root())); layout.addWidget(btn)
        QTimer.singleShot(0, self.refresh_info); return page

    def set_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for i, button in enumerate(self.nav_buttons):
            button.setChecked(i == index); button.setProperty("active", i == index); button.style().unpolish(button); button.style().polish(button)
        if self.PAGE_NAMES[index] == "Главная":
            self.refresh_vault_status()

    def _today_dir(self) -> Path:
        return Path(self.settings.base_directory) / datetime.now().strftime("%Y-%m-%d")

    def _today_jsonl(self) -> Path:
        day = datetime.now().strftime("%Y-%m-%d")
        return self._today_dir() / f"ocr_log_{day}.jsonl"

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self.app_icon, self); menu = QMenu()
        open_action = QAction("Открыть TimeLog OCR", self); open_action.triggered.connect(self.restore_from_tray); menu.addAction(open_action)
        menu.addSeparator()
        self.tray_start_action = QAction("Запустить OCR", self); self.tray_start_action.triggered.connect(self.start_capture); menu.addAction(self.tray_start_action)
        self.tray_pause_action = QAction("Пауза", self); self.tray_pause_action.triggered.connect(self.pause_capture); menu.addAction(self.tray_pause_action)
        self.tray_stop_action = QAction("Остановить OCR", self); self.tray_stop_action.triggered.connect(self.stop_capture_and_archive); menu.addAction(self.tray_stop_action)
        menu.addSeparator()
        data_action = QAction("Открыть папку данных", self); data_action.triggered.connect(self.open_data_folder); menu.addAction(data_action)
        menu.addSeparator()
        exit_action = QAction("Выход", self); exit_action.triggered.connect(self.exit_app); menu.addAction(exit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.restore_from_tray() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        self._update_tray_state()
        if QSystemTrayIcon.isSystemTrayAvailable(): self.tray.show()

    def _state_tray_icon(self, color: str) -> QIcon:
        pixmap = self.app_icon.pixmap(QSize(64, 64))
        if pixmap.isNull():
            pixmap = QPixmap(64, 64); pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor("white")); painter.setBrush(QColor(color))
        painter.drawEllipse(42, 42, 20, 20)
        painter.end()
        return QIcon(pixmap)

    def _update_tray_state(self) -> None:
        if not hasattr(self, "tray"):
            return
        state = self.service.state
        if self._tray_error or state.status == "Ошибка":
            color, label = "#dc2626", f"ошибка: {(self._tray_error or state.last_error or 'ошибка OCR')[:100]}"
        elif self.service.is_paused or self.settings.schedule_manual_pause:
            color, label = "#f59e0b", "ручная пауза"
        elif self.service.is_running:
            color, label = "#16a34a", "работает"
        else:
            color, label = "#6b7280", "остановлен"
        self.tray.setIcon(self._state_tray_icon(color))
        self.tray.setToolTip(f"TimeLog OCR — {label}")
        if hasattr(self, "tray_pause_action"):
            self.tray_pause_action.setText("Возобновить" if self.service.is_paused else "Пауза")
            self.tray_pause_action.setEnabled(self.service.is_running)
            self.tray_start_action.setEnabled(not self.service.is_running)
            self.tray_stop_action.setEnabled(self.service.is_running)

    def _set_manual_schedule_pause(self, paused: bool) -> None:
        self.settings.schedule_manual_pause = paused
        widget = self.settings_widgets.get("schedule_manual_pause")
        if isinstance(widget, QCheckBox):
            widget.setChecked(paused)
        try:
            self._persist_settings(self.settings)
        except Exception:
            LOGGER.exception("Failed to persist manual schedule pause")

    def start_capture(self, _checked: bool = False, *, automatic: bool = False) -> None:
        if automatic and self.settings.schedule_manual_pause:
            self.status_label.setText("Ручная пауза расписания")
            self._refresh_runtime_labels()
            return
        warnings = validate_tesseract(self.settings.tesseract_path, self.settings.ocr_language)
        if warnings:
            self._tray_error = warnings[0]
            self.status_label.setText("Tesseract не найден")
            self._update_tray_state()
            QMessageBox.warning(self, APP_NAME, "Компонент распознавания текста отсутствует или повреждён. Переустановите приложение.")
            return
        if not automatic:
            self._set_manual_schedule_pause(False)
        self._tray_error = ""
        self.service.start(); self.status_label.setText("Работает"); self._refresh_runtime_labels()

    def pause_capture(self) -> None:
        if self.service.is_paused:
            self.service.resume()
            self._set_manual_schedule_pause(False)
        else:
            self.service.pause()
            self._set_manual_schedule_pause(True)
        self._refresh_runtime_labels()

    def stop_capture_and_archive(self, _checked: bool = False, *, automatic: bool = False) -> None:
        if not automatic:
            self._set_manual_schedule_pause(True)
        day_dirs = self.service.stop(); self.status_label.setText("Остановлено"); self._refresh_runtime_labels()
        if self.settings.archive_on_stop: self.create_archive(day_dirs[-1] if day_dirs else None)

    def create_archive(self, day_dir: Path | None = None) -> None:
        target = day_dir or self._today_dir()
        try: self.work_service.export_manual_work_log(target.name)
        except Exception: LOGGER.exception("Manual work log export failed before archive")
        archive_dir = Path(self.settings.archive_directory) if self.settings.archive_directory else None
        result = archive_day(target, Path(self.settings.report_template_path), self.settings.delete_screenshots_after_archive, archive_directory=archive_dir)
        if result.created: QMessageBox.information(self, APP_NAME, f"Архив создан: {result.path}")
        else: QMessageBox.warning(self, APP_NAME, result.warning)

    def save_settings_from_page(self) -> None:
        try:
            candidate = AppSettings(**asdict(self.settings))
            for name, widget in self.settings_widgets.items():
                if isinstance(widget, QLineEdit): setattr(candidate, name, widget.text())
                elif isinstance(widget, QSpinBox): setattr(candidate, name, widget.value())
                elif isinstance(widget, QCheckBox): setattr(candidate, name, widget.isChecked())
                elif isinstance(widget, QComboBox): setattr(candidate, name, widget.currentData() if name == "privacy_handling" else widget.currentText())
            candidate.scheduled_workdays = [day for day, check in enumerate(self.weekday_checks) if check.isChecked()]
            candidate.selected_monitor_indices = [index for index, check in enumerate(self.monitor_checks, start=1) if check.isChecked()]
            candidate.transcriber_model_name = self._selected_model_name()
            warnings = validate_settings(candidate, strict_paths=False)
            ensure_default_report_template(candidate)
            startup_changed = candidate.start_with_windows != self.settings.start_with_windows
            if startup_changed:
                ok, error = set_startup_enabled(candidate.start_with_windows)
                if not ok:
                    raise RuntimeError(f"Не удалось изменить автозапуск Windows: {error}")
            try:
                self._persist_settings(candidate)
            except Exception:
                if startup_changed:
                    set_startup_enabled(self.settings.start_with_windows)
                raise
            for setting_field in fields(AppSettings):
                setattr(self.settings, setting_field.name, getattr(candidate, setting_field.name))
            style_path = resource_path("app/ui/styles") / ("dark.qss" if self.settings.theme == "dark" else "light.qss")
            app = QApplication.instance()
            if app is not None:
                app.setStyleSheet(style_path.read_text(encoding="utf-8") if style_path.exists() else "")
            self.refresh_vault_status()
            QMessageBox.information(self, APP_NAME, "Настройки сохранены" + (("\n" + "\n".join(warnings)) if warnings else ""))
        except Exception as exc:
            LOGGER.exception("Settings update failed")
            QMessageBox.warning(self, APP_NAME, str(exc))

    def refresh_journal(self) -> None:
        date_text = self.journal_date.date().toString("yyyy-MM-dd"); path = Path(self.settings.base_directory) / date_text / f"ocr_log_{date_text}.jsonl"
        self.journal_table.setRowCount(0)
        if not path.exists(): self.journal_hint.setText(f"Файл не найден: {path}"); return
        query = self.journal_search.text().casefold().strip(); rows = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try: rec = json.loads(line)
            except json.JSONDecodeError: continue
            hay = json.dumps(rec, ensure_ascii=False).casefold()
            if query and query not in hay: continue
            rows.append(rec)
        self.journal_table.setRowCount(len(rows))
        for r, rec in enumerate(rows):
            vals = [str(rec.get("timestamp") or rec.get("created_at") or ""), str(rec.get("application") or rec.get("app") or ""), str(rec.get("window_title") or rec.get("title") or ""), str(rec.get("text") or rec.get("ocr_text") or "")]
            for c, val in enumerate(vals): self.journal_table.setItem(r, c, QTableWidgetItem(val))
        self.journal_hint.setText(f"Загружено записей: {len(rows)} из {path}")

    def open_journal_file(self) -> None:
        d = self.journal_date.date().toString("yyyy-MM-dd"); open_path(Path(self.settings.base_directory) / d / f"ocr_log_{d}.jsonl")

    def open_data_folder(self) -> None: open_path(Path(self.settings.base_directory))

    def _vault(self) -> SecretVault:
        path_widget = self.settings_widgets.get("secret_vault_path")
        path = path_widget.text() if isinstance(path_widget, QLineEdit) else self.settings.secret_vault_path
        return SecretVault(path)

    def refresh_vault_table(self) -> None:
        if not hasattr(self, "vault_table"):
            return
        try:
            records = self._vault().list_masked()
        except Exception as exc:  # noqa: BLE001
            self.vault_table.setRowCount(0)
            QMessageBox.warning(self, APP_NAME, f"Не удалось открыть защищённое хранилище: {exc}")
            return
        self.vault_table.setRowCount(len(records))
        for row, record in enumerate(records):
            category = str(record.get("category", ""))
            values = [SECRET_CATEGORY_LABELS.get(category, category), record.get("first_seen", record.get("timestamp", "")), record.get("last_seen", record.get("timestamp", "")), record.get("masked", ""), record.get("count", "1"), record.get("context", "")]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, record.get("id", ""))
                self.vault_table.setItem(row, column, item)
        self.refresh_vault_status(records)

    def refresh_vault_status(self, records: list[dict[str, str]] | None = None) -> None:
        enabled = self.settings.privacy_handling == "vault"
        if records is None:
            try:
                records = self._vault().list_masked() if self._vault().path.exists() else []
            except Exception:
                records = []
        path = self._vault().path
        if enabled:
            summary = f"Защищённое хранилище включено · записей: {len(records)} · {path}"
            detail = summary if records else summary + "\nНовые обнаруженные данные появятся здесь после OCR."
        else:
            summary = "Защищённое хранилище выключено. OCR маскирует секреты, но не сохраняет полные значения."
            detail = (
                "Полные значения не сохраняются в текущем режиме. Чтобы сохранять обнаруженные данные "
                "в защищённом хранилище Windows, включите соответствующий режим в настройках.\n"
                f"Расположение зашифрованного файла: {path}"
            )
        if hasattr(self, "vault_home_status"):
            self.vault_home_status.setText(summary)
        if hasattr(self, "vault_mode_status"):
            self.vault_mode_status.setText(detail)
        if hasattr(self, "vault_enable_button"):
            self.vault_enable_button.setVisible(not enabled)

    def enable_secret_vault(self) -> None:
        warning = (
            "После включения приложение будет сохранять полные обнаруженные значения в локальном "
            "зашифрованном хранилище Windows DPAPI. Хранилище доступно только текущему пользователю Windows.\n\n"
            "Включить защищённое хранилище?"
        )
        if QMessageBox.question(self, APP_NAME, warning, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.settings.privacy_handling = "vault"
        combo = self.settings_widgets.get("privacy_handling")
        if isinstance(combo, QComboBox):
            combo.setCurrentIndex(combo.findData("vault"))
        try:
            self._persist_settings(self.settings)
            self.refresh_vault_table()
        except Exception:
            LOGGER.exception("Failed to enable encrypted vault")
            QMessageBox.warning(self, APP_NAME, "Не удалось включить защищённое хранилище. Проверьте права доступа к папке данных.")

    def _selected_vault_id(self) -> str | None:
        if not hasattr(self, "vault_table") or self.vault_table.currentRow() < 0:
            QMessageBox.information(self, APP_NAME, "Выберите запись в таблице.")
            return None
        item = self.vault_table.item(self.vault_table.currentRow(), 0)
        return str(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def reveal_selected_vault(self) -> None:
        record_id = self._selected_vault_id()
        if not record_id:
            return
        if self.service.is_running:
            QMessageBox.warning(self, APP_NAME, "Сначала остановите OCR, чтобы раскрытое значение не попало в новый кадр.")
            return
        if QMessageBox.question(self, APP_NAME, "Будет показано конфиденциальное значение. Убедитесь, что экран никто не видит и запись экрана отключена.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            value = self._vault().get_value(record_id)
            if value is None:
                QMessageBox.information(self, APP_NAME, "Это старая запись без сохранённого полного значения.")
                return
            self.vault_revealed_value.setText(value)
            self.vault_reveal_timer.start(20_000)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, APP_NAME, f"Не удалось расшифровать выбранную запись: {exc}")

    def hide_revealed_vault(self) -> None:
        if hasattr(self, "vault_revealed_value"):
            self.vault_revealed_value.setText("Значение скрыто")
        if hasattr(self, "vault_reveal_timer"):
            self.vault_reveal_timer.stop()

    def copy_selected_vault(self) -> None:
        record_id = self._selected_vault_id()
        if not record_id:
            return
        if QMessageBox.question(self, APP_NAME, "Скопировать конфиденциальное значение в буфер обмена на 30 секунд?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            value = self._vault().get_value(record_id)
            if value is None:
                QMessageBox.information(self, APP_NAME, "Это старая запись без сохранённого полного значения.")
                return
            clipboard = QApplication.clipboard(); clipboard.setText(value)
            QTimer.singleShot(30_000, lambda expected=value: clipboard.clear() if clipboard.text() == expected else None)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, APP_NAME, f"Не удалось скопировать выбранную запись: {exc}")

    def delete_selected_vault(self) -> None:
        record_id = self._selected_vault_id()
        if record_id and QMessageBox.question(self, APP_NAME, "Удалить выбранную запись без возможности восстановления?") == QMessageBox.StandardButton.Yes:
            self._vault().delete(record_id); self.hide_revealed_vault(); self.refresh_vault_table()

    def open_vault_location(self) -> None:
        open_path(self._vault().path.parent)

    def clear_secret_vault(self) -> None:
        if QMessageBox.question(self, APP_NAME, "Очистить всё защищённое хранилище без возможности восстановления?") == QMessageBox.StandardButton.Yes:
            try:
                self._vault().clear()
                self.hide_revealed_vault()
                self.refresh_vault_table()
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, APP_NAME, f"Не удалось очистить хранилище: {exc}")

    def choose_path(self, name: str, file_mode: bool) -> None:
        edit = self.settings_widgets.get(name)
        if not isinstance(edit, QLineEdit):
            return
        current = str(Path(edit.text()).expanduser()) if edit.text() else str(user_data_root())
        if file_mode:
            selected, _ = QFileDialog.getOpenFileName(self, "Выберите файл", current, "All files (*.*)")
        else:
            selected = QFileDialog.getExistingDirectory(self, "Выберите папку", current)
        if selected:
            edit.setText(str(Path(selected).expanduser().resolve()))

    def restore_transcriber_defaults(self) -> None:
        script = self.settings_widgets.get("transcriber_script_path")
        models = self.settings_widgets.get("transcriber_models_dir")
        if isinstance(script, QLineEdit):
            script.setText(str(DEFAULT_TRANSCRIBER_DIR).replace("\\", "/"))
        if isinstance(models, QLineEdit):
            models.setText(str(user_path("models")))
        probe = AppSettings(**asdict(self.settings))
        probe.transcriber_script_path = str(DEFAULT_TRANSCRIBER_DIR).replace("\\", "/")
        QMessageBox.information(self, APP_NAME, "Встроенный компонент транскрибации найден. Нажмите «Сохранить настройки»." if find_transcriber_entry(probe) else "Компонент транскрибации отсутствует или повреждён. Переустановите приложение.")

    def _selected_model_name(self) -> str:
        value = self.transcription_model.currentData() if hasattr(self, "transcription_model") else None
        return str(value or self.settings.transcriber_model_name)

    def _model_selection_changed(self, _index: int = -1) -> None:
        self.settings.transcriber_model_name = self._selected_model_name()
        self._refresh_model_description()
        try:
            self._persist_settings(self.settings)
        except Exception:
            LOGGER.exception("Unable to persist transcription model selection")

    def _refresh_model_description(self) -> None:
        if not hasattr(self, "model_description"):
            return
        name = self._selected_model_name()
        descriptions = {
            "tiny": "Самая быстрая, минимальная точность",
            "base": "Быстрая, подходит для коротких записей",
            "small": "Баланс скорости и точности · рекомендуется",
            "medium": "Высокая точность, работает заметно дольше",
            "large-v3": "Максимальная точность, самые высокие требования",
        }
        size, memory = model_download_requirements(name)
        installed = model_is_installed(name, self.settings.transcriber_models_dir)
        status = "установлена" if installed else "не установлена"
        self.model_description.setText(f"{descriptions[name]}. Размер загрузки около {size / 1024 ** 2:.0f} МБ, память {memory}. Сейчас: {status}.")
        self.model_download_button.setText("Проверить модель" if installed else "Скачать модель")
        self.model_delete_button.setEnabled(installed and not self._transcription_operation_running())

    def delete_selected_model(self) -> None:
        name = self._selected_model_name()
        if QMessageBox.question(self, APP_NAME, f"Удалить локальные файлы модели {name}? При следующем использовании её потребуется скачать заново.") != QMessageBox.StandardButton.Yes:
            return
        root = Path(self.settings.transcriber_models_dir).resolve()
        targets = [root / name, root / f"models--Systran--faster-whisper-{name}"]
        for target in targets:
            if target.exists() and target.resolve().parent == root:
                shutil.rmtree(target)
        self._refresh_model_description()

    def check_tesseract_action(self) -> None:
        warnings = validate_tesseract(self.settings_widgets["tesseract_path"].text() if isinstance(self.settings_widgets.get("tesseract_path"), QLineEdit) else self.settings.tesseract_path, self.settings.ocr_language)
        QMessageBox.information(self, APP_NAME, "Tesseract доступен." if not warnings else "\n".join(warnings))

    def transcribe_media_action(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Добавить аудио/видео", "", "Media (*.mp3 *.mp4 *.wav *.m4a *.aac *.flac *.ogg *.webm *.mov *.mkv *.avi);;All files (*.*)")
        for path in paths: self.transcription_files.addItem(QListWidgetItem(path))

    def remove_selected_transcription_file(self) -> None:
        for item in self.transcription_files.selectedItems(): self.transcription_files.takeItem(self.transcription_files.row(item))

    def start_transcription_queue(self) -> None:
        paths = [Path(self.transcription_files.item(i).text()) for i in range(self.transcription_files.count())]
        if not paths: QMessageBox.information(self, APP_NAME, "Добавьте аудио или видеофайлы."); return
        if self._transcription_operation_running(): QMessageBox.information(self, APP_NAME, "Загрузка модели или транскрибация уже выполняется."); return
        self.settings.transcriber_model_name = self._selected_model_name()
        if not model_is_installed(self.settings.transcriber_model_name, self.settings.transcriber_models_dir):
            size, memory = model_download_requirements(self.settings.transcriber_model_name)
            answer = QMessageBox.question(
                self,
                APP_NAME,
                f"Модель {self.settings.transcriber_model_name} ещё не установлена. Скачать около {size / 1024 ** 2:.0f} МБ и затем начать распознавание? Требования: {memory}.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        elif not self._confirm_large_model(self.settings.transcriber_model_name):
            return
        self.transcription_progress.setRange(0, 100); self.transcription_progress.setValue(0); self.transcription_text.clear(); self.transcription_text.append("Подготовка модели…")
        self.transcription_thread = QThread(self); self.transcription_worker = TranscriptionWorker(paths, self.settings); self.transcription_worker.moveToThread(self.transcription_thread)
        self._set_transcription_busy(True)
        self.transcription_thread.started.connect(self.transcription_worker.run)
        self.transcription_worker.progress.connect(self._update_transcription_progress)
        self.transcription_worker.message.connect(self.transcription_text.append)
        self.transcription_worker.finished.connect(self._transcription_finished)
        self.transcription_worker.finished.connect(lambda _ok: self.transcription_thread.quit() if self.transcription_thread else None)
        self.transcription_worker.finished.connect(self.transcription_worker.deleteLater)
        self.transcription_thread.finished.connect(self.transcription_thread.deleteLater)
        self.transcription_thread.finished.connect(self._clear_transcription_thread)
        self.transcription_thread.start()

    def download_transcriber_model_action(self) -> None:
        if self._transcription_operation_running():
            QMessageBox.information(self, APP_NAME, "Загрузка модели или транскрибация уже выполняется.")
            return
        model_name = self._selected_model_name()
        if not self._confirm_large_model(model_name):
            return
        self.settings.transcriber_model_name = model_name
        self.transcription_progress.setRange(0, 100)
        self.transcription_progress.setValue(0)
        self.transcription_text.append(f"Проверка модели {model_name}…")
        self.model_download_thread = QThread(self)
        self.model_download_worker = ModelDownloadWorker(model_name, self.settings.transcriber_models_dir)
        self.model_download_worker.moveToThread(self.model_download_thread)
        self._set_transcription_busy(True)
        self.model_download_thread.started.connect(self.model_download_worker.run)
        self.model_download_worker.progress.connect(self._update_transcription_progress)
        self.model_download_worker.finished.connect(self._model_download_finished)
        self.model_download_worker.finished.connect(lambda _ok, _message: self.model_download_thread.quit() if self.model_download_thread else None)
        self.model_download_worker.finished.connect(self.model_download_worker.deleteLater)
        self.model_download_thread.finished.connect(self.model_download_thread.deleteLater)
        self.model_download_thread.finished.connect(self._clear_model_download_thread)
        self.model_download_thread.start()

    def _confirm_large_model(self, model_name: str) -> bool:
        if model_name != "medium":
            return True
        size, memory = model_download_requirements(model_name)
        answer = QMessageBox.question(
            self,
            APP_NAME,
            "Модель medium занимает примерно "
            f"{size / 1024 ** 3:.1f} ГБ и обычно требует {memory}. "
            "Загрузка может занять продолжительное время и будет автоматически продолжена после обрыва. Продолжить?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _transcription_operation_running(self) -> bool:
        return bool(
            (self.transcription_thread and self.transcription_thread.isRunning())
            or (self.model_download_thread and self.model_download_thread.isRunning())
        )

    def _set_transcription_busy(self, busy: bool) -> None:
        if hasattr(self, "transcription_start_button"):
            self.transcription_start_button.setEnabled(not busy)
            self.model_download_button.setEnabled(not busy)
            self.transcription_model.setEnabled(not busy)
            self.model_delete_button.setEnabled(not busy and model_is_installed(self._selected_model_name(), self.settings.transcriber_models_dir))
            self.transcription_cancel_button.setEnabled(busy)

    def _update_transcription_progress(self, value: int, message: str) -> None:
        self.transcription_progress.setRange(0, 100)
        self.transcription_progress.setValue(max(0, min(100, value)))
        self.transcription_status.setText(message)

    def cancel_transcription_operation(self) -> None:
        if self.transcription_worker:
            self.transcription_worker.cancel()
        if self.model_download_worker:
            self.model_download_worker.cancel()
        self.transcription_status.setText("Отмена запрошена — завершается текущая операция…")
        self.transcription_cancel_button.setEnabled(False)

    def _transcription_finished(self, success: bool) -> None:
        self._set_transcription_busy(False)
        if success:
            self.transcription_progress.setValue(100)
            self.transcription_status.setText("Транскрибация завершена")
        else:
            self.transcription_status.setText("Транскрибация не завершена. Проверьте сообщение и повторите.")
        self._refresh_model_description()

    def _model_download_finished(self, success: bool, message: str) -> None:
        self._set_transcription_busy(False)
        self.transcription_text.append(("Готово: " if success else "Ошибка: ") + message)
        self.transcription_status.setText(message)
        if success:
            self.transcription_progress.setValue(100)
        self._refresh_model_description()

    def _clear_transcription_thread(self) -> None:
        self.transcription_thread = None
        self.transcription_worker = None

    def _clear_model_download_thread(self) -> None:
        self.model_download_thread = None
        self.model_download_worker = None

    def copy_transcription_result(self) -> None: QApplication.clipboard().setText(self.transcription_text.toPlainText())

    def save_transcription_result(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить результат", "transcript.txt", "Text (*.txt)")
        if path: Path(path).write_text(self.transcription_text.toPlainText(), encoding="utf-8")

    def refresh_logs(self) -> None:
        text = read_log_tail(user_path("logs")); level = self.log_level.currentText() if hasattr(self, "log_level") else "ALL"
        if level != "ALL": text = "\n".join(line for line in text.splitlines() if level in line)
        self.logs_text.setPlainText(text)

    def open_log_file(self) -> None:
        logs = sorted(user_path("logs").glob("*.log")); open_path(logs[-1] if logs else user_path("logs"))

    def refresh_info(self) -> None:
        info = get_runtime_info(self.settings); pyside = sys.modules.get("PySide6")
        self.info_text.setPlainText("\n".join([
            f"Версия: {APP_VERSION}", f"Папка данных: {Path(self.settings.base_directory).resolve()}", f"Настройки: {SETTINGS_PATH.resolve()}", f"OCR-журнал сегодня: {self._today_jsonl().resolve()}", f"База работ: {Path(self.settings.database_path).resolve()}", f"Папка моделей: {Path(self.settings.transcriber_models_dir).resolve()}", f"Python: {info.python_version}", f"PySide6: {getattr(pyside, '__version__', 'unknown')}", f"Платформа: {info.platform}", f"Tesseract: {info.tesseract_status}", "Данные хранятся локально; privacy mode скрывает секреты в JSONL." ]))

    def check_for_updates_action(self) -> None:
        result = check_for_updates()
        if result.ok and result.url and result.latest_version and result.latest_version != APP_VERSION:
            answer = QMessageBox.question(
                self,
                APP_NAME,
                result.message + "\nОткрыть страницу релиза?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                QDesktopServices.openUrl(QUrl(result.url))
            return
        message_box = QMessageBox.information if result.ok else QMessageBox.warning
        message_box(self, APP_NAME, result.message)

    def _refresh_runtime_labels(self) -> None:
        state = self.service.state; self.status_label.setText(state.status)
        if state.session_started_at: delta = datetime.now() - state.session_started_at; duration = str(delta).split('.')[0]
        else: duration = "00:00:00"
        next_start = next_schedule_start(datetime.now(), self.settings)
        next_text = next_start.strftime("%a %d.%m %H:%M") if next_start else ("ручная пауза" if self.settings.schedule_manual_pause else "—")
        self.duration_label.setText(duration); self.next_schedule_label.setText(f"Следующий запуск: {next_text}")
        if self.home_values:
            self.home_values["Статус OCR"].setText(state.status); self.home_values["Время работы"].setText(duration); self.home_values["Следующий запуск"].setText(self.next_schedule_label.text()); self.home_values["Файл журнала"].setText(str(self._today_jsonl()))
            count = sum(1 for _ in self._today_jsonl().open(encoding="utf-8", errors="ignore")) if self._today_jsonl().exists() else 0
            self.home_values["Записей сегодня"].setText(str(count)); self.home_values["Последнее сообщение"].setText(state.last_error or "—")
        self._update_tray_state()

    def poll_events(self) -> None:
        self._refresh_runtime_labels()
        while True:
            try: event = self.events.get_nowait()
            except queue.Empty: break
            if event.get("type") == "state": self._refresh_runtime_labels()
            elif event.get("type") == "privacy":
                payload = event.get("payload") or {}
                categories = ", ".join(payload.get("categories", [])) if isinstance(payload, dict) else ""
                stored = isinstance(payload, dict) and payload.get("mode") == "vault"
                message = f"Обнаружены конфиденциальные данные ({categories or 'секрет'}): {'сохранены в защищённом хранилище' if stored else 'маскированы без сохранения полного значения'}"
                if self.home_values:
                    self.home_values["Последнее сообщение"].setText(message)
                self.refresh_vault_status()
                if hasattr(self, "vault_dialog") and self.vault_dialog.isVisible():
                    self.refresh_vault_table()

    def restore_from_tray(self) -> None:
        self.showNormal(); self.raise_(); self.activateWindow()

    def _restore_window_geometry(self) -> None:
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else QRect(0, 0, 1400, 850)
        width = max(1100, int(getattr(self.settings, "window_width", 1400) or 1400))
        height = max(700, int(getattr(self.settings, "window_height", 850) or 850))
        width = min(width, available.width())
        height = min(height, available.height())
        x = int(getattr(self.settings, "window_x", -1) or -1)
        y = int(getattr(self.settings, "window_y", -1) or -1)
        if x < available.left() or y < available.top() or x + width > available.right() or y + height > available.bottom():
            x = available.left() + max(0, (available.width() - width) // 2)
            y = available.top() + max(0, (available.height() - height) // 2)
        self.setGeometry(x, y, width, height)

    def reset_ui_geometry(self) -> None:
        self.settings.window_width = 1400
        self.settings.window_height = 850
        self.settings.window_x = -1
        self.settings.window_y = -1
        self.settings.work_splitter_horizontal = [520, 780]
        self.settings.work_splitter_vertical = [380, 420]
        self.settings.work_table_columns = [120, 150, 150, 420, 150, 150]
        self._restore_window_geometry()
        if hasattr(self, "pages") and "Работы" in self.pages:
            self.pages["Работы"]._apply_layout_mode("wide", force=True)

    def _save_ui_geometry(self) -> None:
        geometry = self.geometry()
        self.settings.window_width = max(1100, geometry.width())
        self.settings.window_height = max(700, geometry.height())
        self.settings.window_x = geometry.x()
        self.settings.window_y = geometry.y()
        if "Работы" in getattr(self, "pages", {}):
            self.pages["Работы"]._remember_splitter_state()
        try:
            self._persist_settings(self.settings)
        except Exception:
            LOGGER.exception("Failed to save UI geometry")

    def _persist_settings(self, settings: AppSettings) -> None:
        if self.settings_path is not None:
            save_settings(settings, self.settings_path)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._save_ui_geometry()
        if self.tray.isVisible(): event.ignore(); self.hide()
        else: super().closeEvent(event)

    def exit_app(self) -> None:
        if self._transcription_operation_running():
            self._exit_pending = True
            self.cancel_transcription_operation()
            self.transcription_status.setText("Завершение фоновой операции перед выходом…")
            QTimer.singleShot(250, self._finish_exit_when_workers_stop)
            return
        self._complete_exit()

    def _finish_exit_when_workers_stop(self) -> None:
        if not self._exit_pending:
            return
        if self._transcription_operation_running():
            QTimer.singleShot(250, self._finish_exit_when_workers_stop)
            return
        self._exit_pending = False
        self._complete_exit()

    def _complete_exit(self) -> None:
        self._save_ui_geometry(); self.poll_timer.stop(); self.scheduler.stop()
        if self.service.is_running: self.service.stop()
        self.tray.hide()
        QApplication.quit()
