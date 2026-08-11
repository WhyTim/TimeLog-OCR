# Уведомления о сторонних компонентах

Готовая Windows-сборка TimeLog OCR может включать следующие компоненты:

- Tesseract OCR — Apache License 2.0;
- языковые данные Tesseract `rus`, `eng`, `osd` — лицензии соответствующих проектов tessdata;
- Leptonica — BSD 2-Clause License;
- PySide6 / Qt — LGPLv3/GPLv3 в соответствии с условиями используемой редакции;
- PyInstaller — GPLv2 с исключением для распространения собранных приложений;
- faster-whisper — MIT License;
- CTranslate2 — MIT License;
- FFmpeg/PyAV и их кодеки — условия зависят от конкретной сборки, обычно LGPL/GPL;
- pywin32 — PSF License;
- Pillow — HPND License;
- pytesseract — Apache License 2.0;
- mss — MIT License.

Этот файл является сводным уведомлением, а не заменой полных текстов лицензий. Перед публичным выпуском необходимо сохранить файлы лицензий, поставляемые с конкретными версиями бинарных компонентов, и проверить параметры сборки FFmpeg/PyAV.
