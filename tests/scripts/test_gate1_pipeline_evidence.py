"""Complete scoring orchestration on synthetic CSV/tools/model replies only."""
import csv
import json
import socket

import httpx
import pytest

from app.agents.repo_facts import RepoFactsClient
from app.agents.relevance import RelevanceClient
from app.sql_catalog import DISCOVERY_COLUMNS, PRIOR_ART_COLUMNS, DEMAND_COLUMNS
from app.tools import Budget, ToolBox
from app.tools.base import ToolResult
from scripts.score_candidates import score_all
from scripts.verify_trace import verify

SOURCE = "Write desktop calendar events to a team calendar using a self-hosted server."
ALT = "This self-hosted server writes desktop calendar events to a team calendar."


def _run_orchestration(tmp_path, monkeypatch, concrete_product, *, repair):
    def deny(*args, **kwargs):
        raise AssertionError("network forbidden")
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.delenv("PHOENIX_GATE1_EVIDENCE_REPAIR", raising=False)
    monkeypatch.delenv("PHOENIX_STRICT_SUBSTITUTION", raising=False)
    if repair:
        monkeypatch.setenv("PHOENIX_STRICT_SUBSTITUTION", "1")
        monkeypatch.setenv("PHOENIX_GATE1_EVIDENCE_REPAIR", "1")
    monkeypatch.delenv("PHOENIX_UNKNOWN_BLOCKER_REVIEW", raising=False)
    monkeypatch.setenv("PHOENIX_HMEM", "off")
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    candidate = {"repo_id": "synthetic/calendar-writer", "html_url": "https://github.com/synthetic/calendar-writer",
                 "description": SOURCE, "readme_excerpt": SOURCE, "problem_excerpt": SOURCE,
                 "pushed_at": "2024-01-01", "licence_spdx": "MIT", "data_origin": "synthetic", "code_paths": "main.py"}
    for filename, cols, rows in (("discovery_candidates.csv", DISCOVERY_COLUMNS, [candidate]),
                                 ("prior_art.csv", PRIOR_ART_COLUMNS, []), ("demand_signals.csv", DEMAND_COLUMNS, [])):
        with (snapshot / filename).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=cols)
            writer.writeheader()
            writer.writerows({key: row.get(key, "") for key in cols} for row in rows)

    class Github:
        def call(self, args):
            data = {"items": [{"full_name": "synthetic/other", "description": ALT, "readme": ALT}]}
            return ToolResult(ok=True, data=data, error="", source_url="synthetic", fetched_at="2026-10-27")

    facts = RepoFactsClient(token="", cache_dir=tmp_path / "facts", transport=httpx.MockTransport(lambda req: httpx.Response(200, json={
        "stargazers_count": 1500, "pushed_at": "2026-10-20", "archived": False, "license": {"spdx_id": "MIT"},
    })))
    def respond(prompt, system):
        if "Relevance Problem Extractor" in system:
            return json.dumps({"problem": "Publish calendar events", "target_user": "teams", "category": "calendar-tools",
                               "search_queries": ["calendar publisher"], "quotes": [SOURCE]})
        if "Relevance Alternative Judge" in system:
            return json.dumps({"same_problem": True, "overlap": "full", "reason": "same job", "quotes": [ALT],
                "substitution": {"candidate_quote": SOURCE, "alternative_quote": ALT, "concrete_product": concrete_product,
                                 "same_user": True, "same_operation": True, "same_direction": True, "same_deployment": True}})
        if "Relevance Discovery Judge" in system:
            return json.dumps({"verdict": "CANDIDATE", "reason": "a conflicting model claim", "problem_quote": SOURCE})
        raise AssertionError("Unexpected model step in a refusal/review-only synthetic run")

    run_id = "synthetic-gate1-contract"
    result = score_all(data_dir=snapshot, output_json=tmp_path / "report.json", output_md=tmp_path / "report.md",
        log_dir=tmp_path / "logs", run_id=run_id, evidence_source="csv", facts_client=facts,
        custom_toolbox=ToolBox(tools={"github": Github()}, budget=Budget(max_calls=60)),
        relevance_client=RelevanceClient(cache_dir=tmp_path / "model-mock", mock_handler=respond))
    trace = tmp_path / "logs" / f"trace-{run_id}.jsonl"
    assert verify(trace, expected_run_hash=result["run_hash"])[0]
    events = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    return result, events


@pytest.mark.parametrize("concrete_product,expected", [(True, "ALREADY_EXISTS"), (False, "NEEDS_REVIEW")])
def test_full_orchestration_records_hydrated_facts_and_contract(tmp_path, monkeypatch, concrete_product, expected):
    result, events = _run_orchestration(tmp_path, monkeypatch, concrete_product, repair=True)
    assert result["ineligible"][0]["verdict"] == expected
    verified = next(json.loads(e["output_summary"]) for e in events if e["action"] == "ALTERNATIVES_VERIFIED")
    recorded = verified["alternatives"][0]
    assert recorded["source_facts"]["stars"] == 1500
    assert recorded["source_facts"]["licence_spdx"] == "MIT"
    assert recorded["source_facts"]["fetch_status"] == "ok"
    assert len(recorded["source_facts"]["facts_sha256"]) == 64
    assert recorded["substitution"]["concrete_product"] is concrete_product
    assert len(recorded["verified_text_sha256"]) == 64


def test_default_run_keeps_main_trace_shape_and_candidate_verdict(tmp_path, monkeypatch):
    """All flags off: the legacy six-field ALTERNATIVES_VERIFIED record, no hydrated facts, main's verdict."""
    result, events = _run_orchestration(tmp_path, monkeypatch, True, repair=False)
    verified = next(json.loads(e["output_summary"]) for e in events if e["action"] == "ALTERNATIVES_VERIFIED")
    assert set(verified["alternatives"][0]) == {"alternative_id", "url", "source", "overlap", "reason", "quotes"}
    assert not any(e["action"] == "GATE_DECISION" and "facts_sha256" in e["output_summary"] for e in events)
    # Same verdict as main 37ab838 on this input.
    discovery = next(json.loads(e["output_summary"]) for e in events if e["action"] == "DISCOVERY_FILTER")
    assert discovery["verdict"] == "CANDIDATE" and "SUBSTITUTION" not in discovery["reason"]
