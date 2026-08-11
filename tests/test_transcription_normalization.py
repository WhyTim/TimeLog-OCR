from dataclasses import dataclass
from pathlib import Path

from app.transcription_service import TranscriptionResult, extract_transcription_text, normalize_transcription_result


@dataclass
class TextResult:
    text: str


@dataclass
class Segment:
    text: str


@dataclass
class SegmentResult:
    segments: list[Segment]
    language: str = "ru"
    duration: float = 1.5


def test_extract_transcription_text_from_string():
    assert extract_transcription_text("готовый текст") == "готовый текст"


def test_extract_transcription_text_from_text_attribute():
    assert extract_transcription_text(TextResult("текст атрибутом")) == "текст атрибутом"


def test_extract_transcription_text_from_segments_objects():
    assert extract_transcription_text(SegmentResult([Segment("первый"), Segment("второй")])) == "первый\nвторой"


def test_extract_transcription_text_from_segments_dicts():
    assert extract_transcription_text({"segments": [{"text": "один"}, {"text": "два"}]}) == "один\nдва"


def test_extract_transcription_text_from_empty_or_invalid():
    assert extract_transcription_text(None) == ""
    assert extract_transcription_text(object()) == ""


def test_normalize_transcription_result_reads_transcription_result_file(tmp_path):
    transcript = tmp_path / "result.txt"
    transcript.write_text("текст из файла", encoding="utf-8")
    normalized = normalize_transcription_result(TranscriptionResult(True, transcript_path=transcript), source_path=Path("source.mp4"))
    assert normalized.text == "текст из файла"
    assert normalized.source_path == Path("source.mp4")
