from datetime import datetime

from app.settings import AppSettings
from app.work_log_service import WorkCategory, WorkLogEntry, WorkLogService, append_work_entry, first_line, read_recent_work_entries, work_log_path


def test_append_work_entry_writes_daily_jsonl(tmp_path):
    settings = AppSettings(base_directory=str(tmp_path), database_path=str(tmp_path / "db.sqlite"), privacy_mode_enabled=True)
    now = datetime(2026, 7, 23, 10, 30)
    entry = WorkLogEntry(
        timestamp="2026-07-23T10:30:00",
        requester="Иван",
        client="Клиент A",
        message="Просьба проверить отчет",
        result="Проверил и отправил правки",
        start_time="10:30",
        end_time="10:55",
        duration_minutes=25,
        tags="report",
    )

    path = append_work_entry(settings, entry, now=now)

    assert path == work_log_path(settings, now)
    rows = read_recent_work_entries(settings, now=now)
    assert rows[-1]["requester"] == "Иван"
    assert rows[-1]["duration_minutes"] == 25


def test_work_entry_respects_privacy_mode(tmp_path):
    settings = AppSettings(base_directory=str(tmp_path), database_path=str(tmp_path / "db.sqlite"), privacy_mode_enabled=True)
    entry = WorkLogEntry(
        timestamp="2026-07-23T10:30:00",
        requester="login=admin",
        client="Клиент A",
        message="password=hunter2",
        result="api_key=sk-1234567890abcdef123456",
        start_time="10:30",
        end_time="10:35",
        duration_minutes=5,
    )

    path = append_work_entry(settings, entry, now=datetime(2026, 7, 23, 10, 30))
    data = path.read_text(encoding="utf-8")

    assert "hunter2" not in data
    assert "sk-1234567890abcdef123456" not in data


def test_client_normalization_prevents_duplicates(tmp_path):
    service = WorkLogService(AppSettings(base_directory=str(tmp_path / "ocr"), database_path=str(tmp_path / "db.sqlite")))
    first = service.upsert_client("Доступная страна")
    second = service.upsert_client("  доступная   страна  ")
    assert first == second
    assert service.list_clients("страна") == ["доступная страна"]


def test_contacts_are_linked_and_filtered_by_client(tmp_path):
    service = WorkLogService(AppSettings(base_directory=str(tmp_path / "ocr"), database_path=str(tmp_path / "db.sqlite")))
    client_id = service.upsert_client("Доступная страна")
    contact_id = service.upsert_contact(client_id, "Алена Китаева")
    assert contact_id is not None
    assert service.list_contacts("Доступная страна", "Кит") == ["Алена Китаева"]


def test_update_delete_search_totals_export_and_draft(tmp_path):
    settings = AppSettings(base_directory=str(tmp_path / "ocr"), database_path=str(tmp_path / "db.sqlite"), privacy_mode_enabled=True)
    service = WorkLogService(settings)
    entry = WorkLogEntry(
        work_date="2026-07-23",
        client="Доступная страна",
        requester="Алена Китаева",
        category="Клиент, без оплаты",
        message="Не проводится заказ",
        result="Проверил заказ",
        start_time="10:20",
        end_time="11:10",
        duration_minutes=0,
    )
    saved = service.save_entry(entry)
    assert saved.id is not None
    assert saved.duration_minutes == 50
    assert saved.status == "Готово"
    saved.result = "Проверил заказ и исправил дату"
    updated = service.save_entry(saved)
    assert service.get_entry(updated.id).result.endswith("дату")
    assert service.list_entries("2026-07-23", search="дату")
    assert service.day_totals("2026-07-23")["Клиенты"] == 50
    export = service.export_manual_work_log("2026-07-23")
    assert export.exists()
    assert "Проверил заказ и исправил дату" in export.read_text(encoding="utf-8")
    service.save_draft({"client": "Черновик"})
    assert service.load_draft()["client"] == "Черновик"
    service.clear_draft()
    assert service.load_draft() is None
    service.delete_entry(updated.id)
    assert service.get_entry(updated.id) is None


def test_table_topic_uses_message_or_result_not_identifier():
    assert first_line("\nПроверка выгрузки карточек на ВБ", "1") == "Проверка выгрузки карточек на ВБ"
    assert first_line("", "Проверены настройки отчета") == "Проверены настройки отчета"
    assert first_line("", "") == "Описание не заполнено"


def test_work_entry_allows_empty_description_fields(tmp_path):
    service = WorkLogService(AppSettings(base_directory=str(tmp_path / "ocr"), database_path=str(tmp_path / "db.sqlite")))
    saved = service.save_entry(WorkLogEntry(work_date="2026-07-23", category="Без задач", start_time="10:00", end_time="10:20", message="", result=""))
    assert saved.status == "Готово"
    assert saved.message == ""
    assert saved.result == ""


def test_category_settings_preserve_inactive_used_category(tmp_path):
    service = WorkLogService(AppSettings(base_directory=str(tmp_path / "ocr"), database_path=str(tmp_path / "db.sqlite")))
    service.save_category(WorkCategory(name="Старая", summary_group="Без задач", is_active=True, sort_order=5))
    saved = service.save_entry(WorkLogEntry(work_date="2026-07-23", category="Старая", start_time="10:00", end_time="10:20"))
    service.save_category(WorkCategory(name="Новая", summary_group="Без задач", is_active=True, sort_order=5), original_name="Старая")
    assert service.get_entry(saved.id).category == "Старая"
    assert "Старая" not in service.list_category_names(active_only=True)
    assert service.get_category("Старая").is_active is False
    assert "Новая" in service.list_category_names(active_only=True)


def test_fast_idle_and_break_entries_update_totals(tmp_path):
    service = WorkLogService(AppSettings(base_directory=str(tmp_path / "ocr"), database_path=str(tmp_path / "db.sqlite")))
    service.save_entry(WorkLogEntry(work_date="2026-07-23", category="Без задач", start_time="10:00", end_time="10:20"))
    service.save_entry(WorkLogEntry(work_date="2026-07-23", category="Перерыв", start_time="10:20", end_time="10:40"))
    totals = service.day_totals("2026-07-23")
    assert totals["Без задач"] == 20
    assert totals["Перерыв"] == 20
    assert totals["Всего"] == 40
