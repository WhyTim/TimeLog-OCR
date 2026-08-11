from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

AppStatus = Literal["Остановлено", "Работает", "Приостановлено", "Формируется архив", "Ошибка"]


@dataclass(slots=True)
class ServiceState:
    status: AppStatus = "Остановлено"
    session_started_at: datetime | None = None
    records_count: int = 0
    current_day_dir: Path | None = None
    last_archive_path: Path | None = None
    active_window: str = ""
    monitor_count: int = 0
    last_successful_ocr_at: datetime | None = None
    last_error: str = ""
    computer_locked: bool = False


@dataclass(slots=True)
class MonitorCapture:
    index: int
    info: dict[str, int]
    image: Any


@dataclass(slots=True)
class CaptureResult:
    all_image: Any | None
    monitors: list[MonitorCapture] = field(default_factory=list)
    error: str | None = None
    duration_ms: int = 0
