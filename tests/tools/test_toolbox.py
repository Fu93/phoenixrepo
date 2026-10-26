"""Tests for ToolBox, budget, trace hash-chaining, secrets redaction, and builtin tools."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.adapters.sample_zetaris import SampleZetaris
from app.tools import (
    Budget,
    GitHubTool,
    N8nTool,
    NpmTool,
    PyPiTool,
    ToolBox,
    ToolResult,
    WebFetchTool,
    ZetarisSqlTool,
)
from app.trace_log import HackathonTrace
from scripts.verify_trace import verify


def test_tool_result_sha256_and_evidence_item():
    result = ToolResult(
        ok=True,
        data={"metric": 42, "status": "active"},
        error="",
        source_url="https://example.com/api",
        fetched_at="2026-10-22T20:00:00Z",
    )
    assert result.sha256
    assert result.evidence_id.startswith("tool:")
    item = result.to_evidence_item(repo_id="test/repo")
    assert item["id"] == result.evidence_id
    assert item["url"] == "https://example.com/api"
    assert item["content_hash"] == result.sha256
    assert item["repo_id"] == "test/repo"


def test_every_tool_call_is_logged_and_trace_verifies(tmp_path):
    trace = HackathonTrace("tools-test", tmp_path)
    budget = Budget(max_calls=10)

    def mock_fetch(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="Mock web content for verification")

    transport = httpx.MockTransport(mock_fetch)
    web_tool = WebFetchTool(transport=transport)

    toolbox = ToolBox(tools={"web_fetch": web_tool}, budget=budget, trace=trace)

    # 1. Successful call
    res1 = toolbox.call("web_fetch", {"url": "https://example.com/doc"})
    assert res1.ok is True
    assert "Mock web content" in res1.data

    # 2. Failed call (unknown tool)
    res2 = toolbox.call("nonexistent_tool", {"foo": "bar"})
    assert res2.ok is False
    assert "Unknown tool" in res2.error

    # Seal trace and verify cryptographic hash chain
    run_hash = trace.seal()
    ok, detail = verify(trace.path, expected_run_hash=run_hash)
    assert ok is True
    assert detail == run_hash

    # Verify log content
    lines = [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()]
    tool_events = [line for line in lines if line.get("action") == "TOOL_CALL"]
    assert len(tool_events) == 2
    assert tool_events[0]["status"] == "ok"
    assert "example.com" in tool_events[0]["output_summary"]


def test_secrets_are_redacted_in_trace_log(tmp_path):
    trace = HackathonTrace("secrets-test", tmp_path, secrets=["SUPER_SECRET_VALUE_999"])
    budget = Budget(max_calls=5)

    def mock_fetch(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(mock_fetch)
    web_tool = WebFetchTool(transport=transport)
    toolbox = ToolBox(tools={"web_fetch": web_tool}, budget=budget, trace=trace)

    # Call with sensitive parameters
    toolbox.call(
        "web_fetch",
        {
            "url": "https://example.com/doc",
            "api_key": "SECRET_API_KEY_123",
            "token": "ghp_TOKEN_456",
            "password": "mypassword",
            "note": "using SUPER_SECRET_VALUE_999 here",
        },
    )

    run_hash = trace.seal()
    ok, _ = verify(trace.path, expected_run_hash=run_hash)
    assert ok is True

    log_text = trace.path.read_text(encoding="utf-8")
    assert "SECRET_API_KEY_123" not in log_text
    assert "ghp_TOKEN_456" not in log_text
    assert "mypassword" not in log_text
    assert "SUPER_SECRET_VALUE_999" not in log_text
    assert "[REDACTED]" in log_text


def test_budget_exhaustion_behavior(tmp_path):
    trace = HackathonTrace("budget-test", tmp_path)
    budget = Budget(max_calls=2)

    def mock_fetch(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="hello")

    transport = httpx.MockTransport(mock_fetch)
    web_tool = WebFetchTool(transport=transport)
    toolbox = ToolBox(tools={"web_fetch": web_tool}, budget=budget, trace=trace)

    res1 = toolbox.call("web_fetch", {"url": "https://example.com/1"})
    assert res1.ok is True

    res2 = toolbox.call("web_fetch", {"url": "https://example.com/2"})
    assert res2.ok is True

    # 3rd call exceeds budget of 2
    res3 = toolbox.call("web_fetch", {"url": "https://example.com/3"})
    assert res3.ok is False
    assert res3.error == "BUDGET_EXHAUSTED"

    # Even exhausted calls are appended to trace
    run_hash = trace.seal()
    ok, _ = verify(trace.path, expected_run_hash=run_hash)
    assert ok is True

    lines = [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()]
    exhausted_events = [l for l in lines if l.get("status") == "BUDGET_EXHAUSTED"]
    assert len(exhausted_events) == 1


def test_n8n_tool_disabled_when_env_unset(monkeypatch):
    monkeypatch.delenv("N8N_WEBHOOK_URL", raising=False)
    tool = N8nTool(webhook_url=None)
    res = tool.call({"url": "https://github.com/foo/bar"})
    assert res.ok is False
    assert res.error == "n8n disabled"


def test_n8n_tool_enabled_when_configured():
    def mock_n8n(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://localhost:5678/webhook/phoenix-evidence"
        body = json.loads(request.content)
        assert body["url"] == "https://api.github.com/zen"
        return httpx.Response(200, json={"url": body["url"], "status": 200, "text_excerpt": "Practicality beats purity."})

    transport = httpx.MockTransport(mock_n8n)
    tool = N8nTool(webhook_url="http://localhost:5678/webhook/phoenix-evidence", transport=transport)
    res = tool.call({"url": "https://api.github.com/zen"})
    assert res.ok is True
    assert res.data["text_excerpt"] == "Practicality beats purity."


def test_github_tool_repo_and_issues_search():
    def mock_gh(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "/search/issues" in url_str:
            return httpx.Response(
                200,
                json={"total_count": 2, "items": [{"title": "Sync issue", "comments": 3}]},
            )
        if "/readme" in url_str:
            return httpx.Response(
                200,
                json={"name": "README.md", "content": "IyBZZXNcbkl0IHdvcmtzLg=="},  # "# Yes\nIt works."
            )
        return httpx.Response(200, json={"name": "ytkb", "full_name": "SergioMendolia/ytkb", "stargazers_count": 0})

    transport = httpx.MockTransport(mock_gh)
    tool = GitHubTool(transport=transport, token="test-token")

    # Metadata
    res_repo = tool.call({"repo": "SergioMendolia/ytkb"})
    assert res_repo.ok is True
    assert res_repo.data["full_name"] == "SergioMendolia/ytkb"

    # Readme
    res_readme = tool.call({"repo": "SergioMendolia/ytkb", "action": "readme"})
    assert res_readme.ok is True
    assert "decoded_text" in res_readme.data
    assert "Yes" in res_readme.data["decoded_text"]

    # Issues search
    res_issues = tool.call({"query": "repo:foliant-docs/foliant youtrack", "action": "issues"})
    assert res_issues.ok is True
    assert res_issues.data["total_count"] == 2


def test_npm_and_pypi_tools():
    def mock_registries(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "npmjs.org" in url_str:
            return httpx.Response(200, json={"name": "plop", "description": "Micro-generator", "versions": {"1.0.0": {}}})
        if "pypi.org" in url_str:
            return httpx.Response(200, json={"info": {"name": "youtrack", "summary": "YouTrack REST client"}})
        return httpx.Response(404)

    transport = httpx.MockTransport(mock_registries)
    npm_tool = NpmTool(transport=transport)
    pypi_tool = PyPiTool(transport=transport)

    res_npm = npm_tool.call({"package": "plop"})
    assert res_npm.ok is True
    assert res_npm.data["description"] == "Micro-generator"

    res_pypi = pypi_tool.call({"name": "youtrack"})
    assert res_pypi.ok is True
    assert res_pypi.data["summary"] == "YouTrack REST client"


def test_zetaris_sql_tool_sample():
    client = SampleZetaris()
    tool = ZetarisSqlTool(client=client)
    res = tool.call({"sql": "SELECT repo_id, licence_spdx FROM phoenix.repo_evidence WHERE repo_id = 'jondot/hygen'"})
    assert res.ok is True
    assert len(res.data["rows"]) == 1
    assert res.data["rows"][0]["repo_id"] == "jondot/hygen"
