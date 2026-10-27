"""Evidence must survive retrieval, model classification, and the final gate.

Synthetic counterexamples; no network, model endpoint, human-label injection, or
claim about the frozen v3 score. Model replies deliberately disagree with facts.
"""
import json
import socket
from datetime import date

import httpx
import pytest

from app.agents.blocker import evaluate_blocker
from app.agents.repo_facts import RepoFactsClient, _api_url
from app.agents.relevance import RelevanceClient, _row_from_entry, judge_alternative_relevance, judge_discovery
from app.agents.relevance import SearchResultsList, search_prior_art
from app.agents.alternative_evidence import match_alternative
from app.agents.alternative_evidence import trace_alternatives
from app.tools import Budget, ToolBox
from app.tools.base import ToolResult


TEXT = "Run your own local document conversion service."
CAND = {"repo_id": "newco/converter", "description": TEXT, "readme_excerpt": TEXT, "stars": 5}
PROBLEM = {"problem": TEXT, "target_user": "self-hosting teams", "category": "document-tools"}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("network forbidden")
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.delenv("PHOENIX_UNKNOWN_BLOCKER_REVIEW", raising=False)
    monkeypatch.delenv("PHOENIX_STRICT_SUBSTITUTION", raising=False)
    monkeypatch.setenv("PHOENIX_GATE1_EVIDENCE_REPAIR", "1")  # the repair is opt-in; these tests cover it


def result(**extra):
    return {"alternative_id": "other/converter", "url": "https://github.com/other/converter", "source": "github",
            "description": TEXT, "stars": 900, "pushed_at": "2026-10-20", "archived": False,
            "licence_spdx": "NOASSERTION", "self_hosted": True, **extra}


def entry(**extra):
    return {"same_problem": True, "overlap": "full", "reason": "same job", "quotes": [TEXT], **extra}


def client(tmp_path, payload):
    return RelevanceClient(cache_dir=tmp_path, mock_handler=lambda p, s: json.dumps(payload))


@pytest.mark.parametrize("batch", [False, True])
def test_source_access_evidence_survives_both_model_paths_and_can_refuse(tmp_path, batch):
    raw = result()
    model_entry = entry(self_hosted=False, licence_spdx="GPL-3.0", stars=0)
    if batch:
        row = _row_from_entry(CAND["repo_id"], raw, TEXT, model_entry, PROBLEM, None)
    else:
        row, _ = judge_alternative_relevance(PROBLEM, CAND["repo_id"], raw, client(tmp_path / "alt", model_entry))
    assert row["self_hosted"] is True  # Comes from source, never the model's invented fields.
    assert row["stars"] == 900
    assert row["licence_spdx"] == "NOASSERTION"
    decision = judge_discovery(CAND, [], [], client=client(tmp_path / "judge", {
        "verdict": "CANDIDATE", "problem_quote": TEXT, "reason": "novel",
    }), problem_alternatives=[row])
    assert decision[0] == "ALREADY_EXISTS"


@pytest.mark.parametrize("batch", [False, True])
def test_source_directory_classification_survives_and_does_not_refuse(tmp_path, batch):
    raw = result(page_kind="aggregator", official=True, page_checked_at="2026-10-20")
    if batch:
        row = _row_from_entry(CAND["repo_id"], raw, TEXT, entry(page_kind="product"), PROBLEM, None)
    else:
        row, _ = judge_alternative_relevance(PROBLEM, CAND["repo_id"], raw, client(tmp_path / "alt", entry(page_kind="product")))
    assert row["page_kind"] == "aggregator"
    assert not evaluate_blocker(CAND, row, as_of=date(2026, 10, 26))["blocks"]


@pytest.mark.parametrize("archived", [True, False])
def test_host_facts_replace_search_web_placeholders_including_archived(tmp_path, archived):
    calls = []
    def transport(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={"full_name": "other/converter", "stargazers_count": 1200,
            "pushed_at": "2026-10-20", "archived": archived, "license": {"spdx_id": "MIT"}})
    facts = RepoFactsClient(token="", cache_dir=tmp_path, transport=httpx.MockTransport(transport))
    source = result(source="web", alternative_id="web:https://github.com/other/converter", stars=0,
                    pushed_at="", archived=False, licence_spdx="")
    hydrated = facts.fetch_alternative(source)
    assert source["stars"] == 0 and source["archived"] is False  # No mutation of the source.
    assert hydrated["stars"] == 1200
    assert hydrated["archived"] is archived
    assert hydrated["licence_spdx"] == "MIT"
    assert hydrated["facts_source_url"] == "https://api.github.com/repos/other/converter"
    assert len(hydrated["facts_sha256"]) == 64
    assert len(calls) == 1


