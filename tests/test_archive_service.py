import json
from datetime import datetime
from zipfile import ZipFile

from app.archive_service import archive_day, safe_archive_name, validate_jsonl


def test_validate_jsonl(tmp_path):
    file = tmp_path / "ok.jsonl"
    file.write_text(json.dumps({"a": 1}) + "\n", encoding="utf-8")
    assert validate_jsonl(file) == (True, "")
    bad = tmp_path / "bad.jsonl"
    bad.write_text("{bad}\n", encoding="utf-8")
    assert validate_jsonl(bad)[0] is False


def test_safe_archive_name():
    assert safe_archive_name("2026-07-17", datetime(2026, 7, 17, 8, 9, 10)) == "2026-07-17_08-09-10.zip"


def test_archive_day_contains_jsonl_template_and_transcripts(tmp_path):
    base = tmp_path / "ocr_days"
    day = base / "2026-07-17"
    day.mkdir(parents=True)
    (day / "screenshots").mkdir()
    (day / "screenshots" / "screen.png").write_text("not image", encoding="utf-8")
    jsonl = day / "ocr_log_2026-07-17.jsonl"
    jsonl.write_text(json.dumps({"timestamp": "x"}) + "\n", encoding="utf-8")
    template = tmp_path / "report_template.txt"
    template.write_text("template", encoding="utf-8")
    transcripts = day / "transcripts"
    transcripts.mkdir()
    (transcripts / "call.txt").write_text("hello", encoding="utf-8")
    (day / "transcripts_2026-07-17.jsonl").write_text(json.dumps({"type": "transcription"}) + "\n", encoding="utf-8")
    (day / "manual_work_log_2026-07-17.jsonl").write_text(json.dumps({"type": "work_entry"}) + "\n", encoding="utf-8")
    result = archive_day(day, template, now=datetime(2026, 7, 17, 8, 9, 10))
    assert result.created
    with ZipFile(result.path) as archive:
        assert sorted(archive.namelist()) == [
            "manual_work_log_2026-07-17.jsonl",
            "ocr_log_2026-07-17.jsonl",
            "report_template.txt",
            "transcripts/call.txt",
            "transcripts_2026-07-17.jsonl",
        ]


def test_archive_day_uses_custom_archive_directory(tmp_path):
    base = tmp_path / "ocr_days"
    day = base / "2026-07-18"
    day.mkdir(parents=True)
    (day / "ocr_log_2026-07-18.jsonl").write_text(json.dumps({"timestamp": "x"}) + "\n", encoding="utf-8")
    archive_dir = tmp_path / "custom_archives"

    result = archive_day(day, tmp_path / "missing_template.txt", now=datetime(2026, 7, 18, 8, 9, 10), archive_directory=archive_dir)

    assert result.created
    assert result.path is not None
    assert result.path.parent == archive_dir
