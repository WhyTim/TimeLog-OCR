from __future__ import annotations

import json
import queue
from pathlib import Path

from PIL import Image

from app.models import CaptureResult, MonitorCapture
from app.settings import AppSettings, load_settings


def test_first_run_creates_standard_directories_and_disables_links(tmp_path, monkeypatch):
    import app.settings as settings_module

    root = tmp_path / "LocalAppData" / "TimeLogOCR"
    monkeypatch.setattr(settings_module, "ensure_user_dirs", lambda: _make_user_dirs(root))
    monkeypatch.setattr(settings_module, "user_path", lambda value: root / value)
    settings = load_settings(tmp_path / "settings.json")

    assert settings.privacy_handling == "redact"
    assert settings.save_detected_links is False
    assert (tmp_path / "settings.json").exists()
    assert (root / "models").is_dir()
    assert (root / "data").is_dir()


def _make_user_dirs(root: Path) -> Path:
    for name in ("config", "data", "logs", "ocr_days", "archives", "models", "exports", "backups"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def test_legacy_link_setting_is_migrated_off(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"save_detected_links": True}), encoding="utf-8")
    settings = load_settings(path)
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert settings.save_detected_links is False
    assert persisted["save_detected_links"] is False


def test_windows_utf8_bom_settings_are_accepted(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"interval_seconds": 15}), encoding="utf-8-sig")
    settings = load_settings(path)
    assert settings.interval_seconds == 15
    assert not (tmp_path / "settings.broken.json").exists()


def test_ocr_never_creates_link_markdown_even_for_legacy_runtime_flag(tmp_path, monkeypatch):
    import app.capture_service as capture_module
    from app.capture_service import CaptureService

    image = Image.new("RGB", (20, 20), "white")
    result = CaptureResult(image, [MonitorCapture(1, {"left": 0, "top": 0, "width": 20, "height": 20}, image)])
    monkeypatch.setattr(capture_module, "capture_screens", lambda: result)
    monkeypatch.setattr(capture_module, "perform_ocr", lambda *_args, **_kwargs: ("https://example.test/page", None))
    settings = AppSettings(base_directory=str(tmp_path), save_detected_links=True)
    CaptureService(settings, queue.Queue())._capture_once()
    assert not list(tmp_path.rglob("links-*.md"))


def test_icon_assets_have_alpha_and_required_ico_sizes():
    png_path = Path("assets/app_icon.png")
    ico_path = Path("assets/app_icon.ico")
    png = Image.open(png_path).convert("RGBA")
    assert png.getchannel("A").getextrema() == (0, 255)
    assert png.getpixel((0, 0))[3] == 0
    ico = Image.open(ico_path)
    assert {(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)} <= set(ico.info["sizes"])


def test_pyinstaller_bundles_icons_dpapi_and_tesseract():
    spec = Path("TimeLogOCR.spec").read_text(encoding="utf-8")
    assert 'icon="assets/app_icon.ico"' in spec
    assert '"win32crypt"' in spec
    assert 'tesseract/tessdata' in spec


def test_home_has_vault_button_but_no_links_controls(qtbot, tmp_path):
    from PySide6.QtWidgets import QPushButton
    from app.ui.main_window import MainWindow

    settings = AppSettings(
        base_directory=str(tmp_path / "ocr"),
        archive_directory=str(tmp_path / "archives"),
        database_path=str(tmp_path / "db.sqlite"),
        report_template_path=str(tmp_path / "report.txt"),
        transcriber_models_dir=str(tmp_path / "models"),
    )
    Path(settings.report_template_path).write_text("template", encoding="utf-8")
    window = MainWindow(settings)
    qtbot.addWidget(window)
    buttons = {button.text() for button in window.pages["Главная"].findChildren(QPushButton)}
    assert "Открыть защищённые данные" in buttons
    assert not any("ссыл" in text.casefold() for text in buttons)
    assert "не сохраняет полные значения" in window.vault_home_status.text()
    window.scheduler.stop()


def test_tray_exposes_required_actions_and_four_state_icons(qtbot, tmp_path):
    from app.ui.main_window import MainWindow

    settings = AppSettings(
        base_directory=str(tmp_path / "ocr"),
        archive_directory=str(tmp_path / "archives"),
        database_path=str(tmp_path / "db.sqlite"),
        report_template_path=str(tmp_path / "report.txt"),
        transcriber_models_dir=str(tmp_path / "models"),
    )
    Path(settings.report_template_path).write_text("template", encoding="utf-8")
    window = MainWindow(settings)
    qtbot.addWidget(window)
    actions = {action.text() for action in window.tray.contextMenu().actions() if action.text()}
    assert {"Открыть TimeLog OCR", "Запустить OCR", "Пауза", "Остановить OCR", "Открыть папку данных", "Выход"} <= actions
    keys = {window._state_tray_icon(color).cacheKey() for color in ("#6b7280", "#16a34a", "#f59e0b", "#dc2626")}
    assert len(keys) == 4
    window.scheduler.stop()
