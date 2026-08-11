from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

LOGGER = logging.getLogger(__name__)
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
ENTRY_NAME = "TimeLogOCR"


def build_startup_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimized'
    main_path = Path(sys.argv[0]).resolve()
    return f'"{sys.executable}" "{main_path}" --minimized'


def set_startup_enabled(enabled: bool, winreg_module=None, command: str | None = None) -> tuple[bool, str]:
    if os.name != "nt" and winreg_module is None:
        return False, "Автозапуск через реестр доступен только в Windows."
    try:
        winreg = winreg_module
        if winreg is None:
            import winreg as winreg  # type: ignore[no-redef]
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, ENTRY_NAME, 0, winreg.REG_SZ, command or build_startup_command())
            else:
                try:
                    winreg.DeleteValue(key, ENTRY_NAME)
                except FileNotFoundError:
                    pass
        return True, ""
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("Startup registry operation failed")
        return False, str(exc)
