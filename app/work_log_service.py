from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from .privacy import sanitize_record
from .settings import AppSettings

@dataclass(slots=True)
class WorkCategory:
    name: str
    color: str = "#64748b"
    account_type: str = "другое"
    default_payment_status: str = "none"
    requires_client: bool = False
    requires_contact: bool = False
    summary_group: str = "Другое"
    is_active: bool = True
    sort_order: int = 0


DEFAULT_CATEGORY_DEFINITIONS: tuple[WorkCategory, ...] = (
    WorkCategory("Клиент, с оплатой", "#15803d", "клиент", "paid", True, False, "Клиенты", True, 10),
    WorkCategory("Клиент, без оплаты", "#475569", "клиент", "free", True, False, "Клиенты", True, 20),
    WorkCategory("Внутренняя, с оплатой", "#15803d", "internal", "paid", False, False, "Внутренняя", True, 30),
    WorkCategory("Внутренняя", "#7c3aed", "internal", "internal", False, False, "Внутренняя", True, 40),
    WorkCategory("Обучение", "#7c3aed", "internal", "internal", False, False, "Внутренняя", True, 50),
    WorkCategory("Перерыв", "#1d4ed8", "перерыв", "none", False, False, "Перерыв", True, 60),
    WorkCategory("Без задач", "#64748b", "без задач", "none", False, False, "Без задач", True, 70),
    WorkCategory("Не определено", "#c2410c", "другое", "none", False, False, "Другое", True, 80),
)

CATEGORIES = tuple(category.name for category in DEFAULT_CATEGORY_DEFINITIONS)


def normalize_hex_color(value: str, fallback: str = "#64748b") -> str:
    value = (value or "").strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        return value.lower()
    return fallback

READY_STATUS = "Готово"
NEEDS_STATUS = "Требует уточнения"
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


@dataclass(slots=True)
class WorkStatus:
    name: str
    color: str = "#64748b"
    is_default: bool = False
    is_active: bool = True
    sort_order: int = 0


DEFAULT_STATUS_DEFINITIONS: tuple[WorkStatus, ...] = (
    WorkStatus("С оплатой", "#15803d", False, True, 10),
    WorkStatus("Без оплаты", "#475569", False, True, 20),
    WorkStatus("Требует уточнения", "#c2410c", True, True, 30),
    WorkStatus("Готово", "#15803d", False, True, 40),
    WorkStatus("Внутренняя", "#7c3aed", False, True, 50),
)


@dataclass(slots=True)
class WorkLogEntry:
    timestamp: str = ""
    requester: str = ""
    client: str = ""
    message: str = ""
    result: str = ""
    duration_minutes: int = 0
    tags: str = ""
    id: int | None = None
    work_date: str = ""
    category: str = "Не определено"
    start_time: str = ""
    end_time: str = ""
    lurv_minutes: int = 0
    billable_minutes: int = 0
    task_reference: str = ""
    payment_comment: str = ""
    status: str = NEEDS_STATUS
    created_at: str = ""
    updated_at: str = ""


