"""Opt-in lookup of web-search GitHub repositories before the maturity rule.

The flag defaults off, which is the pre-change path: no extra GitHub call and no verdict change.
Mocked GitHub responses only. No socket, and the scored v3 traces are not read.
"""
from __future__ import annotations

import json
import socket
from datetime import date, timedelta

import httpx
import pytest

from app.agents.relevance import (
    RelevanceClient,
    github_repo_slug,
    judge_discovery,
    search_prior_art,
    web_github_api_lookup_enabled,
)
from app.agents.repo_facts import RepoFactsClient
from app.github_auth import reset_for_tests
from app.tools.base import Budget, ToolBox, ToolResult
from app.tools.builtin import GitHubTool

RECENT = (date.today() - timedelta(days=10)).strftime("%Y-%m-%dT00:00:00Z")
OLD = "2020-01-01T00:00:00Z"
QUOTE = "Sync notes between machines"
CANDIDATE = {
    "repo_id": "me/candidate",
    "description": "A small sync tool for notes.",
    "readme_excerpt": "A small sync tool for notes.",
    "stars": 10,
    "free": True,
    "self_hostable": True,
}
TOKEN = "ghp_web_lookup_secret"


@pytest.fixture
def lookup_on(monkeypatch):
    """The only way the lookup and the UNKNOWN_MATURITY hold run."""
    monkeypatch.setenv("PHOENIX_WEB_GITHUB_API_LOOKUP", "on")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.delenv("PHOENIX_WEB_GITHUB_API_LOOKUP", raising=False)

    def blocked(*_args, **_kwargs):
        raise AssertionError("test tried to use the network")

    monkeypatch.setattr(socket, "create_connection", blocked)
    reset_for_tests()
    yield
    reset_for_tests()


def _llm(_prompt, _sys):
    return json.dumps({
        "verdict": "CANDIDATE",
        "reason": "novel",
        "problem_quote": "A small sync tool for notes",
        "alternative_repo": None,
        "alternative_quote": None,
    })


def _repo_body(**overrides):
    body = {
        "stargazers_count": 500,
        "pushed_at": RECENT,
        "archived": False,
        "license": {"spdx_id": "MIT"},
    }
    body.update(overrides)
    return body


class _Web:
    name = "web_search"

    def __init__(self, items_for_query):
        self.items_for_query = items_for_query

    def call(self, args):
        query = str(args.get("query") or "")
        items = self.items_for_query.get(query, [])
        return ToolResult(ok=True, data={"items": items}, error="", source_url="web", fetched_at="t")


def _search(url: str, handler, *, token: str = ""):
    calls: list[str] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return handler(request)

    web = _Web({"notes": [{"url": url, "title": "Widget", "snippet": QUOTE}]})
    box = ToolBox(
        tools={
            "github": GitHubTool(token=token, transport=httpx.MockTransport(wrapped)),
            "web_search": web,
        },
        budget=Budget(max_calls=30, max_seconds=30),
    )
    found = search_prior_art(CANDIDATE, ["notes"], box)
    return found, calls


def _annotate(row: dict) -> dict:
    row["same_problem"] = True
    row["overlap"] = "full"
    row["quotes"] = [QUOTE]
    row["quote_verified"] = True
    row["description"] = QUOTE
    row["page_kind"] = "product"
    return row


def _judge(tmp_path, rows, found=None):
    client = RelevanceClient(cache_dir=tmp_path, mock_handler=_llm)
    return judge_discovery(
        CANDIDATE,
        prior_rows=[],
        demand_rows=[],
        client=client,
        problem_alternatives=rows,
        source_status=getattr(found, "source_status", None) or {"github": "ok", "web": "ok"},
        source_counts=getattr(found, "source_counts", None) or {"github": 0, "web": len(rows)},
    )


def _web_row(found):
    rows = [row for row in found if row.get("source") == "web"]
    assert len(rows) == 1
    return rows[0]


def test_slug_accepts_a_repository_subpath_and_rejects_site_sections():
    assert github_repo_slug("https://github.com/JetBrains/youtrack-cli/releases") == "JetBrains/youtrack-cli"
    assert github_repo_slug("https://www.github.com/acme/widget.git?tab=readme") == "acme/widget"
    assert github_repo_slug("web:https://github.com/acme/widget/tree/main") == "acme/widget"
    assert github_repo_slug("https://github.com/topics/python") is None
    assert github_repo_slug("https://gist.github.com/acme/widget") is None
    assert github_repo_slug("https://example.com/acme/widget") is None


