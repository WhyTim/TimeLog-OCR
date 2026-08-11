from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

from .version import APP_VERSION

LOGGER = logging.getLogger(__name__)
GITHUB_RELEASES_API = "https://api.github.com/repos/tvbttwork/Daily-report/releases/latest"


@dataclass(slots=True)
class UpdateCheckResult:
    ok: bool
    message: str
    latest_version: str | None = None
    url: str | None = None


def normalize_version(value: str) -> tuple[int, ...]:
    value = value.strip().lstrip("v")
    parts: list[int] = []
    for chunk in value.split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def check_for_updates(api_url: str = GITHUB_RELEASES_API, timeout: int = 5) -> UpdateCheckResult:
    try:
        request = urllib.request.Request(  # noqa: S310 - configurable GitHub URL
            api_url,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "TimeLogOCR"},
        )
        token = os.environ.get("TIMELOGOCR_GITHUB_TOKEN", "").strip()
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - configurable GitHub URL
            payload = json.loads(response.read().decode("utf-8"))
        latest = str(payload.get("tag_name") or payload.get("name") or "").lstrip("v")
        html_url = payload.get("html_url")
        if not latest:
            return UpdateCheckResult(False, "GitHub Releases не вернул номер версии.")
        if normalize_version(latest) > normalize_version(APP_VERSION):
            return UpdateCheckResult(True, f"Доступна новая версия: {latest}", latest, html_url)
        return UpdateCheckResult(True, "Установлена актуальная версия.", latest, html_url)
    except urllib.error.HTTPError as exc:
        LOGGER.warning("Update check HTTP error: %s", exc)
        if exc.code == 404:
            return UpdateCheckResult(
                False,
                "Релизы GitHub недоступны. Для приватного репозитория задайте "
                "переменную TIMELOGOCR_GITHUB_TOKEN с правом чтения репозитория.",
            )
        return UpdateCheckResult(False, f"GitHub вернул ошибку HTTP {exc.code} при проверке обновлений.")
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("Update check failed")
        return UpdateCheckResult(False, f"Не удалось проверить обновления: {exc}")
