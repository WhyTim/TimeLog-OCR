import json
import urllib.error

from app.update_service import GITHUB_RELEASES_API, check_for_updates


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_update_url_targets_the_real_repository():
    assert GITHUB_RELEASES_API == "https://api.github.com/repos/tvbttwork/Daily-report/releases/latest"


def test_update_check_reports_new_release(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: FakeResponse({"tag_name": "v9.0.0", "html_url": "https://example.test/release"}),
    )
    result = check_for_updates()
    assert result.ok is True
    assert result.latest_version == "9.0.0"
    assert result.url == "https://example.test/release"


def test_update_check_uses_optional_private_repo_token(monkeypatch):
    captured = {}

    def open_request(request, **_kwargs):
        captured["authorization"] = request.get_header("Authorization")
        return FakeResponse({"tag_name": "v1.4.0"})

    monkeypatch.setenv("TIMELOGOCR_GITHUB_TOKEN", "test-token")
    monkeypatch.setattr("urllib.request.urlopen", open_request)
    assert check_for_updates().ok is True
    assert captured["authorization"] == "Bearer test-token"


def test_private_repository_404_has_actionable_message(monkeypatch):
    def fail(*_args, **_kwargs):
        raise urllib.error.HTTPError(GITHUB_RELEASES_API, 404, "Not Found", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", fail)
    result = check_for_updates()
    assert result.ok is False
    assert "TIMELOGOCR_GITHUB_TOKEN" in result.message
