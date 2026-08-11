from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
import queue
from threading import Event

import pytest

from app.link_journal import LinkJournal, extract_links, normalize_url
from app.models import CaptureResult, MonitorCapture
from app.privacy import REDACTION, detect_secrets, redact_secrets
from app.scheduler import in_schedule_window, next_schedule_start
from app.secret_vault import SecretVault
from app.settings import AppSettings, load_settings
from app.transcription_service import protect_persisted_text
from tools.local_call_transcriber_v3 import transcribe as transcriber


SYNTHETIC_SECRET = "sk-proj-abcdefghijklmnopqrstuvwxyz123456"


def test_secret_detection_masks_supported_synthetic_values():
    source = (
        f"email=user@example.com password=hunter-two api_key={SYNTHETIC_SECRET} "
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456 "
        "AWS=AKIAIOSFODNN7EXAMPLE phone=+7 (999) 123-45-67"
    )
    findings = detect_secrets(source)
    categories = {finding.category for finding in findings}
    assert {"email", "credential", "openai_api_key", "authorization", "aws_access_key", "phone"} <= categories
    redacted = redact_secrets(source)
    for forbidden in ("user@example.com", "hunter-two", SYNTHETIC_SECRET, "AKIAIOSFODNN7EXAMPLE", "123-45-67"):
        assert forbidden not in redacted


def test_secret_detection_does_not_mask_normal_prose():
    text = "Проверена документация проекта и сформирован ежедневный отчёт без реквизитов доступа."
    assert detect_secrets(text) == []
    assert redact_secrets(text) == text


def test_dpapi_style_vault_never_writes_plaintext_and_supports_delete(tmp_path):
    key = 0xA7
    crypt = lambda value: bytes(byte ^ key for byte in value)
    vault = SecretVault(tmp_path / "secrets.vault", crypt, crypt)
    findings = detect_secrets(f"api_key={SYNTHETIC_SECRET}")
    assert vault.add(findings, "synthetic test") == 1
    assert vault.add(findings, "synthetic test repeated") == 0
    assert SYNTHETIC_SECRET.encode() not in vault.path.read_bytes()
    masked = vault.list_masked()
    assert len(masked) == 1 and "value" not in masked[0] and masked[0]["count"] == "2"
    assert vault.delete(masked[0]["id"])
    assert not vault.path.exists()


def test_transcript_privacy_modes_use_masking_drop_and_vault(tmp_path):
    settings = AppSettings(base_directory=str(tmp_path), secret_vault_path=str(tmp_path / "vault"))
    assert SYNTHETIC_SECRET not in protect_persisted_text(f"token={SYNTHETIC_SECRET}", settings)
    settings.privacy_handling = "drop"
    assert "SECRET_LINE" in protect_persisted_text(f"token={SYNTHETIC_SECRET}", settings)


def test_links_are_normalized_sanitized_and_deduplicated(tmp_path):
    text = "Откройте https : //Example.COM/path?token=synthetic-secret&view=all, затем https://example.com/path?token=other&view=all."
    links = extract_links(text)
    assert links == ["https://example.com/path?token=[REDACTED]&view=all"]
    journal = LinkJournal(tmp_path / "links-2026-08-08.md")
    journal.add_from_text(text, "Browser", datetime(2026, 8, 8, 9, 0))
    journal.add_from_text(text, "Browser", datetime(2026, 8, 8, 9, 1))
    saved = journal.path.read_text(encoding="utf-8")
    assert "synthetic-secret" not in saved and "token=other" not in saved
    assert "| 2 |" in saved


def test_invalid_or_credential_urls_are_safely_normalized():
    assert normalize_url("file:///etc/passwd") is None
    assert normalize_url("https://user:password@example.com/") == "https://example.com/"


