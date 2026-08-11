from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image
from mss import MSS

from .models import CaptureResult, MonitorCapture, ServiceState
from .ocr_service import configure_tesseract, get_image_hash, perform_ocr
from .privacy import SECRET_LINE_MARKER, detect_secrets, sanitize_record
from .secret_vault import SecretVault
from .settings import AppSettings
from .version import APP_VERSION

try:
    import win32gui
except ImportError:  # pragma: no cover - non-Windows CI
    win32gui = None

LOGGER = logging.getLogger(__name__)


def get_active_window_title() -> str:
    if win32gui is None:
        return ""
    try:
        hwnd = win32gui.GetForegroundWindow()
        return win32gui.GetWindowText(hwnd)
    except Exception:  # noqa: BLE001
        LOGGER.exception("Failed to get active window")
        return ""


def grab_monitor(sct: MSS, monitor: dict[str, int]) -> Image.Image:
    screenshot = sct.grab(monitor)
    return Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")


def capture_screens() -> CaptureResult:
    started = time.perf_counter()
    try:
        with MSS() as sct:
            all_image = grab_monitor(sct, sct.monitors[0])
            monitors = [
                MonitorCapture(
                    index=index,
                    info={"left": info["left"], "top": info["top"], "width": info["width"], "height": info["height"]},
                    image=grab_monitor(sct, info),
                )
                for index, info in enumerate(sct.monitors[1:], start=1)
            ]
            return CaptureResult(all_image, monitors, duration_ms=int((time.perf_counter() - started) * 1000))
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("Screen capture failed")
        return CaptureResult(None, [], str(exc), int((time.perf_counter() - started) * 1000))


def build_status(text: str, is_duplicate: bool, error: str | None = None) -> str:
    if error:
        return "ocr_error"
    if not text:
        return "empty_ocr"
    if is_duplicate:
        return "duplicate_screen"
    return "ok"


