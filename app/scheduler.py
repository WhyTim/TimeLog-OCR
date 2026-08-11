from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, time as dt_time, timedelta
from typing import Callable

from .settings import AppSettings

LOGGER = logging.getLogger(__name__)


def parse_hhmm(value: str) -> dt_time:
    hour, minute = value.split(":")
    return dt_time(int(hour), int(minute))


def in_schedule_window(now: datetime, settings: AppSettings) -> bool:
    start = parse_hhmm(settings.scheduled_start_time)
    stop = parse_hhmm(settings.scheduled_stop_time)
    current = now.time().replace(second=0, microsecond=0)
    if start <= stop:
        return now.weekday() in settings.scheduled_workdays and start <= current < stop
    if current >= start:
        return now.weekday() in settings.scheduled_workdays
    if current < stop:
        return (now.weekday() - 1) % 7 in settings.scheduled_workdays
    return False


def next_schedule_start(now: datetime, settings: AppSettings) -> datetime | None:
    if not settings.scheduled_capture_enabled or settings.schedule_manual_pause or not settings.scheduled_workdays:
        return None
    start = parse_hhmm(settings.scheduled_start_time)
    for offset in range(8):
        day = now.date() + timedelta(days=offset)
        if day.weekday() not in settings.scheduled_workdays:
            continue
        candidate = datetime.combine(day, start)
        if candidate > now:
            return candidate
    return None


class Scheduler:
    def __init__(self, settings: AppSettings, start_capture: Callable[[], None], stop_capture: Callable[[], None]) -> None:
        self.settings = settings
        self.start_capture = start_capture
        self.stop_capture = stop_capture
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._was_in_window = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="TimeLogOCRScheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                if self.settings.schedule_manual_pause:
                    self._was_in_window = False
                    self._stop_event.wait(30)
                    continue
                enabled = self.settings.scheduled_capture_enabled
                in_window = enabled and in_schedule_window(datetime.now(), self.settings)
                if in_window and not self._was_in_window:
                    self.start_capture()
                if self._was_in_window and not in_window:
                    self.stop_capture()
                self._was_in_window = in_window
            except Exception:  # noqa: BLE001
                LOGGER.exception("Scheduler iteration failed")
            self._stop_event.wait(30)