def test_active_well_starred_web_repo_blocks(tmp_path, lookup_on):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/search/repositories" in url:
            return httpx.Response(200, json={"items": []})
        return httpx.Response(200, json=_repo_body())

    found, calls = _search("https://github.com/acme/widget", handler)
    row = _annotate(_web_row(found))
    assert row["stars"] == 500
    assert row["pushed_at"] == RECENT
    assert row["archived"] is False
    assert row["licence_spdx"] == "MIT"
    assert row["web_github_lookup"] == "ok"
    assert any(url.endswith("/repos/acme/widget") for url in calls)
    judged = _judge(tmp_path, [row], found)
    assert judged[0] == "ALREADY_EXISTS"
    assert "acme/widget" in judged[1]


def test_archived_old_and_tiny_web_repos_do_not_block(tmp_path, lookup_on):
    cases = [
        _repo_body(stargazers_count=8000, archived=True),
        _repo_body(stargazers_count=8000, pushed_at=OLD),
        _repo_body(stargazers_count=3),
    ]
    for index, body in enumerate(cases):
        def handler(request: httpx.Request, payload=body) -> httpx.Response:
            if "/search/repositories" in str(request.url):
                return httpx.Response(200, json={"items": []})
            return httpx.Response(200, json=payload)

        found, _calls = _search(f"https://github.com/acme/case{index}", handler)
        row = _annotate(_web_row(found))
        judged = _judge(tmp_path / str(index), [row], found)
        assert judged[0] == "CANDIDATE", (index, judged[1])
        assert "UNKNOWN_MATURITY" not in judged[1]
        assert judged.must_beat
        assert "did not block" in judged.must_beat[0]["reason"]


def test_lookup_failure_is_unknown_and_needs_review_not_candidate(tmp_path, lookup_on):
    def handler(request: httpx.Request) -> httpx.Response:
        if "/search/repositories" in str(request.url):
            return httpx.Response(200, json={"items": []})
        return httpx.Response(500, json={"message": "nope"})

    found, _calls = _search("https://github.com/acme/widget", handler)
    row = _annotate(_web_row(found))
    assert row["stars"] == "unknown"
    assert row["stars"] != 0
    assert row["pushed_at"] == "unknown"
    assert row["licence_spdx"] == "unknown"
    assert row["archived"] == "unknown"
    assert row["web_github_lookup"] == "failed"
    assert found.source_status["github"] == "ok"
    judged = _judge(tmp_path, [row], found)
    assert judged[0] == "NEEDS_REVIEW"
    assert judged[0] != "CANDIDATE"
    assert judged[1].startswith("UNKNOWN_MATURITY: web:https://github.com/acme/widget")
    assert judged.evidence
    assert judged.evidence[0]["alternative"] == "web:https://github.com/acme/widget"


def test_releases_url_looks_up_the_repository_not_the_subpath(lookup_on):
    def handler(request: httpx.Request) -> httpx.Response:
        if "/search/repositories" in str(request.url):
            return httpx.Response(200, json={"items": []})
        return httpx.Response(200, json=_repo_body(stargazers_count=900))

    found, calls = _search("https://github.com/JetBrains/youtrack-cli/releases", handler)
    row = _web_row(found)
    assert row["stars"] == 900
    assert any(url.endswith("/repos/JetBrains/youtrack-cli") for url in calls)
    assert not any(url.rstrip("/").endswith("/releases") for url in calls)


def test_topics_url_is_not_looked_up(lookup_on):
    def handler(request: httpx.Request) -> httpx.Response:
        if "/search/repositories" in str(request.url):
            return httpx.Response(200, json={"items": []})
        return httpx.Response(500, json={"message": "should not be called"})

    found, calls = _search("https://github.com/topics/python", handler)
    row = _web_row(found)
    assert row["stars"] == 0
    assert "web_github_lookup" not in row
    assert not any("/repos/" in url for url in calls)


@pytest.mark.parametrize("flag", ["0", "off", "false", "no", "disabled", "OFF"])
def test_flag_off_keeps_placeholder_stars_and_candidate(tmp_path, monkeypatch, flag):
    monkeypatch.setenv("PHOENIX_WEB_GITHUB_API_LOOKUP", flag)

    def handler(request: httpx.Request) -> httpx.Response:
        if "/search/repositories" in str(request.url):
            return httpx.Response(200, json={"items": []})
        raise AssertionError("flag off still called the repo API")

    found, calls = _search("https://github.com/acme/widget", handler)
    row = _annotate(_web_row(found))
    assert row["stars"] == 0
    assert row["pushed_at"] == ""
    assert not any("/repos/" in url for url in calls)
    judged = _judge(tmp_path, [row], found)
    assert judged[0] == "CANDIDATE"
    assert "UNKNOWN_MATURITY" not in judged[1]
    assert "did not block" in judged.must_beat[0]["reason"]