@pytest.mark.parametrize("text", [
    "https://github.com.evil.test/other/converter", "https://evil.test/github.com/other/converter",
    "https://github.com@evil.test/other/converter", "https://github.com/other/converter%2Fsecret",
    "https://github.com/search?q=converter", "other/converter?x=1",
])
def test_repo_api_mapping_does_not_trust_hostname_substrings_or_query_paths(text):
    assert _api_url(text) == (None, None)


def test_unverified_full_overlap_cannot_overrule_a_candidate(tmp_path):
    row = {**result(licence_spdx="MIT"), "same_problem": True, "overlap": "full",
           "quotes": ["This text was never collected"], "quote_verified": True}
    verdict = judge_discovery(CAND, [], [], client=client(tmp_path, {
        "verdict": "CANDIDATE", "problem_quote": TEXT, "reason": "novel",
    }), problem_alternatives=[row])
    assert verdict[0] != "ALREADY_EXISTS"


@pytest.mark.parametrize("batch", [False, True])
def test_verified_context_is_not_lost_when_quote_is_after_excerpt_limit(tmp_path, batch):
    text = "Background material. " * 90 + TEXT
    raw = result(description=text)
    if batch:
        row = _row_from_entry(CAND["repo_id"], raw, text, entry(), PROBLEM, None)
    else:
        row, _ = judge_alternative_relevance(PROBLEM, CAND["repo_id"], raw, client(tmp_path / "alt", entry()))
    judged = judge_discovery(CAND, [], [], client=client(tmp_path / "judge", {
        "verdict": "ALREADY_EXISTS", "problem_quote": TEXT, "alternative_repo": "other/converter",
        "alternative_quote": TEXT,
    }), problem_alternatives=[row])
    assert judged[0] == "ALREADY_EXISTS"


def test_a_model_name_prefix_or_description_mention_does_not_identify_a_repo(tmp_path):
    raw = result(licence_spdx="MIT", description=TEXT + " Compare other/converter-extended.")
    assert match_alternative("other/converter-extended", [raw]) is None
    assert judge_discovery(CAND, [raw], [], client=client(tmp_path, {
        "verdict": "ALREADY_EXISTS", "problem_quote": TEXT, "alternative_repo": "other/converter-extended", "alternative_quote": TEXT,
    }))[0] == "NEEDS_REVIEW"


def test_canonical_repo_url_matches_but_short_name_ambiguity_does_not():
    raw = result()
    assert match_alternative("https://github.com/OTHER/converter.git", [raw]) is raw
    assert match_alternative("converter", [raw, result(alternative_id="third/converter", url="https://github.com/third/converter")]) is None


@pytest.mark.parametrize("initial_status,expected", [("ok", False), ("SOURCE_ERROR", True), ("BUDGET_EXHAUSTED", True)])
def test_broader_retry_merges_coverage_without_hiding_earlier_errors(initial_status, expected):
    initial = SearchResultsList([], source_counts={"github": 0, "web": 0}, source_status={"github": initial_status, "web": "ok"}, insufficient_coverage=True)
    retry = SearchResultsList([result()], source_counts={"github": 1, "web": 0}, source_status={"github": "ok", "web": "ok"})
    initial.merge_retry(retry)
    assert initial.source_counts == {"github": 1, "web": 0}
    assert initial.source_status["github"] == initial_status
    assert initial.insufficient_coverage is expected


class _Tool:
    def __init__(self, function):
        self.function = function
    def call(self, args):
        return ToolResult(ok=True, data=self.function(args), source_url="synthetic", fetched_at="2026-10-27", error="")


