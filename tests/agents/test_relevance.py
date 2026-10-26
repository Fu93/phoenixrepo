"""Unit tests for app/agents/relevance.py.

Verifies:
- Hallucinated quotes are rejected.
- Name-token and generic-word capabilities are rejected.
- Unrelated signals (relevant=false) are rejected / score 0.
- Pull request URLs are rejected.
- LLM down results in LLM_FALLBACK and 0.
- Disk cache hit avoids a second call.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest

from app.agents.relevance import (
    RelevanceClient,
    RelevanceLLMError,
    build_search_queries,
    extract_capabilities,
    extract_problem_statement,
    filter_relevant_alternatives,
    is_valid_demand_signal_url,
    judge_alternative_coverage,
    judge_alternative_relevance,
    judge_demand,
    judge_discovery,
    search_prior_art,
)
from app.tools import Budget, GitHubTool, NpmTool, PyPiTool, ToolBox, WebSearchTool


def test_hallucinated_quote_rejected(tmp_path: Path):
    candidate = {
        "repo_id": "alice/noteapp",
        "readme_excerpt": "NoteApp allows you to write notes offline in markdown format.",
    }
    # LLM hallucinates a quote not present in the readme
    def fake_llm(prompt: str, sys: str) -> str:
        return json.dumps(
            {
                "capabilities": [
                    {
                        "name": "Cloud database sync",
                        "quote": "Seamlessly synchronizes all notes to AWS S3 cloud",
                    }
                ]
            }
        )

    client = RelevanceClient(cache_dir=tmp_path, mock_handler=fake_llm)
    caps, meta = extract_capabilities(candidate, client=client)
    assert caps == []
    assert meta["fallback"] is False


def test_name_token_capability_rejected(tmp_path: Path):
    candidate = {
        "repo_id": "Crazygrandma/NoteSync",
        "readme_excerpt": "Sync notes locally between NoteSync clients. Uses python main code config.",
    }

    def fake_llm(prompt: str, sys: str) -> str:
        return json.dumps(
            {
                "capabilities": [
                    # 1. Made only of repo name tokens
                    {"name": "NoteSync", "quote": "NoteSync clients"},
                    # 2. Made only of generic words
                    {"name": "python main code config", "quote": "python main code config"},
                    # 3. Valid feature with distinct descriptive vocabulary
                    {
                        "name": "Local client note synchronization",
                        "quote": "Sync notes locally between NoteSync clients",
                    },
                ]
            }
        )

    client = RelevanceClient(cache_dir=tmp_path, mock_handler=fake_llm)
    caps, meta = extract_capabilities(candidate, client=client)
    assert len(caps) == 1
    assert caps[0]["name"] == "Local client note synchronization"
    assert caps[0]["quote"] == "Sync notes locally between NoteSync clients"


def test_unrelated_signal_scores_zero(tmp_path: Path):
    capabilities = [
        {
            "name": "Local client note synchronization",
            "quote": "Sync notes locally between NoteSync clients",
        }
    ]
    signal = {
        "signal_id": "sig-1",
        "url": "https://github.com/other/repo/issues/42",
        "text": "Webpack build failed with error in babel-loader configuration",
    }

    def fake_llm(prompt: str, sys: str) -> str:
        return json.dumps(
            {
                "relevant": False,
                "capability": "Local client note synchronization",
                "quote": "",
                "why": "This is a webpack build error, completely unrelated to note sync.",
            }
        )

    client = RelevanceClient(cache_dir=tmp_path, mock_handler=fake_llm)
    result, meta = judge_demand(capabilities, signal, candidate_repo_id="Crazygrandma/NoteSync", client=client)
    assert result is None
    assert meta["fallback"] is False
    assert meta["reason"] == "Judged not relevant"


def test_pr_url_rejected():
    url = "https://github.com/other/repo/pull/123"
    valid, reason = is_valid_demand_signal_url(url, candidate_repo_id="Crazygrandma/NoteSync")
    assert valid is False
    assert "pull request" in reason

    # Also test code file
    valid_blob, reason_blob = is_valid_demand_signal_url(
        "https://github.com/other/repo/blob/main/index.js", candidate_repo_id="Crazygrandma/NoteSync"
    )
    assert valid_blob is False
    assert "code file" in reason_blob

    # Also test candidate's own repo
    valid_self, reason_self = is_valid_demand_signal_url(
        "https://github.com/Crazygrandma/NoteSync/issues/1", candidate_repo_id="Crazygrandma/NoteSync"
    )
    assert valid_self is False
    assert "candidate repo" in reason_self

    # Valid issue URL passes
    valid_issue, _ = is_valid_demand_signal_url(
        "https://github.com/other/repo/issues/100", candidate_repo_id="Crazygrandma/NoteSync"
    )
    assert valid_issue is True


def test_llm_down_llm_fallback_and_zero(tmp_path: Path):
    candidate = {
        "repo_id": "Crazygrandma/NoteSync",
        "readme_excerpt": "Sync notes locally between NoteSync clients.",
    }

    def failing_llm(prompt: str, sys: str) -> str:
        raise RelevanceLLMError("Nightshift daemon unreachable on 127.0.0.1:8088")

    client = RelevanceClient(cache_dir=tmp_path, mock_handler=failing_llm)
    caps, meta = extract_capabilities(candidate, client=client)
    assert caps == []
    assert meta["fallback"] is True
    assert "unreachable" in meta["error"]


def test_cache_hit_avoids_second_call(tmp_path: Path):
    call_count = 0

    def counting_llm(prompt: str, sys: str) -> str:
        nonlocal call_count
        call_count += 1
        return json.dumps(
            {
                "capabilities": [
                    {
                        "name": "Local client note synchronization",
                        "quote": "Sync notes locally between NoteSync clients",
                    }
                ]
            }
        )

    client = RelevanceClient(cache_dir=tmp_path, mock_handler=counting_llm)
    candidate = {
        "repo_id": "Crazygrandma/NoteSync",
        "readme_excerpt": "Sync notes locally between NoteSync clients.",
    }

    # First call - cache miss
    caps1, _ = extract_capabilities(candidate, client=client)
    assert len(caps1) == 1
    assert call_count == 1

    # Second call - cache hit!
    caps2, _ = extract_capabilities(candidate, client=client)
    assert len(caps2) == 1
    assert call_count == 1  # Not incremented!


def test_judge_discovery_accepts_verified_quote(tmp_path: Path):
    candidate = {
        "repo_id": "test/valid-tool",
        "description": "Invoice processor for freelancers.",
        "readme_excerpt": "This tool automates invoice processing for small businesses.",
    }

    def fake_llm(prompt: str, sys: str) -> str:
        return json.dumps(
            {
                "verdict": "CANDIDATE",
                "reason": "Solves real problem of automating invoices.",
                "problem_quote": "automates invoice processing for small businesses",
                "alternative_repo": None,
                "alternative_quote": None,
            }
        )

    client = RelevanceClient(cache_dir=tmp_path, mock_handler=fake_llm)
    verdict, reason = judge_discovery(candidate, prior_rows=[], demand_rows=[], client=client)
    assert verdict == "CANDIDATE"
    assert "Method: llm+verified quote" in reason
    assert "requested=nvidia/nemotron-3-super-120b-a12b" in reason


def test_judge_discovery_downgrades_fabricated_quote_to_needs_review(tmp_path: Path):
    candidate = {
        "repo_id": "test/fake-quote-tool",
        "description": "A simple CLI calculator.",
        "readme_excerpt": "Calculate basic arithmetic from the command line.",
    }

    def fake_llm(prompt: str, sys: str) -> str:
        return json.dumps(
            {
                "verdict": "CANDIDATE",
                "reason": "Solves quantum computing problems.",
                "problem_quote": "computes quantum entanglement tensors",
                "alternative_repo": None,
                "alternative_quote": None,
            }
        )

    client = RelevanceClient(cache_dir=tmp_path, mock_handler=fake_llm)
    verdict, reason = judge_discovery(candidate, prior_rows=[], demand_rows=[], client=client)
    assert verdict == "NEEDS_REVIEW"
    assert "unverified LLM quote" in reason
    assert "Method: llm+verified quote" in reason


def test_judge_discovery_already_exists_missing_prior_repo_needs_review(tmp_path: Path):
    candidate = {
        "repo_id": "test/calc-repo",
        "readme_excerpt": "A basic CLI calculator tool.",
    }
    prior_rows = [
        {
            "alternative_id": "math/existing-calc",
            "excerpt": "A maintained calculator tool.",
        }
    ]

    def fake_llm(prompt: str, sys: str) -> str:
        return json.dumps(
            {
                "verdict": "ALREADY_EXISTS",
                "reason": "Super calc already exists.",
                "problem_quote": "calculator tool",
                "alternative_repo": "nonexistent/super-calc",
                "alternative_quote": "super calc does it all",
            }
        )

    client = RelevanceClient(cache_dir=tmp_path, mock_handler=fake_llm)
    verdict, reason = judge_discovery(candidate, prior_rows=prior_rows, demand_rows=[], client=client)
    assert verdict == "NEEDS_REVIEW"
    assert "unverified LLM quote" in reason


def _already_exists_llm(alternative_repo: str, alternative_quote: str):
    def fake_llm(prompt: str, sys: str) -> str:
        return json.dumps(
            {
                "verdict": "ALREADY_EXISTS",
                "reason": "An alternative already does this.",
                "problem_quote": "self-hosted backend for app data",
                "alternative_repo": alternative_repo,
                "alternative_quote": alternative_quote,
            }
        )

    return fake_llm


_PARA_QUOTE = "Para is a multitenant backend framework for the cloud."


def _para_row(*, stars: int) -> dict:
    return {
        "alternative_id": "Erudika/para",
        "url": "https://github.com/Erudika/para",
        "description": _PARA_QUOTE,
        "excerpt": _PARA_QUOTE,
        "licence_spdx": "Apache-2.0",
        "stars": stars,
        "pushed_at": "2026-09-01T00:00:00Z",
        "archived": False,
        "same_problem": True,
        "overlap": "full",
        "quotes": [_PARA_QUOTE],
        "quote_verified": True,
        "source": "github",
    }


def test_unmatched_llm_name_still_uses_a_verified_blocking_alternative(tmp_path: Path):
    """SelfDB: the model named a repo that is not in prior art, but Erudika/para already blocks."""
    candidate = {
        "repo_id": "selfdb/selfdb",
        "description": "A self-hosted backend for app data.",
        "readme_excerpt": "self-hosted backend for app data",
        "stars": 3,
    }
    client = RelevanceClient(
        cache_dir=tmp_path / "blocks",
        mock_handler=_already_exists_llm("not-in-the-list/missing", "this quote is not on any row"),
    )
    verdict, reason = judge_discovery(
        candidate,
        prior_rows=[_para_row(stars=574)],
        demand_rows=[],
        client=client,
    )
    assert verdict == "ALREADY_EXISTS"
    assert "Erudika/para" in reason
    assert _PARA_QUOTE in reason

    immature = RelevanceClient(
        cache_dir=tmp_path / "immature",
        mock_handler=_already_exists_llm("not-in-the-list/missing", "this quote is not on any row"),
    )
    held, held_reason = judge_discovery(
        candidate,
        prior_rows=[_para_row(stars=2)],
        demand_rows=[],
        client=immature,
    )
    assert held == "NEEDS_REVIEW"
    assert "not found in candidate prior-art" in held_reason


def test_web_hostname_match_is_only_an_aid_and_still_needs_a_verified_quote(tmp_path: Path):
    """appwrite/appwrite can select the appwrite.io web row, and cannot block without that row's quote."""
    candidate = {
        "repo_id": "selfdb/selfdb",
        "description": "A self-hosted backend for app data.",
        "readme_excerpt": "self-hosted backend for app data",
        "stars": 3,
    }
    page = "Appwrite is an open-source backend server for web and mobile developers."
    web_row = {
        "alternative_id": "web:https://appwrite.io/docs",
        "url": "https://appwrite.io/docs",
        "description": f"Appwrite - {page}",
        "excerpt": page,
        "readme": page,
        "source": "web",
        "same_problem": True,
        "overlap": "full",
        "stars": None,
        "pushed_at": "",
        "licence_spdx": "",
    }
    matched = RelevanceClient(
        cache_dir=tmp_path / "quote-ok",
        mock_handler=_already_exists_llm("appwrite/appwrite", "open-source backend server for web and mobile developers"),
    )
    verdict, reason = judge_discovery(candidate, prior_rows=[web_row], demand_rows=[], client=matched)
    assert verdict != "ALREADY_EXISTS"
    assert "not found in candidate prior-art" not in reason

    missed = RelevanceClient(
        cache_dir=tmp_path / "quote-bad",
        mock_handler=_already_exists_llm("appwrite/appwrite", "a sentence that does not appear on the page"),
    )
    refused, refused_reason = judge_discovery(candidate, prior_rows=[web_row], demand_rows=[], client=missed)
    assert refused == "NEEDS_REVIEW"
    assert "alternative_quote" in refused_reason
    assert "ALREADY_EXISTS" not in refused_reason


