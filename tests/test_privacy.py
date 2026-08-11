import json

from app.privacy import redact_secrets, sanitize_record
from app.settings import AppSettings
from app.capture_service import CaptureService


def test_redact_secrets_removes_passwords_tokens_and_api_keys():
    text = "login=admin password=qwerty api_key=sk-1234567890abcdef123456 token: abcdef1234567890abcdef123456"
    redacted = redact_secrets(text)
    assert "qwerty" not in redacted
    assert "sk-1234567890abcdef123456" not in redacted
    assert "abcdef1234567890abcdef123456" not in redacted
    assert "[REDACTED_SECRET" in redacted


def test_sanitize_record_redacts_nested_monitor_text():
    record = {"text": "password=secret123", "monitors": [{"text": "Authorization: Bearer abcdef1234567890abcdef"}]}
    safe = sanitize_record(record)
    dumped = json.dumps(safe)
    assert "secret123" not in dumped
    assert "abcdef1234567890abcdef" not in dumped


def test_capture_service_does_not_write_secrets_to_jsonl(tmp_path):
    settings = AppSettings(base_directory=str(tmp_path), privacy_mode_enabled=True)
    service = CaptureService(settings, __import__("queue").Queue())
    target = tmp_path / "ocr.jsonl"
    service._append_jsonl_log(target, {"text": "username=bob password=hunter2", "screenshot_all": "screenshots/raw.png"})
    data = target.read_text(encoding="utf-8")
    assert "hunter2" not in data
    assert "bob" not in data
    assert "screenshots/raw.png" in data


def test_capture_service_appends_each_record_on_separate_jsonl_line(tmp_path):
    settings = AppSettings(base_directory=str(tmp_path), privacy_mode_enabled=True)
    service = CaptureService(settings, __import__("queue").Queue())
    target = tmp_path / "ocr.jsonl"
    service._append_jsonl_log(target, {"timestamp": "1", "text": "first"})
    service._append_jsonl_log(target, {"timestamp": "2", "text": "second"})
    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["timestamp"] == "1"
    assert json.loads(lines[1])["timestamp"] == "2"


def test_capture_service_repairs_missing_newline_before_append(tmp_path):
    settings = AppSettings(base_directory=str(tmp_path), privacy_mode_enabled=True)
    service = CaptureService(settings, __import__("queue").Queue())
    target = tmp_path / "ocr.jsonl"
    target.write_text('{"timestamp":"old"}', encoding="utf-8")
    service._append_jsonl_log(target, {"timestamp": "new", "text": "next"})
    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["timestamp"] == "new"
