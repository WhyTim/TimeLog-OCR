from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from threading import Event
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

DEFAULT_MODEL = "small"
SUPPORTED_OUTPUT_FORMATS = {"txt", "json"}
MODEL_CHOICES = ("tiny", "base", "small", "medium", "large-v3")
ProgressCallback = Callable[[float, str], None]
TextProcessor = Callable[[str], str]
MODEL_REPOSITORIES = {name: f"Systran/faster-whisper-{name}" for name in MODEL_CHOICES}
MODEL_APPROX_BYTES = {
    "tiny": 78_203_619,
    "base": 147_882_941,
    "small": 486_212_372,
    "medium": 1_530_571_735,
    "large-v3": 3_090_000_000,
}
MODEL_MEMORY_HINTS = {
    "tiny": "~1 ГБ ОЗУ",
    "base": "~1–2 ГБ ОЗУ",
    "small": "~2–3 ГБ ОЗУ",
    "medium": "~4–6 ГБ ОЗУ",
    "large-v3": "~6–10 ГБ ОЗУ",
}


class TranscriptionCancelled(RuntimeError):
    pass


def _format_bytes(value: float) -> str:
    units = ("Б", "КБ", "МБ", "ГБ", "ТБ")
    size = float(max(0, value))
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "Б" else f"{int(size)} {unit}"
        size /= 1024
    return f"{size:.1f} ТБ"


def model_download_requirements(model_name: str) -> tuple[int, str]:
    if model_name not in MODEL_REPOSITORIES:
        raise ValueError(f"Неподдерживаемая модель: {model_name}")
    return MODEL_APPROX_BYTES[model_name], MODEL_MEMORY_HINTS[model_name]


def _directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _model_cache_dir(models_dir: Path, model_name: str) -> Path:
    return models_dir / f"models--Systran--faster-whisper-{model_name}"


def _validate_model_path(path: Path) -> bool:
    required = ("config.json", "model.bin", "tokenizer.json")
    return path.is_dir() and all((path / name).is_file() and (path / name).stat().st_size > 0 for name in required)


def _find_cached_model(cache_dir: Path, model_name: str) -> Path | None:
    direct = cache_dir / model_name
    if _validate_model_path(direct):
        return direct
    snapshots = _model_cache_dir(cache_dir, model_name) / "snapshots"
    if snapshots.exists():
        for candidate in sorted(snapshots.iterdir(), key=lambda path: path.stat().st_mtime, reverse=True):
            if _validate_model_path(candidate):
                return candidate
    return None


def model_is_installed(model_name: str, models_dir: Path | str) -> bool:
    return _find_cached_model(Path(models_dir), model_name) is not None


