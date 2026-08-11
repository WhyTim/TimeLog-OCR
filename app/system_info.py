from __future__ import annotations

import platform
import sys
from dataclasses import dataclass

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

from .ocr_service import check_tesseract
from .settings import AppSettings
from .version import APP_VERSION


@dataclass(slots=True)
class RuntimeInfo:
    app_version: str
    python_version: str
    platform: str
    tesseract_status: str
    cpu_percent: float | None
    memory_percent: float | None


def get_runtime_info(settings: AppSettings) -> RuntimeInfo:
    warnings = check_tesseract(settings.tesseract_path, settings.ocr_language)
    return RuntimeInfo(
        app_version=APP_VERSION,
        python_version=sys.version.split()[0],
        platform=platform.platform(),
        tesseract_status="OK" if not warnings else "; ".join(warnings),
        cpu_percent=psutil.cpu_percent(interval=None) if psutil else None,
        memory_percent=psutil.virtual_memory().percent if psutil else None,
    )
