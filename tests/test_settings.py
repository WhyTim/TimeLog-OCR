import json

from app.settings import AppSettings, load_settings, save_settings, validate_settings


def test_settings_read_write(tmp_path):
    path = tmp_path / "config" / "settings.json"
    settings = AppSettings(interval_seconds=15, base_directory=str(tmp_path / "ocr"))
    save_settings(settings, path)
    loaded = load_settings(path)
    assert loaded.interval_seconds == 15
    assert loaded.base_directory == str(tmp_path / "ocr")
    assert loaded.save_monitor_screenshots is False


def test_invalid_settings_are_backed_up_and_replaced(tmp_path):
    path = tmp_path / "config" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"interval_seconds": "broken"}', encoding="utf-8")

    loaded = load_settings(path)

    assert loaded.interval_seconds == 10
    assert json.loads(path.read_text(encoding="utf-8"))["interval_seconds"] == 10
    assert (path.parent / "settings.broken.json").exists()


def test_settings_save_does_not_leave_temporary_file(tmp_path):
    path = tmp_path / "settings.json"
    save_settings(AppSettings(base_directory=str(tmp_path / "ocr")), path)
    assert path.exists()
    assert not path.with_name(path.name + ".tmp").exists()


def test_schedule_workdays_are_normalized_without_crashing(tmp_path):
    settings = AppSettings(
        base_directory=str(tmp_path / "ocr"),
        archive_directory=str(tmp_path / "archives"),
        transcriber_models_dir=str(tmp_path / "models"),
        scheduled_workdays=[0, "2", "bad", 9],
    )
    warnings = validate_settings(settings, strict_paths=False)
    assert settings.scheduled_workdays == [0, 2]
    assert len(warnings) == 2
