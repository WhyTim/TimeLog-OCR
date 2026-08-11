from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from .privacy import SecretFinding, redact_secrets

Protect = Callable[[bytes], bytes]
Unprotect = Callable[[bytes], bytes]
_VAULT_LOCK = threading.RLock()


def _protect_windows(data: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Защищённое хранилище доступно только в Windows")
    import win32crypt

    return win32crypt.CryptProtectData(data, "TimeLog OCR secrets", None, None, None, 0)


def _unprotect_windows(data: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Защищённое хранилище доступно только в Windows")
    import win32crypt

    return win32crypt.CryptUnprotectData(data, None, None, None, 0)[1]


class SecretVault:
    """Whole-file DPAPI vault. Plaintext values never touch an unencrypted file."""

    def __init__(self, path: Path | str, protect: Protect = _protect_windows, unprotect: Unprotect = _unprotect_windows) -> None:
        self.path = Path(path)
        self._protect = protect
        self._unprotect = unprotect

    def _read(self) -> list[dict[str, str]]:
        if not self.path.exists():
            return []
        payload = self._unprotect(self.path.read_bytes())
        data = json.loads(payload.decode("utf-8"))
        if not isinstance(data, list):
            return []
        records = []
        for item in data:
            if not isinstance(item, dict):
                continue
            record = {str(key): str(value) for key, value in item.items()}
            legacy_timestamp = record.get("timestamp", "")
            record.setdefault("first_seen", legacy_timestamp)
            record.setdefault("last_seen", legacy_timestamp)
            records.append(record)
        return records

    def _write(self, records: list[dict[str, str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not records:
            self.path.unlink(missing_ok=True)
            return
        plaintext = json.dumps(records, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        encrypted = self._protect(plaintext)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            temporary.write_bytes(encrypted)
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def add(self, findings: Iterable[SecretFinding], context: str = "") -> int:
        with _VAULT_LOCK:
            records = self._read()
            timestamp = datetime.now().isoformat(timespec="seconds")
            added = 0
            changed = False
            for finding in findings:
                duplicate = next(
                    (record for record in records if record.get("category") == finding.category and record.get("value") == finding.value),
                    None,
                )
                if duplicate is not None:
                    duplicate["timestamp"] = timestamp
                    duplicate["last_seen"] = timestamp
                    duplicate["context"] = redact_secrets(context)
                    duplicate["count"] = str(int(duplicate.get("count", "1")) + 1)
                    changed = True
                    continue
                records.append({
                    "id": uuid.uuid4().hex,
                    "timestamp": timestamp,
                    "first_seen": timestamp,
                    "last_seen": timestamp,
                    "category": finding.category,
                    "masked": finding.masked,
                    "context": redact_secrets(context),
                    "value": finding.value,
                    "count": "1",
                })
                added += 1
                changed = True
            if changed:
                self._write(records)
            return added

    def list_masked(self) -> list[dict[str, str]]:
        with _VAULT_LOCK:
            return [
                {**{key: value for key, value in record.items() if key != "value"}, "revealable": "yes" if record.get("value") else "no"}
                for record in self._read()
            ]

    def get_value(self, record_id: str) -> str | None:
        with _VAULT_LOCK:
            record = next((item for item in self._read() if item.get("id") == record_id), None)
            return record.get("value") if record and record.get("value") else None

    def delete(self, record_id: str) -> bool:
        with _VAULT_LOCK:
            records = self._read()
            remaining = [record for record in records if record.get("id") != record_id]
            if len(remaining) == len(records):
                return False
            self._write(remaining)
            return True

    def clear(self) -> None:
        with _VAULT_LOCK:
            self.path.unlink(missing_ok=True)