class CaptureService:
    def __init__(self, settings: AppSettings, events: queue.Queue[dict[str, Any]]) -> None:
        self.settings = settings
        self.events = events
        self.state = ServiceState()
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_hashes: dict[str, str] = {}
        self._duplicate_counts: dict[str, int] = {}
        self._session_id = ""
        self._capture_failures = 0
        self._touched_day_dirs: set[Path] = set()
        self._suspend_until: datetime | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def is_paused(self) -> bool:
        return self._pause_event.is_set()

    def start(self) -> None:
        if self.is_running:
            return
        configure_tesseract(self.settings.tesseract_path)
        self._stop_event.clear()
        self._pause_event.clear()
        self._last_hashes.clear()
        self._duplicate_counts.clear()
        self._touched_day_dirs.clear()
        self._capture_failures = 0
        self._suspend_until = None
        self._session_id = str(uuid.uuid4())
        self.state = ServiceState(status="Работает", session_started_at=datetime.now())
        self._thread = threading.Thread(target=self._run, name="TimeLogOCRCapture", daemon=True)
        self._thread.start()
        self._emit("state", self.state)

    def pause(self) -> None:
        if self.is_running:
            self._pause_event.set()
            self.state.status = "Приостановлено"
            self._emit("state", self.state)

    def resume(self) -> None:
        if self.is_running:
            self._pause_event.clear()
            self.state.status = "Работает"
            self._emit("state", self.state)

    def suspend_for(self, minutes: int) -> None:
        self._suspend_until = datetime.now() + __import__("datetime").timedelta(minutes=minutes)
        self.pause()

    def stop(self, wait: bool = True) -> list[Path]:
        self._stop_event.set()
        if wait and self._thread:
            self._thread.join(timeout=max(self.settings.interval_seconds + 30, 35))
        if self._thread and not self._thread.is_alive():
            self._thread = None
        self.state.status = "Остановлено"
        self.state.session_started_at = None
        self._emit("state", self.state)
        return sorted(self._touched_day_dirs)

    def _day_paths(self) -> dict[str, Path | str]:
        date_str = datetime.now().strftime("%Y-%m-%d")
        day_dir = Path(self.settings.base_directory) / date_str
        day_dir.mkdir(parents=True, exist_ok=True)
        if self.settings.save_screenshots:
            (day_dir / "screenshots").mkdir(parents=True, exist_ok=True)
        return {"date_str": date_str, "day_dir": day_dir, "jsonl_log": day_dir / f"ocr_log_{date_str}.jsonl"}

    def _run(self) -> None:
        while not self._stop_event.is_set():
            started = time.perf_counter()
            if self._suspend_until and datetime.now() >= self._suspend_until:
                self._suspend_until = None
                self.resume()
            if self._pause_event.is_set():
                time.sleep(0.5)
                continue
            try:
                self._capture_once()
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("Capture iteration failed")
                self.state.status = "Ошибка"
                self.state.last_error = str(exc)
                self._emit("state", self.state)
            elapsed = time.perf_counter() - started
            self._stop_event.wait(max(0, self.settings.interval_seconds - elapsed))

    def _duplicate_info(self, key: str, image_hash: str) -> tuple[bool, int]:
        previous_hash = self._last_hashes.get(key)
        if previous_hash == image_hash:
            self._duplicate_counts[key] = self._duplicate_counts.get(key, 0) + 1
            is_duplicate = True
        else:
            self._duplicate_counts[key] = 0
            is_duplicate = False
        self._last_hashes[key] = image_hash
        return is_duplicate, self._duplicate_counts[key]

    def _append_jsonl_log(self, jsonl_log: Path, record: dict[str, Any]) -> None:
        safe_record = sanitize_record(record)
        serialized = json.dumps(safe_record, ensure_ascii=False)
        jsonl_log.parent.mkdir(parents=True, exist_ok=True)
        with jsonl_log.open("a+", encoding="utf-8") as handle:
            handle.seek(0, 2)
            if handle.tell() > 0:
                handle.seek(handle.tell() - 1)
                if handle.read(1) != "\n":
                    handle.write("\n")
            handle.write(serialized + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _capture_once(self) -> None:
        paths = self._day_paths()
        day_dir = Path(paths["day_dir"])
        jsonl_log = Path(paths["jsonl_log"])
        self._touched_day_dirs.add(day_dir)
        self.state.current_day_dir = day_dir
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        active_window = get_active_window_title()
        capture_result = capture_screens()
        if capture_result.all_image is None:
            self._capture_failures += 1
            locked = self._capture_failures >= 3
            record = self._base_record(timestamp, active_window, "capture_error")
            record.update({"ocr_error": capture_result.error, "computer_locked": locked, "capture_duration_ms": capture_result.duration_ms})
            self._append_jsonl_log(jsonl_log, record)
            self.state.last_error = capture_result.error or "Ошибка захвата экрана"
            self.state.computer_locked = locked
            self.state.records_count += 1
            self._emit("state", self.state)
            return
        self._capture_failures = 0
        self.state.computer_locked = False
        if self.settings.selected_monitor_indices:
            selected = set(self.settings.selected_monitor_indices)
            capture_result.monitors = [monitor for monitor in capture_result.monitors if monitor.index in selected]
        all_hash = get_image_hash(capture_result.all_image)
        all_is_duplicate, all_duplicate_count = self._duplicate_info("all", all_hash)
        screenshot_all_path = None
        monitors_result: list[dict[str, Any]] = []
        all_text_parts: list[str] = []
        ocr_started = time.perf_counter()
        for monitor in capture_result.monitors:
            monitor_hash = get_image_hash(monitor.image)
            is_duplicate, duplicate_count = self._duplicate_info(f"monitor_{monitor.index}", monitor_hash)
            text, ocr_error = perform_ocr(monitor.image, self.settings.ocr_language)
            if text:
                all_text_parts.append(f"[MONITOR {monitor.index}]\n{text}")
            monitors_result.append({
                "index": monitor.index,
                "status": build_status(text, is_duplicate, ocr_error),
                "info": monitor.info,
                "text": text,
                "text_len": len(text),
                "ocr_error": ocr_error,
                "screenshot": None,
                "image_hash": monitor_hash,
                "is_duplicate": is_duplicate,
                "duplicate_count": duplicate_count,
            })
        if self.settings.ocr_by_monitors:
            final_text, final_ocr_error = "\n\n".join(all_text_parts).strip(), None
        else:
            final_text, final_ocr_error = perform_ocr(capture_result.all_image, self.settings.ocr_language)
        # final_text already contains either the combined per-monitor OCR or the
        # virtual-desktop OCR. Feeding all_text_parts a second time inflated link
        # and vault repeat counters for one physical capture.
        detection_text = "\n".join(part for part in (active_window, final_text) if part)
        findings = detect_secrets(detection_text)
        if findings and self.settings.privacy_handling == "vault":
            try:
                SecretVault(self.settings.secret_vault_path).add(findings, active_window)
            except Exception:  # noqa: BLE001
                LOGGER.exception("Unable to update encrypted secret vault; values were still redacted")
        omit_capture = bool(findings and self.settings.privacy_handling == "drop")
        skip_sensitive_screenshots = bool(findings and self.settings.privacy_skip_screenshots_on_detection)
        if omit_capture:
            final_text = SECRET_LINE_MARKER
            for monitor_record in monitors_result:
                monitor_record["text"] = SECRET_LINE_MARKER
                monitor_record["text_len"] = 0
        if not skip_sensitive_screenshots:
            screenshot_all_path = self._save_all_screenshot(day_dir, timestamp, capture_result.all_image)
            for monitor, monitor_record in zip(capture_result.monitors, monitors_result):
                screenshot_path = self._save_monitor_screenshot(day_dir, timestamp, monitor)
                monitor_record["screenshot"] = str(screenshot_path) if screenshot_path else None
        record = self._base_record(timestamp, active_window, build_status(final_text, all_is_duplicate, final_ocr_error))
        record.update({
            "text": final_text,
            "text_len": len(final_text),
            "ocr_error": final_ocr_error,
            "screenshot_all": str(screenshot_all_path) if screenshot_all_path else None,
            "image_hash_all": all_hash,
            "is_duplicate_all": all_is_duplicate,
            "duplicate_count_all": all_duplicate_count,
            "monitors": monitors_result,
            "monitor_count": len(monitors_result),
            "capture_duration_ms": capture_result.duration_ms,
            "ocr_duration_ms": int((time.perf_counter() - ocr_started) * 1000),
            "computer_locked": False,
            "sensitive_data_detected": bool(findings),
            "sensitive_data_categories": sorted({finding.category for finding in findings}),
            "sensitive_capture_omitted": omit_capture,
        })
        self._append_jsonl_log(jsonl_log, record)
        if findings:
            self._emit("privacy", {"categories": sorted({finding.category for finding in findings}), "mode": self.settings.privacy_handling})
        self.state.active_window = active_window
        self.state.monitor_count = len(monitors_result)
        self.state.last_successful_ocr_at = datetime.now()
        self.state.records_count += 1
        self.state.last_error = final_ocr_error or ""
        self.state.status = "Работает"
        self._emit("state", self.state)

    def _base_record(self, timestamp: str, active_window: str, status: str) -> dict[str, Any]:
        return {
            "timestamp": timestamp,
            "status": status,
            "active_window": active_window,
            "text": "",
            "text_len": 0,
            "ocr_error": None,
            "screenshot_all": None,
            "image_hash_all": "",
            "is_duplicate_all": False,
            "duplicate_count_all": 0,
            "monitors": [],
            "session_id": self._session_id,
            "application_version": APP_VERSION,
            "screenshot_saving_enabled": self.settings.save_screenshots,
            "paused": self.is_paused,
            "computer_locked": False,
        }

    def _save_all_screenshot(self, day_dir: Path, timestamp: str, image: Image.Image) -> Path | None:
        if not self.settings.save_all_screenshot:
            return None
        target_dir = day_dir / "screenshots" / "all"
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{timestamp}_all.png"
        image.save(path)
        return path

    def _save_monitor_screenshot(self, day_dir: Path, timestamp: str, monitor: MonitorCapture) -> Path | None:
        if not self.settings.save_monitor_screenshots:
            return None
        target_dir = day_dir / "screenshots" / f"monitor_{monitor.index}"
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{timestamp}_monitor_{monitor.index}.png"
        monitor.image.save(path)
        return path

    def _emit(self, event_type: str, payload: Any) -> None:
        self.events.put({"type": event_type, "payload": payload})