def test_served_local_runtime_model_raises_and_not_cached(tmp_path: Path):
    # 1. Test via mock handler
    cache_dir = tmp_path / "cache1"
    client1 = RelevanceClient(
        cache_dir=cache_dir,
        mock_handler=lambda p, s: "{}",
        mock_served_model="local-runtime/qwen2.5-coder-1.5b",
    )
    with pytest.raises(RelevanceLLMError) as exc_info:
        client1.complete("test prompt")
    assert "refusing to score with a small local model" in str(exc_info.value)
    assert len(list(cache_dir.glob("*.json"))) == 0

    # 2. Test via mock HTTP transport
    def mock_local_transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "local-runtime/qwen2.5-coder-7b",
                "choices": [{"message": {"content": "ok"}}],
            },
        )

    cache_dir2 = tmp_path / "cache2"
    client2 = RelevanceClient(
        base_url="http://127.0.0.1:8088/v1",
        token="test-token",
        cache_dir=cache_dir2,
        transport=httpx.MockTransport(mock_local_transport),
    )
    with pytest.raises(RelevanceLLMError) as exc_info2:
        client2.complete("test prompt 2")
    assert "refusing to score with a small local model" in str(exc_info2.value)
    assert len(list(cache_dir2.glob("*.json"))) == 0


def test_keyword_fallback_used_when_no_client_and_labelled(monkeypatch):
    monkeypatch.delenv("METERLESS_RUNTIME_TOKEN", raising=False)
    candidate = {
        "repo_id": "test/repo",
        "description": "Not a product.",
        "readme_excerpt": "Not a product.",
        "core_terms": "something",
    }
    verdict, reason = judge_discovery(candidate, prior_rows=[], demand_rows=[], client=None)
    assert "Method: phrase/keyword (fallback)." in reason


def test_timeout_wall_clock_cap_triggers_error(tmp_path: Path):
    def slow_transport(request: httpx.Request) -> httpx.Response:
        time.sleep(0.2)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    client = RelevanceClient(
        base_url="http://127.0.0.1:8088/v1",
        token="test-token",
        cache_dir=tmp_path / "timeout_cache",
        wall_clock_timeout=0.05,
        transport=httpx.MockTransport(slow_transport),
    )
    with pytest.raises(RelevanceLLMError) as exc_info:
        client.complete("slow prompt")
    assert "timed out" in str(exc_info.value)