def test_overnight_schedule_uses_previous_selected_day():
    settings = AppSettings(scheduled_workdays=[0], scheduled_start_time="22:00", scheduled_stop_time="06:00")
    assert in_schedule_window(datetime(2026, 8, 3, 23, 0), settings)  # Monday
    assert in_schedule_window(datetime(2026, 8, 4, 2, 0), settings)   # Tuesday belongs to Monday window
    assert not in_schedule_window(datetime(2026, 8, 5, 2, 0), settings)


def test_next_schedule_respects_days_and_manual_pause():
    settings = AppSettings(scheduled_capture_enabled=True, scheduled_workdays=[0], scheduled_start_time="08:30")
    assert next_schedule_start(datetime(2026, 8, 2, 12, 0), settings) == datetime(2026, 8, 3, 8, 30)
    settings.schedule_manual_pause = True
    assert next_schedule_start(datetime(2026, 8, 2, 12, 0), settings) is None


def test_old_settings_receive_second_audit_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"interval_seconds": 10, "scheduled_workdays": [1, 3]}), encoding="utf-8")
    settings = load_settings(path)
    assert settings.scheduled_workdays == [1, 3]
    assert settings.privacy_handling == "redact"
    assert settings.save_detected_links is False
    assert settings.schedule_manual_pause is False


def test_legacy_transcriber_path_is_migrated_to_bundled_default(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"transcriber_script_path": "missing/local_call_transcriber_v3 — копия.zip"}), encoding="utf-8")
    settings = load_settings(path)
    assert settings.transcriber_script_path == "tools/local_call_transcriber_v3"


def test_capture_uses_only_selected_monitors(tmp_path, monkeypatch):
    from PIL import Image
    from app.capture_service import CaptureService
    import app.capture_service as capture_module

    image = Image.new("RGB", (20, 20), "white")
    capture = CaptureResult(image, [
        MonitorCapture(1, {"left": 0, "top": 0, "width": 20, "height": 20}, image),
        MonitorCapture(2, {"left": 20, "top": 0, "width": 20, "height": 20}, image),
    ])
    monkeypatch.setattr(capture_module, "capture_screens", lambda: capture)
    monkeypatch.setattr(capture_module, "perform_ocr", lambda *_args, **_kwargs: ("safe text", None))
    settings = AppSettings(base_directory=str(tmp_path), selected_monitor_indices=[2])
    CaptureService(settings, queue.Queue())._capture_once()
    saved = json.loads(next(tmp_path.rglob("*.jsonl")).read_text(encoding="utf-8").splitlines()[-1])
    assert saved["monitor_count"] == 1
    assert [monitor["index"] for monitor in saved["monitors"]] == [2]


def test_model_download_returns_real_snapshot_and_reports_progress(tmp_path, monkeypatch):
    snapshot = tmp_path / "models--Systran--faster-whisper-tiny" / "snapshots" / "revision"
    snapshot.mkdir(parents=True)
    for name in ("config.json", "model.bin", "tokenizer.json"):
        (snapshot / name).write_bytes(b"ok")
    progress = []
    result = transcriber.ensure_model("tiny", tmp_path, lambda value, message: progress.append((value, message)))
    assert result == snapshot
    assert progress[-1][0] == 1.0


def test_model_download_can_be_cancelled(tmp_path, monkeypatch):
    cancelled = Event(); cancelled.set()
    monkeypatch.setattr(transcriber, "_remote_model_files", lambda _repo: [("config.json", 2), ("model.bin", 2), ("tokenizer.json", 2)])
    with pytest.raises(transcriber.TranscriptionCancelled):
        transcriber.ensure_model("tiny", tmp_path, cancel_event=cancelled)


