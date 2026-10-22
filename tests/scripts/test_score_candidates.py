"""Tests for scripts/score_candidates.py using fake offline transports."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import httpx
import pytest

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
                    return httpx.Response(200, json={"items": [{"title": "Sync YouTrack knowledge base articles", "comments": 2}]})
                if "billpeet" in url_str:
                    return httpx.Response(200, json={"items": [{"title": "Demand pull: push changes back to YouTrack knowledge base", "comments": 3}]})
                return httpx.Response(200, json={"items": [{"title": "Sync knowledge base articles from YouTrack", "comments": 1}]})
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
    return ToolBox(tools=tools, budget=Budget(max_calls=40, max_seconds=300.0))


def test_score_all_offline_selects_protagonist_and_verifies_trace(tmp_path):
    output_json = tmp_path / "venture_scores.json"
    output_md = tmp_path / "venture_scores.md"
    log_dir = tmp_path / "logs"
    run_id = "test-offline-score"

    toolbox = _create_mock_toolbox()

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
    )

    # Candidate was processed and flagged RATE_LIMITED
    cand = results["candidates"][0]
    assert "RATE_LIMITED" in cand["flags"]

    # System stopped cleanly without crash and still selected protagonist based on stored evidence
    assert results["protagonist"]["repo_id"] == "SergioMendolia/ytkb"

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
    assert results["protagonist"]["total"] >= 50.0

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
    )

    cand = results["candidates"][0]
    dem = cand["criteria"]["demand_evidence"]
    # Empty tool items ignored -> falls back to stored qualifying signal foliant-pr-80 (score 1)
    assert dem["score"] == 1
    assert "demand:foliant-pr-80" in dem["evidence_ids"]
    assert "Stored qualifying external demand signal" in dem["rationale"]
    assert "https://github.com/foliant-docs/foliant/pull/80" in dem["rationale"]


def test_unrelated_text_tool_item_is_ignored_and_falls_back(tmp_path):
    output_json = tmp_path / "venture_scores_unrelated.json"
    output_md = tmp_path / "venture_scores_unrelated.md"
    log_dir = tmp_path / "logs"
    run_id = "test-unrelated-text"

    def mock_transport_fn(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
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
    )

    cand = results["candidates"][0]
    dem = cand["criteria"]["demand_evidence"]
    # Unrelated tool items do not count -> falls back to stored demand signal (score 1)
    assert dem["score"] == 1
    assert "demand:foliant-pr-80" in dem["evidence_ids"]
    assert "Stored qualifying external demand signal" in dem["rationale"]


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



