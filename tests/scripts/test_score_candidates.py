"""Tests for scripts/score_candidates.py using fake offline transports."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import httpx
import pytest

from app.agents.relevance import RelevanceClient, RelevanceLLMError
from app.sql_catalog import DEMAND_COLUMNS, DISCOVERY_COLUMNS, PRIOR_ART_COLUMNS
from app.tools import (
    Budget,
    GitHubTool,
    NpmTool,
    PyPiTool,
    ToolBox,
    WebFetchTool,
    ZetarisSqlTool,
)
from scripts.score_candidates import score_all
from scripts.verify_trace import verify

ROOT = Path(__file__).resolve().parents[2]
REAL_DATA = ROOT / "data" / "real"


def _create_mock_toolbox(gh_status: int = 200, gh_body: dict | None = None) -> ToolBox:
    def mock_transport_fn(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "api.github.com" in url_str:
            if gh_status in {403, 429}:
                return httpx.Response(gh_status, text="API rate limit exceeded")
            if "/search/issues" in url_str:
                if "foliant" in url_str:
                    return httpx.Response(200, json={"items": [{"title": "Sync YouTrack knowledge base articles", "html_url": "https://github.com/foliant-docs/foliant/issues/42", "comments": 2}]})
                if "billpeet" in url_str:
                    return httpx.Response(200, json={"items": [{"title": "Demand pull: push changes back to YouTrack knowledge base", "html_url": "https://github.com/billpeet/yt-cli/issues/10", "comments": 3}]})
                return httpx.Response(200, json={"items": [{"title": "Sync knowledge base articles from YouTrack", "html_url": "https://github.com/external/other/issues/1", "comments": 1}]})
            if "/search/repositories" in url_str:
                if "invoice" in url_str:
                    return httpx.Response(
                        200,
                        json={
                            "items": [
                                {
                                    "full_name": "al1abb/invoify",
                                    "description": "Invoify is a free, web-based invoice generator",
                                    "license": {"spdx_id": "MIT"},
                                    "stargazers_count": 6376,
                                    "pushed_at": "2026-08-24T02:55:34Z",
                                }
                            ]
                        },
                    )
                if "youtrack" in url_str:
                    return httpx.Response(
                        200,
                        json={
                            "items": [
                                {
                                    "full_name": "billpeet/yt-cli",
                                    "description": "YouTrack CLI tool for AI agent and developer use — text/markdown output by default",
                                    "license": {"spdx_id": "MIT"},
                                    "stargazers_count": 0,
                                    "pushed_at": "2026-08-17T07:39:14Z",
                                },
                                {
                                    "full_name": "foliant-docs/foliant",
                                    "description": "Comprehensive markdown-based documentation toolkit Foliant is an all-in-one single-source documentation authoring tool.",
                                    "license": {"spdx_id": "MIT"},
                                    "stargazers_count": 177,
                                    "pushed_at": "2026-03-29T13:40:56Z",
                                },
                            ]
                        },
                    )
                return httpx.Response(200, json={"items": []})
            if "/readme" in url_str:
                return httpx.Response(200, json={"name": "README.md", "content": "IyBZZXNcbkl0IHdvcmtzLg=="})
            return httpx.Response(200, json={"name": "ytkb", "stargazers_count": 0})
        if "registry.npmjs.org" in url_str:
            return httpx.Response(200, json={"name": "pkg", "description": "Package"})
        if "pypi.org" in url_str:
            return httpx.Response(200, json={"info": {"name": "py-pkg"}})
        return httpx.Response(200, text="Competitor analysis: SaaS tier available.")

    transport = httpx.MockTransport(mock_transport_fn)
    tools = {
        "github": GitHubTool(transport=transport),
        "npm": NpmTool(transport=transport),
        "pypi": PyPiTool(transport=transport),
        "web_fetch": WebFetchTool(transport=transport),
        "zetaris_sql": ZetarisSqlTool(),
    }
    # Room for two full prior-art searches. With 40 calls the second repository's search was cut
    # short, which used to pass silently as CANDIDATE and is now (correctly) NEEDS_REVIEW.
    return ToolBox(tools=tools, budget=Budget(max_calls=200, max_seconds=300.0))


def _create_mock_relevance_client(tmp_path: Path) -> RelevanceClient:
    def handler(prompt: str, system_prompt: str) -> str:
        if "Relevance Problem Extractor" in system_prompt or "Extract the problem statement" in prompt or "relevance_problem" in system_prompt:
            if "invoice" in prompt.lower() or "tharani" in prompt.lower():
                return json.dumps({
                    "problem": "Creating and managing invoices with PDF export and email delivery",
                    "target_user": "businesses and freelancers",
                    "category": "finance-tools",
                    "search_queries": ["invoice generator", "invoice pdf export"],
                    "quotes": ["A full-stack web application for creating, managing, and sending professional invoices with multiple customizable templates."],
                })
            return json.dumps({
                "problem": "Syncing YouTrack knowledge base articles to local markdown files",
                "target_user": "developers",
                "category": "developer-tools",
                "search_queries": ["youtrack knowledge base sync", "youtrack markdown cli"],
                "quotes": ["A CLI application for syncing YouTrack knowledge base articles to local markdown files, edit them in your editor of choice and push them back online."],
            })
        if "Relevance Alternative Judge" in system_prompt or "Judge whether this alternative solves the same core user problem" in prompt or "relevance_alternative" in system_prompt:
            if "invoify" in prompt.lower():
                return json.dumps({
                    "same_problem": True,
                    "overlap": "full",
                    "reason": "Invoify is a free web-based invoice generator solving the exact same invoicing problem.",
                    "quotes": ["Invoify is a free, web-based invoice generator"],
                })
            if "yt-cli" in prompt.lower():
                return json.dumps({
                    "same_problem": False,
                    "overlap": "none",
                    "reason": "yt-cli is a generic YouTrack CLI that does not sync knowledge base articles to local markdown (fixture: adjacent tool, different problem).",
                    "quotes": ["YouTrack CLI tool for AI agent and developer use"],
                })
            return json.dumps({
                "same_problem": False,
                "overlap": "none",
                "reason": "Alternative does not solve the same problem.",
                "quotes": [],
            })
        if "Relevance Discovery Judge" in system_prompt or "relevance_discovery" in system_prompt or "Judge whether this candidate is CANDIDATE" in prompt:
            if "invoice" in prompt.lower() or "tharani" in prompt.lower():
                return json.dumps({
                    "verdict": "ALREADY_EXISTS",
                    "reason": "An existing maintained free alternative invoify already covers invoice generation.",
                    "problem_quote": "A full-stack web application for creating, managing, and sending professional invoices with multiple customizable templates.",
                    "alternative_repo": "al1abb/invoify",
                    "alternative_quote": "Invoify is a free, web-based invoice generator",
                })
            return json.dumps({
                "verdict": "CANDIDATE",
                "reason": "Solves real problem of syncing YouTrack KB to local markdown.",
                "problem_quote": "A CLI application for syncing YouTrack knowledge base articles to local markdown files, edit them in your editor of choice and push them back online.",
                "alternative_repo": None,
                "alternative_quote": None,
            })
        if "Extract up to 5 user-facing capabilities" in prompt:
            return json.dumps({
                "capabilities": [
                    {
                        "name": "download knowledge base pages",
                        "quote": "Download all pages from a YouTrack knowledge base, preserving hierarchy and order",
                    },
                    {
                        "name": "push changes back to YouTrack",
                        "quote": "Push changes back to YouTrack (update only, no creation)",
                    },
                    {
                        "name": "compare local files with server versions",
                        "quote": "Compare local files with server versions to see what's changed",
                    },
                ]
            })
        if "Judge if this signal represents demand" in prompt:
            for q in [
                "Sync YouTrack knowledge base articles",
                "Demand pull: push changes back to YouTrack knowledge base",
                "Sync knowledge base articles from YouTrack",
            ]:
                if q in prompt:
                    return json.dumps({
                        "relevant": True,
                        "capability": "push changes back to YouTrack" if "push changes back" in q else "download knowledge base pages",
                        "quote": q,
                        "why": "External issue explicitly requests this capability.",
                    })
            return json.dumps({"relevant": False, "capability": None, "quote": None, "why": "No match"})
        if "Judge if the alternative covers this capability" in prompt:
            return json.dumps({"covered": False, "quote": "", "why": "Alternative does not cover"})
        return json.dumps({})

    return RelevanceClient(mock_handler=handler, cache_dir=tmp_path / "rel_cache")


def test_score_all_offline_selects_protagonist_and_verifies_trace(tmp_path):
    output_json = tmp_path / "venture_scores.json"
    output_md = tmp_path / "venture_scores.md"
    log_dir = tmp_path / "logs"
    run_id = "test-offline-score"

    toolbox = _create_mock_toolbox()
    rel_client = _create_mock_relevance_client(tmp_path)

    results = score_all(
        data_dir=REAL_DATA,
        output_json=output_json,
        output_md=output_md,
        max_calls=40,
        max_seconds=300.0,
        use_llm=False,
        run_id=run_id,
        log_dir=log_dir,
        custom_toolbox=toolbox,
        command_str="score_candidates.py --test",
        relevance_client=rel_client,
    )

    # 1. Output files exist
    assert output_json.exists()
    assert output_md.exists()

    # 2. Protagonist selected from real data
    prot = results["protagonist"]
    assert prot["repo_id"] == "SergioMendolia/ytkb"
    assert prot["total"] >= 50.0
    assert prot["feasibility"] >= 2
    assert "Selected protagonist" in prot["reason"]

    # 3. Ineligible repos listed with existing reason
    ineligible = results["ineligible"]
    assert len(ineligible) >= 1
    invoice = next(item for item in ineligible if item["repo_id"] == "tharani-2006/Invoice-generator-application")
    assert invoice["verdict"] == "ALREADY_EXISTS"
    assert "invoify" in invoice["reason"]
    evidence = invoice.get("decision_evidence") or []
    assert evidence and evidence[0]["alternative"] == "al1abb/invoify"
    assert evidence[0]["overlap"] == "full"
    assert "Invoify is a free, web-based invoice generator" in evidence[0]["quote"]
    assert evidence[0]["reason"]
    assert invoice["rescue_potential"]["changes_verdict"] is False
    assert "score" in invoice["rescue_potential"]
    ytkb = next(item for item in results["candidates"] if item["repo_id"] == "SergioMendolia/ytkb")
    assert ytkb["rescue_potential"]["changes_verdict"] is False
    assert invoice["verdict"] == "ALREADY_EXISTS"

    # 4. JSON structure & run hash
    saved_json = json.loads(output_json.read_text(encoding="utf-8"))
    assert saved_json["run_hash"] == results["run_hash"]
    assert saved_json["protagonist"]["repo_id"] == "SergioMendolia/ytkb"

    # 5. Every cited evidence id exists in evidence list, and all items store text
    for cand in saved_json["candidates"]:
        known_eids = {e["id"] for e in cand["evidence"]}
        for cname, cinfo in cand["criteria"].items():
            for eid in cinfo.get("evidence_ids") or []:
                assert eid in known_eids, f"Cited evidence id {eid} in {cname} missing from evidence array!"
        for e in cand["evidence"]:
            assert e.get("text") is not None, f"Evidence item {e['id']} missing text!"

    # 6. Markdown contains run hash, tables, and trivial selection note
    md_text = output_md.read_text(encoding="utf-8")
    assert results["run_hash"] in md_text
    assert "SergioMendolia/ytkb" in md_text
    assert "tharani-2006/Invoice-generator-application" in md_text
    assert "ALREADY_EXISTS" in md_text
    assert "This score does not change the verdict. A refused repository stays refused." in md_text
    assert "selection is trivial: only one eligible candidate" in md_text

    # 7. Criteria rationales contain required quotes and checks
    cand_crit = saved_json["candidates"][0]["criteria"]
    diff_crit = cand_crit["differentiation"]
    assert "Missing capabilities" in diff_crit["rationale"]
    assert "Alternatives checked" in diff_crit["rationale"]
    assert "Candidate quote" in diff_crit["rationale"]

    dem_crit = cand_crit["demand_evidence"]
    assert dem_crit["score"] >= 1
    assert "http" in dem_crit["rationale"]

    # 7. Trace hash chain verifies
    trace_path = log_dir / f"trace-{run_id}.jsonl"
    assert trace_path.exists()
    ok, run_hash = verify(trace_path, expected_run_hash=results["run_hash"])
    assert ok is True
    assert run_hash == results["run_hash"]


def test_rate_limited_handled_cleanly(tmp_path):
    output_json = tmp_path / "venture_scores_rl.json"
    output_md = tmp_path / "venture_scores_rl.md"
    log_dir = tmp_path / "logs"
    run_id = "test-rate-limit"

    # Toolbox returning HTTP 403 Rate limit
    toolbox = _create_mock_toolbox(gh_status=403)

    results = score_all(
        data_dir=REAL_DATA,
        output_json=output_json,
        output_md=output_md,
        max_calls=40,
        max_seconds=300.0,
        use_llm=False,
        run_id=run_id,
        log_dir=log_dir,
        custom_toolbox=toolbox,
        relevance_client=_create_mock_relevance_client(tmp_path),
    )

    # A rate-limited GitHub search is a source error (README: "A search that errors is a source
    # error: the verdict is NEEDS_REVIEW with INSUFFICIENT_PRIOR_ART_COVERAGE"). This test used to
    # expect CANDIDATE here: every GitHub query failed, one npm row was checked, and ytkb was
    # still picked as the protagonist. The run must stop cleanly and leave the call to a human.
    assert results["candidates"] == []
    ytkb = next(r for r in results["ineligible"] if r["repo_id"] == "SergioMendolia/ytkb")
    assert ytkb["verdict"] == "NEEDS_REVIEW"
    assert "INSUFFICIENT_PRIOR_ART_COVERAGE" in ytkb["reason"]
    assert "github" in ytkb["reason"]
    assert not (results.get("protagonist") or {}).get("repo_id")

    # Trace verifies
    trace_path = log_dir / f"trace-{run_id}.jsonl"
    ok, _ = verify(trace_path, expected_run_hash=results["run_hash"])
    assert ok is True





def test_llm_failure_falls_back_to_deterministic_baseline(tmp_path, monkeypatch):
    output_json = tmp_path / "venture_scores_fallback.json"
    output_md = tmp_path / "venture_scores_fallback.md"
    log_dir = tmp_path / "logs"
    run_id = "test-llm-fallback"

    toolbox = _create_mock_toolbox()

    # Pass use_llm=True with invalid/failing LLM environment
    monkeypatch.delenv("METERLESS_RUNTIME_TOKEN", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "meterless")

    results = score_all(
        data_dir=REAL_DATA,
        output_json=output_json,
        output_md=output_md,
        max_calls=40,
        max_seconds=300.0,
        use_llm=True,  # Request LLM
        run_id=run_id,
        log_dir=log_dir,
        custom_toolbox=toolbox,
    )

    # Fallback to deterministic baseline
    cand = results["candidates"][0]
    assert "LLM_FALLBACK" in cand["flags"]
    assert results["protagonist"]["repo_id"] == "SergioMendolia/ytkb"
    assert results["protagonist"]["total"] >= 20.0

    # Trace verifies
    trace_path = log_dir / f"trace-{run_id}.jsonl"
    ok, _ = verify(trace_path, expected_run_hash=results["run_hash"])
    assert ok is True


def test_empty_text_tool_item_scores_zero_or_fallback(tmp_path):
    output_json = tmp_path / "venture_scores_empty.json"
    output_md = tmp_path / "venture_scores_empty.md"
    log_dir = tmp_path / "logs"
    run_id = "test-empty-text"

    def mock_transport_fn(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "/search/repositories" in url_str:
            # Prior-art search succeeds and finds one unrelated repo, so coverage is adequate
            return httpx.Response(200, json={"items": [{"full_name": "someone/unrelated-tool", "html_url": "https://github.com/someone/unrelated-tool", "description": "Unrelated tool"}]})
        if "registry.npmjs.org" in url_str:
            return httpx.Response(200, json={"objects": [], "total": 0})
        if "pypi.org/search" in url_str:
            return httpx.Response(200, text="<html><body><div>There were no results</div></body></html>")
        if "api.github.com" in url_str:
            if "/search/issues" in url_str:
                # Empty text / empty items: does not qualify
                return httpx.Response(200, json={"items": []})
            if "/readme" in url_str:
                return httpx.Response(200, json={"name": "README.md", "content": "IyBZZXNcbkl0IHdvcmtzLg=="})
            return httpx.Response(200, json={"name": "ytkb", "stargazers_count": 0})
        return httpx.Response(200, text="")

    transport = httpx.MockTransport(mock_transport_fn)
    tools = {
        "github": GitHubTool(transport=transport),
        "npm": NpmTool(transport=transport),
        "pypi": PyPiTool(transport=transport),
        "web_fetch": WebFetchTool(transport=transport),
        "zetaris_sql": ZetarisSqlTool(),
    }
    toolbox = ToolBox(tools=tools, budget=Budget(max_calls=40, max_seconds=300.0))

    results = score_all(
        data_dir=REAL_DATA,
        output_json=output_json,
        output_md=output_md,
        max_calls=40,
        max_seconds=300.0,
        use_llm=False,
        run_id=run_id,
        log_dir=log_dir,
        custom_toolbox=toolbox,
        relevance_client=_create_mock_relevance_client(tmp_path),
    )

    cand = results["candidates"][0]
    dem = cand["criteria"]["demand_evidence"]
    # Empty tool items ignored, and PR in stored signals rejected -> score 0
    assert dem["score"] == 0
    assert len(dem["evidence_ids"]) == 0


def test_unrelated_text_tool_item_is_ignored_and_falls_back(tmp_path):
    output_json = tmp_path / "venture_scores_unrelated.json"
    output_md = tmp_path / "venture_scores_unrelated.md"
    log_dir = tmp_path / "logs"
    run_id = "test-unrelated-text"

    def mock_transport_fn(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "/search/repositories" in url_str:
            # Prior-art search succeeds and finds one unrelated repo, so coverage is adequate
            return httpx.Response(200, json={"items": [{"full_name": "someone/unrelated-tool", "html_url": "https://github.com/someone/unrelated-tool", "description": "Unrelated tool"}]})
        if "registry.npmjs.org" in url_str:
            return httpx.Response(200, json={"objects": [], "total": 0})
        if "pypi.org/search" in url_str:
            return httpx.Response(200, text="<html><body><div>There were no results</div></body></html>")
        if "api.github.com" in url_str:
            if "/search/issues" in url_str:
                # Non-empty text, but completely unrelated to candidate core_terms
                return httpx.Response(200, json={"items": [{"title": "Fix memory leak in Python C-extension on Linux", "comments": 5}]})
            if "/readme" in url_str:
                return httpx.Response(200, json={"name": "README.md", "content": "IyBZZXNcbkl0IHdvcmtzLg=="})
            return httpx.Response(200, json={"name": "ytkb", "stargazers_count": 0})
        return httpx.Response(200, text="Unrelated text about compilers")

    transport = httpx.MockTransport(mock_transport_fn)
    tools = {
        "github": GitHubTool(transport=transport),
        "npm": NpmTool(transport=transport),
        "pypi": PyPiTool(transport=transport),
        "web_fetch": WebFetchTool(transport=transport),
        "zetaris_sql": ZetarisSqlTool(),
    }
    toolbox = ToolBox(tools=tools, budget=Budget(max_calls=40, max_seconds=300.0))

    results = score_all(
        data_dir=REAL_DATA,
        output_json=output_json,
        output_md=output_md,
        max_calls=40,
        max_seconds=300.0,
        use_llm=False,
        run_id=run_id,
        log_dir=log_dir,
        custom_toolbox=toolbox,
        relevance_client=_create_mock_relevance_client(tmp_path),
    )

    cand = results["candidates"][0]
    dem = cand["criteria"]["demand_evidence"]
    # Unrelated tool items do not count, and PR in stored signals rejected -> score 0
    assert dem["score"] == 0
    assert len(dem["evidence_ids"]) == 0


def test_differentiation_requires_quotes_and_evaluates_gaps():
    from scripts.score_candidates import compute_differentiation

    candidate = {
        "repo_id": "test/candidate",
        "core_terms": "missing_feature|another_gap",
        "description": "A tool with missing_feature support and another_gap integration built in.",
    }
    prior_rows = [
        {
            "alternative_id": "comp/free-alt",
            "url": "https://github.com/comp/free-alt",
            "licence_spdx": "MIT",
            "pushed_at": "2026-10-01T00:00:00Z",
            "days_since_push": 10,
            "excerpt": "A tool that does not do missing_feature or anything related.",
        }
    ]
    demand_rows = [
        {
            "signal_id": "sig-1",
            "url": "https://github.com/comp/free-alt/issues/12",
            "excerpt": "Users desperately need missing_feature in this project please!",
        }
    ]
    cand_ev_id = "candidate:test/candidate"
    evidence_index = {
        cand_ev_id: {
            "id": cand_ev_id,
            "url": "https://github.com/test/candidate",
            "text": candidate["description"],
        },
        "demand:sig-1": {
            "id": "demand:sig-1",
            "url": "https://github.com/comp/free-alt/issues/12",
            "text": demand_rows[0]["excerpt"],
        },
    }

    crit = compute_differentiation(candidate, prior_rows, demand_rows, evidence_index)
    # 2 missing capabilities ("missing_feature", "another_gap") + external user demand quote in demand:sig-1 -> score 3
    assert crit.score == 3
    assert "missing_feature" in crit.rationale
    assert "another_gap" in crit.rationale
    assert "comp/free-alt" in crit.rationale
    # Exact quotes present
    assert "Candidate quotes" in crit.rationale
    assert "External user demand quote" in crit.rationale


def test_three_candidates_different_evidence_strength_different_totals(tmp_path):
    """Offline test verifying that candidates of different evidence strength receive strictly different totals."""
    snap_dir = tmp_path / "snapshot"
    snap_dir.mkdir(parents=True, exist_ok=True)
    out_json = tmp_path / "scores.json"
    out_md = tmp_path / "scores.md"
    log_dir = tmp_path / "logs"

    collected_at = "2026-10-22T23:00:00+00:00"
    pushed_at = "2024-06-01T00:00:00Z"
    from app.agents.market_filter import days_between
    days_str = str(days_between(pushed_at, collected_at))

    # 1. discovery_candidates.csv
    candidates = [
        {
            "repo_id": "weak/candidate-tool",
            "html_url": "https://github.com/weak/candidate-tool",
            "description": "Simple basic text converter.",
            "readme_excerpt": "Simple basic text converter.\nBuilt for developers.\n- convert text\n- basic formatting",
            "problem_excerpt": "Simple basic text converter.",
            "user_excerpt": "developers",
            "scope_excerpt": "convert text",
            "code_paths": "src/main.py|src/util.py",
            "created_at": "2024-01-01T00:00:00Z",
            "pushed_at": pushed_at,
            "days_since_push": days_str,
            "stargazers_count": "0",
            "forks_count": "0",
            "open_issues": "0",
            "licence_spdx": "MIT",
            "core_terms": "convert text|basic formatting",
            "cohort": "discovery",
            "data_origin": "test",
            "collected_at": collected_at,
        },
        {
            "repo_id": "medium/runner-system",
            "html_url": "https://github.com/medium/runner-system",
            "description": "Task execution runner with queuing and error retry logic.",
            "readme_excerpt": "Task execution runner with queuing and error retry logic.\nBuilt for devops teams.\n- task queuing\n- error retry logic",
            "problem_excerpt": "Task execution runner with queuing and error retry logic.",
            "user_excerpt": "devops teams",
            "scope_excerpt": "task queuing",
            "code_paths": "src/main.py|src/runner.py|src/queue.py",
            "created_at": "2024-01-01T00:00:00Z",
            "pushed_at": pushed_at,
            "days_since_push": days_str,
            "stargazers_count": "0",
            "forks_count": "0",
            "open_issues": "0",
            "licence_spdx": "MIT",
            "core_terms": "task queuing|error retry",
            "cohort": "discovery",
            "data_origin": "test",
            "collected_at": collected_at,
        },
        {
            "repo_id": "strong/data-pipeline",
            "html_url": "https://github.com/strong/data-pipeline",
            "description": "Enterprise cloud data pipeline with distributed consensus, metrics, and cloud backup. Flexible SaaS subscription pricing.",
            "readme_excerpt": "Enterprise cloud data pipeline with distributed consensus, metrics, and cloud backup. Flexible SaaS subscription pricing.\nBuilt for data engineering teams.\n- distributed consensus\n- cloud backup\n- metrics collection",
            "problem_excerpt": "Enterprise cloud data pipeline with distributed consensus, metrics, and cloud backup. Flexible SaaS subscription pricing.",
            "user_excerpt": "data engineering teams",
            "scope_excerpt": "distributed consensus",
            "code_paths": "src/main.go|src/engine.go|src/consensus.go|src/metrics.go|src/storage.go|tests/engine_test.go|.github/workflows/ci.yml",
            "created_at": "2024-01-01T00:00:00Z",
            "pushed_at": pushed_at,
            "days_since_push": days_str,
            "stargazers_count": "0",
            "forks_count": "0",
            "open_issues": "0",
            "licence_spdx": "Apache-2.0",
            "core_terms": "distributed consensus|cloud backup",
            "cohort": "discovery",
            "data_origin": "test",
            "collected_at": collected_at,
        },
    ]

    # 2. prior_art.csv
    prior_rows = [
        # Weak: alternative covers basic formatting (so only 1 missing capability)
        {
            "repo_id": "weak/candidate-tool",
            "alternative_id": "comp/text-alt",
            "url": "https://github.com/comp/text-alt",
            "licence_spdx": "MIT",
            "stars": "150",
            "pushed_at": "2026-10-01T00:00:00Z",
            "days_since_push": "21",
            "excerpt": "A tool that does basic formatting easily.",
            "candidate_excerpt": "Simple basic text converter.",
            "required_terms": "convert text|basic formatting",
            "data_origin": "test",
            "collected_at": collected_at,
        },
        # Medium: alternative unmaintained, lacks features (2 missing capabilities, no user request quote -> diff 2)
        {
            "repo_id": "medium/runner-system",
            "alternative_id": "comp/runner-alt",
            "url": "https://github.com/comp/runner-alt",
            "licence_spdx": "MIT",
            "stars": "50",
            "pushed_at": "2024-01-01T00:00:00Z",
            "days_since_push": "900",
            "excerpt": "Old abandoned runner without features.",
            "candidate_excerpt": "Task execution runner with queuing and error retry logic.",
            "required_terms": "task queuing|error retry",
            "data_origin": "test",
            "collected_at": collected_at,
        },
        # Strong: alternative unmaintained, lacks features (2 missing capabilities + user request in issues -> diff 3)
        {
            "repo_id": "strong/data-pipeline",
            "alternative_id": "comp/pipeline-alt",
            "url": "https://github.com/comp/pipeline-alt",
            "licence_spdx": "MIT",
            "stars": "120",
            "pushed_at": "2024-01-01T00:00:00Z",
            "days_since_push": "900",
            "excerpt": "Legacy pipeline without consensus or backup.",
            "candidate_excerpt": "Enterprise cloud data pipeline with distributed consensus, metrics, and cloud backup.",
            "required_terms": "distributed consensus|cloud backup",
            "data_origin": "test",
            "collected_at": collected_at,
        },
    ]

    # 3. demand_signals.csv
    demand_rows = [
        {
            "repo_id": "weak/candidate-tool",
            "signal_id": "dem-weak",
            "kind": "github_issue",
            "url": "https://github.com/external/text-repo/issues/10",
            "excerpt": "We need convert text and basic formatting in our text processing pipeline.",
            "comments": "3",
            "reactions": "2",
            "stars": "",
            "pushed_at": "",
            "days_since_push": "",
            "query": "convert text basic formatting",
            "data_origin": "test",
            "collected_at": collected_at,
        },
        {
            "repo_id": "medium/runner-system",
            "signal_id": "dem-med",
            "kind": "related_repo",
            "url": "https://github.com/external/runner-repo/pull/25",
            "excerpt": "Please implement task queuing and error retry for failed jobs.",
            "comments": "5",
            "reactions": "4",
            "stars": "150",
            "pushed_at": "2026-09-01T00:00:00Z",
            "days_since_push": "51",
            "query": "task queuing error retry",
            "data_origin": "test",
            "collected_at": collected_at,
        },
        {
            "repo_id": "strong/data-pipeline",
            "signal_id": "dem-strong",
            "kind": "github_issue",
            "url": "https://github.com/external/pipeline-repo/issues/99",
            "excerpt": "Users urgently need distributed consensus and cloud backup for high availability.",
            "comments": "12",
            "reactions": "10",
            "stars": "",
            "pushed_at": "",
            "days_since_push": "",
            "query": "distributed consensus cloud backup",
            "data_origin": "test",
            "collected_at": collected_at,
        },
    ]

    # Write CSVs
    def _write(path: Path, cols: tuple[str, ...], rows: list[dict[str, str]]) -> None:
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(cols), lineterminator="\n")
            w.writeheader()
            for r in rows:
                w.writerow({c: r.get(c, "") for c in cols})

    _write(snap_dir / "discovery_candidates.csv", DISCOVERY_COLUMNS, candidates)
    _write(snap_dir / "prior_art.csv", PRIOR_ART_COLUMNS, prior_rows)
    _write(snap_dir / "demand_signals.csv", DEMAND_COLUMNS, demand_rows)

    def mock_transport_fn(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": []})

    transport = httpx.MockTransport(mock_transport_fn)
    tools = {
        "github": GitHubTool(transport=transport),
        "npm": NpmTool(transport=transport),
        "pypi": PyPiTool(transport=transport),
        "web_fetch": WebFetchTool(transport=transport),
        "zetaris_sql": ZetarisSqlTool(),
    }
    toolbox = ToolBox(tools=tools, budget=Budget(max_calls=40, max_seconds=300.0))

    results = score_all(
        data_dir=snap_dir,
        output_json=out_json,
        output_md=out_md,
        max_calls=40,
        max_seconds=300.0,
        use_llm=False,
        run_id="test-three-candidates",
        log_dir=log_dir,
        custom_toolbox=toolbox,
    )

    cands = {c["repo_id"]: c for c in results["candidates"]}
    assert len(cands) == 3

    # All three must receive strictly different total scores!
    total_weak = cands["weak/candidate-tool"]["total"]
    total_med = cands["medium/runner-system"]["total"]
    total_strong = cands["strong/data-pipeline"]["total"]

    assert total_weak < total_med < total_strong
    assert len({total_weak, total_med, total_strong}) == 3

    # Strong candidate selected as protagonist
    assert results["protagonist"]["repo_id"] == "strong/data-pipeline"


def test_scorer_fails_when_not_discriminating(tmp_path, capsys):
    """Fail the run (exit non-zero) if more than 50% of eligible candidates share an identical score vector."""
    snap_dir = tmp_path / "snapshot_nodiscrim"
    snap_dir.mkdir(parents=True, exist_ok=True)

    collected_at = "2026-10-22T23:00:00+00:00"
    pushed_at = "2024-06-01T00:00:00Z"
    from app.agents.market_filter import days_between
    days_str = str(days_between(pushed_at, collected_at))

    # 4 candidates with identical characteristics -> identical score vector
    candidates = []
    prior_rows = []
    demand_rows = []
    for i in range(1, 5):
        rid = f"clone/repo-{i}"
        candidates.append(
            {
                "repo_id": rid,
                "html_url": f"https://github.com/{rid}",
                "description": "Standard tool for syncing local markdown notes.",
                "readme_excerpt": "Standard tool for syncing local markdown notes.\nBuilt for developers.\n- sync markdown\n- local notes",
                "problem_excerpt": "Standard tool for syncing local markdown notes.",
                "user_excerpt": "developers",
                "scope_excerpt": "sync markdown",
                "code_paths": "src/main.py|src/util.py|src/sync.py",
                "created_at": "2024-01-01T00:00:00Z",
                "pushed_at": pushed_at,
                "days_since_push": days_str,
                "stargazers_count": "0",
                "forks_count": "0",
                "open_issues": "0",
                "licence_spdx": "MIT",
                "core_terms": "sync markdown|local notes",
                "cohort": "discovery",
                "data_origin": "test",
                "collected_at": collected_at,
            }
        )
        prior_rows.append(
            {
                "repo_id": rid,
                "alternative_id": f"comp/alt-{i}",
                "url": f"https://github.com/comp/alt-{i}",
                "licence_spdx": "MIT",
                "stars": "50",
                "pushed_at": "2024-01-01T00:00:00Z",
                "days_since_push": "900",
                "excerpt": "Unmaintained tool without sync features.",
                "candidate_excerpt": "Standard tool for syncing local markdown notes.",
                "required_terms": "sync markdown|local notes",
                "data_origin": "test",
                "collected_at": collected_at,
            }
        )
        demand_rows.append(
            {
                "repo_id": rid,
                "signal_id": f"dem-{i}",
                "kind": "github_issue",
                "url": f"https://github.com/external/issue/{i}",
                "excerpt": "Please implement sync markdown and local notes for our team.",
                "comments": "3",
                "reactions": "2",
                "stars": "",
                "pushed_at": "",
                "days_since_push": "",
                "query": "sync markdown local notes",
                "data_origin": "test",
                "collected_at": collected_at,
            }
        )

    def _write(path: Path, cols: tuple[str, ...], rows: list[dict[str, str]]) -> None:
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(cols), lineterminator="\n")
            w.writeheader()
            for r in rows:
                w.writerow({c: r.get(c, "") for c in cols})

    _write(snap_dir / "discovery_candidates.csv", DISCOVERY_COLUMNS, candidates)
    _write(snap_dir / "prior_art.csv", PRIOR_ART_COLUMNS, prior_rows)
    _write(snap_dir / "demand_signals.csv", DEMAND_COLUMNS, demand_rows)

    def mock_transport_fn(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": []})

    transport = httpx.MockTransport(mock_transport_fn)
    tools = {
        "github": GitHubTool(transport=transport),
        "npm": NpmTool(transport=transport),
        "pypi": PyPiTool(transport=transport),
        "web_fetch": WebFetchTool(transport=transport),
        "zetaris_sql": ZetarisSqlTool(),
    }
    toolbox = ToolBox(tools=tools, budget=Budget(max_calls=40, max_seconds=300.0))

    with pytest.raises(SystemExit) as exc_info:
        score_all(
            data_dir=snap_dir,
            output_json=tmp_path / "out.json",
            output_md=tmp_path / "out.md",
            max_calls=40,
            max_seconds=300.0,
            use_llm=False,
            custom_toolbox=toolbox,
        )

    assert exc_info.value.code != 0
    captured = capsys.readouterr()
    assert "SCORER NOT DISCRIMINATING" in captured.err


def test_zero_alternatives_marked_needs_prior_art(tmp_path):
    """Candidate with zero alternatives checked is marked NEEDS_PRIOR_ART and not scored."""
    snap_dir = tmp_path / "snapshot_no_prior"
    snap_dir.mkdir(parents=True, exist_ok=True)

    collected_at = "2026-10-22T23:00:00+00:00"
    pushed_at = "2024-06-01T00:00:00Z"
    from app.agents.market_filter import days_between
    days_str = str(days_between(pushed_at, collected_at))

    candidates = [
        {
            "repo_id": "lone/solo-project",
            "html_url": "https://github.com/lone/solo-project",
            "description": "Unique standalone idea with no alternatives checked.",
            "readme_excerpt": "Unique standalone idea with no alternatives checked.\nBuilt for developers.\n- feature one\n- feature two",
            "problem_excerpt": "Unique standalone idea with no alternatives checked.",
            "user_excerpt": "developers",
            "scope_excerpt": "feature one",
            "code_paths": "src/main.py|src/util.py",
            "created_at": "2024-01-01T00:00:00Z",
            "pushed_at": pushed_at,
            "days_since_push": days_str,
            "stargazers_count": "0",
            "forks_count": "0",
            "open_issues": "0",
            "licence_spdx": "MIT",
            "core_terms": "feature one|feature two",
            "cohort": "discovery",
            "data_origin": "test",
            "collected_at": collected_at,
        }
    ]
    # Zero prior art rows!
    prior_rows = []
    demand_rows = [
        {
            "repo_id": "lone/solo-project",
            "signal_id": "dem-solo",
            "kind": "github_issue",
            "url": "https://github.com/external/issue/1",
            "excerpt": "We need feature one and feature two please.",
            "comments": "3",
            "reactions": "2",
            "stars": "",
            "pushed_at": "",
            "days_since_push": "",
            "query": "feature one feature two",
            "data_origin": "test",
            "collected_at": collected_at,
        }
    ]

    def _write(path: Path, cols: tuple[str, ...], rows: list[dict[str, str]]) -> None:
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(cols), lineterminator="\n")
            w.writeheader()
            for r in rows:
                w.writerow({c: r.get(c, "") for c in cols})

    _write(snap_dir / "discovery_candidates.csv", DISCOVERY_COLUMNS, candidates)
    _write(snap_dir / "prior_art.csv", PRIOR_ART_COLUMNS, prior_rows)
    _write(snap_dir / "demand_signals.csv", DEMAND_COLUMNS, demand_rows)

    def mock_transport_fn(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": []})

    transport = httpx.MockTransport(mock_transport_fn)
    tools = {
        "github": GitHubTool(transport=transport),
        "npm": NpmTool(transport=transport),
        "pypi": PyPiTool(transport=transport),
        "web_fetch": WebFetchTool(transport=transport),
        "zetaris_sql": ZetarisSqlTool(),
    }
    toolbox = ToolBox(tools=tools, budget=Budget(max_calls=40, max_seconds=300.0))

    results = score_all(
        data_dir=snap_dir,
        output_json=tmp_path / "out.json",
        output_md=tmp_path / "out.md",
        max_calls=40,
        max_seconds=300.0,
        use_llm=False,
        custom_toolbox=toolbox,
    )

    # Must be marked NEEDS_PRIOR_ART in ineligible
    assert len(results["candidates"]) == 0
    ineligible = {item["repo_id"]: item for item in results["ineligible"]}
    assert "lone/solo-project" in ineligible
    assert ineligible["lone/solo-project"]["verdict"] == "NEEDS_PRIOR_ART"
    assert "Zero alternatives checked" in ineligible["lone/solo-project"]["reason"]
    assert results["protagonist"]["repo_id"] is None


def test_score_candidates_aborts_on_local_model_served(tmp_path):
    output_json = tmp_path / "scores.json"
    output_md = tmp_path / "scores.md"
    log_dir = tmp_path / "logs"

    toolbox = _create_mock_toolbox()
    mock_client = RelevanceClient(
        mock_handler=lambda p, s: "{}",
        mock_served_model="local-runtime/qwen2.5-coder-1.5b",
        cache_dir=tmp_path / "cache",
    )

    with pytest.raises(RelevanceLLMError) as exc_info:
        score_all(
            data_dir=REAL_DATA,
            output_json=output_json,
            output_md=output_md,
            relevance_client=mock_client,
            log_dir=log_dir,
            custom_toolbox=toolbox,
            command_str="score_candidates.py --test-abort",
        )

    assert "refusing to score with a small local model" in str(exc_info.value)
    assert output_json.exists()
    saved = json.loads(output_json.read_text(encoding="utf-8"))
    assert saved.get("status") == "ABORTED"
    assert "refusing to score with a small local model" in saved.get("error", "")

    # Trace logs should contain SCORING_ABORTED
    trace_files = list(log_dir.glob("*.jsonl"))
    assert len(trace_files) >= 1
    events = [
        json.loads(line)
        for line in trace_files[0].read_text(encoding="utf-8").strip().splitlines()
        if line.strip()
    ]
    abort_events = [e for e in events if e.get("action") == "SCORING_ABORTED"]
    assert len(abort_events) == 1
    assert abort_events[0]["status"] == "error"


def test_single_discovery_filter_event_and_retry_with_broader_queries(tmp_path):
    """Bug 2: Exactly one DISCOVERY_FILTER event per repo; retry with broader queries if search yields 0 results."""
    from scripts.score_candidates import main, score_all

    snap_dir = tmp_path / "snapshot"
    snap_dir.mkdir(parents=True, exist_ok=True)
    out_json = tmp_path / "scores.json"
    out_md = tmp_path / "scores.md"
    log_dir = tmp_path / "logs"

    # Candidate with 0 prior art rows
    candidate = {
        "repo_id": "testowner/solitary-tool",
        "html_url": "https://github.com/testowner/solitary-tool",
        "description": "Unique niche tool for developer workflows.",
        "readme_excerpt": "Unique niche tool for developer workflows with local storage.",
        "problem_excerpt": "Unique niche tool for developer workflows.",
        "user_excerpt": "developers",
        "scope_excerpt": "developer workflows",
        "code_paths": "main.py",
        "created_at": "2024-01-01T00:00:00Z",
        "pushed_at": "2024-06-01T00:00:00Z",
        "days_since_push": "100",
        "stargazers_count": "0",
        "forks_count": "0",
        "open_issues": "0",
        "licence_spdx": "MIT",
        "core_terms": "developer workflows",
        "cohort": "discovery",
        "data_origin": "test",
        "collected_at": "2026-10-22T23:00:00+00:00",
    }

    # Write CSVs: 0 prior art rows for this candidate
    def _write(path: Path, cols: tuple[str, ...], rows: list[dict[str, str]]) -> None:
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(cols), lineterminator="\n")
            w.writeheader()
            for r in rows:
                w.writerow({c: r.get(c, "") for c in cols})

    _write(snap_dir / "discovery_candidates.csv", DISCOVERY_COLUMNS, [candidate])
    _write(snap_dir / "prior_art.csv", PRIOR_ART_COLUMNS, [])
    _write(snap_dir / "demand_signals.csv", DEMAND_COLUMNS, [])

    searches_called = []

    def mock_transport_fn(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "/search/repositories" in url_str:
            searches_called.append(url_str)
            # Always return 0 results to force retry then NEEDS_PRIOR_ART
            return httpx.Response(200, json={"items": []})
        return httpx.Response(200, json={"items": []})

    transport = httpx.MockTransport(mock_transport_fn)
    toolbox = ToolBox(
        tools={"github": GitHubTool(transport=transport)},
        budget=Budget(max_calls=20, max_seconds=30.0),
    )

    def mock_handler(prompt: str, system_prompt: str) -> str:
        if "Relevance Problem Extractor" in system_prompt or "Extract the problem statement" in prompt:
            return json.dumps({
                "problem": "Unique niche tool for developer workflows",
                "target_user": "developers",
                "category": "developer-tools",
                "search_queries": ["unique niche tool", "solitary developer workflow"],
                "quotes": ["Unique niche tool for developer workflows"],
            })
        return json.dumps({"verdict": "CANDIDATE", "reason": "Good", "problem_quote": "Unique niche tool for developer workflows"})

    rel_client = RelevanceClient(mock_handler=mock_handler, cache_dir=tmp_path / "cache")

    results = score_all(
        data_dir=snap_dir,
        output_json=out_json,
        output_md=out_md,
        max_calls=20,
        max_seconds=30.0,
        use_llm=False,
        run_id="test-retry-events",
        log_dir=log_dir,
        custom_toolbox=toolbox,
        relevance_client=rel_client,
    )

    # 1. Broader search was retried (searches_called has more queries than original 2)
    assert len(searches_called) > 2

    # 2. Candidate is ineligible with NEEDS_PRIOR_ART and records queries tried
    inel = results["ineligible"][0]
    assert inel["verdict"] == "NEEDS_PRIOR_ART"
    assert "Queries tried:" in inel["reason"]

    # 3. Trace log contains EXACTLY ONE DISCOVERY_FILTER event for this candidate!
    trace_path = log_dir / "trace-test-retry-events.jsonl"
    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    df_events = [e for e in events if e.get("action") == "DISCOVERY_FILTER" and "solitary-tool" in json.dumps(e.get("input_summary", {}))]
    assert len(df_events) == 1
    out_summary = df_events[0]["output_summary"]
    if isinstance(out_summary, str):
        out_summary = json.loads(out_summary)
    assert out_summary["verdict"] == "NEEDS_PRIOR_ART"


def test_problem_extraction_llm_error_is_unjudged_not_judged_without_search(tmp_path):
    """Regression (trial5f, ytkb): if problem extraction fails with an LLM error
    (e.g. Nightshift 502/timeout), the prior-art search never runs. The repo must be
    NEEDS_REVIEW, never judged CANDIDATE from CSV rows alone."""
    snap_dir = tmp_path / "snap"
    snap_dir.mkdir()
    out_json = tmp_path / "out.json"
    out_md = tmp_path / "out.md"
    log_dir = tmp_path / "logs"
    candidate = {
        "repo_id": "testowner/flaky-llm-tool",
        "html_url": "https://github.com/testowner/flaky-llm-tool",
        "description": "Sync tool for knowledge base articles.",
        "readme_excerpt": "Sync tool for knowledge base articles to local markdown.",
        "problem_excerpt": "Sync tool for knowledge base articles.",
        "user_excerpt": "developers",
        "scope_excerpt": "knowledge base sync",
        "code_paths": "main.py",
        "created_at": "2024-01-01T00:00:00Z",
        "pushed_at": "2024-06-01T00:00:00Z",
        "days_since_push": "100",
        "stargazers_count": "0",
        "forks_count": "0",
        "open_issues": "0",
        "licence_spdx": "MIT",
        "core_terms": "knowledge base sync",
        "cohort": "discovery",
        "data_origin": "test",
        "collected_at": "2026-10-22T23:00:00+00:00",
    }
    prior = {c: "" for c in PRIOR_ART_COLUMNS}
    prior.update({"repo_id": candidate["repo_id"], "alternative_id": "someone/partial-alt"})

    def _write(path, cols, rows):
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(cols), lineterminator="\n")
            w.writeheader()
            for r in rows:
                w.writerow({c: r.get(c, "") for c in cols})

    _write(snap_dir / "discovery_candidates.csv", DISCOVERY_COLUMNS, [candidate])
    _write(snap_dir / "prior_art.csv", PRIOR_ART_COLUMNS, [prior])
    _write(snap_dir / "demand_signals.csv", DEMAND_COLUMNS, [])

    searches_called = []

    def mock_transport_fn(request):
        searches_called.append(str(request.url))
        return httpx.Response(200, json={"items": []})

    toolbox = ToolBox(
        tools={"github": GitHubTool(transport=httpx.MockTransport(mock_transport_fn))},
        budget=Budget(max_calls=20, max_seconds=30.0),
    )

    def mock_handler(prompt, system_prompt):
        if "Relevance Problem Extractor" in system_prompt or "Extract the problem statement" in prompt:
            raise RuntimeError("HTTP 502 from runtime")
        return json.dumps({"verdict": "CANDIDATE", "reason": "Good", "problem_quote": "Sync tool for knowledge base articles."})

    rel_client = RelevanceClient(mock_handler=mock_handler, cache_dir=tmp_path / "cache")
    results = score_all(
        data_dir=snap_dir,
        output_json=out_json,
        output_md=out_md,
        max_calls=20,
        max_seconds=30.0,
        use_llm=False,
        run_id="test-problem-llm-error",
        log_dir=log_dir,
        custom_toolbox=toolbox,
        relevance_client=rel_client,
    )

    ranked = results.get("ranked") or results.get("candidates") or []
    assert all(r.get("repo_id") != candidate["repo_id"] for r in ranked)
    inel = [r for r in results["ineligible"] if r["repo_id"] == candidate["repo_id"]]
    assert len(inel) == 1
    assert inel[0]["verdict"] == "UNJUDGED"
    assert "PROBLEM_EXTRACTION_FAILED" in inel[0]["reason"]
    assert "prior-art search was not run" in inel[0]["reason"]


def test_llm_502_after_extraction_does_not_abort_run(tmp_path, monkeypatch):
    """Regression (full77a/b): a Nightshift 502 in the alternatives or judgment step must
    mark only that repo UNJUDGED (LLM_UNAVAILABLE), not abort the whole run."""
    snap_dir = tmp_path / "snap"
    snap_dir.mkdir()
    out_json = tmp_path / "out.json"
    out_md = tmp_path / "out.md"
    log_dir = tmp_path / "logs"
    candidate = {
        "repo_id": "testowner/flaky-llm-tool",
        "html_url": "https://github.com/testowner/flaky-llm-tool",
        "description": "Sync tool for knowledge base articles.",
        "readme_excerpt": "Sync tool for knowledge base articles to local markdown.",
        "problem_excerpt": "Sync tool for knowledge base articles.",
        "user_excerpt": "developers",
        "scope_excerpt": "knowledge base sync",
        "code_paths": "main.py",
        "created_at": "2024-01-01T00:00:00Z",
        "pushed_at": "2024-06-01T00:00:00Z",
        "days_since_push": "100",
        "stargazers_count": "0",
        "forks_count": "0",
        "open_issues": "0",
        "licence_spdx": "MIT",
        "core_terms": "knowledge base sync",
        "cohort": "discovery",
        "data_origin": "test",
        "collected_at": "2026-10-22T23:00:00+00:00",
    }
    prior = {c: "" for c in PRIOR_ART_COLUMNS}
    prior.update({"repo_id": candidate["repo_id"], "alternative_id": "someone/partial-alt"})

    def _write(path, cols, rows):
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(cols), lineterminator="\n")
            w.writeheader()
            for r in rows:
                w.writerow({c: r.get(c, "") for c in cols})

    _write(snap_dir / "discovery_candidates.csv", DISCOVERY_COLUMNS, [candidate])
    _write(snap_dir / "prior_art.csv", PRIOR_ART_COLUMNS, [prior])
    _write(snap_dir / "demand_signals.csv", DEMAND_COLUMNS, [])

    searches_called = []

    def mock_transport_fn(request):
        searches_called.append(str(request.url))
        return httpx.Response(200, json={"items": []})

    toolbox = ToolBox(
        tools={"github": GitHubTool(transport=httpx.MockTransport(mock_transport_fn))},
        budget=Budget(max_calls=20, max_seconds=30.0),
    )

    import scripts.score_candidates as sc

    monkeypatch.setattr(
        sc,
        "extract_problem_statement",
        lambda cand, client=None: (
            {"problem": "Sync knowledge base articles", "category": "knowledge base sync", "target_user": "developers", "queries": ["knowledge base sync"]},
            {},
        ),
    )

    def _boom(**kwargs):
        raise RelevanceLLMError("Nightshift returned HTTP 502")

    monkeypatch.setattr(sc, "filter_relevant_alternatives", _boom)

    def mock_handler(prompt, system_prompt):
        return "{}"

    rel_client = RelevanceClient(mock_handler=mock_handler, cache_dir=tmp_path / "cache")
    results = score_all(
        data_dir=snap_dir,
        output_json=out_json,
        output_md=out_md,
        max_calls=20,
        max_seconds=30.0,
        use_llm=False,
        run_id="test-llm-502-after-extraction",
        log_dir=log_dir,
        custom_toolbox=toolbox,
        relevance_client=rel_client,
    )

    ranked = results.get("ranked") or results.get("candidates") or []
    assert all(r.get("repo_id") != candidate["repo_id"] for r in ranked)
    inel = [r for r in results["ineligible"] if r["repo_id"] == candidate["repo_id"]]
    assert len(inel) == 1
    assert inel[0]["verdict"] == "UNJUDGED"
    assert "LLM_UNAVAILABLE" in inel[0]["reason"]
    assert "HTTP 502" in inel[0]["reason"]


def test_llm_flag_exits_code_2_when_unconfigured(monkeypatch, capsys):
    """Bug 3: When --llm is passed and LLM cannot be configured, exit code 2 with error to stderr."""
    from scripts.score_candidates import main

    monkeypatch.delenv("METERLESS_RUNTIME_TOKEN", raising=False)
    monkeypatch.setattr("sys.argv", ["score_candidates.py", "--llm"])

    exit_code = main()
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "METERLESS_RUNTIME_TOKEN" in captured.err or "--llm was passed but" in captured.err


def test_model_mismatch_flag_and_call_summary(tmp_path):
    """Bug 4: Add MODEL_MISMATCH flag when served != requested, and include model_calls in JSON/MD output."""
    output_json = tmp_path / "scores.json"
    output_md = tmp_path / "scores.md"
    log_dir = tmp_path / "logs"

    toolbox = _create_mock_toolbox()
    base_client = _create_mock_relevance_client(tmp_path)
    # Set requested model and mock served model to different strings
    client = RelevanceClient(
        model="nvidia/nemotron-3-super-120b-a12b",
        mock_served_model="gemini-3.1-pro-preview",
        mock_handler=base_client.mock_handler,
        cache_dir=tmp_path / "cache",
    )

    results = score_all(
        data_dir=REAL_DATA,
        output_json=output_json,
        output_md=output_md,
        max_calls=40,
        max_seconds=300.0,
        use_llm=False,
        run_id="test-mismatch",
        log_dir=log_dir,
        custom_toolbox=toolbox,
        relevance_client=client,
    )

    # Candidate has MODEL_MISMATCH flag
    cand = results["candidates"][0]
    assert "MODEL_MISMATCH" in cand["flags"]

    # JSON output has call counts per served model
    assert "model_calls" in results
    assert results["model_calls"].get("gemini-3.1-pro-preview", 0) > 0

    # MD output contains Model Call Summary
    md_content = output_md.read_text(encoding="utf-8")
    assert "gemini-3.1-pro-preview" in md_content
    assert "Model Call Summary" in md_content or "Served Models" in md_content


def test_scorer_not_discriminating_writes_output_and_exits_code_3(tmp_path, capsys):
    """Problem 1: Discrimination guard writes output, records warning/vectors, emits trace event, seals trace, and exits with code 3."""
    from scripts.verify_trace import verify

    snap_dir = tmp_path / "snapshot_nodiscrim"
    snap_dir.mkdir(parents=True, exist_ok=True)
    out_json = tmp_path / "out.json"
    out_md = tmp_path / "out.md"
    log_dir = tmp_path / "logs"

    collected_at = "2026-10-22T23:00:00+00:00"
    pushed_at = "2024-06-01T00:00:00Z"
    from app.agents.market_filter import days_between
    days_str = str(days_between(pushed_at, collected_at))

    candidates = []
    prior_rows = []
    demand_rows = []
    for i in range(1, 5):
        rid = f"clone/repo-{i}"
        candidates.append({
            "repo_id": rid,
            "html_url": f"https://github.com/{rid}",
            "description": "Standard tool for syncing local markdown notes.",
            "readme_excerpt": "Standard tool for syncing local markdown notes.\nBuilt for developers.\n- sync markdown\n- local notes",
            "problem_excerpt": "Standard tool for syncing local markdown notes.",
            "user_excerpt": "developers",
            "scope_excerpt": "sync markdown",
            "code_paths": "src/main.py|src/util.py|src/sync.py",
            "created_at": "2024-01-01T00:00:00Z",
            "pushed_at": pushed_at,
            "days_since_push": days_str,
            "stargazers_count": "0",
            "forks_count": "0",
            "open_issues": "0",
            "licence_spdx": "MIT",
            "core_terms": "sync markdown|local notes",
            "cohort": "discovery",
            "data_origin": "test",
            "collected_at": collected_at,
        })
        prior_rows.append({
            "repo_id": rid,
            "alternative_id": f"comp/alt-{i}",
            "url": f"https://github.com/comp/alt-{i}",
            "licence_spdx": "MIT",
            "stars": "50",
            "pushed_at": "2024-01-01T00:00:00Z",
            "days_since_push": "900",
            "excerpt": "Unmaintained tool without sync features.",
            "candidate_excerpt": "Standard tool for syncing local markdown notes.",
            "required_terms": "sync markdown|local notes",
            "data_origin": "test",
            "collected_at": collected_at,
        })
        demand_rows.append({
            "repo_id": rid,
            "signal_id": f"dem-{i}",
            "kind": "github_issue",
            "url": f"https://github.com/external/issue/{i}",
            "excerpt": "Please implement sync markdown and local notes for our team.",
            "comments": "3",
            "reactions": "2",
            "stars": "",
            "pushed_at": "",
            "days_since_push": "",
            "query": "sync markdown local notes",
            "data_origin": "test",
            "collected_at": collected_at,
        })

    def _write(path: Path, cols: tuple[str, ...], rows: list[dict[str, str]]) -> None:
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(cols), lineterminator="\n")
            w.writeheader()
            for r in rows:
                w.writerow({c: r.get(c, "") for c in cols})

    _write(snap_dir / "discovery_candidates.csv", DISCOVERY_COLUMNS, candidates)
    _write(snap_dir / "prior_art.csv", PRIOR_ART_COLUMNS, prior_rows)
    _write(snap_dir / "demand_signals.csv", DEMAND_COLUMNS, demand_rows)

    toolbox = ToolBox(
        tools={"github": GitHubTool(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"items": []})))},
        budget=Budget(max_calls=40, max_seconds=300.0),
    )

    with pytest.raises(SystemExit) as exc_info:
        score_all(
            data_dir=snap_dir,
            output_json=out_json,
            output_md=out_md,
            max_calls=40,
            max_seconds=300.0,
            use_llm=False,
            run_id="test-nodiscrim-output",
            log_dir=log_dir,
            custom_toolbox=toolbox,
        )

    # Exit code must be 3
    assert exc_info.value.code == 3
    captured = capsys.readouterr()
    assert "SCORER NOT DISCRIMINATING" in captured.err

    # Output JSON and MD must still be written
    assert out_json.exists()
    assert out_md.exists()

    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert "SCORER_NOT_DISCRIMINATING" in data.get("warnings", [])
    assert len(data.get("duplicated_vectors", [])) > 0
    # Protagonist is null / not selected with reason
    assert data["protagonist"] is None or data["protagonist"]["repo_id"] is None
    prot_reason = data["protagonist"]["reason"] if data["protagonist"] else data.get("protagonist_reason", "")
    assert "discriminat" in prot_reason.lower() or "SCORER_NOT_DISCRIMINATING" in prot_reason

    # Trace must be sealed and verify_trace must pass
    trace_path = log_dir / "trace-test-nodiscrim-output.jsonl"
    assert trace_path.exists()
    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(e.get("action") == "SCORER_NOT_DISCRIMINATING" for e in events)
    assert events[-1].get("action") == "RUN_SEAL"

    ok, msg = verify(trace_path, expected_run_hash=data["run_hash"])
    assert ok is True, msg


def test_venture_analyst_and_relevance_client_model_consistency(monkeypatch):
    """Problem 2: Both venture_analyst LLM and RelevanceClient must use Settings().requested_model()."""
    from app.llm.provider import build_llm
    from config.settings import Settings

    target_model = "nvidia/nemotron-3.5-super-vl-120b-a12b"
    monkeypatch.setenv("MODEL_NAME", target_model)
    monkeypatch.setenv("METERLESS_RUNTIME_TOKEN", "test-token")

    settings = Settings()
    assert settings.requested_model() == target_model

    # RelevanceClient must use requested_model
    rel_client = RelevanceClient(
        base_url=settings.meterless_base_url,
        token=settings.meterless_runtime_token,
        model=settings.requested_model(),
    )
    assert rel_client.model == target_model

    # Venture analyst LLM (build_llm) must request the same model
    llm = build_llm(settings, sample=False)
    assert llm.model == target_model
    assert llm.lanes[0] == target_model
    assert llm.requested_model == target_model


def test_venture_analyst_retries_non_json_response(tmp_path, monkeypatch):
    """Problem 3: Non-JSON reply from venture analyst retries once with corrective message before fallback."""
    out_json = tmp_path / "out.json"
    out_md = tmp_path / "out.md"
    log_dir = tmp_path / "logs"

    call_messages = []

    valid_json_response = json.dumps({
        "criteria": {
            "problem_severity": {"score": 3, "evidence_ids": ["candidate:SergioMendolia/ytkb"], "rationale": "High severity"},
            "demand_evidence": {"score": 2, "evidence_ids": ["candidate:SergioMendolia/ytkb"], "rationale": "Moderate demand"},
            "differentiation": {"score": 2, "evidence_ids": ["candidate:SergioMendolia/ytkb"], "rationale": "Clear gap"},
            "feasibility": {"score": 3, "evidence_ids": ["candidate:SergioMendolia/ytkb"], "rationale": "High feasibility"},
            "productization_path": {"score": 2, "evidence_ids": ["candidate:SergioMendolia/ytkb"], "rationale": "Direct path"},
            "moat": {"score": 1, "evidence_ids": ["candidate:SergioMendolia/ytkb"], "rationale": "Moderate moat"},
        }
    })

    class MockScriptedLLM:
        requested_model = "test-model"
        served_model = "test-model"

        def complete(self, messages, **kwargs):
            call_messages.append(list(messages))
            if len(call_messages) == 1:
                # First attempt: non-JSON response
                return "I apologize, but here is my textual analysis without JSON format."
            # Second attempt (retry): valid JSON response
            return valid_json_response

    monkeypatch.setenv("METERLESS_RUNTIME_TOKEN", "mock-token")
    import app.llm.provider
    monkeypatch.setattr(app.llm.provider, "build_llm", lambda *a, **kw: MockScriptedLLM())

    toolbox = _create_mock_toolbox()
    mock_client = _create_mock_relevance_client(tmp_path)
    results = score_all(
        data_dir=REAL_DATA,
        output_json=out_json,
        output_md=out_md,
        max_calls=40,
        max_seconds=300.0,
        use_llm=True,
        run_id="test-retry-nonjson",
        log_dir=log_dir,
        custom_toolbox=toolbox,
        relevance_client=mock_client,
    )

    # Must have called LLM twice for the first candidate
    assert len(call_messages) >= 2
    # Second call must include the corrective message
    assert call_messages[1][-1]["content"] == "Return only the JSON object matching the schema."

    # Succeeded on retry: no LLM_FALLBACK
    cand = results["candidates"][0]
    assert "LLM_FALLBACK" not in cand["flags"]
    assert cand["criteria"]["problem_severity"]["score"] == 3

    # Trace must record retry_count == 1
    trace_path = log_dir / "trace-test-retry-nonjson.jsonl"
    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    score_events = [e for e in events if e.get("action") == "LLM_SCORE"]
    assert len(score_events) > 0
    assert score_events[0].get("retry_count") == 1

    # Part B: Double failure falls back to deterministic analyst with LLM_FALLBACK flag and retry_count=1
    class AlwaysFailsLLM:
        requested_model = "test-model"
        served_model = "test-model"

        def complete(self, messages, **kwargs):
            return "Still invalid non-JSON output."

    monkeypatch.setattr(app.llm.provider, "build_llm", lambda *a, **kw: AlwaysFailsLLM())
    out_json2 = tmp_path / "out2.json"
    out_md2 = tmp_path / "out2.md"
    results2 = score_all(
        data_dir=REAL_DATA,
        output_json=out_json2,
        output_md=out_md2,
        max_calls=40,
        max_seconds=300.0,
        use_llm=True,
        run_id="test-retry-doublefail",
        log_dir=log_dir,
        custom_toolbox=toolbox,
        relevance_client=mock_client,
    )
    cand2 = results2["candidates"][0]
    assert "LLM_FALLBACK" in cand2["flags"]

    trace_path2 = log_dir / "trace-test-retry-doublefail.jsonl"
    events2 = [json.loads(line) for line in trace_path2.read_text(encoding="utf-8").splitlines() if line.strip()]
    fb_events = [e for e in events2 if e.get("action") == "LLM_FALLBACK" and e.get("agent_name") == "venture_analyst"]
    assert len(fb_events) > 0
    assert fb_events[0].get("retry_count") == 1









def test_gate1_writes_hmem_verdict_memory_without_changing_verdicts(tmp_path):
    """Meterless H-MEM is on the gate-1 path as verdict memory: same verdicts, lines in the sealed chain."""
    log_dir = tmp_path / "logs"
    runs = []
    for run_id in ("hmem-run-1", "hmem-run-2"):
        results = score_all(
            data_dir=REAL_DATA,
            output_json=tmp_path / f"{run_id}.json",
            output_md=tmp_path / f"{run_id}.md",
            max_calls=40,
            max_seconds=300.0,
            use_llm=False,
            run_id=run_id,
            log_dir=log_dir,
            custom_toolbox=_create_mock_toolbox(),
            relevance_client=_create_mock_relevance_client(tmp_path),
        )
        runs.append(results)
        trace_path = log_dir / f"trace-{run_id}.jsonl"
        ok, why = verify(trace_path, results["run_hash"])
        assert ok, why
        lines = [json.loads(x) for x in trace_path.read_text(encoding="utf-8").splitlines() if x.strip()]
        assert lines[-1]["action"] == "RUN_SEAL"
        hmem = [x for x in lines if x["action"] == "HMEM_VERDICT_MEMORY"]
        rows = [*results["ineligible"], *results["candidates"]]
        assert len(hmem) == len(rows) > 0

    def verdicts(r):
        return {x["repo_id"]: x["verdict"] for x in [*r["ineligible"], *r["candidates"]]}

    assert verdicts(runs[0]) == verdicts(runs[1])
    first, second = runs[0]["hmem_verdict_memory"], runs[1]["hmem_verdict_memory"]
    assert all(not s["remembered_before"] for s in first)
    assert all(s["remembered_before"][0]["run_id"] == "hmem-run-1" and not s["verdict_changed"] for s in second)
