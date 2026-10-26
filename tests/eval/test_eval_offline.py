"""The offline eval reports phrase-method accuracy. It does not invent a live score."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.build_eval_cases import _cases
from scripts.eval_ideas import evaluate

ROOT = Path(__file__).resolve().parents[2]


def test_committed_cases_match_the_builder_and_have_twelve_labels():
    committed = json.loads((ROOT / "data" / "eval" / "cases.json").read_text(encoding="utf-8"))
    assert committed["cases"] == _cases()
    assert len(committed["cases"]) == 12
    labels = {case["repo_id"]: case["label"] for case in committed["cases"]}
    assert labels["tharani-2006/Invoice-generator-application"] == "ALREADY_EXISTS"
    assert labels["SergioMendolia/ytkb"] == "CANDIDATE"
    assert labels["jondot/hygen"] == "ALREADY_EXISTS"
    assert labels["matt-dz/wecook"] == "ALREADY_EXISTS"
    assert labels["bytesbeard/color-palette-generator"] == "NO_GO"
    assert labels["mbhogal/splitfree"] == "CANDIDATE"
    queries = json.loads((ROOT / "data" / "eval" / "queries.json").read_text(encoding="utf-8"))["queries"]
    assert len(queries) >= 8
    assert any(item["source"] == "npm" and item["total_count"] == 585 for item in queries)
    assert any(item["source"] == "pypi" and item["total_count"] is None for item in queries)
    assert any(item.get("total_count") == 0 for item in queries)


def test_deterministic_report_matches_a_fresh_run():
    report = evaluate()
    assert report["mode"] == "deterministic-phrase"
    assert report["cases"] == 12
    by_id = {item["repo_id"]: item for item in report["results"]}
    assert by_id["tharani-2006/Invoice-generator-application"]["match"] is True
    assert by_id["SergioMendolia/ytkb"]["match"] is True
    assert by_id["jondot/hygen"]["match"] is False
    assert by_id["jondot/hygen"]["predicted"] == "NO_GO"
    assert by_id["matt-dz/wecook"]["predicted"] == "NO_GO"
    assert by_id["bytesbeard/color-palette-generator"]["predicted"] == "ALREADY_EXISTS"
    assert by_id["mbhogal/splitfree"]["match"] is True
    assert report["correct"] == sum(1 for item in report["results"] if item["match"])
    assert report["accuracy"] == report["correct"] / report["cases"]
    assert "Method: phrase/keyword." in by_id["SergioMendolia/ytkb"]["reason"]
