from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
import logging

from app.paths import app_root, copy_missing_tree, default_tesseract_path, ensure_user_dirs, user_data_root, user_path
from typing import Any

LOGGER = logging.getLogger(__name__)
SETTINGS_PATH = user_path("config/settings.json")
TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


@dataclass(slots=True)
class AppSettings:
    interval_seconds: int = 10
    ocr_language: str = "rus+eng"
    tesseract_path: str = field(default_factory=lambda: str(default_tesseract_path()))
    save_monitor_screenshots: bool = False
    save_all_screenshot: bool = False
    ocr_by_monitors: bool = True
    selected_monitor_indices: list[int] = field(default_factory=list)
    start_minimized: bool = False
    start_with_windows: bool = False
    start_capture_on_app_launch: bool = False
    scheduled_capture_enabled: bool = False
    scheduled_start_time: str = "08:30"
    scheduled_stop_time: str = "17:30"
    scheduled_workdays: list[int] = field(default_factory=lambda: [0, 1, 2, 3, 4])
    schedule_manual_pause: bool = False
    archive_on_stop: bool = True
    delete_screenshots_after_archive: bool = False
    base_directory: str = str(user_path("ocr_days"))
    archive_directory: str = str(user_path("archives"))
    report_template_path: str = str(user_path("config/report_template.txt"))
    database_path: str = str(user_path("data/timelog.db"))
    theme: str = "light"
    privacy_mode_enabled: bool = True
    privacy_handling: str = "redact"
    privacy_skip_screenshots_on_detection: bool = True
    secret_vault_path: str = str(user_path("data/secrets.vault"))
    # Retained only for safe migration of old settings. Link journaling is no
    # longer a user-facing feature and is always disabled at load time.
    save_detected_links: bool = False
    transcription_enabled: bool = True
    transcriber_script_path: str = "tools/local_call_transcriber_v3"
    transcriber_models_dir: str = str(user_path("models"))
    transcriber_model_name: str = "small"
    transcriber_command_template: str = ""
    transcriber_timeout_seconds: int = 7200
    first_run_completed: bool = False
    window_width: int = 1400
    window_height: int = 850
    window_x: int = -1
    window_y: int = -1
    work_splitter_horizontal: list[int] = field(default_factory=lambda: [520, 780])
    work_splitter_vertical: list[int] = field(default_factory=lambda: [380, 420])
    work_table_columns: list[int] = field(default_factory=lambda: [120, 150, 150, 420, 150, 150])

    @property
    def save_screenshots(self) -> bool:
        return self.save_monitor_screenshots or self.save_all_screenshot


def default_settings() -> AppSettings:
    ensure_user_dirs()
    return AppSettings()


def ensure_default_report_template(settings: AppSettings) -> Path:
    target = Path(settings.report_template_path)
    if target.exists() and target.stat().st_size > 0:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text("# Ежедневный отчет\n\nДата: {date}\n\n## Выполнено\n- \n", encoding="utf-8")
        LOGGER.warning("Report template restored at %s", target)
    except OSError:
        LOGGER.exception("Failed to restore report template")
        raise
    return target


def migrate_legacy_data(settings: AppSettings | None = None) -> list[str]:
    settings = settings or default_settings()
    root = ensure_user_dirs()
    backup_root = root / "backups" / "legacy_migration"
    copied: list[str] = []
    candidates = [app_root(), Path.cwd()]
    mapping = {
        "config/settings.json": Path(settings.report_template_path).parent / "settings.json",
        "data/timelog.db": Path(settings.database_path),
        "timelog.db": Path(settings.database_path),
        "ocr_days": Path(settings.base_directory),
        "archives": Path(settings.archive_directory),
        "models": Path(settings.transcriber_models_dir),
        "report_template.txt": Path(settings.report_template_path),
    }
    for base in candidates:
        for rel, dest in mapping.items():
            source = base / rel
            try:
                copied.extend(copy_missing_tree(source, dest, backup_root / rel.replace("/", "_")))
            except OSError:
                LOGGER.exception("Legacy migration failed for %s", source)
    if copied:
        LOGGER.info("Legacy migration copied %d files into %s", len(copied), root)
    return copied


def _backup_broken_settings(path: Path) -> Path:
    candidate = path.with_name(f"{path.stem}.broken{path.suffix}")
    index = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}.broken-{index}{path.suffix}")
        index += 1
    path.replace(candidate)
    return candidate


