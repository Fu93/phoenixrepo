"""Tests for capped sub-agents, tool allowlists, child budgets, and external demand rules."""

from __future__ import annotations

import httpx
import pytest

from app.agents.subagents import (
    CRITERION_TOOL_ALLOWLIST,
    SubAgent,
    SubAgentManager,
    is_candidate_repo_url,
)
from app.tools import Budget, GitHubTool, NpmTool, PyPiTool, ToolBox, WebFetchTool, ZetarisSqlTool


def _create_mock_toolbox(calls: int = 20) -> ToolBox:
    def mock_transport_fn(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "api.github.com/search/issues" in url_str:
            return httpx.Response(200, json={"items": [{"title": "Foliant issue 80", "comments": 2}]})
        if "api.github.com/repos/SergioMendolia/ytkb" in url_str:
            return httpx.Response(200, json={"stargazers_count": 0, "forks_count": 0, "name": "ytkb"})
        if "registry.npmjs.org" in url_str:
            return httpx.Response(200, json={"name": "test-pkg", "description": "Useful package"})
        if "pypi.org" in url_str:
            return httpx.Response(200, json={"info": {"name": "test-py", "summary": "Useful py"}})
        return httpx.Response(200, text="Competitor pricing: $10/user/month")

    transport = httpx.MockTransport(mock_transport_fn)
    tools = {
        "github": GitHubTool(transport=transport),
        "npm": NpmTool(transport=transport),
        "pypi": PyPiTool(transport=transport),
        "web_fetch": WebFetchTool(transport=transport),
        "zetaris_sql": ZetarisSqlTool(),
    }
    return ToolBox(tools=tools, budget=Budget(max_calls=calls))


def test_subagent_works_on_one_criterion_and_one_repo():
    tb = _create_mock_toolbox()
    sub = SubAgent(criterion="demand_evidence", repo_id="SergioMendolia/ytkb", toolbox=tb)
    assert sub.criterion == "demand_evidence"
    assert sub.repo_id == "SergioMendolia/ytkb"


def test_subagent_tool_allowlist_enforced():
    tb = _create_mock_toolbox()

    # 1. demand_evidence allows github, web_fetch, npm, pypi; NOT zetaris_sql
    sub_demand = SubAgent(criterion="demand_evidence", repo_id="SergioMendolia/ytkb", toolbox=tb)
    assert set(sub_demand.toolbox.tools.keys()) == {"github", "web_fetch", "npm", "pypi"}
    res_disallowed = sub_demand.toolbox.call("zetaris_sql", {"sql": "SELECT 1"})
    assert res_disallowed.ok is False
    assert "Unknown tool 'zetaris_sql'" in res_disallowed.error

    # 2. feasibility allows ONLY github
    sub_feas = SubAgent(criterion="feasibility", repo_id="SergioMendolia/ytkb", toolbox=tb)
    assert set(sub_feas.toolbox.tools.keys()) == {"github"}
    res_web = sub_feas.toolbox.call("web_fetch", {"url": "https://example.com"})
    assert res_web.ok is False
    assert "Unknown tool 'web_fetch'" in res_web.error

    # 3. moat allows ONLY web_fetch
    sub_moat = SubAgent(criterion="moat", repo_id="SergioMendolia/ytkb", toolbox=tb)
    assert set(sub_moat.toolbox.tools.keys()) == {"web_fetch"}
    res_gh = sub_moat.toolbox.call("github", {"repo": "SergioMendolia/ytkb"})
    assert res_gh.ok is False
    assert "Unknown tool 'github'" in res_gh.error

    # 4. productization_path allows ONLY web_fetch
    sub_prod = SubAgent(criterion="productization_path", repo_id="SergioMendolia/ytkb", toolbox=tb)
    assert set(sub_prod.toolbox.tools.keys()) == {"web_fetch"}


def test_child_budget_carved_from_parent():
    tb = _create_mock_toolbox(calls=10)
    assert tb.budget.remaining_calls == 10

    # SubAgent gets child budget of max 3 calls
    sub = SubAgent(criterion="demand_evidence", repo_id="SergioMendolia/ytkb", toolbox=tb, max_calls=3)
    assert sub.toolbox.budget.remaining_calls == 3

    # Make 3 calls through sub-agent
    r1 = sub.toolbox.call("github", {"query": "test1"})
    r2 = sub.toolbox.call("github", {"query": "test2"})
    r3 = sub.toolbox.call("github", {"query": "test3"})
    assert r1.ok is True
    assert r2.ok is True
    assert r3.ok is True

    # 4th call through sub-agent fails due to child budget
    r4 = sub.toolbox.call("github", {"query": "test4"})
    assert r4.ok is False
    assert r4.error == "BUDGET_EXHAUSTED"

    # Parent budget consumed 3 calls (10 - 3 = 7 remaining)
    assert tb.budget.remaining_calls == 7
    assert tb.budget.calls_used == 3


def test_subagent_cannot_spawn_subagents():
    tb = _create_mock_toolbox()
    sub = SubAgent(criterion="demand_evidence", repo_id="SergioMendolia/ytkb", toolbox=tb)
    with pytest.raises(RuntimeError, match="Sub-agents cannot spawn sub-agents"):
        sub.spawn_subagent("feasibility", "repo")


def test_subagent_hard_cap_enforced():
    tb = _create_mock_toolbox()
    manager = SubAgentManager(max_subagents=3)

    s1 = manager.spawn("demand_evidence", "repo1", tb)
    s2 = manager.spawn("feasibility", "repo1", tb)
    s3 = manager.spawn("moat", "repo1", tb)
    assert manager.spawned_count == 3
    assert manager.can_spawn() is False

    with pytest.raises(RuntimeError, match="Sub-agent cap reached"):
        manager.spawn("productization_path", "repo1", tb)


def test_subagent_cannot_use_candidate_repo_stars_as_demand():
    def mock_transport_fn(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "api.github.com/repos/candidate/repo" in url_str:
            # Candidate repo metadata claiming stars
            return httpx.Response(200, json={"stargazers_count": 50, "description": "candidate has 50 stars"})
        if "alternative/repo" in url_str or "search/issues" in url_str:
            return httpx.Response(200, json={"items": [{"title": "External demand on alternative", "comments": 5}]})
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(mock_transport_fn)
    tools = {"github": GitHubTool(transport=transport)}
    tb = ToolBox(tools=tools, budget=Budget(max_calls=10))

    sub = SubAgent(criterion="demand_evidence", repo_id="candidate/repo", toolbox=tb)

    # 1. Inspect URL helper
    assert is_candidate_repo_url("https://api.github.com/repos/candidate/repo", "candidate/repo") is True
    assert is_candidate_repo_url("https://api.github.com/repos/other/repo", "candidate/repo") is False

    # 2. Investigating alternative repo succeeds and returns external evidence
    items = sub.investigate(alternative_repos=["alternative/repo"])
    assert len(items) == 1
    assert "External demand" in items[0]["text"]
    assert items[0]["id"].startswith("tool:")

    # 3. Investigating with query pointing to candidate repo itself is rejected from demand
    items_candidate = sub.investigate(query="repo:candidate/repo")
    # Must NOT include candidate repo as demand evidence
    for item in items_candidate:
        assert "candidate/repo" not in item["url"]


def test_demand_issue_search_skips_non_github_alternative_ids():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"items": [{"title": "External demand on alternative", "comments": 1}]})

    tools = {"github": GitHubTool(transport=httpx.MockTransport(handler))}
    sub = SubAgent(
        criterion="demand_evidence",
        repo_id="candidate/repo",
        toolbox=ToolBox(tools=tools, budget=Budget(max_calls=10)),
    )
    sub.investigate(alternative_repos=[
        "web:https://appwrite.io/docs",
        "npm:appwrite",
        "https://example.com/post",
        "other/notes-alt",
    ])
    blob = " ".join(seen)
    assert "notes-alt" in blob
    assert "appwrite.io" not in blob
    assert "web%3A" not in blob and "web:" not in blob
    assert "npm%3A" not in blob and "npm:" not in blob
    assert "example.com" not in blob
