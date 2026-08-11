from __future__ import annotations

import json
import logging
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Callable, Iterable

from .privacy import SECRET_LINE_MARKER, detect_secrets, redact_secrets, sanitize_record
from .paths import resource_path
from .secret_vault import SecretVault
from .settings import AppSettings

LOGGER = logging.getLogger(__name__)
SUPPORTED_MEDIA_EXTENSIONS = {".mp3", ".mp4", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".webm", ".mov", ".mkv", ".avi"}
DEFAULT_TRANSCRIBER_DIR = Path("tools/local_call_transcriber_v3")
TRANSCRIBER_ARCHIVE_NAME = "local_call_transcriber_v3 — копия.zip"
ProgressCallback = Callable[[float, str], None]




@dataclass(slots=True)
class NormalizedTranscription:
    text: str = ""
    segments: list[str] | None = None
    language: str = ""
    duration: float | None = None
    source_path: Path | None = None


def _segment_text(segment: object) -> str:
    if isinstance(segment, dict):
        return str(segment.get("text") or "").strip()
    return str(getattr(segment, "text", "") or "").strip()


def extract_transcription_text(result: object) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        if "text" in result and result.get("text") is not None:
            return str(result.get("text") or "")
        segments = result.get("segments")
        if isinstance(segments, (list, tuple)):
            return "\n".join(text for text in (_segment_text(segment) for segment in segments) if text)
        return ""
    text = getattr(result, "text", None)
    if text is not None:
        return str(text or "")
    segments = getattr(result, "segments", None)
    if isinstance(segments, (list, tuple)):
        return "\n".join(text for text in (_segment_text(segment) for segment in segments) if text)
    transcript_path = getattr(result, "transcript_path", None)
    if transcript_path:
        path = Path(transcript_path)
        if path.exists():
            return path.read_text(encoding="utf-8", errors="replace")
    return ""


def normalize_transcription_result(result: object, source_path: Path | None = None) -> NormalizedTranscription:
    text = extract_transcription_text(result)
    raw_segments = result.get("segments") if isinstance(result, dict) else getattr(result, "segments", None)
    segments = None
    if isinstance(raw_segments, (list, tuple)):
        segments = [part for part in (_segment_text(segment) for segment in raw_segments) if part]
    language = str(result.get("language", "") if isinstance(result, dict) else getattr(result, "language", "") or "")
    duration = result.get("duration") if isinstance(result, dict) else getattr(result, "duration", None)
    return NormalizedTranscription(text=text, segments=segments, language=language, duration=duration, source_path=source_path)

@dataclass(slots=True)
class TranscriptionResult:
    success: bool
    transcript_path: Path | None = None
    index_path: Path | None = None
    jsonl_path: Path | None = None
    message: str = ""


