# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

hiddenimports = [
    *collect_submodules("tools.local_call_transcriber_v3"),
    "faster_whisper",
    "win32crypt",
    "win32timezone",
]

datas = [
    ("assets", "assets"),
    ("tools/local_call_transcriber_v3", "tools/local_call_transcriber_v3"),
    ("app/ui/styles", "app/ui/styles"),
    *collect_data_files("faster_whisper"),
]

binaries = collect_dynamic_libs("ctranslate2")

# A release build made on Windows carries the OCR engine and rus/eng language
# data inside the executable. Developers can override the source directory.
tesseract_dir = Path(os.environ.get("TIMELOGOCR_TESSERACT_DIR", r"C:\Program Files\Tesseract-OCR"))
if tesseract_dir.exists():
    for binary in [tesseract_dir / "tesseract.exe", *tesseract_dir.glob("*.dll")]:
        if binary.exists():
            binaries.append((str(binary), "tesseract"))
    for data_name in ("eng.traineddata", "rus.traineddata", "osd.traineddata"):
        source = tesseract_dir / "tessdata" / data_name
        if source.exists():
            datas.append((str(source), "tesseract/tessdata"))
    for directory in ("configs", "tessconfigs"):
        source = tesseract_dir / "tessdata" / directory
        if source.exists():
            datas.append((str(source), f"tesseract/tessdata/{directory}"))

excludes = [
    "IPython",
    "matplotlib",
    "numba",
    "pandas",
    "pytest",
    "scipy",
    "tensorflow",
    "torch",
    "torchaudio",
    "torchvision",
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="TimeLogOCR",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    icon="assets/app_icon.ico",
    version="file_version_info.txt",
    uac_admin=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
