"""Gate-1 label checks. The live judge is never called."""
import json
from pathlib import Path

import pytest

from scripts import eval_gate1 as eg


def test_committed_labels_match_the_required_sets_and_quotes():
    payload = json.loads(eg.LABELS.read_text(encoding="utf-8"))
    assert eg.validate_labels(payload) == []
    by_id = {case["repo_id"]: case for case in payload["cases"]}
    assert set(by_id["orangekame3/mirrormate"]["acceptable"]) == {"CANDIDATE", "NEEDS_REVIEW"}
    assert "ALREADY_EXISTS" in by_id["orangekame3/mirrormate"]["forbidden"]
    assert "ALREADY_EXISTS" not in by_id["orangekame3/mirrormate"]["acceptable"]
    for repo_id in (
        "Fu93/hygen",
        "SergioMendolia/ytkb",
        "tharani-2006/Invoice-generator-application",
    ):
        assert by_id[repo_id]["acceptable"] == ["ALREADY_EXISTS"]
        roles = {item["role"] for item in by_id[repo_id]["evidence"]}
        assert {"candidate", "alternative"} <= roles
    for case in payload["cases"]:
        for item in case["evidence"]:
            assert item["quote"] in item["text"]
            assert item["source_url"].startswith("https://")
            assert item["fetched_at"] == "2026-10-25"


def test_without_a_token_the_runner_is_not_called(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("METERLESS_RUNTIME_TOKEN", raising=False)
    called: list[str] = []

    def runner(repo_id: str) -> dict:
        called.append(repo_id)
        return {"verdict": "CANDIDATE"}

    report = eg.evaluate(runner=runner)
    assert called == []
    assert report["labels_valid"] is True
    assert report["live_ran"] is False
    assert report["agreement"] is None
    assert report["results"] == []


def test_placeholder_token_does_not_count(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("METERLESS_RUNTIME_TOKEN", "rt-local")
    called: list[str] = []
    report = eg.evaluate(runner=lambda repo_id: called.append(repo_id) or {"verdict": "CANDIDATE"})
    assert called == []
    assert report["live_ran"] is False
    assert report["agreement"] is None


def test_a_token_uses_the_injected_runner_and_does_not_invent_agreement(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("METERLESS_RUNTIME_TOKEN", "test-token-not-used-for-network")
    seen: list[str] = []

    def runner(repo_id: str) -> dict:
        seen.append(repo_id)
        return {"verdict": "ALREADY_EXISTS", "reason": "injected"}

    report = eg.evaluate(runner=runner)
    assert report["live_ran"] is True
    assert set(seen) == set(eg.REQUIRED)
    by_id = {row["repo_id"]: row for row in report["results"]}
    assert by_id["orangekame3/mirrormate"]["agreed"] is False
    assert by_id["orangekame3/mirrormate"]["verdict"] == "ALREADY_EXISTS"
    for repo_id in ("Fu93/hygen", "SergioMendolia/ytkb", "tharani-2006/Invoice-generator-application"):
        assert by_id[repo_id]["agreed"] is True
    assert report["agreement"] == {"compared": 4, "agreed": 3, "not_compared": 0}


def test_a_judge_that_returns_nothing_is_not_counted_as_agreement(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("METERLESS_RUNTIME_TOKEN", "test-token-not-used-for-network")
    report = eg.evaluate(runner=lambda _repo_id: {})
    assert report["agreement"]["compared"] == 0
    assert report["agreement"]["agreed"] == 0
    assert report["agreement"]["not_compared"] == 4
    assert all(row["verdict"] is None and row["agreed"] is False for row in report["results"])


def test_invalid_labels_skip_the_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("METERLESS_RUNTIME_TOKEN", "test-token-not-used-for-network")
    bad = {
        "cases": [
            {
                "repo_id": "orangekame3/mirrormate",
                "acceptable": ["CANDIDATE", "NEEDS_REVIEW"],
                "forbidden": ["ALREADY_EXISTS"],
                "evidence": [
                    {
                        "role": "candidate",
                        "quote": "this sentence is not in the text",
                        "text": "a different sentence",
                        "source_url": "https://github.com/orangekame3/mirrormate",
                    }
                ],
            }
        ]
    }
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    called: list[str] = []
    report = eg.evaluate(path, runner=lambda repo_id: called.append(repo_id) or {"verdict": "CANDIDATE"})
    assert called == []
    assert report["labels_valid"] is False
    assert report["live_ran"] is False
    assert report["agreement"] is None
    assert any("not a substring" in err for err in report["label_errors"])
