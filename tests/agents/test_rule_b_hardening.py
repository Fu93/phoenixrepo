"""Rule B identity, unknown access facts, and #67 integration. No model/network."""
import hashlib
import json
import socket
from datetime import date

import pytest

from app.agents.package_identity import own_distribution_package
from app.agents.relevance import RelevanceClient, _unknown_blocker_review, judge_discovery
from app.trace_log import HackathonTrace
from scripts.verify_trace import verify

CANDIDATE = {"repo_id": "acme/widget", "description": "Sync notes between folders.", "stars": 20}
QUOTE = "Sync notes between folders."


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("network forbidden")
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setenv("PHOENIX_UNKNOWN_BLOCKER_REVIEW", "1")


def web(**extra):
    return {"alternative_id": "web:https://example.com/tool", "url": "https://example.com/tool", "source": "web",
            "same_problem": True, "same_job": True, "overlap": "full", "stars": 0, "pushed_at": "",
            "description": QUOTE, "excerpt": QUOTE, "quotes": [QUOTE], "quote_verified": True, **extra}


def package(**extra):
    return web(url="https://pkgs.alpinelinux.org/package/v3.23/community/aarch64/widget", **extra)


@pytest.mark.parametrize("licence", ["NOASSERTION", "unknown", "NONE", "null", "N/A", "  Unknown  "])
def test_unknown_licence_markers_do_not_count_as_a_proven_non_match(licence):
    assert _unknown_blocker_review(CANDIDATE, [web(licence_spdx=licence)], None, as_of=date(2026, 10, 26))


@pytest.mark.parametrize("field", ["free_tier", "self_hosted"])
def test_unknown_access_strings_do_not_count_as_a_known_boolean(field):
    assert _unknown_blocker_review(CANDIDATE, [web(**{field: "unknown"})], None)


def test_a_known_non_permissive_licence_keeps_the_existing_policy():
    assert _unknown_blocker_review(CANDIDATE, [web(licence_spdx="GPL-3.0")], None) is None


@pytest.mark.parametrize("body", [
    "Project | https://github.com/acme/widget\n",
    '<table><tr><th>Project</th><td><a href="https://github.com/acme/widget">upstream</a></td></tr></table>',
])
def test_explicit_project_metadata_excludes_an_own_distribution_package(body):
    row = package(page_text=body)
    receipt = own_distribution_package(CANDIDATE, row)
    assert receipt["project_url"] == "https://github.com/acme/widget"
    assert receipt["collected_text_sha256"] == hashlib.sha256(body.encode()).hexdigest()
    assert _unknown_blocker_review(CANDIDATE, [row], None) is None


@pytest.mark.parametrize("body", [
    "widget has the same name", "See https://github.com/acme/widget", "Project | https://github.com/other/widget",
    "Project | https://github.com/acme/widget-extra", "Project | https://github.com.evil.test/acme/widget",
    "Project | https://github.com@evil.test/acme/widget", "Project | /acme/widget",
    "Project | https://github.com/acme/widget\nProject | https://github.com/other/widget",
])
def test_name_similarity_or_ambiguous_upstream_does_not_release_the_hold(body):
    row = package(page_text=body)
    assert own_distribution_package(CANDIDATE, row) is None
    assert _unknown_blocker_review(CANDIDATE, [row], None)


def test_project_metadata_from_an_unrecognized_host_does_not_release_the_hold():
    row = web(page_text="Project | https://github.com/acme/widget\n")
    assert own_distribution_package(CANDIDATE, row) is None
    assert _unknown_blocker_review(CANDIDATE, [row], None)


def test_conflicting_collected_fields_do_not_release_the_hold():
    row = package(page_text="Project | https://github.com/acme/widget\n", text="Project | https://github.com/other/widget\n")
    assert own_distribution_package(CANDIDATE, row) is None
    assert _unknown_blocker_review(CANDIDATE, [row], None)


def test_own_package_does_not_hide_another_unknown_competitor():
    row = package(page_text="Project | https://github.com/acme/widget\n")
    hit = _unknown_blocker_review(CANDIDATE, [row, web()], None)
    assert hit["row"]["url"] == "https://example.com/tool"


def test_project_metadata_can_be_read_from_an_existing_collected_text_field():
    row = package(readme="Project | https://github.com/acme/widget\n")
    assert own_distribution_package(CANDIDATE, row)


def test_flag_off_remains_off_even_with_new_identity_data(monkeypatch):
    monkeypatch.delenv("PHOENIX_UNKNOWN_BLOCKER_REVIEW")
    assert _unknown_blocker_review(CANDIDATE, [web(licence_spdx="NOASSERTION")], None) is None


def test_hold_is_recorded_in_the_same_gate_decision_chain(tmp_path):
    trace = HackathonTrace("rule-b", tmp_path / "logs")
    client = RelevanceClient(cache_dir=tmp_path / "cache", mock_handler=lambda p, s: json.dumps({
        "verdict": "CANDIDATE", "reason": "new tool", "problem_quote": QUOTE,
    }))
    verdict = judge_discovery(CANDIDATE, [web()], [], client=client, trace=trace)
    assert verdict[0] == "NEEDS_REVIEW"
    trace.seal()
    assert verify(trace.path, expected_run_hash=trace.run_hash)[0]
    records = [json.loads(line) for line in trace.path.read_text().splitlines()]
    gate = [r for r in records if r["action"] == "GATE_DECISION"]
    assert len(gate) == 1
    output = json.loads(gate[0]["output_summary"])
    assert output["raw_verdict"] == "CANDIDATE"
    assert output["final_verdict"] == "NEEDS_REVIEW"
    assert output["path"] == "UNKNOWN_BLOCKER_FACTS"
    assert output["gate_alternative"]["url"] == "https://example.com/tool"
