"""Maturity rule and the 16-case over-refusal audit, replayed with no network and no Nightshift."""
import json
from datetime import date

import pytest

from app.agents.blocker import evaluate_blocker
from app.agents.relevance import RelevanceClient, judge_discovery
from app.first_gate import REVIVED_NOT_RECOMMENDED, apply_first_gate
from scripts.eval_gate1 import LABELS, rule_observation

AS_OF = date(2026, 10, 25)
LEGIT = {
    "goodreasonai/ScrapeServ",
    "Selfdb-io/SelfDB",
    "rianvdm/raindrop-wordpress-linkblog-sync",
    "Shabi-x/go-file-sync",
    "tharani-2006/Invoice-generator-application",
}


def _audit_cases() -> list[dict]:
    payload = json.loads(LABELS.read_text(encoding="utf-8"))
    return [case for case in payload["cases"] if isinstance(case.get("audit"), dict)]


@pytest.mark.parametrize("case", _audit_cases(), ids=lambda case: case["repo_id"])
def test_audit_fixture_blocks_only_the_five_legitimate_refusals(case: dict):
    audit = case["audit"]
    decision = evaluate_blocker(audit["candidate"], audit["blocker"], as_of=AS_OF)
    assert decision["verdict_if_blocked"] != "RESURRECTED"
    if case["repo_id"] in LEGIT:
        assert audit["class"] == "LEGIT"
        assert decision["blocks"] is True
        assert decision["role"] == "block"
        assert decision["verdict_if_blocked"] == "ALREADY_EXISTS"
    else:
        assert audit["class"] in {"WEAK", "WRONG"}
        assert decision["blocks"] is False
        assert decision["verdict_if_blocked"] is None
        assert decision["role"] in {"must_beat", "not_alternative", "source_error"}


def test_rule_observation_on_the_sixteen_case_audit():
    payload = json.loads(LABELS.read_text(encoding="utf-8"))
    obs = rule_observation(payload)
    assert obs["set_size"] == 16
    assert obs["over_refusal_count"] == 0
    assert obs["false_pass_count"] == 0
    assert obs["over_refusal_rate"] == 0.0
    assert obs["false_pass_rate"] == 0.0
    assert "16-case" in obs["note"]
    assert "not a live Nightshift" in obs["note"]


def _client(tmp_path, payload: dict):
    return RelevanceClient(
        cache_dir=tmp_path,
        mock_handler=lambda _prompt, _sys: json.dumps(payload),
    )


def test_a_mature_verified_alternative_stays_already_exists(tmp_path):
    candidate = {
        "repo_id": "goodreasonai/ScrapeServ",
        "description": "Self-hosted URL to screenshot API.",
        "readme_excerpt": "Self-hosted URL to screenshot API.",
        "stars": 1174,
    }
    prior = [{
        "alternative_id": "gotenberg/gotenberg",
        "url": "https://github.com/gotenberg/gotenberg",
        "excerpt": "Gotenberg is a Docker-powered stateless API for PDF files.",
        "overlap": "full",
        "same_problem": True,
        "same_job": True,
        "stars": 13439,
        "pushed_at": "2026-10-25T00:00:00Z",
        "archived": False,
        "licence_spdx": "MIT",
        "page_kind": "product",
        "self_hosted": True,
    }]
    judged = judge_discovery(
        candidate=candidate,
        prior_rows=prior,
        demand_rows=[],
        client=_client(tmp_path, {
            "verdict": "ALREADY_EXISTS",
            "reason": "Gotenberg already provides a self-hosted URL screenshot API.",
            "problem_quote": "Self-hosted URL to screenshot API.",
            "alternative_repo": "gotenberg/gotenberg",
            "alternative_quote": "stateless API for PDF files",
        }),
    )
    assert judged[0] == "ALREADY_EXISTS"
    assert judged[0] != "RESURRECTED"
    assert judged.evidence
    assert judged.evidence[0]["alternative"] == "gotenberg/gotenberg"


def test_a_stale_small_full_overlap_is_recorded_and_does_not_refuse(tmp_path):
    candidate = {
        "repo_id": "samwho/spacer",
        "description": "Add spacers between the characters of a file.",
        "readme_excerpt": "Add spacers between the characters of a file.",
        "stars": 1818,
    }
    prior = [{
        "alternative_id": "hionay/tailer",
        "url": "https://github.com/hionay/tailer",
        "excerpt": "tailer is a Go port of spacer.",
        "overlap": "full",
        "same_problem": True,
        "same_job": True,
        "stars": 126,
        "pushed_at": "2023-07-01T00:00:00Z",
        "archived": False,
        "licence_spdx": "MIT",
        "page_kind": "product",
    }]
    judged = judge_discovery(
        candidate=candidate,
        prior_rows=prior,
        demand_rows=[],
        client=_client(tmp_path, {
            "verdict": "ALREADY_EXISTS",
            "reason": "tailer already does the same job.",
            "problem_quote": "Add spacers between the characters of a file.",
            "alternative_repo": "hionay/tailer",
            "alternative_quote": "a Go port of spacer",
        }),
    )
    assert judged[0] != "ALREADY_EXISTS"
    assert judged[0] in {"CANDIDATE", "NEEDS_REVIEW"}
    assert judged[0] != "RESURRECTED"
    assert judged.must_beat
    assert "hionay/tailer" in judged.must_beat[0]["alternative"]
    assert "did not block" in judged.must_beat[0]["reason"]


def test_a_bad_quote_stays_needs_review(tmp_path):
    candidate = {
        "repo_id": "goodreasonai/ScrapeServ",
        "description": "Self-hosted URL to screenshot API.",
        "readme_excerpt": "Self-hosted URL to screenshot API.",
        "stars": 1174,
    }
    prior = [{
        "alternative_id": "gotenberg/gotenberg",
        "excerpt": "Gotenberg is a Docker-powered stateless API for PDF files.",
        "overlap": "full",
        "same_problem": True,
        "same_job": True,
        "stars": 13439,
        "pushed_at": "2026-10-25T00:00:00Z",
        "archived": False,
        "licence_spdx": "MIT",
        "page_kind": "product",
        "self_hosted": True,
    }]
    judged = judge_discovery(
        candidate=candidate,
        prior_rows=prior,
        demand_rows=[],
        client=_client(tmp_path, {
            "verdict": "ALREADY_EXISTS",
            "reason": "Gotenberg already does this.",
            "problem_quote": "Self-hosted URL to screenshot API.",
            "alternative_repo": "gotenberg/gotenberg",
            "alternative_quote": "this sentence was never in the alternative",
        }),
    )
    assert judged[0] == "NEEDS_REVIEW"
    assert "unverified LLM quote" in judged[1]
    assert judged[0] != "CANDIDATE"
    assert judged[0] != "ALREADY_EXISTS"


def test_a_refused_repository_cannot_become_resurrected():
    decision, _reason = apply_first_gate(
        "RESURRECTED",
        "the repository's own tests exited 0",
        {"verdict": "ALREADY_EXISTS", "reason": "a mature alternative blocks"},
    )
    assert decision == REVIVED_NOT_RECOMMENDED
    assert decision != "RESURRECTED"
