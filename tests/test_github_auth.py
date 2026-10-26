"""An expired GITHUB_TOKEN must not turn every gate-1 judgement into a source error."""

from __future__ import annotations

import httpx
import pytest

from app import github_auth
from app.agents.repo_facts import RepoFactsClient
from app.tools.builtin import GitHubTool


@pytest.fixture(autouse=True)
def _reset():
    github_auth.reset_for_tests()
    yield
    github_auth.reset_for_tests()


def _expired_token_server(seen: list[str | None]):
    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("Authorization")
        seen.append(auth)
        if auth:
            return httpx.Response(401, json={"message": "Bad credentials"})
        return httpx.Response(200, json={"items": [], "full_name": "o/r", "stargazers_count": 3})

    return httpx.MockTransport(handler)


def test_search_retries_without_an_expired_token_and_stops_sending_it(capsys):
    seen: list[str | None] = []
    tool = GitHubTool(token="ghp_expired", transport=_expired_token_server(seen))
    assert tool.call({"action": "repositories", "query": "x"}).ok
    assert tool.call({"action": "repositories", "query": "y"}).ok
    assert seen == ["Bearer ghp_expired", None, None]
    err = capsys.readouterr().err
    assert err.count("HTTP 401") == 1 and "ghp_expired" not in err


def test_repo_facts_survive_an_expired_token(tmp_path):
    seen: list[str | None] = []
    client = RepoFactsClient(token="ghp_expired", transport=_expired_token_server(seen))
    assert client._get("https://api.github.com/repos/o/r", "github") == {"items": [], "full_name": "o/r", "stargazers_count": 3}
    assert seen == ["Bearer ghp_expired", None]


def test_judge_repo_get_retries_without_the_token(monkeypatch):
    import urllib.error

    from scripts import judge_repo

    calls: list[bool] = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"full_name": "o/r"}'

    def fake_urlopen(req, timeout=30):
        has_auth = req.get_header("Authorization") is not None
        calls.append(has_auth)
        if has_auth:
            raise urllib.error.HTTPError(req.full_url, 401, "Bad credentials", {}, None)
        return _Resp()

    monkeypatch.setattr(judge_repo.urllib.request, "urlopen", fake_urlopen)
    assert judge_repo._get("https://api.github.com/repos/o/r", "ghp_expired") == {"full_name": "o/r"}
    assert calls == [True, False]


def test_load_env_file_falls_back_to_the_repository_root(tmp_path, monkeypatch):
    from pathlib import Path

    from app import console

    root = tmp_path / "repo"
    (root / "app").mkdir(parents=True)
    (root / ".env").write_text("PHOENIX_TEST_ROOT_ONLY=yes\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.delenv("PHOENIX_TEST_ROOT_ONLY", raising=False)
    monkeypatch.setattr(console, "__file__", str(root / "app" / "console.py"))
    console.load_env_file()
    import os

    assert os.environ.get("PHOENIX_TEST_ROOT_ONLY") == "yes"
    monkeypatch.delenv("PHOENIX_TEST_ROOT_ONLY", raising=False)
    assert Path(console.__file__).parent.name == "app"
