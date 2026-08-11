from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .privacy import redact_secrets

URL_RE = re.compile(r"(?ix)\bhttps?\s*:\s*/\s*/\s*[^\s<>\"']+")
TRAILING = ".,;:!?)]}»”'"
SENSITIVE_QUERY_KEYS = re.compile(r"(?i)(?:token|key|api[_-]?key|password|passwd|pwd|auth|authorization|code|secret|session|cookie)")
LINK_REDACTION = "[REDACTED]"
SPACED_UUID_RE = re.compile(
    r"(?i)\b([0-9a-f]{8})\s*-\s*([0-9a-f]{4})\s*-\s*([0-9a-f]{4})\s*-\s*([0-9a-f]{4})\s*-\s*([0-9a-f]{12})\b"
)


@dataclass(slots=True)
class LinkEntry:
    first_seen: str
    last_seen: str
    url: str
    window_title: str = ""
    count: int = 1


def _repair_ocr_url_text(text: str) -> str:
    repaired = re.sub(r"(?i)\b(https?)\s*:\s*/\s*/\s*", r"\1://", text or "")
    # Tesseract frequently inserts spaces around UUID hyphens. Restrict the
    # repair to the canonical 8-4-4-4-12 shape to avoid joining normal prose.
    repaired = SPACED_UUID_RE.sub(r"\1-\2-\3-\4-\5", repaired)
    # Join a wrapped URL only when the previous line ends at URL punctuation.
    return re.sub(r"(?i)(https?://[^\s<>\"']*[/?&#=_-])\s*\r?\n\s*(?=[a-z0-9%])", r"\1", repaired)


def normalize_url(value: str) -> str | None:
    candidate = re.sub(r"\s*:\s*/\s*/\s*", "://", value.strip()).rstrip(TRAILING)
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    host = parsed.hostname.lower()
    netloc = host
    if parsed.port:
        netloc += f":{parsed.port}"
    query = []
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        query.append((key, LINK_REDACTION if SENSITIVE_QUERY_KEYS.search(key) else redact_secrets(item)))
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "", urlencode(query, safe="[]"), ""))


def extract_links(text: str) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()
    for match in URL_RE.finditer(_repair_ocr_url_text(text)):
        normalized = normalize_url(match.group(0))
        if normalized and normalized not in seen:
            seen.add(normalized)
            links.append(normalized)
    return links


class LinkJournal:
    HEADER = "# Ссылки с экрана\n\n| Первое обнаружение | Последнее обнаружение | Ссылка | Окно | Повторы |\n|---|---|---|---|---:|\n"

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def _read(self) -> dict[str, LinkEntry]:
        entries: dict[str, LinkEntry] = {}
        if not self.path.exists():
            return entries
        for line in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.startswith("| ") or line.startswith("| Время"):
                continue
            parts = [part.strip() for part in line.strip("|").split("|")]
            try:
                if len(parts) == 5 and parts[2].startswith("http"):
                    entries[parts[2]] = LinkEntry(parts[0], parts[1], parts[2], parts[3], int(parts[4]))
                elif len(parts) == 4 and parts[1].startswith("http"):  # v1.5 migration
                    entries[parts[1]] = LinkEntry(parts[0], parts[0], parts[1], parts[2], int(parts[3]))
            except ValueError:
                continue
        return entries

    def add_from_text(self, text: str, window_title: str = "", now: datetime | None = None) -> int:
        links = extract_links(text)
        if not links:
            return 0
        entries = self._read()
        timestamp = (now or datetime.now()).isoformat(timespec="seconds")
        safe_window = redact_secrets(window_title).replace("|", "\\|")
        for link in links:
            if link in entries:
                entries[link].count += 1
                entries[link].last_seen = timestamp
            else:
                entries[link] = LinkEntry(timestamp, timestamp, link, safe_window, 1)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rows = [self.HEADER]
        rows.extend(f"| {entry.first_seen} | {entry.last_seen} | {entry.url} | {entry.window_title} | {entry.count} |\n" for entry in entries.values())
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            temporary.write_text("".join(rows), encoding="utf-8")
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)
        return len(links)

    def list_entries(self) -> list[LinkEntry]:
        return list(self._read().values())