def load_settings(path: Path = SETTINGS_PATH) -> AppSettings:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        settings = default_settings()
        migrate_legacy_data(settings)
        ensure_default_report_template(settings)
        save_settings(settings, path)
        return settings
    try:
        # Windows PowerShell 5 writes UTF-8 JSON with a BOM by default. Accept
        # it so a valid user-edited settings file is not treated as corrupt.
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("Settings root must be a JSON object")
        defaults = asdict(default_settings())
        defaults.update({k: v for k, v in data.items() if k in defaults})
        settings = AppSettings(**defaults)
        migrated = False
        legacy_transcriber = settings.transcriber_script_path.replace("\\", "/").strip()
        if not legacy_transcriber or legacy_transcriber == "tools/local_call_transcriber_v3" or (
            "local_call_transcriber" in legacy_transcriber.lower() and not Path(settings.transcriber_script_path).exists()
        ):
            settings.transcriber_script_path = "tools/local_call_transcriber_v3"
            migrated = True
        if not settings.transcriber_models_dir.strip() or not Path(settings.transcriber_models_dir).is_absolute():
            settings.transcriber_models_dir = str(user_path("models"))
            migrated = True
        # Product policy: screen links are no longer collected. Existing files
        # are deliberately left untouched.
        if settings.save_detected_links:
            settings.save_detected_links = False
            migrated = True
        configured_tesseract = Path(settings.tesseract_path)
        bundled_tesseract = default_tesseract_path()
        if bundled_tesseract.exists() and (not configured_tesseract.exists() or configured_tesseract != bundled_tesseract):
            settings.tesseract_path = str(bundled_tesseract)
            migrated = True
        validate_settings(settings, strict_paths=False)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError) as exc:
        broken = _backup_broken_settings(path)
        LOGGER.warning("Invalid settings moved to %s: %s", broken, exc)
        settings = default_settings()
        ensure_default_report_template(settings)
        save_settings(settings, path)
        return settings
    ensure_default_report_template(settings)
    if migrated:
        save_settings(settings, path)
    return settings


def save_settings(settings: AppSettings, path: Path = SETTINGS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_settings(settings: AppSettings, strict_paths: bool = True) -> list[str]:
    warnings: list[str] = []
    if settings.interval_seconds < 5:
        raise ValueError("Интервал OCR должен быть не меньше 5 секунд.")
    for label, value in (("время начала", settings.scheduled_start_time), ("время окончания", settings.scheduled_stop_time)):
        if not TIME_PATTERN.match(value):
            raise ValueError(f"Некорректное {label}: используйте HH:MM.")
    for label, directory in (("Базовая папка", settings.base_directory), ("Папка архивов", settings.archive_directory), ("Папка моделей", settings.transcriber_models_dir)):
        if not str(directory).strip():
            raise ValueError(f"{label} не указана.")
        target_dir = Path(directory)
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            probe = target_dir / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
        except OSError as exc:
            raise ValueError(f"{label} недоступна для записи: {target_dir}") from exc
    tesseract = Path(settings.tesseract_path)
    if strict_paths and not tesseract.exists():
        raise ValueError(f"Tesseract не найден: {tesseract}")
    if not Path(settings.report_template_path).exists():
        warnings.append(f"Файл шаблона отчёта не найден: {settings.report_template_path}")
    if settings.theme not in {"light", "dark"}:
        raise ValueError("Тема должна быть light или dark.")
    if settings.privacy_handling not in {"redact", "drop", "vault", "warn"}:
        raise ValueError("Некорректный режим обработки конфиденциальных данных.")
    if not str(settings.secret_vault_path).strip():
        raise ValueError("Не указан путь защищённого хранилища.")
    try:
        Path(settings.secret_vault_path).parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError("Папка защищённого хранилища недоступна для записи.") from exc
    if settings.transcriber_timeout_seconds < 30:
        raise ValueError("Таймаут транскрибации должен быть не меньше 30 секунд.")
    normalized_workdays: set[int] = set()
    for day in settings.scheduled_workdays or []:
        try:
            normalized = int(day)
        except (TypeError, ValueError):
            warnings.append(f"Некорректный день расписания пропущен: {day!r}")
            continue
        if 0 <= normalized <= 6:
            normalized_workdays.add(normalized)
        else:
            warnings.append(f"День расписания вне диапазона 0–6 пропущен: {normalized}")
    settings.scheduled_workdays = sorted(normalized_workdays)
    normalized_monitors: set[int] = set()
    for index in settings.selected_monitor_indices or []:
        try:
            normalized = int(index)
        except (TypeError, ValueError):
            continue
        if normalized > 0:
            normalized_monitors.add(normalized)
    settings.selected_monitor_indices = sorted(normalized_monitors)
    return warnings
