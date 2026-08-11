from __future__ import annotations

import hashlib
import logging
import subprocess
from pathlib import Path

import pytesseract
from PIL import Image, ImageOps

LOGGER = logging.getLogger(__name__)
OCR_CONFIG = "--oem 3 --psm 6"

try:
    RESAMPLE = Image.Resampling.LANCZOS
except AttributeError:
    RESAMPLE = Image.LANCZOS


def configure_tesseract(tesseract_path: str) -> None:
    pytesseract.pytesseract.tesseract_cmd = tesseract_path


def check_tesseract(tesseract_path: str, language: str) -> list[str]:
    warnings: list[str] = []
    if not Path(tesseract_path).exists():
        warnings.append(f"Tesseract не найден: {tesseract_path}")
        return warnings
    configure_tesseract(tesseract_path)
    try:
        output = subprocess.check_output([tesseract_path, "--list-langs"], text=True, stderr=subprocess.STDOUT)
        available = set(output.splitlines()[1:])
        for lang in language.split("+"):
            if lang and lang not in available:
                warnings.append(f"Языковой пакет Tesseract не найден: {lang}")
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"Не удалось проверить языки Tesseract: {exc}")
    return warnings


def preprocess_for_ocr(image: Image.Image) -> Image.Image:
    img = image.convert("L")
    width, height = img.size
    img = img.resize((int(width * 1.5), int(height * 1.5)), RESAMPLE)
    return ImageOps.autocontrast(img)


def perform_ocr(image: Image.Image, language: str) -> tuple[str, str | None]:
    try:
        prepared = preprocess_for_ocr(image)
        text = pytesseract.image_to_string(prepared, lang=language, config=OCR_CONFIG)
        return text.strip(), None
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("OCR error")
        return "", str(exc)


def get_image_hash(image: Image.Image) -> str:
    small = image.resize((320, 180), RESAMPLE).convert("L")
    return hashlib.md5(small.tobytes()).hexdigest()
