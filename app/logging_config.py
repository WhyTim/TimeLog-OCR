from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path


def setup_logging(log_dir: Path | None = None) -> None:
    from app.paths import user_path
    log_dir = log_dir or user_path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"app_{datetime.now():%Y-%m-%d}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8")],
        force=True,
    )
