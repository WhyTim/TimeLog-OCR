from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
import importlib.util

QT_AVAILABLE = importlib.util.find_spec("PySide6") is not None and importlib.util.find_spec("pytestqt") is not None
qt_required = pytest.mark.skipif(not QT_AVAILABLE, reason="PySide6/pytest-qt are not installed")

from app.settings import AppSettings
from app.work_log_service import WorkLogService


def _settings(tmp_path):
    return AppSettings(base_directory=str(tmp_path / "ocr"), database_path=str(tmp_path / "db.sqlite"), report_template_path=str(tmp_path / "report_template.txt"))


def _qt():
    pytest.importorskip("PySide6")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QPushButton, QScrollArea, QTableView
    from app.ui.main_window import MainWindow
    from app.ui.pages.work_page import WorkPage
    return Qt, QPushButton, QScrollArea, QTableView, MainWindow, WorkPage


def test_main_entrypoint_does_not_import_legacy_ui():
    source = Path("main.py").read_text(encoding="utf-8")
    assert "tkinter" not in source
    assert "ttkbootstrap" not in source
    assert "pystray" not in source


def test_main_window_source_has_no_placeholder_pages():
    source = Path("app/ui/main_window.py").read_text(encoding="utf-8")
    forbidden = [
        "Основные настройки пока сохранены",
        "Откройте сегодняшний JSONL",
        "OCR, архивация и состояние приложения.",
    ]
    for text in forbidden:
        assert text not in source


def test_work_page_source_removes_unused_fields():
    source = Path("app/ui/pages/work_page.py").read_text(encoding="utf-8")
    assert "В ЛУРВ" not in source
    assert "Клиенту" not in source
    assert "Дополнительно" not in source


@qt_required
def test_main_window_can_be_created(qtbot, tmp_path):
    Qt, _QPushButton, _QScrollArea, _QTableView, MainWindow, _WorkPage = _qt()
    window = MainWindow(_settings(tmp_path))
    qtbot.addWidget(window)
    window.show()
    assert window.isVisible()
    assert callable(getattr(window, "download_transcriber_model_action", None))
    assert all(window.pages[name] is not None for name in window.PAGE_NAMES)
    assert "Работы" not in window.pages
    assert set(window.shortcut_actions) == {"Ctrl+R", "Ctrl+P", "Ctrl+S", "Ctrl+L", "F1"}
    assert callable(window.scheduler.start_capture)
    window.shortcut_actions["Ctrl+L"].trigger()
    assert window.PAGE_NAMES[window.stack.currentIndex()] == "Логи"
    window.shortcut_actions["F1"].trigger()
    assert window.PAGE_NAMES[window.stack.currentIndex()] == "Информация"


@qt_required
def test_first_run_wizard_applies_values(qtbot, tmp_path):
    from app.ui.application import FirstRunWizard

    settings = _settings(tmp_path)
    wizard = FirstRunWizard(settings)
    qtbot.addWidget(wizard)
    wizard.base_directory.setText(str(tmp_path / "данные с пробелом"))
    wizard.theme.setCurrentText("dark")
    wizard.start_minimized.setChecked(True)

    wizard.apply_to(settings)

    assert settings.base_directory == str(tmp_path / "данные с пробелом")
    assert settings.theme == "dark"
    assert settings.start_minimized is True


@qt_required
def test_work_page_has_scroll_table_and_fixed_actions(qtbot, tmp_path):
    _Qt, QPushButton, QScrollArea, QTableView, _MainWindow, WorkPage = _qt()
    page = WorkPage(WorkLogService(_settings(tmp_path)))
    qtbot.addWidget(page)
    page.resize(1024, 680)
    page.show()
    assert page.findChild(QScrollArea) is not None
    assert page.findChild(QTableView) is not None
    for text in ("Сохранить", "Сохранить и следующая", "Новая работа", "Удалить"):
        assert any(button.text() == text and button.isVisible() for button in page.findChildren(QPushButton))


@qt_required
def test_new_work_default_time_is_last_20_minutes(qtbot, tmp_path):
    _Qt, _QPushButton, _QScrollArea, _QTableView, _MainWindow, WorkPage = _qt()
    page = WorkPage(WorkLogService(_settings(tmp_path)), now_provider=lambda: datetime(2026, 7, 23, 19, 42, 13))
    qtbot.addWidget(page)
    assert page.start_time.time().toString("HH:mm") == "19:22"
    assert page.end_time.time().toString("HH:mm") == "19:42"
    assert page.fact_time.text() == "00:20"


@qt_required
def test_work_page_create_select_update_without_duplicate(qtbot, tmp_path):
    Qt, _QPushButton, _QScrollArea, _QTableView, _MainWindow, WorkPage = _qt()
    service = WorkLogService(_settings(tmp_path))
    page = WorkPage(service)
    qtbot.addWidget(page)
    page.client_combo.setCurrentText("Купава")
    page.contact_combo.setCurrentText("Василий")
    page.category_combo.setCurrentText("Клиент, без оплаты")
    page.message_edit.setPlainText("Проверка выгрузки карточек на ВБ")
    page.result_edit.setPlainText("Проверены настройки отчета")
    page.start_time.setTime(page.start_time.time().fromString("16:44", "HH:mm"))
    page.end_time.setTime(page.end_time.time().fromString("16:46", "HH:mm"))
    page.save_entry()
    assert len(service.list_entries(page.work_date())) == 1
    assert "16:44–16:46" in page.model.data(page.model.index(0, 0), Qt.ItemDataRole.DisplayRole)
    page.table.selectRow(0)
    page.result_edit.setPlainText("Проверены настройки отчета и источники данных")
    page.save_entry()
    entries = service.list_entries(page.work_date())
    assert len(entries) == 1
    assert entries[0].result.endswith("данных")


@qt_required
def test_work_page_tab_focus_and_quick_actions(qtbot, tmp_path):
    _Qt, _QPushButton, _QScrollArea, _QTableView, _MainWindow, WorkPage = _qt()
    service = WorkLogService(_settings(tmp_path))
    page = WorkPage(service, now_provider=lambda: datetime(2026, 7, 23, 19, 42, 13))
    qtbot.addWidget(page)
    assert page.message_edit.tabChangesFocus()
    assert page.result_edit.tabChangesFocus()
    assert page.focusNextPrevChild(True)
    page.quick_save_category("Без задач", 20)
    page.quick_save_category("Перерыв", 20)
    totals = service.day_totals(page.work_date())
    assert totals["Без задач"] == 20
    assert totals["Перерыв"] == 20


@qt_required
def test_work_page_saves_empty_descriptions(qtbot, tmp_path):
    _Qt, _QPushButton, _QScrollArea, _QTableView, _MainWindow, WorkPage = _qt()
    service = WorkLogService(_settings(tmp_path))
    page = WorkPage(service)
    qtbot.addWidget(page)
    page.category_combo.setCurrentText("Без задач")
    page.message_edit.clear()
    page.result_edit.clear()
    page.save_entry()
    entries = service.list_entries(page.work_date())
    assert len(entries) == 1
    assert entries[0].message == ""
    assert entries[0].result == ""
