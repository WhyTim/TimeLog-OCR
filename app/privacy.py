from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

REDACTION = "[REDACTED_SECRET]"
SECRET_LINE_MARKER = "[REDACTED_SECRET_LINE]"


@dataclass(frozen=True, slots=True)
class SecretFinding:
    category: str
    value: str
    start: int
    end: int
    masked: str


PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL)
JWT_RE = re.compile(r"\beyJ[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}\b")
GITHUB_TOKEN_RE = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")
OPENAI_KEY_RE = re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")
AWS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
BEARER_RE = re.compile(r"(?i)\b(?:bearer|basic)\s+(?P<secret>[a-z0-9._~+/=-]{12,})")
BASIC_AUTH_URL_RE = re.compile(r"(?i)\bhttps?://(?P<secret>[^\s:/@]+:[^\s@]+)@")
CONNECTION_STRING_RE = re.compile(
    r"(?ix)\b(?:server|host|data\s+source)\s*=.+?;.*?\b(?:password|pwd)\s*=\s*(?P<secret>[^;\s]+)"
)
SECRET_KEY_RE = re.compile(
    r"(?ix)\b(?:password|passwd|pwd|pass|secret|token|api[_-]?key|apikey|access[_-]?key|"
    r"access[_-]?token|refresh[_-]?token|authorization|client[_-]?secret|cookie|session(?:id)?|"
    r"login|username|user[_-]?name|email|пароль|логин)\b\s*[:=]\s*(?P<secret>\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d ()-]{8,}\d)(?!\w)")
LONG_CANDIDATE_RE = re.compile(r"\b[A-Za-z0-9_./+-]{24,}={0,2}\b")
UUID_RE = re.compile(r"(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
URL_SPAN_RE = re.compile(r"(?i)\bhttps?\s*:\s*/\s*/\s*[^\s<>\"']+")


def _entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = Counter(value)
    return -sum((count / len(value)) * math.log2(count / len(value)) for count in counts.values())


def mask_secret(value: str, category: str = "secret") -> str:
    clean = value.strip("\"'")
    if category == "email" and "@" in clean:
        local, domain = clean.rsplit("@", 1)
        suffix = domain.split(".")[-1] if "." in domain else ""
        return f"{local[:1]}***@***.{suffix}" if suffix else f"{local[:1]}***@***"
    if category == "phone":
        digits = re.sub(r"\D", "", clean)
        return f"***{digits[-4:]}" if len(digits) >= 4 else REDACTION
    if len(clean) >= 12:
        return f"{clean[:4]}…{clean[-3:]}"
    return REDACTION


def detect_secrets(text: str) -> list[SecretFinding]:
    if not text:
        return []
    candidates: list[tuple[int, int, str, str]] = []
    url_spans = [match.span() for match in URL_SPAN_RE.finditer(text)]

    def add(pattern: re.Pattern[str], category: str, group: str | int = 0, *, skip_urls: bool = False) -> None:
        for match in pattern.finditer(text):
            start, end = match.span(group)
            value = match.group(group)
            if value and not (skip_urls and any(start < url_end and end > url_start for url_start, url_end in url_spans)):
                candidates.append((start, end, category, value))

    add(PRIVATE_KEY_RE, "private_key")
    add(JWT_RE, "jwt")
    add(GITHUB_TOKEN_RE, "github_token")
    add(OPENAI_KEY_RE, "openai_api_key")
    add(AWS_KEY_RE, "aws_access_key")
    add(BEARER_RE, "authorization", "secret")
    add(BASIC_AUTH_URL_RE, "basic_auth", "secret")
    add(CONNECTION_STRING_RE, "connection_string_password", "secret")
    add(SECRET_KEY_RE, "credential", "secret")
    add(EMAIL_RE, "email", skip_urls=True)
    add(PHONE_RE, "phone", skip_urls=True)
    for match in LONG_CANDIDATE_RE.finditer(text):
        value = match.group(0)
        if UUID_RE.fullmatch(value) or any(match.start() < end and match.end() > start for start, end in url_spans):
            continue
        if any(char.isalpha() for char in value) and any(char.isdigit() for char in value) and _entropy(value) >= 3.5:
            candidates.append((match.start(), match.end(), "high_entropy_secret", value))

    findings: list[SecretFinding] = []
    occupied: list[tuple[int, int]] = []
    priority = {"private_key": 0, "jwt": 1, "github_token": 2, "openai_api_key": 3, "aws_access_key": 4, "email": 5, "phone": 6}
    for start, end, category, value in sorted(candidates, key=lambda item: (item[0], -(item[1] - item[0]), priority.get(item[2], 20))):
        if any(start < used_end and end > used_start for used_start, used_end in occupied):
            continue
        findings.append(SecretFinding(category, value, start, end, mask_secret(value, category)))
        occupied.append((start, end))
    return sorted(findings, key=lambda finding: finding.start)


def redact_secrets(text: str) -> str:
    """Mask credentials and common PII before any local persistence."""
    findings = detect_secrets(text)
    if not findings:
        return text
    chunks: list[str] = []
    cursor = 0
    for finding in findings:
        chunks.append(text[cursor:finding.start])
        chunks.append(finding.masked if finding.category in {"email", "phone"} else REDACTION)
        cursor = finding.end
    chunks.append(text[cursor:])
    return "".join(chunks)


def sanitize_json_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, list):
        return [sanitize_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: sanitize_json_value(item) for key, item in value.items()}
    return value


def sanitize_record(record: dict[str, Any]) -> dict[str, Any]:
    sanitized = sanitize_json_value(record)
    assert isinstance(sanitized, dict)
    return sanitized