def test_unrelated_search_results_rejected_as_alternatives(tmp_path: Path):
    problem = {
        "problem": "Syncing markdown notes across machines",
        "target_user": "knowledge workers",
        "category": "productivity",
    }
    res_unrelated = {
        "alternative_id": "robotics/arm-controller",
        "url": "https://github.com/robotics/arm-controller",
        "description": "Inverse kinematics solver for 6-DOF robot arm",
        "readme": "Detailed ROS kinematics and dynamics equations.",
    }
    res_related = {
        "alternative_id": "bob/vault-sync",
        "url": "https://github.com/bob/vault-sync",
        "description": "A lightweight tool to sync markdown notes across machines",
        "readme": "Keep notes in sync with end-to-end encryption.",
    }

    def fake_llm(prompt: str, sys: str) -> str:
        if "arm-controller" in prompt or "kinematics" in prompt:
            return json.dumps({
                "same_problem": False,
                "overlap": "none",
                "reason": "Inverse kinematics solver for robotics is unrelated to note syncing.",
                "quotes": [],
            })
        return json.dumps({
            "same_problem": True,
            "overlap": "full",
            "reason": "Direct alternative for syncing markdown notes across devices.",
            "quotes": ["sync markdown notes across machines"],
        })

    client = RelevanceClient(cache_dir=tmp_path, mock_handler=fake_llm)
    filtered = filter_relevant_alternatives(
        problem_statement=problem,
        candidate_repo_id="alice/note-sync",
        search_results=[res_unrelated, res_related],
        client=client,
    )
    assert len(filtered) == 1
    assert filtered[0]["alternative_id"] == "bob/vault-sync"
    assert filtered[0]["same_problem"] is True
    assert filtered[0]["overlap"] == "full"


def test_full_overlap_verified_alternative_leads_to_already_exists(tmp_path: Path):
    candidate = {
        "repo_id": "charlie/web-invoice",
        "description": "Web app for generating PDF invoices for freelancers.",
        "readme_excerpt": "Generate beautiful PDF invoices directly from your browser.",
    }

    # Case A: Full overlap verified alternative leads to ALREADY_EXISTS
    prior_rows_full = [
        {
            "alternative_id": "inc/free-invoice",
            "url": "https://github.com/inc/free-invoice",
            "licence_spdx": "MIT",
            "excerpt": "Free open-source web app to generate PDF invoices directly from your browser.",
            "overlap": "full",
            "same_problem": True,
            "stars": 800,
            "pushed_at": "2026-09-01T00:00:00Z",
            "archived": False,
            "page_kind": "product",
        }
    ]

    def fake_llm_full(prompt: str, sys: str) -> str:
        return json.dumps({
            "verdict": "ALREADY_EXISTS",
            "reason": "free-invoice already covers generating PDF invoices in the browser.",
            "problem_quote": "Generate beautiful PDF invoices directly from your browser.",
            "alternative_repo": "inc/free-invoice",
            "alternative_quote": "generate PDF invoices directly from your browser",
        })

    client_full = RelevanceClient(cache_dir=tmp_path / "c_full", mock_handler=fake_llm_full)
    verdict_full, reason_full = judge_discovery(
        candidate=candidate,
        prior_rows=prior_rows_full,
        demand_rows=[],
        client=client_full,
    )
    assert verdict_full == "ALREADY_EXISTS"
    assert "Method: llm+verified quote" in reason_full

    # Case B: Partial overlap alternative cannot trigger ALREADY_EXISTS (downgraded to NEEDS_REVIEW)
    prior_rows_partial = [
        {
            "alternative_id": "inc/partial-invoice",
            "url": "https://github.com/inc/partial-invoice",
            "licence_spdx": "MIT",
            "excerpt": "General accounting CLI with an invoice PDF plugin.",
            "overlap": "partial",
            "same_problem": True,
        }
    ]

    def fake_llm_partial(prompt: str, sys: str) -> str:
        return json.dumps({
            "verdict": "ALREADY_EXISTS",
            "reason": "partial-invoice covers some invoice features.",
            "problem_quote": "Generate beautiful PDF invoices directly from your browser.",
            "alternative_repo": "inc/partial-invoice",
            "alternative_quote": "General accounting CLI with an invoice PDF plugin.",
        })

    client_partial = RelevanceClient(cache_dir=tmp_path / "c_part", mock_handler=fake_llm_partial)
    verdict_part, reason_part = judge_discovery(
        candidate=candidate,
        prior_rows=prior_rows_partial,
        demand_rows=[],
        client=client_partial,
    )
    assert verdict_part == "NEEDS_REVIEW"
    assert "requires verified full overlap" in reason_part


def test_unverified_quote_in_problem_step_gives_needs_review(tmp_path: Path):
    candidate = {
        "repo_id": "dave/file-compressor",
        "description": "Fast command line gzip compression tool.",
        "readme_excerpt": "Compress files quickly using multithreaded gzip.",
    }

    def fake_llm(prompt: str, sys: str) -> str:
        return json.dumps({
            "problem": "Large distributed data lakes need petabyte-scale streaming compression",
            "target_user": "Big data architects",
            "category": "infrastructure",
            "search_queries": ["streaming data lake compression", "parquet gzip streaming"],
            "quotes": ["petabyte-scale streaming compression for big data"],
        })

    client = RelevanceClient(cache_dir=tmp_path, mock_handler=fake_llm)
    problem_data, meta = extract_problem_statement(candidate, client=client)
    assert problem_data is None
    assert meta["fallback"] is False
    assert "unverified LLM quote:" in meta["error"]
    assert "petabyte-scale streaming compression" in meta["error"]


