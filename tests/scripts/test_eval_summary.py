"""eval_summary recomputes from result rows; infrastructure rows are never disagreements."""

from __future__ import annotations

import json

from scripts import eval_summary as es
from scripts.eval_gate1 import LABELS

LABEL_DATA = json.loads(LABELS.read_text(encoding="utf-8"))


def test_old_style_report_with_unjudged_is_summarised_correctly():
    # As written by the earlier eval_gate1: UNJUDGED counted as compared and disagreed.
    report = {
        "agreement": {"compared": 3, "agreed": 1, "not_compared": 0},
        "results": [
            {"repo_id": "tharani-2006/Invoice-generator-application", "verdict": "UNJUDGED", "agreed": False,
             "reason": "UNJUDGED (LLM_UNAVAILABLE): Nightshift returned HTTP 502", "answers_used": 1, "answers_by_nemotron": 1,
             "served_models": {"nvidia/nemotron-3-ultra-550b-a55b": 1}},
            {"repo_id": "DSRegoitia/CalendarSyncProject", "verdict": "CANDIDATE", "agreed": True, "reason": "nothing blocks",
             "answers_used": 9, "answers_by_nemotron": 9, "served_models": {"nvidia/nemotron-3-ultra-550b-a55b": 9}},
            {"repo_id": "goodreasonai/ScrapeServ", "verdict": "NEEDS_REVIEW", "agreed": False, "reason": "partial overlap of a mature product",
             "answers_used": 5, "answers_by_nemotron": 5, "served_models": {"nvidia/nemotron-3.5-lightning-30b-a3b": 5}},
        ],
        "not_started": ["Selfdb-io/SelfDB"],
    }
    s = es.summarise(report, LABEL_DATA)
    assert s["judged"] == 2 and s["not_judged"] == ["tharani-2006/Invoice-generator-application"]
    assert s["agreed"] == 1 and s["disagreed"] == ["goodreasonai/ScrapeServ"]
    assert s["false_pass"] == ["goodreasonai/ScrapeServ"] and s["over_refusal"] == []
    assert s["answers_used"] == 15 and s["answers_by_nemotron"] == 15
    assert s["candidates"] == ["DSRegoitia/CalendarSyncProject"]
    md = es.markdown(s, "logs/eval-nemotron.json")
    assert "| Verdict inside the human label's acceptable set | 1 of 2 |" in md
    assert "15 of 15" in md and "1 not judged" in md and "Selfdb-io/SelfDB" in md


def test_rows_outside_the_label_file_are_ignored():
    s = es.summarise({"results": [{"repo_id": "someone/else", "verdict": "ALREADY_EXISTS"}]}, LABEL_DATA)
    assert s["cases_in_report"] == 0 and s["judged"] == 0
