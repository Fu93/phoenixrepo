"""Regression: a malformed discovery reply is retried, never turned into a permanent NEEDS_REVIEW."""
from __future__ import annotations

import json

from app.agents.relevance import RelevanceClient, judge_discovery


def _candidate():
    return {
        "repo_id": "acme/mirror",
        "description": "A talking mirror",
        "readme_excerpt": "Just talk to the mirror. No phone to pull out.",
    }


def test_discovery_retries_malformed_json_and_uses_the_valid_retry(tmp_path):
    calls = []

    def handler(prompt: str, system: str) -> str:
        calls.append(prompt)
        if len(calls) == 1:
            return "Sure! Here is my verdict: the repo seems novel"  # prose, no JSON
        return json.dumps({
            "verdict": "NO_GO",
            "reason": "boilerplate",
            "problem_quote": "unknown",
        })

    client = RelevanceClient(cache_dir=tmp_path, mock_handler=handler, mock_served_model="mock-model")
    verdict, reason = judge_discovery(
        _candidate(), [], [], client=client,
        source_status={"github": "ok", "web": "ok"}, source_counts={"github": 3, "web": 3},
    )
    assert len(calls) == 2
    assert "not valid JSON" not in reason
    assert verdict != "CANDIDATE"


def test_discovery_still_needs_review_when_every_attempt_is_malformed(tmp_path):
    client = RelevanceClient(cache_dir=tmp_path, mock_handler=lambda p, s: "no json at all", mock_served_model="mock-model")
    verdict, reason = judge_discovery(
        _candidate(), [], [], client=client,
        source_status={"github": "ok", "web": "ok"}, source_counts={"github": 3, "web": 3},
    )
    assert verdict == "NEEDS_REVIEW"
    assert "not valid JSON" in reason