@pytest.mark.parametrize("raw", [None, "", "0", "off", "false", "no", "disabled", "maybe"])
def test_lookup_stays_off_unless_the_value_is_explicitly_truthy(monkeypatch, raw):
    if raw is None:
        monkeypatch.delenv("PHOENIX_WEB_GITHUB_API_LOOKUP", raising=False)
    else:
        monkeypatch.setenv("PHOENIX_WEB_GITHUB_API_LOOKUP", raw)
    assert web_github_api_lookup_enabled() is False


@pytest.mark.parametrize("raw", ["1", "true", "yes", "on", "enabled", "TRUE", "On"])
def test_explicit_truthy_values_enable_the_lookup(monkeypatch, raw):
    monkeypatch.setenv("PHOENIX_WEB_GITHUB_API_LOOKUP", raw)
    assert web_github_api_lookup_enabled() is True


def test_default_off_matches_pre_pr_behaviour(tmp_path, monkeypatch):
    """Unset flag: no repo lookup, placeholder stars=0, and no UNKNOWN_MATURITY hold."""
    monkeypatch.delenv("PHOENIX_WEB_GITHUB_API_LOOKUP", raising=False)
    assert web_github_api_lookup_enabled() is False

    def handler(request: httpx.Request) -> httpx.Response:
        if "/search/repositories" in str(request.url):
            return httpx.Response(200, json={"items": []})
        raise AssertionError("default off called the repo API")

    found, calls = _search("https://github.com/acme/widget", handler)
    row = _annotate(_web_row(found))
    assert row["stars"] == 0
    assert row["pushed_at"] == ""
    assert "web_github_lookup" not in row
    assert not any("/repos/" in url for url in calls)
    judged = _judge(tmp_path, [row], found)
    assert judged[0] == "CANDIDATE"
    assert judged[0] != "ALREADY_EXISTS"
    assert "UNKNOWN_MATURITY" not in judged[1]
    assert "stars=0" in judged.must_beat[0]["reason"]

    unknown = _annotate({
        "alternative_id": "web:https://github.com/acme/widget",
        "url": "https://github.com/acme/widget",
        "source": "web",
        "stars": "unknown",
        "pushed_at": "unknown",
        "licence_spdx": "unknown",
        "archived": "unknown",
        "web_github_lookup": "failed",
        "description": QUOTE,
    })
    held = _judge(tmp_path / "unknown", [unknown])
    assert held[0] == "CANDIDATE"
    assert "UNKNOWN_MATURITY" not in held[1]


def test_lookups_are_sequential_and_capped_by_the_github_quota(lookup_on):
    class _GitHub:
        name = "github"

        def __init__(self):
            self.repo_calls: list[str] = []
            self.inflight = 0
            self.max_inflight = 0

        def call(self, args):
            if args.get("action") != "repo":
                return ToolResult(ok=True, data={"items": []}, error="", source_url="u", fetched_at="t")
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
            self.repo_calls.append(str(args.get("repo")))
            self.inflight -= 1
            return ToolResult(
                ok=True,
                data=_repo_body(stargazers_count=10, pushed_at=OLD),
                error="",
                source_url="u",
                fetched_at="t",
            )

    def items(prefix, count):
        return [{"url": f"https://github.com/acme/{prefix}{i}", "title": "t", "snippet": "s"} for i in range(count)]

    q2 = items("extra", 3) + [{"url": "https://github.com/acme/repo0/releases", "title": "rel", "snippet": "s"}]
    github = _GitHub()
    box = ToolBox(
        tools={"github": github, "web_search": _Web({"q1": items("repo", 6), "q2": q2})},
        budget=Budget(max_calls=40, max_seconds=30),
    )
    found = search_prior_art({"repo_id": "me/candidate"}, ["q1", "q2"], box)
    web_rows = [row for row in found if row.get("source") == "web"]
    looked_up = [row for row in web_rows if row.get("web_github_lookup") == "ok"]
    failed = [row for row in web_rows if row.get("web_github_lookup") == "failed"]
    assert github.max_inflight == 1
    assert len(github.repo_calls) == 6
    assert github.repo_calls.count("acme/repo0") == 1
    assert len(looked_up) == 7
    assert len(failed) == 3
    assert all(row["stars"] == 10 for row in looked_up)
    assert all(row["stars"] == "unknown" and row["stars"] != 0 for row in failed)


def test_token_is_sent_and_not_recorded(tmp_path, capsys, lookup_on):
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "/search/repositories" in str(request.url):
            return httpx.Response(200, json={"items": []})
        seen.append(request.headers.get("Authorization"))
        if len(seen) == 1:
            return httpx.Response(401, json={"message": "bad token"})
        return httpx.Response(200, json=_repo_body())

    found, _calls = _search("https://github.com/acme/widget", handler, token=TOKEN)
    row = _web_row(found)
    captured = capsys.readouterr()
    blob = json.dumps(row)
    assert row["stars"] == 500
    assert seen[0] == f"Bearer {TOKEN}"
    assert seen[1] is None
    assert TOKEN not in blob
    assert TOKEN not in captured.out
    assert TOKEN not in captured.err


