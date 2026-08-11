from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_DATA_DIR_NAME = "TimeLogOCR"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_path(relative_path: str | Path) -> Path:
    base = Path(getattr(sys, "_MEIPASS", app_root()))
    return base / relative_path


def default_tesseract_path() -> Path:
    """Prefer the OCR engine bundled with the packaged application."""
    bundled = resource_path("tesseract/tesseract.exe")
    if bundled.exists():
        return bundled
    return Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")


def user_data_root() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / APP_DATA_DIR_NAME


def user_path(relative_path: str | Path) -> Path:
    return user_data_root() / relative_path


def writable_path(relative_path: str | Path) -> Path:
    return user_path(relative_path)


def ensure_user_dirs() -> Path:
    root = user_data_root()
    for name in ("config", "data", "logs", "ocr_days", "archives", "models", "exports", "backups"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def copy_missing_tree(source: Path, destination: Path, backup_root: Path) -> list[str]:
    copied: list[str] = []
    if not source.exists():
        return copied
    if source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            backup = backup_root / destination.name
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination, backup)
            if destination.stat().st_mtime >= source.stat().st_mtime:
                return copied
        shutil.copy2(source, destination)
        return [str(destination)]
    for item in source.rglob("*"):
        rel = item.relative_to(source)
        target = destination / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            backup = backup_root / rel
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
            if target.stat().st_mtime >= item.stat().st_mtime:
                continue
        shutil.copy2(item, target)
        copied.append(str(target))
    return copied
