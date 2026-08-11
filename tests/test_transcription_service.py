from datetime import datetime

from app.settings import AppSettings
import app.transcription_service as transcription_service
from app.transcription_service import find_transcriber_entry, is_supported_media, transcribe_media_file


def test_supported_media_extensions():
    assert is_supported_media(__import__("pathlib").Path("call.mp4"))
    assert is_supported_media(__import__("pathlib").Path("call.mp3"))
    assert not is_supported_media(__import__("pathlib").Path("note.txt"))


def test_transcribe_reports_missing_local_script(tmp_path, monkeypatch):
    media = tmp_path / "call.mp3"
    media.write_bytes(b"fake")
    settings = AppSettings(base_directory=str(tmp_path / "ocr_days"), transcriber_script_path=str(tmp_path / "missing"))
    monkeypatch.setattr(transcription_service, "DEFAULT_TRANSCRIBER_DIR", tmp_path / "also_missing")
    result = transcribe_media_file(media, settings, now=datetime(2026, 7, 22, 8, 30, 0))
    assert not result.success
    assert "Компонент транскрибации отсутствует или повреждён" in result.message
    assert result.index_path is not None
    assert result.index_path.exists()


def test_default_transcriber_is_found_in_pyinstaller_resources(tmp_path, monkeypatch):
    bundled = tmp_path / "tools" / "local_call_transcriber_v3"
    bundled.mkdir(parents=True)
    entry = bundled / "transcribe.py"
    entry.write_text("# bundled test entry\n", encoding="utf-8")
    settings = AppSettings(transcriber_script_path="tools/local_call_transcriber_v3")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(transcription_service, "resource_path", lambda relative: tmp_path / relative)

    assert find_transcriber_entry(settings) == entry.resolve()
