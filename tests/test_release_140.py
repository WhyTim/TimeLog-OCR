from __future__ import annotations

import os
from pathlib import Path

from app.paths import resource_path, user_data_root
from app.settings import AppSettings, ensure_default_report_template, migrate_legacy_data
from app.single_instance import SingleInstance
from app.ui.main_window import SHOW_JOURNAL_PAGE, SHOW_WORK_PAGE, MainWindow
from app.work_log_service import WorkLogEntry, WorkLogService


def test_default_data_root_uses_localappdata(monkeypatch, tmp_path):
    monkeypatch.setattr(os, "name", "nt", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert user_data_root() == tmp_path / "TimeLogOCR"


def test_report_template_fallback_created(tmp_path):
    settings = AppSettings(report_template_path=str(tmp_path / "config" / "report_template.txt"))
    path = ensure_default_report_template(settings)
    assert path.exists()
    assert path.read_text(encoding="utf-8")


def test_legacy_data_migration_keeps_source(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "report_template.txt").write_text("old", encoding="utf-8")
    settings = AppSettings(report_template_path=str(tmp_path / "new" / "config" / "report_template.txt"))
    monkeypatch.setattr("app.settings.app_root", lambda: legacy)
    monkeypatch.chdir(legacy)
    copied = migrate_legacy_data(settings)
    assert str(Path(settings.report_template_path)) in copied
    assert (legacy / "report_template.txt").exists()


def test_journal_page_hidden_flag():
    assert SHOW_JOURNAL_PAGE is False
    assert "Журнал" not in MainWindow.PAGE_NAMES


def test_work_page_hidden_without_removing_implementation():
    assert SHOW_WORK_PAGE is False
    assert "Работы" not in MainWindow.PAGE_NAMES
    assert Path("app/ui/pages/work_page.py").exists()


def test_tvb_migrates_to_internal(tmp_path):
    settings = AppSettings(base_directory=str(tmp_path / "ocr"), database_path=str(tmp_path / "db.sqlite"))
    service = WorkLogService(settings)
    entry = service.save_entry(WorkLogEntry(work_date="2026-07-24", category="Внутренняя", start_time="09:00", end_time="09:20"))
    assert service.get_entry(entry.id).category == "Внутренняя"
    assert "Внутренняя" in service.day_totals("2026-07-24")


def test_statuses_are_editable(tmp_path):
    settings = AppSettings(base_directory=str(tmp_path / "ocr"), database_path=str(tmp_path / "db.sqlite"))
    service = WorkLogService(settings)
    statuses = service.list_statuses(active_only=False)
    assert {status.name for status in statuses} >= {"Готово", "Внутренняя"}


def test_icon_resource_available():
    assert resource_path("assets/app_icon.ico").exists()
    assert resource_path("assets/app_icon.png").exists()


def test_single_instance_server_name_stable(qtbot):
    instance = SingleInstance("TimeLogOCR-test-release-140")
    try:
        assert instance.acquire() is True
    finally:
        instance.release()


def test_second_instance_cannot_replace_active_server(qtbot):
    first = SingleInstance("TimeLogOCR-test-active-server")
    second = SingleInstance("TimeLogOCR-test-active-server")
    try:
        assert first.acquire() is True
        assert second.acquire() is False
    finally:
        second.release()
        first.release()


def test_reference_dialog_has_category_and_status_tabs(qtbot, tmp_path):
    from app.ui.pages.work_page import CategoryDialog
    settings = AppSettings(base_directory=str(tmp_path / "ocr"), database_path=str(tmp_path / "db.sqlite"))
    dialog = CategoryDialog(WorkLogService(settings))
    qtbot.addWidget(dialog)
    labels = [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())]
    assert labels == ["Категории", "Статусы"]