def test_model_download_resumes_the_same_part_file(tmp_path, monkeypatch):
    payloads = {"config.json": b"{}", "model.bin": b"m" * (2 * 1024 * 1024), "tokenizer.json": b"[]"}
    files = [(name, len(value)) for name, value in payloads.items()]
    cancelled = Event()
    interrupt_once = [True]

    class Response:
        def __init__(self, data: bytes, start: int, interrupt: bool):
            self.data = data[start:]
            self.position = 0
            self.status = 206 if start else 200
            self.interrupt = interrupt

        def __enter__(self): return self
        def __exit__(self, *_args): return False

        def read(self, size: int) -> bytes:
            if self.position >= len(self.data):
                return b""
            chunk = self.data[self.position:self.position + size]
            self.position += len(chunk)
            if self.interrupt and self.position >= 1024 * 1024:
                cancelled.set()
                interrupt_once[0] = False
            return chunk

    def fake_urlopen(request, timeout=0):
        filename = request.full_url.split("/resolve/main/", 1)[1].split("?", 1)[0]
        start = int((request.get_header("Range") or "bytes=0-").split("=")[1].split("-")[0])
        return Response(payloads[filename], start, filename == "model.bin" and interrupt_once[0])

    monkeypatch.setattr(transcriber, "_remote_model_files", lambda _repo: files)
    monkeypatch.setattr(transcriber, "urlopen", fake_urlopen)
    with pytest.raises(transcriber.TranscriptionCancelled):
        transcriber.ensure_model("tiny", tmp_path, cancel_event=cancelled)
    part = tmp_path / "tiny" / "model.bin.part"
    assert part.stat().st_size == 1024 * 1024
    cancelled.clear()
    model = transcriber.ensure_model("tiny", tmp_path, cancel_event=cancelled)
    assert (model / "model.bin").stat().st_size == len(payloads["model.bin"])
    assert not part.exists()


def test_sensitive_capture_skips_images_and_sanitizes_all_outputs(tmp_path, monkeypatch):
    from PIL import Image
    from app.capture_service import CaptureService
    import app.capture_service as capture_module

    image = Image.new("RGB", (20, 20), "white")
    capture = CaptureResult(image, [MonitorCapture(1, {"left": 0, "top": 0, "width": 20, "height": 20}, image)])
    monkeypatch.setattr(capture_module, "capture_screens", lambda: capture)
    monkeypatch.setattr(capture_module, "perform_ocr", lambda *_args, **_kwargs: (f"api_key={SYNTHETIC_SECRET} https://example.test/?token={SYNTHETIC_SECRET}", None))
    settings = AppSettings(
        base_directory=str(tmp_path),
        save_all_screenshot=True,
        save_monitor_screenshots=True,
        privacy_handling="redact",
        privacy_skip_screenshots_on_detection=True,
        save_detected_links=True,
    )
    service = CaptureService(settings, queue.Queue())
    service._capture_once()
    saved = "\n".join(path.read_text(encoding="utf-8") for path in tmp_path.rglob("*.jsonl"))
    links = "\n".join(path.read_text(encoding="utf-8") for path in tmp_path.rglob("*.md"))
    assert SYNTHETIC_SECRET not in saved and SYNTHETIC_SECRET not in links
    assert not list(tmp_path.rglob("*.png"))
    assert "sensitive_data_detected" in saved


def test_schedule_controls_are_exposed_and_manual_pause_persists(qtbot, tmp_path, monkeypatch):
    from app.ui.main_window import MainWindow
    import app.ui.main_window as window_module

    persistence_calls = []
    monkeypatch.setattr(window_module, "save_settings", lambda *_args: persistence_calls.append(_args))
    settings = AppSettings(
        base_directory=str(tmp_path / "ocr"),
        archive_directory=str(tmp_path / "archives"),
        database_path=str(tmp_path / "db.sqlite"),
        report_template_path=str(tmp_path / "report.txt"),
        transcriber_models_dir=str(tmp_path / "models"),
        scheduled_workdays=[0, 2, 4],
    )
    Path(settings.report_template_path).write_text("template", encoding="utf-8")
    window = MainWindow(settings)
    qtbot.addWidget(window)
    assert [check.isChecked() for check in window.weekday_checks] == [True, False, True, False, True, False, False]
    window._set_manual_schedule_pause(True)
    assert settings.schedule_manual_pause is True
    assert window.settings_widgets["schedule_manual_pause"].isChecked()
    window._save_ui_geometry()
    assert persistence_calls == []
    window.scheduler.stop()
