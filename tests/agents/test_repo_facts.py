"""Host-API facts. Mock transport only. The token stays out of the cache and out of logs."""
from datetime import date

import httpx

from app.agents.blocker import evaluate_blocker
from app.agents.relevance import search_prior_art
from app.agents.repo_facts import RepoFactsClient
from app.tools import Budget, GitHubTool, NpmTool, ToolBox, WebSearchTool


TOKEN = "ghp_test_secret_value"


def test_github_facts_are_cached_and_the_token_is_not_stored(tmp_path, capsys):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={
            "stargazers_count": 13439,
            "archived": False,
            "pushed_at": "2026-10-25T12:00:00Z",
            "license": {"spdx_id": "MIT"},
            "forks_count": 900,
        })

    client = RepoFactsClient(token=TOKEN, cache_dir=tmp_path, transport=httpx.MockTransport(handler))
    row = {"alternative_id": "gotenberg/gotenberg", "url": "https://github.com/gotenberg/gotenberg"}
    first = client.fetch_alternative(row)
    second = client.fetch_alternative(row)
    assert first["stars"] == 13439
    assert first["archived"] is False
    assert first["pushed_at"] == "2026-10-25T12:00:00Z"
    assert first["licence_spdx"] == "MIT"
    assert first["fetch_status"] == "ok"
    assert second["stars"] == 13439
    assert len(calls) == 1
    assert calls[0].headers["Authorization"] == f"Bearer {TOKEN}"
    cached = list(tmp_path.glob("*.json"))
    assert len(cached) == 1
    body = cached[0].read_text(encoding="utf-8")
    err = capsys.readouterr()
    assert TOKEN not in body
    assert TOKEN not in err.out
    assert TOKEN not in err.err


def test_codeberg_and_gitlab_use_that_hosts_api(tmp_path):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        url = str(request.url)
        if "codeberg.org" in url:
            return httpx.Response(200, json={
                "stars_count": 3,
                "archived": False,
                "updated_at": "2026-10-18T00:00:00Z",
                "forks_count": 0,
            })
        return httpx.Response(200, json={
            "star_count": 40,
            "archived": False,
            "last_activity_at": "2026-09-01T00:00:00Z",
            "forks_count": 2,
        })

    client = RepoFactsClient(token=TOKEN, cache_dir=tmp_path, transport=httpx.MockTransport(handler))
    codeberg = client.fetch_alternative({"url": "https://codeberg.org/saturn-sync/saturn", "alternative_id": "saturn"})
    gitlab = client.fetch_alternative({"url": "https://gitlab.com/group/proj", "alternative_id": "group/proj"})
    assert any(url == "https://codeberg.org/api/v1/repos/saturn-sync/saturn" for url in seen)
    assert any("gitlab.com/api/v4/projects/group%2Fproj" in url for url in seen)
    assert codeberg["host"] == "codeberg" and codeberg["stars"] == 3
    assert gitlab["host"] == "gitlab" and gitlab["stars"] == 40
    assert codeberg["fetch_status"] == "ok"
    blob = "".join(path.read_text(encoding="utf-8") for path in tmp_path.glob("*.json"))
    assert TOKEN not in blob


def test_a_fetch_failure_is_source_error_and_cannot_block(tmp_path):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "nope"})

    client = RepoFactsClient(cache_dir=tmp_path, transport=httpx.MockTransport(handler))
    row = client.fetch_alternative({"alternative_id": "someone/missing", "url": "https://github.com/someone/missing"})
    assert row["fetch_status"] == "source_error"
    decision = evaluate_blocker(
        {"stars": 10, "free": True, "self_hostable": True},
        row,
        as_of=date(2026, 10, 25),
    )
    assert decision["blocks"] is False
    assert decision["role"] == "source_error"
    assert decision["verdict_if_blocked"] is None