def test_status_color_persists_and_loads(tmp_path):
    from app.work_log_service import WorkStatus
    db = tmp_path / "db.sqlite"
    settings = AppSettings(base_directory=str(tmp_path / "ocr"), database_path=str(db))
    service = WorkLogService(settings)
    service.save_status(WorkStatus("Проверка", "#123456", False, True, 99))
    reopened = WorkLogService(settings)
    assert reopened.get_status("Проверка").color == "#123456"


def test_used_status_delete_deactivates(tmp_path):
    settings = AppSettings(base_directory=str(tmp_path / "ocr"), database_path=str(tmp_path / "db.sqlite"))
    service = WorkLogService(settings)
    saved = service.save_entry(WorkLogEntry(work_date="2026-07-24", category="Не определено", start_time="09:00", end_time="09:20"))
    assert saved.status == "Требует уточнения"
    physically_deleted = service.delete_status("Требует уточнения")
    assert physically_deleted is False
    assert service.get_status("Требует уточнения").is_active is False


def test_default_status_persists(tmp_path):
    from app.work_log_service import WorkStatus
    settings = AppSettings(base_directory=str(tmp_path / "ocr"), database_path=str(tmp_path / "db.sqlite"))
    service = WorkLogService(settings)
    service.save_status(WorkStatus("Новый default", "#abcdef", True, True, 77))
    defaults = [status.name for status in WorkLogService(settings).list_statuses(False) if status.is_default]
    assert defaults == ["Новый default"]


def test_status_color_role_applies_in_table(qtbot, tmp_path):
    from PySide6.QtGui import QColor
    from app.ui.work.work_table_model import STATUS_BG_ROLE, STATUS_FG_ROLE, WorkTableModel
    from app.work_log_service import WorkStatus
    model = WorkTableModel()
    entry = WorkLogEntry(category="Внутренняя", status="Готово", start_time="09:00", end_time="09:20", duration_minutes=20)
    model.set_statuses([WorkStatus("Внутренняя", "#000000", False, True, 1)])
    model.set_entries([entry])
    index = model.index(0, 5)
    assert model.data(index, STATUS_BG_ROLE) == QColor("#000000")
    assert model.data(index, STATUS_FG_ROLE) == QColor("#ffffff")


def test_work_page_modes_and_hysteresis(qtbot, tmp_path):
    from PySide6.QtCore import Qt
    from app.ui.pages.work_page import WorkPage
    settings = AppSettings(base_directory=str(tmp_path / "ocr"), database_path=str(tmp_path / "db.sqlite"))
    page = WorkPage(WorkLogService(settings))
    qtbot.addWidget(page)
    page._update_layout_for_width(1400)
    assert page.splitter.orientation() == Qt.Orientation.Horizontal
    page._update_layout_for_width(1230)
    assert page.splitter.orientation() == Qt.Orientation.Horizontal
    page._update_layout_for_width(1100)
    assert page.splitter.orientation() == Qt.Orientation.Vertical
    page._update_layout_for_width(1230)
    assert page.splitter.orientation() == Qt.Orientation.Vertical
    page._update_layout_for_width(1280)
    assert page.splitter.orientation() == Qt.Orientation.Horizontal


def test_splitter_states_are_separate(qtbot, tmp_path):
    from app.ui.pages.work_page import WorkPage
    settings = AppSettings(base_directory=str(tmp_path / "ocr"), database_path=str(tmp_path / "db.sqlite"))
    page = WorkPage(WorkLogService(settings))
    qtbot.addWidget(page)
    page.resize(1400, 800)
    page.show()
    qtbot.wait(10)
    page._apply_layout_mode("wide", force=True)
    page.splitter.setSizes([500, 700])
    page._remember_splitter_state()
    horizontal = page.splitter.sizes()
    page._apply_layout_mode("compact", force=True)
    page.splitter.setSizes([300, 400])
    page._remember_splitter_state()
    vertical = page.splitter.sizes()
    assert settings.work_splitter_horizontal == horizontal
    assert settings.work_splitter_vertical == vertical