def test_candidate_itself_and_forks_are_excluded(tmp_path: Path):
    candidate = {
        "repo_id": "eve/super-tool",
        "description": "Super tool for automation.",
        "readme_excerpt": "Super tool for automation.",
        "primary_language": "Python",
    }

    def mock_transport_fn(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "/search/repositories" in url_str:
            return httpx.Response(
                200,
                json={
                    "items": [
                        # 1. Candidate itself
                        {"full_name": "eve/super-tool", "name": "super-tool", "fork": False, "description": "Candidate itself"},
                        # 2. Fork via parent field
                        {"full_name": "forker1/super-tool", "name": "super-tool", "fork": True, "parent": {"full_name": "eve/super-tool"}, "description": "Fork"},
                        # 3. Fork via source field
                        {"full_name": "forker2/my-tool", "name": "my-tool", "fork": True, "source": {"full_name": "eve/super-tool"}, "description": "Another fork"},
                        # 4. Fork via description note
                        {"full_name": "forker3/super-tool", "name": "super-tool", "fork": True, "description": "forked from eve/super-tool"},
                        # 5. Legitimate competitor repository
                        {"full_name": "competitor/awesome-tool", "name": "awesome-tool", "fork": False, "description": "Real competitor alternative", "license": {"spdx_id": "MIT"}, "stargazers_count": 500, "pushed_at": "2026-08-01T00:00:00Z"},
                    ]
                },
            )
        return httpx.Response(200, json={"items": []})

    transport = httpx.MockTransport(mock_transport_fn)
    toolbox = ToolBox(
        tools={"github": GitHubTool(transport=transport)},
        budget=Budget(max_calls=10, max_seconds=30.0),
    )

    results = search_prior_art(
        candidate=candidate,
        queries=["super tool automation"],
        toolbox=toolbox,
        language="Python",
    )
    assert len(results) == 1
    assert results[0]["alternative_id"] == "competitor/awesome-tool"
    assert results[0]["stars"] == 500


def test_cache_key_changes_when_model_changes(tmp_path: Path):
    cache_dir = tmp_path / "model_cache"
    client1 = RelevanceClient(
        model="gemini-3.1-pro-preview",
        cache_dir=cache_dir,
        mock_handler=lambda p, s: '{"model": "gemini-3.1"}',
    )
    client2 = RelevanceClient(
        model="meterless/auto",
        cache_dir=cache_dir,
        mock_handler=lambda p, s: '{"model": "meterless"}',
    )

    prompt = "Test prompt input"
    system_prompt = "Test system prompt"

    resp1, req1, _ = client1.complete(prompt, system_prompt)
    resp2, req2, _ = client2.complete(prompt, system_prompt)

    assert resp1 == '{"model": "gemini-3.1"}'
    assert resp2 == '{"model": "meterless"}'
    assert req1 != req2

    cache_files = list(cache_dir.glob("*.json"))
    assert len(cache_files) == 2

    # Check that each cache file recorded its own model
    models_in_cache = set()
    for cf in cache_files:
        data = json.loads(cf.read_text(encoding="utf-8"))
        models_in_cache.add(data["model"])

    assert "gemini-3.1-pro-preview" in models_in_cache
    assert "meterless/auto" in models_in_cache


def test_judge_discovery_verifies_against_union_of_prior_art_csv_and_problem_search(tmp_path: Path):
    candidate = {
        "repo_id": "tharani-2006/Invoice-generator-application",
        "description": "Full-stack invoice generator application.",
        "readme_excerpt": "A full-stack web application for creating, managing, and sending professional invoices with multiple customizable templates.",
    }
    # (a) row from prior_art.csv without overlap field
    prior_rows = [
        {
            "alternative_id": "al1abb/invoify",
            "url": "https://github.com/al1abb/invoify",
            "licence_spdx": "MIT",
            "excerpt": "Invoify is a free, web-based invoice generator built with Next.js",
            "stars": 6376,
            "pushed_at": "2026-08-24T02:55:34Z",
            "archived": False,
            "page_kind": "product",
        }
    ]
    # (b) alternative from problem-based search
    problem_alternatives = [
        {
            "alternative_id": "other/invoice-maker",
            "url": "https://github.com/other/invoice-maker",
            "licence_spdx": "MIT",
            "excerpt": "Another invoice tool for freelancers",
            "overlap": "partial",
            "same_problem": True,
        }
    ]

    def fake_llm(prompt: str, sys: str) -> str:
        return json.dumps({
            "verdict": "ALREADY_EXISTS",
            "reason": "al1abb/invoify already exists and covers invoice generation.",
            "problem_quote": "full-stack web application for creating, managing, and sending professional invoices",
            "alternative_repo": "al1abb/invoify",
            "alternative_quote": "Invoify is a free, web-based invoice generator",
        })

    client = RelevanceClient(cache_dir=tmp_path, mock_handler=fake_llm)
    verdict, reason = judge_discovery(
        candidate=candidate,
        prior_rows=prior_rows,
        demand_rows=[],
        client=client,
        problem_alternatives=problem_alternatives,
    )
    assert verdict == "ALREADY_EXISTS"
    assert "invoify" in reason


def test_search_includes_self_hosted_category_query():
    from app.agents.relevance import build_search_queries

    candidate = {
        "repo_id": "jocxfin/pwgen",
        "description": "Simple self hosted password generator.",
        "readme_excerpt": "Simple self hosted password generator with docker support.",
    }
    problem_statement = {
        "problem": "Generating secure passwords in isolated environments",
        "target_user": "self-hosters",
        "category": "security",
        "search_queries": ["docker password generator", "generate secure passphrases"],
    }
    queries = build_search_queries(candidate, problem_statement)
    # Must include at least one query built from problem category plus 'self-hosted' or 'open source'
    assert any("self-hosted" in q.lower() and "security" in q.lower() for q in queries) or \
           any("open source" in q.lower() and "security" in q.lower() for q in queries)


def test_relevance_cache_hit_logs_cache_hit_label(tmp_path: Path, capsys):
    """Problem 4: Cached relevance calls must be labelled with 'cache_hit served=<model>' in stderr."""
    client = RelevanceClient(
        cache_dir=tmp_path,
        mock_handler=lambda p, s: '{"answer": "ok"}',
        mock_served_model="gemini-3.1-pro-preview",
    )
    # Call 1: Populates cache
    client.complete("hello", "system prompt", repo_id="test/repo", step="discovery")
    _, err1 = capsys.readouterr()
    assert "served=gemini-3.1-pro-preview" in err1
    assert "cache_hit" not in err1

    # Call 2: Cache hit
    client.complete("hello", "system prompt", repo_id="test/repo", step="discovery")
    _, err2 = capsys.readouterr()
    assert "cache_hit served=gemini-3.1-pro-preview" in err2


def test_mocked_live_call_with_latency_is_not_cache_hit_until_second_call(tmp_path: Path, capsys):
    """Task 5: Mocked live call with latency has cache_hit=false, second call has cache_hit=true."""
    def live_handler(request: httpx.Request) -> httpx.Response:
        time.sleep(0.02)
        body = {
            "model": "nvidia/nemotron-3-super-120b-a12b",
            "choices": [{"message": {"content": '{"result": "live_ok"}'}}],
        }
        return httpx.Response(200, json=body)

    client = RelevanceClient(
        token="test-token",
        model="nvidia/nemotron-3-super-120b-a12b",
        cache_dir=tmp_path / "cache",
        transport=httpx.MockTransport(live_handler),
    )

    # First call: live network call
    text1, _, _ = client.complete("prompt1", "system1", repo_id="live/repo", step="problem")
    assert text1 == '{"result": "live_ok"}'
    assert client.last_cache_hit is False
    _, err1 = capsys.readouterr()
    assert "cache_hit" not in err1
    assert "served=nvidia/nemotron-3-super-120b-a12b" in err1

    # Second call: served from local cache
    text2, _, _ = client.complete("prompt1", "system1", repo_id="live/repo", step="problem")
    assert text2 == '{"result": "live_ok"}'
    assert client.last_cache_hit is True
    _, err2 = capsys.readouterr()
    assert "cache_hit served=nvidia/nemotron-3-super-120b-a12b" in err2


def test_ytkb_regression_with_fixtures_becomes_already_exists(tmp_path: Path):
    """A zero-star docs hit with a verified quote does not refuse the repository.

    The quote is still checked. Without maintenance and maturity facts the alternative
    is recorded as something to beat, not as ALREADY_EXISTS.
    """
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "prior_art"
    ddg_html = (fixtures_dir / "ddg_search_results.html").read_text(encoding="utf-8")
    npm_json = (fixtures_dir / "npm_search_results.json").read_text(encoding="utf-8")
    pypi_html = (fixtures_dir / "pypi_search_results.html").read_text(encoding="utf-8")

    candidate = {
        "repo_id": "SergioMendolia/ytkb",
        "html_url": "https://github.com/SergioMendolia/ytkb",
        "description": "A CLI application for syncing YouTrack knowledge base articles to local markdown files, edit them in your editor of choice and push them back online.",
        "readme_excerpt": "A CLI application for syncing YouTrack knowledge base articles to local markdown files, edit them in your editor of choice and push them back online.",
        "core_terms": "youtrack, markdown, knowledge base",
    }
    problem_statement = {
        "problem": "Developers and technical writers cannot easily edit and sync YouTrack knowledge base articles offline in their local markdown editor.",
        "target_user": "Developers and technical writers using YouTrack knowledge base",
        "category": "developer-tools",
        "search_queries": ["youtrack cli", "youtrack markdown export"],
    }

    def mock_transport_fn(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "duckduckgo.com" in url_str:
            return httpx.Response(200, text=ddg_html)
        if "registry.npmjs.org" in url_str:
            return httpx.Response(200, text=npm_json)
        if "pypi.org" in url_str:
            return httpx.Response(200, text=pypi_html)
        if "api.github.com/search/repositories" in url_str:
            return httpx.Response(200, json={"items": []})
        return httpx.Response(404)

    transport = httpx.MockTransport(mock_transport_fn)
    toolbox = ToolBox(
        tools={
            "github": GitHubTool(transport=transport),
            "npm": NpmTool(transport=transport),
            "pypi": PyPiTool(transport=transport),
            "web_search": WebSearchTool(transport=transport),
        },
        budget=Budget(max_calls=20, max_seconds=30.0),
    )

    search_results = search_prior_art(
        candidate=candidate,
        queries=["youtrack cli"],
        toolbox=toolbox,
    )
    assert len(search_results) > 0
    # Must have found yt-cli in DuckDuckGo search results
    assert any("yt-cli" in r["alternative_id"] or "yt-cli" in r["url"] for r in search_results)

    def fake_llm(prompt: str, sys_prompt: str) -> str:
        if "relevance_alternative" in sys_prompt or "Alternative Repository" in prompt:
            return json.dumps({
                "same_problem": True,
                "overlap": "full",
                "reason": "yt-cli provides full round trip markdown editing and sync for YouTrack articles.",
                "quotes": [
                    "save it to a local markdown file"
                ],
            })
        if "relevance_discovery" in sys_prompt or "Candidate Repository: SergioMendolia/ytkb" in prompt:
            return json.dumps({
                "verdict": "ALREADY_EXISTS",
                "reason": "yt-cli already exists and covers full markdown round trip for YouTrack articles.",
                "problem_quote": "syncing YouTrack knowledge base articles to local markdown files",
                "alternative_repo": "yt-cli",
                "alternative_quote": "save it to a local markdown file",
            })
        return json.dumps({"verdict": "NO_GO", "reason": "unknown"})

    client = RelevanceClient(cache_dir=tmp_path / "cache", mock_handler=fake_llm)

    problem_alts = filter_relevant_alternatives(
        problem_statement=problem_statement,
        candidate_repo_id=candidate["repo_id"],
        search_results=search_results,
        client=client,
    )
    assert len(problem_alts) > 0

    judged = judge_discovery(
        candidate=candidate,
        prior_rows=[],
        demand_rows=[],
        client=client,
        problem_alternatives=problem_alts,
        source_status=search_results.source_status,
        source_counts=search_results.source_counts,
        insufficient_coverage=search_results.insufficient_coverage,
    )
    verdict, reason = judged
    assert verdict != "ALREADY_EXISTS"
    assert judged.must_beat
    assert any("yt-cli" in str(item.get("alternative")) for item in judged.must_beat)
    assert "did not block" in reason or "did not block" in judged.must_beat[0]["reason"]


def test_zero_results_across_all_sources_yields_needs_review_insufficient_coverage(tmp_path: Path):
    """Task 6: zero results across all sources -> NEEDS_REVIEW / INSUFFICIENT_PRIOR_ART_COVERAGE."""
    candidate = {
        "repo_id": "niche/obscure-idea",
        "description": "Obscure tool with no competition found.",
        "readme_excerpt": "Obscure tool with no competition found anywhere.",
    }
    client = RelevanceClient(
        cache_dir=tmp_path / "cache",
        mock_handler=lambda p, s: json.dumps({
            "verdict": "CANDIDATE",
            "reason": "No competition found, looks good",
            "problem_quote": "Obscure tool with no competition found",
        }),
    )

    verdict, reason = judge_discovery(
        candidate=candidate,
        prior_rows=[],
        demand_rows=[],
        client=client,
        source_counts={"github": 0, "npm": 0, "pypi": 0, "web": 0},
        source_status={"github": "ok", "npm": "ok", "pypi": "ok", "web": "ok"},
    )
    assert verdict == "NEEDS_REVIEW"
    assert "INSUFFICIENT_PRIOR_ART_COVERAGE" in reason


def test_source_error_in_one_source_yields_needs_review(tmp_path: Path):
    """Task 6: one source SOURCE_ERROR -> NEEDS_REVIEW."""
    candidate = {
        "repo_id": "test/tool",
        "description": "A developer tool.",
        "readme_excerpt": "A developer tool solving friction.",
    }
    client = RelevanceClient(
        cache_dir=tmp_path / "cache",
        mock_handler=lambda p, s: json.dumps({
            "verdict": "CANDIDATE",
            "reason": "Good candidate",
            "problem_quote": "A developer tool solving friction",
        }),
    )

    verdict, reason = judge_discovery(
        candidate=candidate,
        prior_rows=[],
        demand_rows=[],
        client=client,
        source_counts={"github": 3, "npm": 1, "pypi": 0, "web": 2},
        source_status={"github": "ok", "npm": "ok", "pypi": "SOURCE_ERROR", "web": "ok"},
    )
    assert verdict == "NEEDS_REVIEW"
    assert "INSUFFICIENT_PRIOR_ART_COVERAGE" in reason


def test_fabricated_quote_in_alternative_snippet_is_rejected_needs_review(tmp_path: Path):
    """Task 6: fabricated quote not present in snippet -> rejected, NEEDS_REVIEW."""
    candidate = {
        "repo_id": "SergioMendolia/ytkb",
        "description": "CLI to sync YouTrack knowledge base articles to local markdown files",
        "readme_excerpt": "CLI to sync YouTrack knowledge base articles to local markdown files",
    }
    problem_alternatives = [
        {
            "alternative_id": "yt-cli",
            "url": "https://yt-cli.readthedocs.io/en/latest/commands/articles.html",
            "description": "yt-cli: YouTrack CLI Articles Commands",
            "readme": "yt articles fetch ID --file x.md and yt articles edit --file x.md",
            "overlap": "full",
            "same_problem": True,
        }
    ]

    # LLM hallucinates an alternative quote not present anywhere in the alternative's text
    def fake_llm(p: str, s: str) -> str:
        return json.dumps({
            "verdict": "ALREADY_EXISTS",
            "reason": "yt-cli already exists",
            "problem_quote": "sync YouTrack knowledge base articles to local markdown files",
            "alternative_repo": "yt-cli",
            "alternative_quote": "This quote was hallucinated and is not present in yt-cli snippet or readme.",
        })

    client = RelevanceClient(cache_dir=tmp_path / "cache", mock_handler=fake_llm)
    verdict, reason = judge_discovery(
        candidate=candidate,
        prior_rows=[],
        demand_rows=[],
        client=client,
        problem_alternatives=problem_alternatives,
    )
    assert verdict == "NEEDS_REVIEW"
    assert "unverified LLM quote" in reason


def test_query_generation_includes_cli_api_client_and_official_feature_patterns():
    """Task 6: query generation includes the cli / api client / official-feature patterns."""
    candidate = {
        "repo_id": "SergioMendolia/ytkb",
        "description": "A CLI application for syncing YouTrack knowledge base articles to local markdown files, edit them in your editor of choice and push them back online.",
        "readme_excerpt": "A CLI application for syncing YouTrack knowledge base articles to local markdown files, edit them in your editor of choice and push them back online.",
        "core_terms": "youtrack, markdown, knowledge base",
    }
    problem_statement = {
        "problem": "Developers and technical writers cannot easily edit and sync YouTrack knowledge base articles offline in their local markdown editor.",
        "target_user": "Developers and technical writers using YouTrack knowledge base",
        "category": "developer-tools",
        "search_queries": [
            "youtrack knowledge base sync",
            "youtrack markdown export",
            "sync knowledge base markdown",
            "youtrack cli editor",
        ],
    }
    queries = build_search_queries(candidate, problem_statement)
    queries_lower = [q.lower() for q in queries]

    assert 4 <= len(queries) <= 8
    # Must include <platform> cli
    assert any("youtrack cli" in q for q in queries_lower)
    # Must include <platform> api client
    assert any("youtrack api client" in q for q in queries_lower)
    # Must include <platform> <job> tool or export format
    assert any("youtrack" in q and ("export" in q or "sync" in q) for q in queries_lower)
    # Must include official-feature query pattern
    assert any("export" in q and "markdown" in q for q in queries_lower) or any("built-in" in q for q in queries_lower)







def test_verified_already_exists_survives_one_source_error(tmp_path: Path):
    """Positive verified evidence (full overlap + verbatim quotes) must not be downgraded by an unrelated source error."""
    candidate = {
        "repo_id": "SergioMendolia/ytkb",
        "description": "CLI to sync YouTrack knowledge base articles to local markdown files",
        "readme_excerpt": "CLI to sync YouTrack knowledge base articles to local markdown files",
    }
    problem_alternatives = [
        {
            "alternative_id": "yt-cli",
            "url": "https://yt-cli.readthedocs.io/en/latest/commands/articles.html",
            "description": "Fetch an article's content from YouTrack and save it to a local markdown file.",
            "readme": "Fetch an article's content from YouTrack and save it to a local markdown file.",
            "overlap": "full",
            "same_problem": True,
            "stars": 400,
            "pushed_at": "2026-08-01T00:00:00Z",
            "archived": False,
            "licence_spdx": "MIT",
            "free_tier": True,
            "page_kind": "product",
            "host": "other",
        }
    ]

    def fake_llm(p: str, s: str) -> str:
        return json.dumps({
            "verdict": "ALREADY_EXISTS",
            "reason": "yt-cli covers the same markdown round trip.",
            "problem_quote": "sync YouTrack knowledge base articles to local markdown files",
            "alternative_repo": "yt-cli",
            "alternative_quote": "save it to a local markdown file",
        })

    client = RelevanceClient(cache_dir=tmp_path / "cache", mock_handler=fake_llm)
    verdict, reason = judge_discovery(
        candidate=candidate,
        prior_rows=[],
        demand_rows=[],
        client=client,
        problem_alternatives=problem_alternatives,
        source_counts={"github": 0, "npm": 0, "pypi": 2, "web": 0},
        source_status={"github": "ok", "npm": "ok", "pypi": "ok", "web": "SOURCE_ERROR"},
    )
    assert verdict == "ALREADY_EXISTS", reason

def test_stack_component_is_not_treated_as_platform():
    """An invoice app built on MongoDB must be searched as an invoice tool, not a MongoDB tool."""
    candidate = {
        "repo_id": "someone/invoice-app",
        "description": "Invoice generator web app built with Express and MongoDB",
        "readme_excerpt": "Create professional invoices. Data stored in MongoDB.",
    }
    problem_statement = {
        "problem": "Freelancers need to create professional invoices quickly.",
        "category": "finance-tools",
        "search_queries": ["invoice generator web app", "freelance invoice manager"],
    }
    queries = build_search_queries(candidate, problem_statement)
    assert queries[0] == "invoice generator web app"
    assert not any(q.startswith("mongodb") for q in queries)


def test_round_robin_merge_keeps_later_queries():
    """The first query must not fill every slot; later (job) queries still contribute rows."""
    def mock_transport_fn(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "api.github.com/search/repositories" in url_str:
            q = request.url.params.get("q", "")
            prefix = "first" if q.startswith("first") else "second"
            return httpx.Response(200, json={"items": [
                {"full_name": f"{prefix}/repo{i}", "html_url": f"https://github.com/{prefix}/repo{i}", "description": prefix}
                for i in range(5)
            ]})
        if "/readme" in url_str:
            return httpx.Response(404)
        if "registry.npmjs.org" in url_str:
            return httpx.Response(200, json={"objects": [{"package": {"name": f"first-pkg{i}", "description": "first"}} for i in range(10)], "total": 10})
        if "duckduckgo.com" in url_str:
            return httpx.Response(200, text="<html><body></body></html>")
        return httpx.Response(404)

    transport = httpx.MockTransport(mock_transport_fn)
    toolbox = ToolBox(
        tools={
            "github": GitHubTool(transport=transport),
            "npm": NpmTool(transport=transport),
            "web_search": WebSearchTool(transport=transport),
        },
        budget=Budget(max_calls=40, max_seconds=30.0),
    )
    results = search_prior_art(
        candidate={"repo_id": "me/candidate"},
        queries=["first query", "second query"],
        toolbox=toolbox,
    )
    ids = [r["alternative_id"] for r in results]
    assert any(i.startswith("second/") for i in ids), ids



# --- Mainstream overlap and web quotas (trial5g: ytkb passed with only 3 web rows) ---

def _cand_llm(prompt: str, sys: str) -> str:
    return json.dumps({
        "verdict": "CANDIDATE",
        "reason": "Unique feature mix.",
        "problem_quote": "automates invoice processing for small businesses",
        "alternative_repo": None,
        "alternative_quote": None,
    })


_CAND = {
    "repo_id": "test/valid-tool",
    "description": "Invoice processor for freelancers.",
    "readme_excerpt": "This tool automates invoice processing for small businesses.",
}


def test_web_same_problem_partial_overlap_blocks_candidate(tmp_path: Path):
    web_alt = {
        "alternative_id": "web:https://vendor.example/help/invoices",
        "url": "https://vendor.example/help/invoices",
        "same_problem": True,
        "overlap": "partial",
        "excerpt": "Built-in invoice automation.",
        "official": True,
        "commercial": True,
        "pricing_page": True,
        "free_tier": True,
        "released_at": "2026-09-01",
        "page_checked_at": "2026-10-01",
        "page_kind": "product",
        "archived": False,
        "host": "web",
    }
    client = RelevanceClient(cache_dir=tmp_path, mock_handler=_cand_llm)
    verdict, reason = judge_discovery(_CAND, prior_rows=[], demand_rows=[], client=client, problem_alternatives=[web_alt])
    assert verdict == "NEEDS_REVIEW"
    assert "MAINSTREAM_OVERLAP" in reason and "vendor.example" in reason


def test_github_partial_overlap_alone_does_not_trigger_mainstream_rule(tmp_path: Path):
    gh_alt = {"alternative_id": "someone/tool", "url": "https://github.com/someone/tool", "same_problem": True, "overlap": "partial"}
    client = RelevanceClient(cache_dir=tmp_path, mock_handler=_cand_llm)
    verdict, reason = judge_discovery(_CAND, prior_rows=[], demand_rows=[], client=client, problem_alternatives=[gh_alt])
    assert "MAINSTREAM_OVERLAP" not in reason


def test_search_prior_art_web_runs_every_query_and_keeps_web_quota():
    web_queries = []

    def handler(request: httpx.Request) -> httpx.Response:
        u = str(request.url)
        if "mcp.exa.ai" in u:
            q = json.loads(request.content)["params"]["arguments"]["query"]
            web_queries.append(q)
            text = "".join(f"Title: {q} doc {i}\nURL: https://docs.example/{q.replace(' ', '-')}/{i}\nHighlights:\nabout {q}\n\n" for i in range(3))
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": text}]}})
        if "hn.algolia.com" in u:
            return httpx.Response(200, json={"hits": []})
        if "api.github.com/search/repositories" in u:
            q = request.url.params.get("q", "").replace(" ", "")
            return httpx.Response(200, json={"items": [
                {"full_name": f"{q}/repo{i}", "html_url": f"https://github.com/{q}/repo{i}", "description": q} for i in range(5)
            ]})
        if "registry.npmjs.org" in u:
            return httpx.Response(200, json={"objects": [{"package": {"name": f"pkg{i}", "description": "x"}} for i in range(5)], "total": 5})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    toolbox = ToolBox(
        tools={
            "github": GitHubTool(transport=transport),
            "npm": NpmTool(transport=transport),
            "web_search": WebSearchTool(transport=transport),
        },
        budget=Budget(max_calls=80, max_seconds=60.0),
    )
    queries = [f"query {i}" for i in range(6)]
    results = search_prior_art(candidate={"repo_id": "me/candidate"}, queries=queries, toolbox=toolbox)
    assert sorted(web_queries) == sorted(queries)
    assert results.source_counts["web"] >= 6
    assert len(results) <= 15



# --- Batched alternative judging and Markdown-tolerant quotes ---------------

from app.agents.relevance import quote_in_text


def test_quote_in_text_ignores_markdown_bold_but_not_word_changes():
    readme = "# Rammingen\n\n**Rammingen** is a self-hosted file synchronization and backup system."
    assert quote_in_text("Rammingen is a self-hosted file synchronization and backup system.", readme)
    assert not quote_in_text("Rammingen is a cloud file synchronization and backup system.", readme)


_ALTS = [
    {"alternative_id": "a/one", "url": "https://github.com/a/one", "description": "Sync YouTrack articles to markdown files.", "source": "github"},
    {"alternative_id": "web:https://vendor.example/export", "url": "https://vendor.example/export", "description": "Export articles as Markdown files.", "source": "web"},
    {"alternative_id": "b/two", "url": "https://github.com/b/two", "description": "A weather app.", "source": "github"},
]
_PROB = {"problem": "Sync YouTrack KB with markdown", "target_user": "devs", "category": "developer-tools"}


def test_batch_one_call_and_quotes_verified_per_item(tmp_path: Path):
    calls = []

    def llm(prompt: str, sys: str) -> str:
        calls.append(sys)
        return json.dumps({"results": [
            {"index": 0, "same_problem": True, "overlap": "full", "reason": "same", "quotes": ["Sync YouTrack articles to markdown files"]},
            {"index": 1, "same_problem": True, "overlap": "partial", "reason": "export only", "quotes": ["made up quote"]},
            {"index": 2, "same_problem": False, "overlap": "none", "reason": "unrelated", "quotes": []},
        ]})

    client = RelevanceClient(cache_dir=tmp_path, mock_handler=llm)
    # Two alternatives fit in one chunk. A third would be a separate call.
    rows = filter_relevant_alternatives(_PROB, "me/cand", _ALTS[:2], client=client)
    assert len(calls) == 1 and "BATCH MODE" in calls[0]
    assert [r["alternative_id"] for r in rows] == ["a/one"]


def test_batch_missing_index_retries_only_the_missing_items(tmp_path: Path):
    """A reply that judges one index is kept. Only the indexes it omitted are retried."""
    from app.agents.relevance import judge_alternatives_batch

    calls = []

    def llm(prompt: str, sys: str) -> str:
        calls.append("batch" if "BATCH MODE" in sys else "single")
        if "BATCH MODE" in sys:
            return json.dumps({"results": [{"index": 0, "same_problem": False, "overlap": "none", "reason": "", "quotes": []}]})
        return json.dumps({"same_problem": False, "overlap": "none", "reason": "no", "quotes": []})

    client = RelevanceClient(cache_dir=tmp_path, mock_handler=llm)
    rows = judge_alternatives_batch(_PROB, "me/cand", _ALTS, client=client)
    assert rows == []
    # Index 0 was parsed, so it is not sent to a single call. Indexes 1 and 2 share one
    # smaller batch; that reply still has no usable index, so only those two go per-item.
    assert calls[0] == "batch"
    assert calls.count("single") == 2
    assert calls.count("batch") == 2


def test_complete_json_retries_unusable_answer_and_never_caches_it(tmp_path):
    """Regression (full77c): an invalid-JSON answer was cached and replayed. Now it is discarded and retried."""
    calls = []

    def handler(prompt, system_prompt):
        calls.append(prompt)
        return "not json at all" if len(calls) == 1 else '{"ok": true}'

    client = RelevanceClient(mock_handler=handler, cache_dir=tmp_path / "c")
    obj, text, req, srv = client.complete_json("question", "sys", repo_id="r/x", step="problem")
    assert obj == {"ok": True}
    assert len(calls) == 2
    assert "Retry 1" in calls[1]
    # The bad first answer is not in the cache: only the good answer file remains.
    cached = [json.loads(f.read_text(encoding="utf-8"))["response_text"] for f in (tmp_path / "c").glob("*.json")]
    assert "not json at all" not in cached
    assert '{"ok": true}' in cached


def test_complete_json_gives_up_after_attempts(tmp_path):
    client = RelevanceClient(mock_handler=lambda p, s: "nope", cache_dir=tmp_path / "c")
    obj, text, req, srv = client.complete_json("q", "s", attempts=2)
    assert obj is None
    assert list((tmp_path / "c").glob("*.json")) == []


def test_batch_retries_on_missing_indexes_before_falling_back(tmp_path):
    from app.agents.relevance import judge_alternatives_batch

    calls = []

    def handler(prompt, system_prompt):
        calls.append(prompt)
        if len(calls) == 1:
            return json.dumps({"results": [{"index": 0, "same_problem": False, "overlap": "none", "reason": "x", "quotes": []}]})
        return json.dumps({"results": [
            {"index": 0, "same_problem": False, "overlap": "none", "reason": "x", "quotes": []},
            {"index": 1, "same_problem": False, "overlap": "none", "reason": "y", "quotes": []},
        ]})

    client = RelevanceClient(mock_handler=handler, cache_dir=tmp_path / "c")
    results = [
        {"alternative_id": "a/one", "url": "https://github.com/a/one", "description": "one tool"},
        {"alternative_id": "b/two", "url": "https://github.com/b/two", "description": "two tool"},
    ]
    out = judge_alternatives_batch({"problem": "p", "target_user": "u", "category": "c"}, "cand/x", results, client=client)
    assert out == []
    assert len(calls) == 2


def test_batch_unusable_emits_fallback_event_with_reason(tmp_path):
    from app.agents.relevance import judge_alternatives_batch

    events = []

    class T:
        def event(self, **kw):
            events.append(kw)

    client = RelevanceClient(mock_handler=lambda p, s: "garbage", cache_dir=tmp_path / "c")
    results = [
        {"alternative_id": "a/one", "url": "u1", "description": "one"},
        {"alternative_id": "b/two", "url": "u2", "description": "two"},
    ]
    assert judge_alternatives_batch({"problem": "p"}, "cand/x", results, client=client, trace=T()) is None
    assert any(e["action"] == "ALTERNATIVE_BATCH_FALLBACK" and "unusable" in e["output_summary"]["reason"] for e in events)


def test_batch_retries_once_on_transient_error_then_succeeds(tmp_path):
    from app.agents.relevance import judge_alternatives_batch

    calls = []
    events = []

    class T:
        def event(self, **kw):
            events.append(kw)

    def llm(prompt, sys):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("Nightshift returned HTTP 502")
        return json.dumps({"results": [
            {"index": 0, "same_problem": True, "overlap": "full", "reason": "same", "quotes": ["Sync YouTrack articles to markdown files"]},
            {"index": 1, "same_problem": False, "overlap": "none", "reason": "no", "quotes": []},
        ]})

    client = RelevanceClient(mock_handler=llm, cache_dir=tmp_path / "c")
    results = _ALTS[:2]
    rows = judge_alternatives_batch(_PROB, "cand/x", results, client=client, trace=T())
    assert rows is not None and [r["alternative_id"] for r in rows] == ["a/one"]
    assert any(e["action"] == "ALTERNATIVE_BATCH_RETRY" for e in events)
    assert not any(e["action"] == "ALTERNATIVE_BATCH_FALLBACK" for e in events)


def test_batch_gives_up_after_two_transient_errors(tmp_path):
    from app.agents.relevance import judge_alternatives_batch

    def llm(prompt, sys):
        raise RuntimeError("Nightshift returned HTTP 502")

    client = RelevanceClient(mock_handler=llm, cache_dir=tmp_path / "c")
    assert judge_alternatives_batch(_PROB, "cand/x", _ALTS[:2], client=client) is None


def test_truncated_batch_reply_keeps_closed_objects_and_retries_the_rest(tmp_path):
    """A reply cut off inside a later object must not discard the objects that already closed."""
    from app.agents.relevance import extract_result_entries, judge_alternatives_batch

    truncated = (
        '{"results": ['
        '{"index": 0, "same_problem": true, "overlap": "full", "reason": "same", '
        '"quotes": ["Sync YouTrack articles to markdown files"]},'
        '{"index": 1, "same_problem": false, "overlap": "none", "reason": "no", "quotes": []},'
        '{"index": 2, "same_problem": true, "overlap": "par'
    )
    assert [e["index"] for e in extract_result_entries(truncated)] == [0, 1]
    calls = []

    def llm(prompt, sys):
        calls.append(prompt)
        if len(calls) == 1:
            return truncated
        return json.dumps({"same_problem": False, "overlap": "none", "reason": "cut off", "quotes": []})

    client = RelevanceClient(mock_handler=llm, cache_dir=tmp_path / "c", mock_served_model="served-batch")
    rows = judge_alternatives_batch(_PROB, "cand/x", _ALTS, client=client)
    assert [r["alternative_id"] for r in rows] == ["a/one"]
    assert len(calls) == 2
    # The follow-up is only the alternative whose object was cut off.
    assert "b/two" in calls[1]
    assert "a/one" not in calls[1]
    assert "vendor.example" not in calls[1]


def test_every_batch_call_is_traced_with_served_model(tmp_path):
    from app.agents.relevance import judge_alternatives_batch

    events = []

    class T:
        def event(self, **kw):
            events.append(kw)

    def llm(prompt, sys):
        if "BATCH MODE" in sys and prompt.count("Alternative:") > 1:
            return (
                '{"results": ['
                '{"index": 0, "same_problem": false, "overlap": "none", "reason": "x", "quotes": []},'
                '{"index": 1, "same_problem": false, "overlap": "none", "reason": "y", "quotes": []}'
            )
        return json.dumps({"same_problem": False, "overlap": "none", "reason": "z", "quotes": []})

    client = RelevanceClient(mock_handler=llm, cache_dir=tmp_path / "c", mock_served_model="served-batch")
    assert judge_alternatives_batch(_PROB, "cand/x", _ALTS, client=client, trace=T()) == []
    calls = [e for e in events if e["action"] in {"ALTERNATIVE_BATCH_CALL", "ALTERNATIVE_CALL"}]
    assert calls
    assert all(e["llm"]["served_model"] == "served-batch" for e in calls)
    assert any(e["action"] == "ALTERNATIVE_BATCH_CALL" for e in calls)
    assert any(e["action"] == "ALTERNATIVE_CALL" for e in calls)


def test_build_search_queries_adds_product_discovery_queries_early():
    from app.agents.relevance import build_search_queries

    ps = {
        "problem": "Developers repeat boilerplate",
        "target_user": "developers",
        "category": "developer-tools",
        "search_queries": ["automate boilerplate code", "project scaffolding tool"],
    }
    qs = build_search_queries({"repo_id": "me/gen", "description": "a code generator"}, ps)
    assert "best automate boilerplate code tools" in qs[:4]
    assert "best project scaffolding tools" in qs[:4]
    assert len(qs) <= 8


def test_web_full_overlap_with_quote_overrides_llm_candidate(tmp_path: Path):
    web_alt = {
        "alternative_id": "web:https://vendor.example/help/invoices",
        "url": "https://vendor.example/help/invoices",
        "same_problem": True,
        "overlap": "full",
        "quotes": ["Built-in invoice automation"],
        "excerpt": "Built-in invoice automation.",
        "official": True,
        "commercial": True,
        "pricing_page": True,
        "free_tier": True,
        "released_at": "2026-09-01",
        "page_checked_at": "2026-10-01",
        "page_kind": "product",
        "archived": False,
        "host": "web",
    }
    client = RelevanceClient(cache_dir=tmp_path, mock_handler=_cand_llm)
    verdict, reason = judge_discovery(_CAND, prior_rows=[], demand_rows=[], client=client, problem_alternatives=[web_alt])
    assert verdict == "ALREADY_EXISTS"
    assert "vendor.example" in reason and "Built-in invoice automation" in reason


def test_github_full_overlap_with_quote_overrides_llm_candidate(tmp_path: Path):
    gh_alt = {
        "alternative_id": "someone/tool",
        "url": "https://github.com/someone/tool",
        "same_problem": True,
        "overlap": "full",
        "quotes": ["sync local Markdown files with YouTrack Knowledge Base articles"],
        "stars": 250,
        "pushed_at": "2026-09-15T00:00:00Z",
        "archived": False,
        "licence_spdx": "MIT",
        "page_kind": "product",
    }
    client = RelevanceClient(cache_dir=tmp_path, mock_handler=_cand_llm)
    verdict, reason = judge_discovery(_CAND, prior_rows=[], demand_rows=[], client=client, problem_alternatives=[gh_alt])
    assert verdict == "ALREADY_EXISTS"
    assert "someone/tool" in reason and "overrides LLM CANDIDATE" in reason


def test_github_partial_overlap_blocks_candidate(tmp_path: Path):
    gh_alt = {
        "alternative_id": "someone/tool",
        "url": "https://github.com/someone/tool",
        "same_problem": True,
        "overlap": "partial",
        "stars": 250,
        "pushed_at": "2026-09-15T00:00:00Z",
        "archived": False,
        "licence_spdx": "MIT",
        "page_kind": "product",
    }
    client = RelevanceClient(cache_dir=tmp_path, mock_handler=_cand_llm)
    verdict, reason = judge_discovery(_CAND, prior_rows=[], demand_rows=[], client=client, problem_alternatives=[gh_alt])
    assert verdict == "NEEDS_REVIEW"
    assert "ALTERNATIVE_OVERLAP" in reason


def test_filter_relevant_alternatives_uses_chunks_and_falls_back_per_failed_chunk(tmp_path: Path):
    from app.agents.relevance import ALTERNATIVE_CHUNK_SIZE, filter_relevant_alternatives

    n = ALTERNATIVE_CHUNK_SIZE + 2
    alts = [
        {"alternative_id": f"x/r{i}", "url": f"https://github.com/x/r{i}", "description": f"Tool number {i} syncs things.", "source": "github"}
        for i in range(n)
    ]
    batch_calls = []
    single_calls = []

    def llm(prompt: str, sys: str) -> str:
        if "results" in sys and "one entry per alternative" in sys:
            batch_calls.append(prompt)
            if "Tool number 0 " not in prompt:
                raise RuntimeError("Nightshift returned HTTP 502")
            idx = [int(x.split("]")[0]) for x in prompt.split("\n[")[1:]] if False else None
            import re
            ids = [int(m) for m in re.findall(r"^\[(\d+)\] Alternative", prompt, flags=re.M)]
            return json.dumps({"results": [{"index": i, "same_problem": False, "overlap": "none", "reason": "no", "quotes": []} for i in ids]})
        single_calls.append(prompt)
        return json.dumps({"same_problem": False, "overlap": "none", "reason": "no", "quotes": []})

    client = RelevanceClient(mock_handler=llm, cache_dir=tmp_path / "c")
    assert filter_relevant_alternatives(_PROB, "cand/x", alts, client=client) == []
    assert len(batch_calls) == 1 + 2  # chunk 1 once, chunk 2 failed and retried once
    assert len(single_calls) == 2  # only the failed chunk (2 alternatives) fell back