def test_web_found_github_repo_keeps_the_host_star_count(tmp_path):
    """rosswintle/sync-raindrop arrived from web search with unknown stars, not a measured zero."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "mcp.exa.ai" in url:
            text = (
                "Title: Sync Raindrop\n"
                "URL: https://github.com/rosswintle/sync-raindrop\n"
                "Highlights:\n"
                "Sync Raindrop bookmarks to a folder\n\n"
            )
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": text}]}})
        if "hn.algolia.com" in url:
            return httpx.Response(200, json={"hits": []})
        if "api.github.com/search/repositories" in url or "registry.npmjs.org" in url:
            return httpx.Response(200, json={"items": [], "objects": [], "total": 0})
        if url.rstrip("/").endswith("/repos/rosswintle/sync-raindrop"):
            return httpx.Response(200, json={
                "stargazers_count": 128,
                "archived": False,
                "pushed_at": "2026-08-01T00:00:00Z",
                "license": {"spdx_id": "MIT"},
                "forks_count": 4,
            })
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    toolbox = ToolBox(
        tools={
            "github": GitHubTool(transport=transport),
            "npm": NpmTool(transport=transport),
            "web_search": WebSearchTool(transport=transport),
        },
        budget=Budget(max_calls=20, max_seconds=30.0),
    )
    found = search_prior_art({"repo_id": "me/linkblog"}, ["sync raindrop bookmarks"], toolbox)
    row = next(item for item in found if "rosswintle/sync-raindrop" in str(item.get("alternative_id")))
    assert row["source"] == "web"
    assert row["stars"] is None
    filled = RepoFactsClient(cache_dir=tmp_path, transport=transport).fetch_alternative(row)
    assert filled["stars"] == 128
    assert filled["pushed_at"] == "2026-08-01T00:00:00Z"
    assert filled["licence_spdx"] == "MIT"


def test_a_complete_github_row_with_zero_stars_is_not_refetched(tmp_path):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={
            "stargazers_count": 999,
            "archived": False,
            "pushed_at": "2026-08-01T00:00:00Z",
            "license": {"spdx_id": "MIT"},
        })

    client = RepoFactsClient(cache_dir=tmp_path, transport=httpx.MockTransport(handler))
    row = client.fetch_alternative({
        "alternative_id": "someone/empty",
        "url": "https://github.com/someone/empty",
        "source": "github",
        "stars": 0,
        "pushed_at": "2026-01-01T00:00:00Z",
        "archived": False,
        "licence_spdx": "MIT",
    })
    assert row["stars"] == 0
    assert calls == []


def test_rescue_fetch_reads_ci_and_bot_pull_requests(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/repos/ri-char/pwatch"):
            return httpx.Response(200, json={
                "stargazers_count": 265,
                "forks_count": 64,
                "open_issues_count": 4,
                "pushed_at": "2024-07-06T00:00:00Z",
                "archived": False,
                "default_branch": "main",
                "license": {"spdx_id": "MIT"},
            })
        if "/actions/workflows" in url:
            return httpx.Response(200, json={"total_count": 2, "workflows": []})
        if "/actions/runs" in url:
            return httpx.Response(200, json={"total_count": 0, "workflow_runs": []})
        if "is%3Aissue" in url or "is:issue" in url:
            return httpx.Response(200, json={"items": [
                {"title": "i can't use it successful", "comments": 0, "user": {"login": "a"}},
            ]})
        if "dependabot" in url:
            return httpx.Response(200, json={"total_count": 2})
        if "renovate" in url:
            return httpx.Response(200, json={"total_count": 1})
        return httpx.Response(404)

    client = RepoFactsClient(token=TOKEN, cache_dir=tmp_path, transport=httpx.MockTransport(handler))
    facts = client.fetch_rescue("ri-char/pwatch")
    assert facts["fetch_status"] == "ok"
    assert facts["stars"] == 265
    assert facts["ci_state"] == "ci_configured_zero_runs"
    assert facts["external_breakage"] == 1
    assert facts["dependabot_open"] == 2
    assert facts["renovate_open"] == 1
    blob = "".join(path.read_text(encoding="utf-8") for path in tmp_path.glob("*.json"))
    assert TOKEN not in blob
