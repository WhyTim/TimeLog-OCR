from __future__ import annotations

import json
import importlib.util
from datetime import datetime

import pytest

from app.link_journal import LinkJournal, extract_links
from app.privacy import SecretFinding, detect_secrets
from app.secret_vault import SecretVault

QT_AVAILABLE = importlib.util.find_spec("PySide6") is not None and importlib.util.find_spec("pytestqt") is not None


SAFE_URLS = [
    "https://app.holst.so/w/d6e1ce9c-094e-4597-98ad-ea1994ad63c1/all",
    "https://app.yoodli.ai/signin",
    "https://chatgpt.com/c/12345678-1234-1234-1234-123456789abc",
    "https://claude.ai/chat/12345678-1234-1234-1234-123456789abc",
    "https://teams.microsoft.com/l/chat/0/0",
    "https://meet.google.com/abc-defg-hij",
    "https://github.com/example/project/issues/123",
]


def test_safe_application_and_chat_urls_are_preserved_and_not_secrets():
    text = "\n".join(SAFE_URLS)
    assert extract_links(text) == SAFE_URLS
    assert detect_secrets(text) == []


def test_markdown_ocr_spacing_wrapping_and_query_redaction():
    text = (
        f"[Holst]({SAFE_URLS[0]}).\n"
        "https : //example.net/spaced-path,\n"
        "https://example.com/long/path/\ncontinued?token=SYNTHETIC_TEST_VALUE&view=all"
    )
    links = extract_links(text)
    assert SAFE_URLS[0] in links
    assert "https://example.net/spaced-path" in links
    assert "https://example.com/long/path/continued?token=[REDACTED]&view=all" in links


def test_ocr_spaces_inside_uuid_are_repaired_without_marking_it_secret():
    text = "https://app.holst.so/w/d6e1ce9c -094e-4597 -98ad-ea1994ad63c1/all"
    assert extract_links(text) == [SAFE_URLS[0]]
    assert detect_secrets(text) == []


def test_link_journal_migrates_old_rows_and_tracks_first_and_last_seen(tmp_path):
    path = tmp_path / "links.md"
    path.write_text(
        "# old\n\n| Время | Ссылка | Окно | Повторы |\n|---|---|---|---:|\n"
        f"| 2026-08-08T09:00:00 | {SAFE_URLS[1]} | Browser | 2 |\n",
        encoding="utf-8",
    )
    journal = LinkJournal(path)
    journal.add_from_text(SAFE_URLS[1], "Browser", datetime(2026, 8, 8, 10, 0))
    entry = journal.list_entries()[0]
    assert entry.first_seen == "2026-08-08T09:00:00"
    assert entry.last_seen == "2026-08-08T10:00:00"
    assert entry.count == 3


def test_vault_can_reveal_only_encrypted_current_records_and_migrates_legacy(tmp_path):
    key = 0x6D
    crypt = lambda value: bytes(byte ^ key for byte in value)
    vault = SecretVault(tmp_path / "vault", crypt, crypt)
    finding = SecretFinding("credential", "SyntheticOnly_123", 0, 17, "Synt…123")
    vault.add([finding], "Password field")
    record = vault.list_masked()[0]
    assert record["revealable"] == "yes"
    assert vault.get_value(record["id"]) == "SyntheticOnly_123"
    assert b"SyntheticOnly_123" not in vault.path.read_bytes()

    legacy = [{"id": "old", "timestamp": "2026-01-01T00:00:00", "category": "credential", "masked": "old***", "count": "1"}]
    vault.path.write_bytes(crypt(json.dumps(legacy).encode()))
    old = vault.list_masked()[0]
    assert old["first_seen"] == old["last_seen"] == "2026-01-01T00:00:00"
    assert old["revealable"] == "no"
    assert vault.get_value("old") is None


def test_vault_fails_closed_when_dpapi_context_cannot_decrypt(tmp_path):
    vault_path = tmp_path / "vault"
    vault_path.write_bytes(b"encrypted")
    vault = SecretVault(vault_path, lambda value: value, lambda _value: (_ for _ in ()).throw(RuntimeError("wrong user")))
    with pytest.raises(RuntimeError, match="wrong user"):
        vault.list_masked()


@pytest.mark.skipif(not QT_AVAILABLE, reason="PySide6/pytest-qt are not installed")
def test_vault_has_top_level_masked_view_and_explicit_temporary_reveal(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton
    from app.settings import AppSettings
    from app.ui.main_window import MainWindow

    key = 0x51
    crypt = lambda value: bytes(byte ^ key for byte in value)
    vault = SecretVault(tmp_path / "vault", crypt, crypt)
    finding = SecretFinding("credential", "SyntheticOnly_123", 0, 17, "Synt...123")
    vault.add([finding], "Synthetic window")
    settings = AppSettings(
        base_directory=str(tmp_path / "ocr"),
        database_path=str(tmp_path / "db.sqlite"),
        report_template_path=str(tmp_path / "report.txt"),
        secret_vault_path=str(tmp_path / "unused.vault"),
    )
    window = MainWindow(settings)
    qtbot.addWidget(window)
    window._vault = lambda: vault
    window.open_vault_window()
    window.vault_table.selectRow(0)
    assert "Защищённые данные" not in window.PAGE_NAMES
    assert window.vault_table.item(0, 3).text() == "Synt...123"
    assert all("SyntheticOnly_123" not in window.vault_table.item(0, column).text() for column in range(6))
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes)

    window.reveal_selected_vault()
    assert window.vault_revealed_value.text() == "SyntheticOnly_123"
    window.hide_revealed_vault()
    assert window.vault_revealed_value.text() == "Значение скрыто"

    window.copy_selected_vault()
    assert QApplication.clipboard().text() == "SyntheticOnly_123"
    QApplication.clipboard().setText("replaced by user")

    home_buttons = {button.text() for button in window.pages["Главная"].findChildren(QPushButton)}
    assert "Открыть защищённые данные" in home_buttons
    assert not {"Открыть ссылки за сегодня", "Открыть папку со ссылками", "Обновить список"} & home_buttons
