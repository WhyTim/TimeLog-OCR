import queue
from pathlib import Path

from app.capture_service import CaptureService
from app.settings import AppSettings


class FakeThread:
    def __init__(self, *args, **kwargs):
        self.alive = False

    def start(self):
        self.alive = True

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        self.alive = False


def test_start_resets_session_specific_state(monkeypatch, tmp_path):
    monkeypatch.setattr("app.capture_service.threading.Thread", FakeThread)
    service = CaptureService(AppSettings(base_directory=str(tmp_path)), queue.Queue())
    service._last_hashes["all"] = "old"
    service._duplicate_counts["all"] = 7
    service._touched_day_dirs.add(Path("old-day"))
    service._capture_failures = 2

    service.start()

    assert service._last_hashes == {}
    assert service._duplicate_counts == {}
    assert service._touched_day_dirs == set()
    assert service._capture_failures == 0
    assert service.state.session_started_at is not None

    service.stop()
    assert service.state.session_started_at is None
    assert service.is_running is False