@pytest.mark.parametrize("fork_of_candidate", [False, True])
def test_web_repo_hit_gets_host_facts_and_readme_even_when_keyword_search_misses(fork_of_candidate):
    calls = []
    def github(args):
        calls.append(args)
        if args["action"] == "repositories":
            return {"items": []}
        if args["action"] == "readme":
            return {"decoded_text": TEXT}
        return {"stargazers_count": 1200, "pushed_at": "2026-10-20", "archived": False, "license": {"spdx_id": "MIT"},
                "fork": fork_of_candidate, "parent": {"full_name": CAND["repo_id"] if fork_of_candidate else "unrelated/project"}}
    box = ToolBox(tools={"github": _Tool(github), "web_search": _Tool(lambda a: {"items": [{
        "url": "https://github.com/other/converter", "title": "Converter", "snippet": "An actual search snippet"}]})}, budget=Budget(max_calls=10))
    found = search_prior_art(CAND, ["local document conversion service"], box)
    if fork_of_candidate:
        assert not found
        assert found.own_filtered[0]["reason"] == "fork of this repository"
    else:
        assert found[0]["stars"] == 1200 and found[0]["licence_spdx"] == "MIT"
        assert TEXT in found[0]["readme"] and "An actual search snippet" in found[0]["readme"]
        assert {a["action"] for a in calls} == {"repositories", "repo", "readme"}


def test_optional_web_enrichment_respects_remaining_tool_budget():
    calls = []
    def github(args):
        calls.append(args)
        return {"items": []}
    box = ToolBox(tools={"github": _Tool(github), "web_search": _Tool(lambda a: {"items": [{
        "url": "https://github.com/other/converter", "snippet": TEXT}]})}, budget=Budget(max_calls=2))
    found = search_prior_art(CAND, ["converter"], box)
    assert found and [a["action"] for a in calls] == ["repositories"]
    assert box.budget.calls_used == 2


@pytest.mark.parametrize("batch", [False, True])
def test_explicit_source_job_mismatch_is_not_lost(tmp_path, batch):
    raw = result(same_job=False, licence_spdx="MIT")
    if batch:
        parsed = _row_from_entry(CAND["repo_id"], raw, TEXT, entry(same_job=True), PROBLEM, None)
    else:
        parsed, _ = judge_alternative_relevance(PROBLEM, CAND["repo_id"], raw, client(tmp_path, entry(same_job=True)))
    assert parsed["same_job"] is False
    assert not evaluate_blocker(CAND, parsed, as_of=date(2026, 10, 26))["blocks"]


def test_unknown_boolean_strings_do_not_establish_official_or_access_status():
    unknown = {**result(stars=None, official="unknown", commercial="false", licence_spdx="", free_tier="unknown", self_hosted="false"),
               "same_problem": True, "overlap": "full", "page_checked_at": "2026-10-20"}
    decision = evaluate_blocker(CAND, unknown, as_of=date(2026, 10, 26))
    assert not decision["blocks"]
    assert "stars" in decision["unknown_reasons"]
    assert "licence/free tier" in decision["unknown_reasons"]


def test_nested_gitlab_namespace_is_part_of_project_identity():
    expected = ("gitlab", "https://gitlab.com/api/v4/projects/group%2Fsubgroup%2Fproject")
    assert _api_url("https://gitlab.com/group/subgroup/project") == expected
    assert _api_url("https://gitlab.com/group/subgroup/project/-/blob/main/README.md") == expected
    assert _api_url("https://gitlab.com/-/project") == (None, None)


def test_large_proof_snapshot_is_complete_json_and_does_not_include_raw_page(tmp_path):
    from app.trace_log import HackathonTrace
    rows = [{**result(), "quotes": [TEXT * 25], "page_html": "RAW_PAGE_NOT_IN_TRACE", "text": TEXT * 100,
             "substitution": {"candidate_quote": TEXT * 20, "alternative_quote": TEXT * 20}} for _ in range(15)]
    payload, limit = trace_alternatives(rows)
    assert limit > 20000
    trace = HackathonTrace("large-evidence", tmp_path)
    trace.event(agent_name="orchestrator", action="ALTERNATIVES_VERIFIED", output_summary=payload, summary_limit=limit)
    record = json.loads(trace.path.read_text().splitlines()[0])
    assert json.loads(record["output_summary"]) == payload
    assert "RAW_PAGE_NOT_IN_TRACE" not in record["output_summary"]
