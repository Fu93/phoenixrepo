"""fill_results writes numbers only from the report and the verified traces."""
from __future__ import annotations

from pathlib import Path

from app.trace_log import HackathonTrace
from scripts import fill_results as fr

NEMO = "nvidia/nemotron-3-ultra-550b-a55b"
LABELS = {"cases": [
    {"repo_id": "DSRegoitia/CalendarSyncProject", "acceptable": ["CANDIDATE", "NEEDS_REVIEW"], "audit": {"class": "WRONG"}},
    {"repo_id": "Selfdb-io/SelfDB", "acceptable": ["ALREADY_EXISTS"], "audit": {"class": "LEGIT"}},
    {"repo_id": "ri-char/pwatch", "acceptable": ["CANDIDATE", "NEEDS_REVIEW"], "audit": {"class": "WEAK"}},
]}


def _trace(logs: Path, run_id: str, providers: list[str], cached: int = 0) -> str:
    trace = HackathonTrace(run_id, log_dir=logs)
    for i, provider in enumerate(providers):
        summary = {"requested_model": NEMO, "served_model": NEMO}
        if provider != "nightshift":
            summary["provider"] = provider
        if i < cached:
            summary["cache_hit"] = True
        trace.event(agent_name="orchestrator", action="LLM_CALL",
                    input_summary={"repo_id": "x", "step": "discovery" if i == len(providers) - 1 else "problem"},
                    output_summary=summary, llm={"requested_model": NEMO, "served_model": NEMO})
    return trace.seal()


def test_fill_writes_only_verified_numbers(tmp_path: Path):
    logs = tmp_path / "logs"
    h1 = _trace(logs, "r1", ["nightshift", "nvidia-direct", "nvidia-direct"], cached=1)
    _trace(logs, "r2", ["nightshift"])
    report = {"results": [
        {"repo_id": "DSRegoitia/CalendarSyncProject", "verdict": "CANDIDATE", "reason": "nothing blocks", "run_id": "r1",
         "answers_used": 3, "answers_by_nemotron": 3, "served_models": {NEMO: 3}},
        {"repo_id": "Selfdb-io/SelfDB", "verdict": "ALREADY_EXISTS", "reason": "para | full", "run_id": "r2",
         "answers_used": 1, "answers_by_nemotron": 1, "served_models": {NEMO: 1}},
        {"repo_id": "ri-char/pwatch", "verdict": "UNJUDGED", "reason": "Nightshift 502", "run_id": "missing"},
    ]}
    data = fr.collect(report, LABELS, logs)
    values = fr.slots(data, "logs/eval.json", readme=True)
    assert "**4** of **4** answers used (2 of 3 case traces verified" in values["nemotron-answer-share"]
    assert "nightshift **2**" in values["provider-split"] and "nvidia-direct **2**" in values["provider-split"]
    assert "Nemotron in **2** of the **2** cases" in values["nemotron-answer-share"]
    assert f"`{NEMO}` (nvidia-direct)" in values["per-case-table"]
    assert "1 of the used answers were replayed from the LLM cache" in values["provider-split"]
    assert "**CANDIDATE** (agrees with the human label)" in values["calendarsync-final-verdict"]
    assert h1 in values["calendarsync-final-run-hash"] and "--require-nemotron" in values["calendarsync-final-run-hash"]
    table = values["per-case-table"]
    assert "| `ri-char/pwatch` |" in table and "not judged" in table and "trace not found" in table
    assert "para \\| full" in table
    doc = ("x\n<!-- BEGIN numbers: provider-split -->\n**TBD**\n<!-- END numbers: provider-split -->\n"
           "<!-- BEGIN numbers: zetaris-gate1-run -->\nkeep\n<!-- END numbers: zetaris-gate1-run -->\n")
    out, done = fr.apply(doc, values)
    assert done == ["provider-split"] and "keep" in out and "**TBD**" not in out
    again, _ = fr.apply(out, values)
    assert again == out


def test_no_traces_means_tbd(tmp_path: Path):
    report = {"results": [{"repo_id": "Selfdb-io/SelfDB", "verdict": "ALREADY_EXISTS", "reason": "x", "run_id": "nope"}]}
    values = fr.slots(fr.collect(report, LABELS, tmp_path), "r.json", readme=False)
    assert "**TBD**" in values["nemotron-answer-share"]
    assert "build-window-commit-count" in values and "calendarsync-final-verdict" not in values