def normalize_name(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def parse_hhmm(value: str) -> time:
    if not TIME_RE.match(value):
        raise ValueError("Время нужно указать в формате HH:MM.")
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def calculate_duration_minutes(start_value: str, end_value: str) -> int:
    start = parse_hhmm(start_value)
    end = parse_hhmm(end_value)
    start_minutes = start.hour * 60 + start.minute
    end_minutes = end.hour * 60 + end.minute
    if end_minutes < start_minutes:
        end_minutes += 24 * 60
    return end_minutes - start_minutes


def category_requires_client(category: str) -> bool:
    for definition in DEFAULT_CATEGORY_DEFINITIONS:
        if definition.name == category:
            return definition.requires_client
    return category.startswith("Клиент")


def work_status(entry: WorkLogEntry, category: WorkCategory | None = None) -> str:
    try:
        calculate_duration_minutes(entry.start_time, entry.end_time)
    except ValueError:
        return NEEDS_STATUS
    if entry.category == "Не определено":
        return NEEDS_STATUS
    requires_client = category.requires_client if category else category_requires_client(entry.category)
    if requires_client and not entry.client.strip():
        return NEEDS_STATUS
    return READY_STATUS


def first_line(*values: str) -> str:
    for value in values:
        for line in value.splitlines():
            if line.strip():
                return line.strip()
    return "Описание не заполнено"


class WorkLogService:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.db_path = Path(settings.database_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def migrate(self) -> None:
        with self.connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS clients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_used_at TEXT NOT NULL,
                    last_category TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS contacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_id INTEGER,
                    name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_used_at TEXT NOT NULL,
                    UNIQUE(client_id, normalized_name),
                    FOREIGN KEY(client_id) REFERENCES clients(id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS work_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    work_date TEXT NOT NULL,
                    client_id INTEGER,
                    contact_id INTEGER,
                    category TEXT NOT NULL,
                    source_message TEXT NOT NULL,
                    work_result TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    duration_minutes INTEGER NOT NULL,
                    lurv_minutes INTEGER NOT NULL,
                    billable_minutes INTEGER NOT NULL,
                    task_reference TEXT NOT NULL,
                    payment_comment TEXT NOT NULL,
                    tags TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(client_id) REFERENCES clients(id),
                    FOREIGN KEY(contact_id) REFERENCES contacts(id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS work_drafts (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS work_statuses (
                    name TEXT PRIMARY KEY,
                    color TEXT NOT NULL,
                    is_default INTEGER NOT NULL,
                    is_active INTEGER NOT NULL,
                    sort_order INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS work_categories (
                    name TEXT PRIMARY KEY,
                    color TEXT NOT NULL,
                    account_type TEXT NOT NULL,
                    default_payment_status TEXT NOT NULL,
                    requires_client INTEGER NOT NULL,
                    requires_contact INTEGER NOT NULL,
                    summary_group TEXT NOT NULL,
                    is_active INTEGER NOT NULL,
                    sort_order INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            now = datetime.now().isoformat(timespec="seconds")
            for status in DEFAULT_STATUS_DEFINITIONS:
                conn.execute("""
                    INSERT OR IGNORE INTO work_statuses(name, color, is_default, is_active, sort_order, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (status.name, status.color, int(status.is_default), int(status.is_active), status.sort_order, now, now))
            for category in DEFAULT_CATEGORY_DEFINITIONS:
                conn.execute("""
                    INSERT OR IGNORE INTO work_categories(name, color, account_type, default_payment_status, requires_client, requires_contact, summary_group, is_active, sort_order, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (category.name, category.color, category.account_type, category.default_payment_status, int(category.requires_client), int(category.requires_contact), category.summary_group, int(category.is_active), category.sort_order, now, now))
            conn.execute("UPDATE work_categories SET name='Внутренняя, с оплатой', account_type='internal', summary_group='Внутренняя', updated_at=? WHERE name='ТВБ, с оплатой'", (now,))
            conn.execute("UPDATE work_categories SET name='Внутренняя', account_type='internal', summary_group='Внутренняя', updated_at=? WHERE name='ТВБ, внутреннее'", (now,))
            conn.execute("UPDATE work_categories SET name='Обучение', account_type='internal', summary_group='Внутренняя', updated_at=? WHERE name='Обучение ТВБ'", (now,))
            conn.execute("UPDATE work_entries SET category='Внутренняя, с оплатой', updated_at=? WHERE category='ТВБ, с оплатой'", (now,))
            conn.execute("UPDATE work_entries SET category='Внутренняя', updated_at=? WHERE category='ТВБ, внутреннее'", (now,))
            conn.execute("UPDATE work_entries SET category='Обучение', updated_at=? WHERE category='Обучение ТВБ'", (now,))
            conn.execute("INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(1, ?)", (now,))


    def list_statuses(self, active_only: bool = True) -> list[WorkStatus]:
        sql = "SELECT * FROM work_statuses"
        if active_only:
            sql += " WHERE is_active = 1"
        sql += " ORDER BY sort_order, name"
        with self.connect() as conn:
            return [WorkStatus(row["name"], row["color"], bool(row["is_default"]), bool(row["is_active"]), int(row["sort_order"])) for row in conn.execute(sql).fetchall()]

    def get_status(self, name: str) -> WorkStatus | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM work_statuses WHERE name = ?", (name,)).fetchone()
        return WorkStatus(row["name"], row["color"], bool(row["is_default"]), bool(row["is_active"]), int(row["sort_order"])) if row else None

    def delete_status(self, name: str) -> bool:
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as conn:
            used = conn.execute("SELECT 1 FROM work_entries WHERE status = ? LIMIT 1", (name,)).fetchone()
            if used:
                conn.execute("UPDATE work_statuses SET is_active = 0, updated_at = ? WHERE name = ?", (now, name))
                return False
            conn.execute("DELETE FROM work_statuses WHERE name = ?", (name,))
            return True

    def save_status(self, status: WorkStatus, original_name: str | None = None) -> WorkStatus:
        clean = " ".join(status.name.strip().split())
        if not clean:
            raise ValueError("Название статуса не может быть пустым.")
        status.name = clean
        status.color = normalize_hex_color(status.color)
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as conn:
            if status.is_default:
                conn.execute("UPDATE work_statuses SET is_default = 0")
            if original_name and original_name != clean:
                used = conn.execute("SELECT 1 FROM work_entries WHERE status = ? LIMIT 1", (original_name,)).fetchone()
                if used:
                    conn.execute("UPDATE work_statuses SET is_active = 0, updated_at = ? WHERE name = ?", (now, original_name))
                else:
                    conn.execute("DELETE FROM work_statuses WHERE name = ?", (original_name,))
            conn.execute("""
                INSERT INTO work_statuses(name, color, is_default, is_active, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET color=excluded.color, is_default=excluded.is_default, is_active=excluded.is_active, sort_order=excluded.sort_order, updated_at=excluded.updated_at
            """, (status.name, status.color, int(status.is_default), int(status.is_active), status.sort_order, now, now))
        return status

    def set_status_active(self, name: str, is_active: bool) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE work_statuses SET is_active = ?, updated_at = ? WHERE name = ?", (int(is_active), datetime.now().isoformat(timespec="seconds"), name))

    def list_categories(self, active_only: bool = True) -> list[WorkCategory]:
        sql = "SELECT * FROM work_categories"
        if active_only:
            sql += " WHERE is_active = 1"
        sql += " ORDER BY sort_order, name"
        with self.connect() as conn:
            return [self._row_to_category(row) for row in conn.execute(sql).fetchall()]

    def list_category_names(self, active_only: bool = True) -> list[str]:
        return [category.name for category in self.list_categories(active_only=active_only)]

    def get_category(self, name: str) -> WorkCategory | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM work_categories WHERE name = ?", (name,)).fetchone()
        return self._row_to_category(row) if row else None

    def save_category(self, category: WorkCategory, original_name: str | None = None) -> WorkCategory:
        clean = " ".join(category.name.strip().split())
        if not clean:
            raise ValueError("Название категории не может быть пустым.")
        category.name = clean
        category.color = normalize_hex_color(category.color)
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as conn:
            if original_name and original_name != clean:
                used = conn.execute("SELECT 1 FROM work_entries WHERE category = ? LIMIT 1", (original_name,)).fetchone()
                if used:
                    conn.execute("UPDATE work_categories SET is_active = 0, updated_at = ? WHERE name = ?", (now, original_name))
                else:
                    conn.execute("DELETE FROM work_categories WHERE name = ?", (original_name,))
            conn.execute("""
                INSERT INTO work_categories(name, color, account_type, default_payment_status, requires_client, requires_contact, summary_group, is_active, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET color=excluded.color, account_type=excluded.account_type, default_payment_status=excluded.default_payment_status, requires_client=excluded.requires_client, requires_contact=excluded.requires_contact, summary_group=excluded.summary_group, is_active=excluded.is_active, sort_order=excluded.sort_order, updated_at=excluded.updated_at
            """, (category.name, category.color, category.account_type, category.default_payment_status, int(category.requires_client), int(category.requires_contact), category.summary_group, int(category.is_active), category.sort_order, now, now))
        return category

    def delete_category(self, name: str) -> bool:
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as conn:
            used = conn.execute("SELECT 1 FROM work_entries WHERE category = ? LIMIT 1", (name,)).fetchone()
            if used:
                conn.execute("UPDATE work_categories SET is_active = 0, updated_at = ? WHERE name = ?", (now, name))
                return False
            conn.execute("DELETE FROM work_categories WHERE name = ?", (name,))
            return True

    def set_category_active(self, name: str, is_active: bool) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE work_categories SET is_active = ?, updated_at = ? WHERE name = ?", (int(is_active), datetime.now().isoformat(timespec="seconds"), name))

    def _row_to_category(self, row: sqlite3.Row) -> WorkCategory:
        return WorkCategory(
            name=row["name"],
            color=row["color"],
            account_type=row["account_type"],
            default_payment_status=row["default_payment_status"],
            requires_client=bool(row["requires_client"]),
            requires_contact=bool(row["requires_contact"]),
            summary_group=row["summary_group"],
            is_active=bool(row["is_active"]),
            sort_order=int(row["sort_order"]),
        )

    def upsert_client(self, name: str, category: str | None = None) -> int | None:
        clean = " ".join(name.strip().split())
        if not clean:
            return None
        normalized = normalize_name(clean)
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as conn:
            row = conn.execute("SELECT id FROM clients WHERE normalized_name = ?", (normalized,)).fetchone()
            if row:
                conn.execute("UPDATE clients SET name = ?, updated_at = ?, last_used_at = ?, last_category = COALESCE(?, last_category) WHERE id = ?", (clean, now, now, category, row["id"]))
                return int(row["id"])
            cur = conn.execute("INSERT INTO clients(name, normalized_name, created_at, updated_at, last_used_at, last_category) VALUES (?, ?, ?, ?, ?, ?)", (clean, normalized, now, now, now, category))
            return int(cur.lastrowid)

    def upsert_contact(self, client_id: int | None, name: str) -> int | None:
        clean = " ".join(name.strip().split())
        if not clean:
            return None
        normalized = normalize_name(clean)
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as conn:
            row = conn.execute("SELECT id FROM contacts WHERE COALESCE(client_id, 0) = COALESCE(?, 0) AND normalized_name = ?", (client_id, normalized)).fetchone()
            if row:
                conn.execute("UPDATE contacts SET name = ?, updated_at = ?, last_used_at = ? WHERE id = ?", (clean, now, now, row["id"]))
                return int(row["id"])
            cur = conn.execute("INSERT INTO contacts(client_id, name, normalized_name, created_at, updated_at, last_used_at) VALUES (?, ?, ?, ?, ?, ?)", (client_id, clean, normalized, now, now, now))
            return int(cur.lastrowid)

    def list_clients(self, query: str = "") -> list[str]:
        pattern = f"%{normalize_name(query)}%"
        with self.connect() as conn:
            return [row["name"] for row in conn.execute("SELECT name FROM clients WHERE normalized_name LIKE ? ORDER BY last_used_at DESC, name", (pattern,)).fetchall()]

    def list_contacts(self, client_name: str = "", query: str = "") -> list[str]:
        client_id = self.find_client_id(client_name)
        pattern = f"%{normalize_name(query)}%"
        sql = "SELECT name FROM contacts WHERE normalized_name LIKE ?"
        params: list[Any] = [pattern]
        if client_id is not None:
            sql += " ORDER BY CASE WHEN client_id = ? THEN 0 ELSE 1 END, last_used_at DESC, name"
            params.append(client_id)
        else:
            sql += " ORDER BY last_used_at DESC, name"
        with self.connect() as conn:
            return [row["name"] for row in conn.execute(sql, params).fetchall()]

    def find_client_id(self, name: str) -> int | None:
        normalized = normalize_name(name)
        if not normalized:
            return None
        with self.connect() as conn:
            row = conn.execute("SELECT id FROM clients WHERE normalized_name = ?", (normalized,)).fetchone()
            return int(row["id"]) if row else None

    def save_entry(self, entry: WorkLogEntry) -> WorkLogEntry:
        now = datetime.now().isoformat(timespec="seconds")
        entry.work_date = entry.work_date or date.today().isoformat()
        entry.duration_minutes = calculate_duration_minutes(entry.start_time, entry.end_time)
        entry.lurv_minutes = entry.lurv_minutes or entry.duration_minutes
        entry.billable_minutes = entry.billable_minutes or entry.duration_minutes
        entry.status = work_status(entry, self.get_category(entry.category))
        client_id = self.upsert_client(entry.client, entry.category)
        contact_id = self.upsert_contact(client_id, entry.requester)
        with self.connect() as conn:
            if entry.id:
                created = conn.execute("SELECT created_at FROM work_entries WHERE id = ?", (entry.id,)).fetchone()
                conn.execute("""
                    UPDATE work_entries SET work_date=?, client_id=?, contact_id=?, category=?, source_message=?, work_result=?, start_time=?, end_time=?, duration_minutes=?, lurv_minutes=?, billable_minutes=?, task_reference=?, payment_comment=?, tags=?, status=?, updated_at=? WHERE id=?
                """, (entry.work_date, client_id, contact_id, entry.category, entry.message, entry.result, entry.start_time, entry.end_time, entry.duration_minutes, entry.lurv_minutes, entry.billable_minutes, entry.task_reference, entry.payment_comment, entry.tags, entry.status, now, entry.id))
                entry.created_at = created["created_at"] if created else now
            else:
                cur = conn.execute("""
                    INSERT INTO work_entries(work_date, client_id, contact_id, category, source_message, work_result, start_time, end_time, duration_minutes, lurv_minutes, billable_minutes, task_reference, payment_comment, tags, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (entry.work_date, client_id, contact_id, entry.category, entry.message, entry.result, entry.start_time, entry.end_time, entry.duration_minutes, entry.lurv_minutes, entry.billable_minutes, entry.task_reference, entry.payment_comment, entry.tags, entry.status, now, now))
                entry.id = int(cur.lastrowid)
                entry.created_at = now
            entry.updated_at = now
        self.export_manual_work_log(entry.work_date)
        return entry

    def get_entry(self, entry_id: int) -> WorkLogEntry | None:
        with self.connect() as conn:
            row = conn.execute(self._select_sql("WHERE w.id = ?"), (entry_id,)).fetchone()
            return self._row_to_entry(row) if row else None

    def delete_entry(self, entry_id: int) -> None:
        entry = self.get_entry(entry_id)
        with self.connect() as conn:
            conn.execute("DELETE FROM work_entries WHERE id = ?", (entry_id,))
        if entry:
            self.export_manual_work_log(entry.work_date)

    def list_entries(self, work_date: str, filter_name: str = "Все", search: str = "") -> list[WorkLogEntry]:
        clauses = ["w.work_date = ?"]
        params: list[Any] = [work_date]
        if filter_name == "Требует уточнения":
            clauses.append("w.status = ?")
            params.append(NEEDS_STATUS)
        elif filter_name == "С оплатой":
            clauses.append("w.category LIKE ?")
            params.append("%с оплатой%")
        elif filter_name == "Без оплаты":
            clauses.append("w.category LIKE ?")
            params.append("%без оплаты%")
        if search.strip():
            clauses.append("(COALESCE(c.name, '') LIKE ? OR COALESCE(ct.name, '') LIKE ? OR w.source_message LIKE ? OR w.work_result LIKE ?)")
            pattern = f"%{search.strip()}%"
            params.extend([pattern, pattern, pattern, pattern])
        with self.connect() as conn:
            return [self._row_to_entry(row) for row in conn.execute(self._select_sql("WHERE " + " AND ".join(clauses) + " ORDER BY w.start_time, w.id"), params).fetchall()]

    def day_totals(self, work_date: str) -> dict[str, int]:
        totals = {"Клиенты": 0, "Внутренняя": 0, "Без задач": 0, "Перерыв": 0, "Всего": 0}
        for entry in self.list_entries(work_date):
            minutes = entry.duration_minutes
            category = self.get_category(entry.category)
            group = category.summary_group if category else ""
            if group in totals and group != "Всего":
                totals[group] += minutes
            elif entry.category.startswith("Клиент"):
                totals["Клиенты"] += minutes
            elif entry.category.startswith("Внутренняя") or entry.category.startswith("ТВБ") or entry.category in {"Обучение", "Обучение ТВБ"}:
                totals["Внутренняя"] += minutes
            elif entry.category == "Без задач":
                totals["Без задач"] += minutes
            elif entry.category == "Перерыв":
                totals["Перерыв"] += minutes
            totals["Всего"] += minutes
        return totals

    def export_manual_work_log(self, work_date: str) -> Path:
        target = Path(self.settings.base_directory) / work_date / f"manual_work_log_{work_date}.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        entries = self.list_entries(work_date)
        with target.open("w", encoding="utf-8") as handle:
            for entry in entries:
                record = {
                    "id": entry.id,
                    "date": entry.work_date,
                    "client": entry.client,
                    "contact": entry.requester,
                    "category": entry.category,
                    "source_message": entry.message,
                    "work_result": entry.result,
                    "start_time": entry.start_time,
                    "end_time": entry.end_time,
                    "duration_minutes": entry.duration_minutes,
                    "lurv_minutes": entry.lurv_minutes,
                    "billable_minutes": entry.billable_minutes,
                    "task_reference": entry.task_reference,
                    "payment_comment": entry.payment_comment,
                    "status": "ready" if entry.status == READY_STATUS else "needs_clarification",
                    "created_at": entry.created_at,
                    "updated_at": entry.updated_at,
                }
                if self.settings.privacy_mode_enabled:
                    record = sanitize_record(record)
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return target

    def save_draft(self, payload: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO work_drafts(id, payload, updated_at) VALUES (1, ?, ?)", (json.dumps(payload, ensure_ascii=False), datetime.now().isoformat(timespec="seconds")))

    def load_draft(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT payload FROM work_drafts WHERE id = 1").fetchone()
        return json.loads(row["payload"]) if row else None

    def clear_draft(self) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM work_drafts WHERE id = 1")

    def _select_sql(self, suffix: str) -> str:
        return """
            SELECT w.*, c.name AS client_name, ct.name AS contact_name
            FROM work_entries w
            LEFT JOIN clients c ON c.id = w.client_id
            LEFT JOIN contacts ct ON ct.id = w.contact_id
        """ + suffix

    def _row_to_entry(self, row: sqlite3.Row) -> WorkLogEntry:
        return WorkLogEntry(
            id=row["id"],
            timestamp=row["created_at"],
            work_date=row["work_date"],
            client=row["client_name"] or "",
            requester=row["contact_name"] or "",
            category=row["category"],
            message=row["source_message"],
            result=row["work_result"],
            start_time=row["start_time"],
            end_time=row["end_time"],
            duration_minutes=row["duration_minutes"],
            lurv_minutes=row["lurv_minutes"],
            billable_minutes=row["billable_minutes"],
            task_reference=row["task_reference"],
            payment_comment=row["payment_comment"],
            tags=row["tags"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


def append_work_entry(settings: AppSettings, entry: WorkLogEntry, now: datetime | None = None) -> Path:
    if now:
        entry.work_date = now.date().isoformat()
    saved = WorkLogService(settings).save_entry(entry)
    return Path(settings.base_directory) / saved.work_date / f"manual_work_log_{saved.work_date}.jsonl"


def read_recent_work_entries(settings: AppSettings, limit: int = 50, now: datetime | None = None) -> list[dict[str, object]]:
    work_date = (now or datetime.now()).date().isoformat()
    return [asdict(entry) for entry in WorkLogService(settings).list_entries(work_date)[-limit:]]


def work_log_path(settings: AppSettings, now: datetime | None = None) -> Path:
    work_date = (now or datetime.now()).date().isoformat()
    return Path(settings.base_directory) / work_date / f"manual_work_log_{work_date}.jsonl"