def _remote_model_files(repo_id: str) -> list[tuple[str, int]]:
    api_url = f"https://huggingface.co/api/models/{quote(repo_id, safe='/')}?blobs=true"
    with urlopen(Request(api_url, headers={"User-Agent": "TimeLogOCR/1.5"}), timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    allowed = {"config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.txt", "vocabulary.json"}
    files = []
    for sibling in payload.get("siblings", []):
        name = str(sibling.get("rfilename", ""))
        size = int(sibling.get("size") or 0)
        if name in allowed and size > 0:
            files.append((name, size))
    if not {"config.json", "model.bin", "tokenizer.json"} <= {name for name, _size in files}:
        raise RuntimeError("Hugging Face не вернул обязательные файлы модели")
    return files


def ensure_model(
    model_name: str = DEFAULT_MODEL,
    models_dir: Path | str = "models",
    progress_callback: ProgressCallback | None = None,
    cancel_event: Event | None = None,
) -> Path:
    """Return a complete model or download it to a fixed resumable .part file."""
    cache_dir = Path(models_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    model_download_requirements(model_name)
    cached = _find_cached_model(cache_dir, model_name)
    if progress_callback:
        progress_callback(0.0 if cached is None else 1.0, f"Проверка файлов модели {model_name}")
    if cached is not None:
        if progress_callback:
            progress_callback(1.0, f"Модель {model_name} готова")
        return cached

    repo_id = MODEL_REPOSITORIES[model_name]
    try:
        remote_files = _remote_model_files(repo_id)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
        raise RuntimeError(f"Не удалось получить список файлов модели {model_name}: {exc}") from exc
    total_bytes = sum(size for _name, size in remote_files)
    target_dir = cache_dir / model_name
    target_dir.mkdir(parents=True, exist_ok=True)
    existing_bytes = 0
    for filename, size in remote_files:
        final = target_dir / filename
        part = target_dir / f"{filename}.part"
        if final.exists() and final.stat().st_size == size:
            existing_bytes += size
        elif part.exists():
            existing_bytes += min(part.stat().st_size, size)
    required_bytes = max(0, total_bytes - existing_bytes)
    free_bytes = shutil.disk_usage(cache_dir).free
    if required_bytes and free_bytes < required_bytes + 256 * 1024 * 1024:
        raise RuntimeError(
            f"Недостаточно места для {model_name}: требуется ещё примерно {_format_bytes(required_bytes)}, "
            f"свободно {_format_bytes(free_bytes)}."
        )
    started = time.monotonic()
    transferred_this_run = 0
    downloaded_total = existing_bytes
    last_report = 0.0

    def report(stage: str, force: bool = False) -> None:
        nonlocal last_report
        if not progress_callback:
            return
        now = time.monotonic()
        if not force and now - last_report < 0.25:
            return
        last_report = now
        elapsed = max(time.monotonic() - started, 0.001)
        speed = transferred_this_run / elapsed
        remaining = max(0, total_bytes - downloaded_total)
        eta = remaining / speed if speed > 1 else 0
        details = f"{_format_bytes(downloaded_total)} / {_format_bytes(total_bytes)}"
        if speed > 1:
            details += f" · {_format_bytes(speed)}/с · осталось ~{int(eta // 60):02d}:{int(eta % 60):02d}"
        progress_callback(min(0.99, downloaded_total / max(total_bytes, 1)), f"{stage}: {details}")

    token = os.getenv("HF_TOKEN") or os.getenv("HUGGING_FACE_HUB_TOKEN")
    headers = {"User-Agent": "TimeLogOCR/1.5"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        report("Подготовка загрузки", force=True)
        for filename, expected_size in remote_files:
            if cancel_event and cancel_event.is_set():
                raise TranscriptionCancelled("Загрузка модели отменена пользователем")
            final = target_dir / filename
            part = target_dir / f"{filename}.part"
            if final.exists() and final.stat().st_size == expected_size:
                continue
            if part.exists() and part.stat().st_size > expected_size:
                invalid_size = part.stat().st_size
                invalid = part.with_suffix(part.suffix + f".invalid-{int(time.time())}")
                part.replace(invalid)
                downloaded_total -= min(invalid_size, expected_size)
            offset = part.stat().st_size if part.exists() else 0
            request_headers = dict(headers)
            if offset:
                request_headers["Range"] = f"bytes={offset}-"
            url = f"https://huggingface.co/{repo_id}/resolve/main/{quote(filename)}?download=true"
            with urlopen(Request(url, headers=request_headers), timeout=60) as response:
                append = offset > 0 and getattr(response, "status", 200) == 206
                if offset and not append:
                    downloaded_total -= offset
                    offset = 0
                with part.open("ab" if append else "wb") as handle:
                    while True:
                        if cancel_event and cancel_event.is_set():
                            raise TranscriptionCancelled("Загрузка модели отменена пользователем")
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        handle.write(chunk)
                        transferred_this_run += len(chunk)
                        downloaded_total += len(chunk)
                        report(f"Загрузка {filename}")
                    handle.flush()
                    os.fsync(handle.fileno())
            if part.stat().st_size != expected_size:
                raise RuntimeError(
                    f"Неполный файл {filename}: {_format_bytes(part.stat().st_size)} из {_format_bytes(expected_size)}"
                )
            part.replace(final)
    except TranscriptionCancelled:
        raise
    except (HTTPError, URLError, TimeoutError, OSError, RuntimeError) as exc:
        raise RuntimeError(f"Не удалось скачать модель {model_name}: {exc}") from exc
    if cancel_event and cancel_event.is_set():
        raise TranscriptionCancelled("Загрузка модели отменена пользователем")
    if not _validate_model_path(target_dir):
        raise RuntimeError(f"Модель {model_name} загружена не полностью. Повторите загрузку для докачки файлов.")
    if progress_callback:
        progress_callback(1.0, f"Модель {model_name} готова: {_format_bytes(total_bytes)}")
    return target_dir


def transcribe_file(
    input_path: Path | str,
    output_path: Path | str,
    *,
    model_name: str = DEFAULT_MODEL,
    models_dir: Path | str = "models",
    language: str | None = None,
    output_format: str = "txt",
    progress_callback: ProgressCallback | None = None,
    cancel_event: Event | None = None,
    text_processor: TextProcessor | None = None,
) -> dict[str, object]:
    """Transcribe an audio/video file and write txt/json output."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("Не установлен faster-whisper. Выполните: pip install -r requirements.txt") from exc

    source = Path(input_path)
    target = Path(output_path)
    if not source.exists():
        raise FileNotFoundError(f"Файл не найден: {source}")
    if output_format not in SUPPORTED_OUTPUT_FORMATS:
        raise ValueError(f"Неподдерживаемый формат результата: {output_format}")

    cache_dir = Path(models_dir)
    if progress_callback:
        progress_callback(0.02, "Проверка/загрузка модели")
    model_path = ensure_model(model_name, cache_dir, progress_callback, cancel_event)
    if cancel_event and cancel_event.is_set():
        raise RuntimeError("Транскрибация отменена пользователем")
    if progress_callback:
        progress_callback(0.08, "Загрузка модели")
    model = WhisperModel(str(model_path), device="cpu", compute_type="int8", local_files_only=True)
    if progress_callback:
        progress_callback(0.12, "Распознавание")
    segments, info = model.transcribe(str(source), language=language, vad_filter=True)
    items = []
    text_parts = []
    duration = max(float(getattr(info, "duration", 0) or 0), 1.0)
    for segment in segments:
        if cancel_event and cancel_event.is_set():
            raise RuntimeError("Транскрибация отменена пользователем")
        if progress_callback:
            progress_callback(min(0.95, max(0.12, segment.end / duration)), f"Сегмент {segment.start:.1f}–{segment.end:.1f} сек")
        item = {"start": segment.start, "end": segment.end, "text": segment.text.strip()}
        items.append(item)
        text_parts.append(item["text"])
    result = {
        "source_file": str(source),
        "model": model_name,
        "language": getattr(info, "language", language),
        "duration": getattr(info, "duration", None),
        "segments": items,
        "text": "\n".join(part for part in text_parts if part),
    }
    if text_processor:
        result["text"] = text_processor(str(result["text"]))
    if progress_callback:
        progress_callback(0.98, "Сохранение результата")
    target.parent.mkdir(parents=True, exist_ok=True)
    if output_format == "json":
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        target.write_text(str(result["text"]), encoding="utf-8")
    if progress_callback:
        progress_callback(1.0, "Готово")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local TimeLog OCR audio/video transcriber")
    parser.add_argument("input", help="Audio/video file path")
    parser.add_argument("--output", "-o", required=True, help="Transcript output file")
    parser.add_argument("--model", choices=MODEL_CHOICES, default=DEFAULT_MODEL, help="faster-whisper model name, default: small")
    parser.add_argument("--models-dir", default="models", help="Directory for downloaded models")
    parser.add_argument("--language", default=None, help="Optional language code, e.g. ru/en")
    parser.add_argument("--format", choices=sorted(SUPPORTED_OUTPUT_FORMATS), default="txt", help="Output format")
    args = parser.parse_args(argv)
    transcribe_file(args.input, args.output, model_name=args.model, models_dir=args.models_dir, language=args.language, output_format=args.format)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
