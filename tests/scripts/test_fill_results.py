"""fill_results writes numbers only from the report and the verified traces."""
from __future__ import annotations

import json
import shutil
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
    assert (
        "2 explicit nvidia-direct; 2 `LLM_CALL` lines with no provider field "
        "= Nightshift route inferred from absence of the marker; "
        "0 `LLM_SCORE` lines with no provider record."
    ) in values["provider-split"]
    assert f"nightshift: `{NEMO}` 2; nvidia-direct: `{NEMO}` 2" in values["provider-split"]
    assert "Nemotron in **2** of the **2** cases" in values["nemotron-answer-share"]
    assert f"`{NEMO}` (nvidia-direct)" in values["per-case-table"]
    assert "1 of the used answer was replayed from the LLM cache" in values["provider-split"]
    assert fr._cache_clause(2).startswith(" 2 of the used answers were replayed")
    assert fr._cache_clause(0) == ""
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


def _readme_slot(name: str) -> str:
    text = (fr.ROOT / "README.md").read_text(encoding="utf-8")
    begin = f"<!-- BEGIN numbers: {name} -->\n"
    end = f"\n<!-- END numbers: {name} -->"
    start = text.index(begin) + len(begin)
    return text[start:text.index(end, start)]


def test_provider_split_dry_run_matches_readme_on_committed_v3(tmp_path: Path, capsys):
    """Dry-run only. Do not pass --write; the README slot is the expected text."""

    labels = json.loads((fr.ROOT / "data" / "eval" / "gate1_labels.json").read_text(encoding="utf-8"))
    audit = {case["repo_id"] for case in labels["cases"] if "audit" in case}
    manifest = json.loads((fr.ROOT / "data" / "replays" / "manifest.json").read_text(encoding="utf-8"))
    logs = tmp_path / "logs"
    logs.mkdir()
    results = []
    for row in manifest["replays"]:
        if row.get("trace_class") != "RECORDED":
            continue
        record_path = fr.ROOT / "data" / "replays" / row["id"] / "record.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        run_id = str((record.get("recorded_run") or {}).get("run_id") or "")
        repo = record.get("repo_id") or row.get("repo_id")
        if repo not in audit or not run_id.startswith("judge-20261026"):
            continue
        shutil.copyfile(fr.ROOT / "data" / "replays" / row["id"] / "trace.jsonl", logs / f"trace-{run_id}.jsonl")
        verdict = record.get("verdict") or {}
        results.append({
            "repo_id": repo,
            "verdict": verdict.get("verdict") or "",
            "reason": verdict.get("reason") or "",
            "run_id": run_id,
        })
    assert len(results) == 16
    report = tmp_path / "eval-v3.json"
    report.write_text(json.dumps({"results": results}), encoding="utf-8")
    readme_before = (fr.ROOT / "README.md").read_text(encoding="utf-8")
    draft_before = (fr.ROOT / "docs" / "SUBMISSION_DRAFT.md").read_text(encoding="utf-8")
    assert fr.main([str(report), "--logs", str(logs)]) == 0
    assert (fr.ROOT / "README.md").read_text(encoding="utf-8") == readme_before
    assert (fr.ROOT / "docs" / "SUBMISSION_DRAFT.md").read_text(encoding="utf-8") == draft_before
    printed = capsys.readouterr().out.split("===== docs/SUBMISSION_DRAFT.md", 1)[0]
    body = printed.split("--- provider-split\n", 1)[1].split("\n--- ", 1)[0].strip("\n")
    assert body == _readme_slot("provider-split")


def test_no_traces_means_tbd(tmp_path: Path):
    report = {"results": [{"repo_id": "Selfdb-io/SelfDB", "verdict": "ALREADY_EXISTS", "reason": "x", "run_id": "nope"}]}
    values = fr.slots(fr.collect(report, LABELS, tmp_path), "r.json", readme=False)
    assert "**TBD**" in values["nemotron-answer-share"]
    assert "build-window-commit-count" in values and "calendarsync-final-verdict" not in values


def test_nemotron_evidence_counts_tiers_latency_and_failures(tmp_path: Path):
    logs = tmp_path / "logs"
    trace = HackathonTrace("ev", log_dir=logs)
    rows = [
        ({"tier": "fast", "elapsed_s": 10.0}, "ok"),
        ({"tier": "fast", "elapsed_s": 30.0}, "ok"),
        ({"tier": "fast"}, "error"),
        ({"tier": "deep", "elapsed_s": 90.0, "provider": "nvidia-direct", "routing": {"fallback_reason": "nightshift HTTP 502"}, "reasoning": "x" * 5000}, "ok"),
        ({"tier": "fast", "provider": "nvidia-direct", "routing": {"fallback_reason": "nightshift HTTP 502"}, "elapsed_s": 20.0}, "ok"),
        ({"tier": "fast", "cache_hit": True}, "ok"),
    ]
    for extra, status in rows:
        served = "" if status == "error" else NEMO
        trace.event(agent_name="orchestrator", action="LLM_CALL", input_summary={"repo_id": "x", "step": "discovery"},
                    output_summary={"requested_model": NEMO, "served_model": served, **extra}, status=status,
                    llm={"requested_model": NEMO, "served_model": served}, summary_limit=4000)
    trace.seal()
    report = {"results": [{"repo_id": "Selfdb-io/SelfDB", "verdict": "ALREADY_EXISTS", "reason": "x", "run_id": "ev"}]}
    out = fr.nemotron_evidence(fr.collect(report, LABELS, logs))
    assert f"| fast | `{NEMO}` 4 |" in out and f"| deep | `{NEMO}` 1 |" in out
    assert f"| `{NEMO}` | 4 | 25 | 90 |" in out
    assert "reasoning field or `<think>` block: **1** of **1**" in out
    assert "non-Nemotron answer: **2** (answered **2**, failed **0**, rejected **0**)" in out
    assert "the recorded reason on the answered ones: HTTP 502 2" in out
    assert "other Nightshift errors traced: **1**" in out


def test_per_case_table_counts_false_passes_by_verdict(tmp_path: Path):
    labels = {"cases": [
        {"repo_id": "a/one", "acceptable": ["ALREADY_EXISTS"], "audit": {"class": "LEGIT"}},
        {"repo_id": "b/two", "acceptable": ["ALREADY_EXISTS"], "audit": {"class": "LEGIT"}},
        {"repo_id": "c/three", "acceptable": ["ALREADY_EXISTS"], "audit": {"class": "LEGIT"}},
    ]}
    report = {"results": [
        {"repo_id": "a/one", "verdict": "CANDIDATE", "reason": "x", "run_id": "n1"},
        {"repo_id": "b/two", "verdict": "ALREADY_EXISTS", "reason": "x", "run_id": "n2"},
        {"repo_id": "c/three", "verdict": "UNJUDGED", "reason": "Nightshift 502", "run_id": "n3"},
    ]}
    table = fr.per_case_table(fr.collect(report, labels, tmp_path))
    assert "Of the 2 judged cases the label says to refuse: 1 `ALREADY_EXISTS`, 1 `CANDIDATE`." in table
