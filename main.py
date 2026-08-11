from __future__ import annotations

import argparse
import logging
import os
import sys
import ctypes

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from app.logging_config import setup_logging
from app.paths import resource_path
from app.settings import load_settings
from app.single_instance import SingleInstance, SingleInstanceClient
from app.ui.application import create_main_window
from app.version import APP_NAME, APP_VERSION


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--minimized", action="store_true", help="Запустить свернутым в трей")
    return parser.parse_args()


def install_global_exception_handler() -> None:
    def handle(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logging.critical("Unhandled exception", exc_info=(exc_type, exc_value, exc_traceback))

    sys.excepthook = handle


def set_windows_app_id() -> None:
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"TimeLogOCR.TimeLogOCR.{APP_VERSION}")
        except Exception:  # noqa: BLE001
            logging.exception("Failed to set Windows AppUserModelID")


def main() -> int:
    args = parse_args()
    setup_logging()
    install_global_exception_handler()
    set_windows_app_id()
    app = QApplication.instance() or QApplication(sys.argv)
    app.setWindowIcon(QIcon(str(resource_path("assets/app_icon.ico"))))
    instance = SingleInstance()
    if not instance.acquire():
        SingleInstanceClient().notify_existing()
        QMessageBox.information(None, APP_NAME, "TimeLog OCR уже запущен. Открыто существующее окно.")
        return 0
    try:
        settings = load_settings()
        window = create_main_window(app, settings, start_minimized=args.minimized)
        instance.activate_requested.connect(window.restore_from_tray)
        if not args.minimized and not settings.start_minimized:
            window.show()
        return app.exec()
    except Exception:
        logging.exception("Application startup failed")
        raise
    finally:
        instance.release()


if __name__ == "__main__":
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.exit(main())
