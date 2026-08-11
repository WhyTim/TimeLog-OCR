from __future__ import annotations

import json
import logging
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ArchiveResult:
    created: bool
    path: Path | None = None
    warning: str = ""


def validate_jsonl(jsonl_file: Path) -> tuple[bool, str]:
    if not jsonl_file.exists():
        return False, f"JSONL отсутствует: {jsonl_file}"
    if jsonl_file.stat().st_size == 0:
        return False, f"JSONL пустой: {jsonl_file}"
    with jsonl_file.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                json.loads(line)
            except json.JSONDecodeError as exc:
                return False, f"Поврежденная JSONL строка {line_number}: {exc}"
    return True, ""


def safe_archive_name(date_part: str, now: datetime | None = None) -> str:
    current = now or datetime.now()
    return f"{date_part}_{current:%H-%M-%S}.zip"


def unique_archive_path(archive_dir: Path, date_part: str, now: datetime | None = None) -> Path:
    archive_dir.mkdir(parents=True, exist_ok=True)
    candidate = archive_dir / safe_archive_name(date_part, now)
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    for index in range(1, 1000):
        next_candidate = archive_dir / f"{stem}_{index}.zip"
        if not next_candidate.exists():
            return next_candidate
    raise RuntimeError("Не удалось подобрать уникальное имя архива.")


def archive_day(
    day_dir: Path,
    report_template_file: Path,
    delete_screenshots_after_archive: bool = False,
    now: datetime | None = None,
    archive_directory: Path | None = None,
) -> ArchiveResult:
    try:
        if not day_dir.exists():
            return ArchiveResult(False, warning=f"Папка дня не найдена: {day_dir}")
        date_part = day_dir.name
        jsonl_file = day_dir / f"ocr_log_{date_part}.jsonl"
        valid, warning = validate_jsonl(jsonl_file)
        if not valid:
            return ArchiveResult(False, warning=warning)
        archive_dir = archive_directory or day_dir.parent / "_archives"
        zip_path = unique_archive_path(archive_dir, date_part, now)
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
            zip_file.write(jsonl_file, arcname=jsonl_file.name)
            if report_template_file.exists():
                zip_file.write(report_template_file, arcname=report_template_file.name)
            else:
                warning = f"Файл шаблона отчёта не найден: {report_template_file}"
            transcripts_dir = day_dir / "transcripts"
            if transcripts_dir.exists():
                for transcript in sorted(path for path in transcripts_dir.rglob("*") if path.is_file()):
                    zip_file.write(transcript, arcname=str(transcript.relative_to(day_dir)))
            transcripts_index = day_dir / f"transcripts_{date_part}.jsonl"
            if transcripts_index.exists():
                zip_file.write(transcripts_index, arcname=transcripts_index.name)
            manual_work_log = day_dir / f"manual_work_log_{date_part}.jsonl"
            if manual_work_log.exists():
                zip_file.write(manual_work_log, arcname=manual_work_log.name)
        if delete_screenshots_after_archive:
            shutil.rmtree(day_dir / "screenshots", ignore_errors=True)
        return ArchiveResult(True, zip_path, warning)
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("Archive creation failed")
        return ArchiveResult(False, warning=f"Ошибка создания архива: {exc}")