def is_supported_media(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_MEDIA_EXTENSIONS


def today_paths(settings: AppSettings, now: datetime | None = None) -> dict[str, Path | str]:
    current = now or datetime.now()
    date_str = current.strftime("%Y-%m-%d")
    day_dir = Path(settings.base_directory) / date_str
    return {
        "date_str": date_str,
        "day_dir": day_dir,
        "transcripts_dir": day_dir / "transcripts",
        "transcripts_index": day_dir / f"transcripts_{date_str}.jsonl",
        "ocr_jsonl": day_dir / f"ocr_log_{date_str}.jsonl",
    }


def can_use_bundled_transcriber(settings: AppSettings) -> bool:
    return not settings.transcriber_command_template.strip() and Path(settings.transcriber_script_path) == DEFAULT_TRANSCRIBER_DIR


def find_transcriber_entry(settings: AppSettings) -> Path | None:
    configured = Path(settings.transcriber_script_path)
    candidates = [
        configured,
        resource_path(configured) if not configured.is_absolute() else configured,
        DEFAULT_TRANSCRIBER_DIR,
        resource_path(DEFAULT_TRANSCRIBER_DIR),
    ]
    visited: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in visited:
            continue
        visited.add(candidate)
        if candidate.is_file():
            return candidate
        if candidate.is_dir():
            for name in ("main.py", "transcribe.py", "local_call_transcriber.py", "app.py"):
                script = candidate / name
                if script.exists():
                    return script
    return None


def append_jsonl(path: Path, record: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(sanitize_record(record), ensure_ascii=False) + "\n")


def protect_persisted_text(text: str, settings: AppSettings, context: str = "") -> str:
    findings = detect_secrets(text)
    if not findings:
        return text
    if settings.privacy_handling == "vault":
        try:
            SecretVault(settings.secret_vault_path).add(findings, context)
        except Exception:  # noqa: BLE001
            LOGGER.exception("Unable to update encrypted secret vault; transcript was still redacted")
    if settings.privacy_handling == "drop":
        return SECRET_LINE_MARKER
    return redact_secrets(text)


def build_transcriber_command(entry: Path, media_path: Path, output_path: Path, settings: AppSettings) -> list[str]:
    template = settings.transcriber_command_template.strip()
    if template:
        return [part.format(input=str(media_path), output=str(output_path), script=str(entry)) for part in shlex.split(template)]
    if entry.suffix.lower() == ".py":
        return [sys.executable, str(entry), str(media_path), "--output", str(output_path)]
    return [str(entry), str(media_path), "--output", str(output_path)]


def transcribe_media_file(
    media_path: Path,
    settings: AppSettings,
    now: datetime | None = None,
    progress_callback: ProgressCallback | None = None,
    cancel_event: Event | None = None,
) -> TranscriptionResult:
    source = Path(media_path)
    if not source.exists():
        return TranscriptionResult(False, message=f"Файл не найден: {source}")
    if not is_supported_media(source):
        return TranscriptionResult(False, message=f"Неподдерживаемый формат: {source.suffix}")
    if not settings.transcription_enabled:
        return TranscriptionResult(False, message="Транскрибация выключена в настройках.")

    paths = today_paths(settings, now)
    transcripts_dir = Path(paths["transcripts_dir"])
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    output_path = transcripts_dir / f"{timestamp}_{source.stem}.txt"
    record = {
        "type": "transcription",
        "timestamp": (now or datetime.now()).isoformat(timespec="seconds"),
        "status": "queued",
        "source_file": str(source),
        "transcript_file": str(output_path),
    }
    append_jsonl(Path(paths["transcripts_index"]), record)

    entry = find_transcriber_entry(settings)
    if entry is None:
        message = "Компонент транскрибации отсутствует или повреждён. Переустановите приложение или восстановите стандартные настройки."
        record.update({"status": "error", "error": message})
        append_jsonl(Path(paths["transcripts_index"]), record)
        return TranscriptionResult(False, output_path, Path(paths["transcripts_index"]), Path(paths["ocr_jsonl"]), message)

    record.update({"status": "processing", "transcriber_entry": str(entry), "model": settings.transcriber_model_name, "models_dir": settings.transcriber_models_dir})
    append_jsonl(Path(paths["transcripts_index"]), record)
    try:
        if can_use_bundled_transcriber(settings):
            from tools.local_call_transcriber_v3 import transcribe_file

            transcribe_file(
                source,
                output_path,
                model_name=settings.transcriber_model_name,
                models_dir=settings.transcriber_models_dir,
                progress_callback=progress_callback,
                cancel_event=cancel_event,
                text_processor=lambda text: protect_persisted_text(text, settings, str(source)),
            )
        else:
            command = build_transcriber_command(entry, source, output_path, settings)
            completed = subprocess.run(command, cwd=str(entry.parent), text=True, capture_output=True, timeout=settings.transcriber_timeout_seconds, check=False)
            if completed.returncode != 0:
                message = (completed.stderr or completed.stdout or f"Код выхода {completed.returncode}").strip()
                record.update({"status": "error", "error": message[-4000:]})
                append_jsonl(Path(paths["transcripts_index"]), record)
                return TranscriptionResult(False, output_path, Path(paths["transcripts_index"]), Path(paths["ocr_jsonl"]), message)
            if not output_path.exists():
                output_path.write_text(completed.stdout, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        cancelled = "отменена пользователем" in str(exc).lower()
        if cancelled:
            LOGGER.info("Transcription cancelled")
        else:
            LOGGER.exception("Transcription failed")
        message = f"Ошибка транскрибации: {exc}"
        record.update({"status": "cancelled" if cancelled else "error", "error": message})
        append_jsonl(Path(paths["transcripts_index"]), record)
        return TranscriptionResult(False, output_path, Path(paths["transcripts_index"]), Path(paths["ocr_jsonl"]), message)
    text = protect_persisted_text(output_path.read_text(encoding="utf-8", errors="replace"), settings, str(source))
    output_path.write_text(text, encoding="utf-8")
    done_record = {
        "type": "transcription",
        "timestamp": (now or datetime.now()).isoformat(timespec="seconds"),
        "status": "ready",
        "source_file": str(source),
        "transcript_file": str(output_path),
        "text": text,
    }
    append_jsonl(Path(paths["transcripts_index"]), done_record)
    append_jsonl(Path(paths["ocr_jsonl"]), done_record)
    return TranscriptionResult(True, output_path, Path(paths["transcripts_index"]), Path(paths["ocr_jsonl"]), "Транскрибация завершена.")


def transcribe_media_files(media_paths: Iterable[Path], settings: AppSettings) -> list[TranscriptionResult]:
    return [transcribe_media_file(path, settings) for path in media_paths]