def test_placeholder_zero_is_not_unknown_maturity(tmp_path, lookup_on):
    row = _annotate({
        "alternative_id": "web:https://github.com/acme/widget",
        "url": "https://github.com/acme/widget",
        "source": "web",
        "stars": 0,
        "pushed_at": "",
        "licence_spdx": "",
        "description": QUOTE,
    })
    judged = _judge(tmp_path, [row])
    assert judged[0] == "CANDIDATE"
    assert "UNKNOWN_MATURITY" not in judged[1]
    assert "stars=0" in judged.must_beat[0]["reason"]


def test_known_immature_facts_are_not_held_for_unknown_maturity(tmp_path, lookup_on):
    stale = _annotate({
        "alternative_id": "web:https://github.com/acme/old",
        "url": "https://github.com/acme/old",
        "source": "web",
        "stars": "unknown",
        "pushed_at": OLD,
        "licence_spdx": "unknown",
        "archived": "unknown",
        "web_github_lookup": "failed",
        "description": QUOTE,
    })
    archived = _annotate({
        "alternative_id": "web:https://github.com/acme/arch",
        "url": "https://github.com/acme/arch",
        "source": "web",
        "stars": "unknown",
        "pushed_at": "unknown",
        "licence_spdx": "MIT",
        "archived": True,
        "description": QUOTE,
    })
    for index, row in enumerate((stale, archived)):
        judged = _judge(tmp_path / str(index), [row])
        assert judged[0] == "CANDIDATE", judged[1]
        assert "UNKNOWN_MATURITY" not in judged[1]


def test_unverified_quote_does_not_hold_unknown_maturity(tmp_path, lookup_on):
    row = {
        "alternative_id": "web:https://github.com/acme/widget",
        "url": "https://github.com/acme/widget",
        "source": "web",
        "same_problem": True,
        "overlap": "full",
        "quotes": ["this sentence is not on the page"],
        "quote_verified": False,
        "description": QUOTE,
        "stars": "unknown",
        "pushed_at": "unknown",
        "licence_spdx": "unknown",
        "archived": "unknown",
        "web_github_lookup": "failed",
    }
    judged = _judge(tmp_path, [row])
    assert judged[0] == "CANDIDATE"
    assert "UNKNOWN_MATURITY" not in judged[1]


def test_already_exists_is_not_rewritten_by_an_unknown_row(tmp_path, lookup_on):
    mature = _annotate({
        "alternative_id": "other/mature",
        "url": "https://github.com/other/mature",
        "source": "github",
        "stars": 500,
        "pushed_at": RECENT,
        "archived": False,
        "licence_spdx": "MIT",
        "description": QUOTE,
    })
    unknown = _annotate({
        "alternative_id": "web:https://github.com/acme/widget",
        "url": "https://github.com/acme/widget",
        "source": "web",
        "stars": "unknown",
        "pushed_at": "unknown",
        "licence_spdx": "unknown",
        "archived": "unknown",
        "web_github_lookup": "failed",
        "description": QUOTE,
    })
    judged = _judge(tmp_path, [mature, unknown])
    assert judged[0] == "ALREADY_EXISTS"
    assert "UNKNOWN_MATURITY" not in judged[1]


def test_flag_off_does_not_hold_a_row_already_marked_unknown(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOENIX_WEB_GITHUB_API_LOOKUP", "off")
    row = _annotate({
        "alternative_id": "web:https://github.com/acme/widget",
        "url": "https://github.com/acme/widget",
        "source": "web",
        "stars": "unknown",
        "pushed_at": "unknown",
        "licence_spdx": "unknown",
        "archived": "unknown",
        "web_github_lookup": "failed",
        "description": QUOTE,
    })
    judged = _judge(tmp_path, [row])
    assert judged[0] == "CANDIDATE"
    assert "UNKNOWN_MATURITY" not in judged[1]


def test_facts_client_does_not_refetch_a_finished_web_lookup(tmp_path):
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("repo facts client refetched a web GitHub lookup")

    client = RepoFactsClient(cache_dir=tmp_path, transport=httpx.MockTransport(handler))
    filled = client.fetch_alternative({
        "url": "https://github.com/acme/widget",
        "alternative_id": "web:https://github.com/acme/widget",
        "stars": 500,
        "pushed_at": RECENT,
        "archived": False,
        "licence_spdx": "",
        "web_github_lookup": "ok",
    })
    failed = client.fetch_alternative({
        "url": "https://github.com/acme/widget",
        "stars": "unknown",
        "pushed_at": "unknown",
        "web_github_lookup": "failed",
    })
    assert filled["stars"] == 500
    assert filled["fetch_status"] == "ok"
    assert failed["stars"] == "unknown"
    assert failed["fetch_status"] == "unknown"
