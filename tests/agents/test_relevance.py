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
from app.tools import Budget, GitHubTool, ToolBox


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
    assert "requested=meterless/auto" in reason


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




