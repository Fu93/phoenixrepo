#!/usr/bin/env python3
"""Build data/replays from files already committed in this repository.

No Nightshift call, no network, no Docker. Hash chains written here are SAMPLE
seals: logs/ is gitignored and no historical trace jsonl is in git. The script
copies recorded rows and, for the gate-2 card, calls revive() so the blocked
state is the function's own return value.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.revive_loop import revive  # noqa: E402
from app.trace_log import HackathonTrace  # noqa: E402
from scripts.verify_trace import verify  # noqa: E402

OUT = ROOT / "data" / "replays"
INVOICE = "tharani-2006/Invoice-generator-application"
HYGEN = "Fu93/hygen"
MIRROR = "orangekame3/mirrormate"


def main() -> int:
    scores = json.loads((ROOT / "docs/revival/venture_scores.json").read_text(encoding="utf-8"))
    labels = json.loads((ROOT / "data/eval/gate1_labels.json").read_text(encoding="utf-8"))
    discovery = _csv_row(ROOT / "data/real/discovery_candidates.csv", INVOICE)
    prior = _csv_row(ROOT / "data/real/prior_art.csv", INVOICE)
    readme_line = _readme_rule()
    invoice_case = _case(labels, INVOICE)
    hygen_case = _case(labels, HYGEN)
    mirror_case = _case(labels, MIRROR)
    ineligible = next(row for row in scores["ineligible"] if row["repo_id"] == INVOICE)
    if ineligible["verdict"] != "ALREADY_EXISTS":
        raise SystemExit("venture_scores invoice verdict is not ALREADY_EXISTS")
    if prior["excerpt"] not in ineligible["reason"]:
        raise SystemExit("prior-art excerpt is not inside the recorded verdict reason")

    header = {
        "date": scores["date"],
        "run_id": scores["run_id"],
        "run_hash": scores["run_hash"],
        "command": scores["command"],
        "llm_used": scores["llm_used"],
        "source": "docs/revival/venture_scores.json",
    }
    quote_row = {
        "alternative_id": prior["alternative_id"],
        "url": prior["url"],
        "excerpt": prior["excerpt"],
        "stars": prior["stars"],
        "pushed_at": prior["pushed_at"],
        "days_since_push": prior["days_since_push"],
        "licence_spdx": prior["licence_spdx"],
        "source": "data/real/prior_art.csv",
    }
    blocker = invoice_case["audit"]["blocker"]
    audit_quote = next(item["quote"] for item in invoice_case["evidence"] if "InvoicePlane" in item.get("source_url", ""))
    audit_alt = {
        "alternative_id": blocker["alternative_id"],
        "url": blocker["url"],
        "quote": audit_quote,
        "stars": blocker["stars"],
        "forks": blocker.get("forks"),
        "pushed_at": blocker.get("pushed_at"),
        "overlap": blocker.get("overlap"),
        "source": "data/eval/gate1_labels.json",
        "note": (
            "Human audit blocker dated 2026-10-25. Not the alternative named in "
            "docs/revival/venture_scores.json. phoenix_overrefusal_audit.md is not in this repository."
        ),
    }
    rescue_inputs = {
        "stargazers_count": int(discovery["stargazers_count"]),
        "forks_count": int(discovery["forks_count"]),
        "open_issues": int(discovery["open_issues"]),
        "pushed_at": discovery["pushed_at"],
        "days_since_push": discovery["days_since_push"],
        "as_of": discovery["collected_at"][:10],
        "source": "data/real/discovery_candidates.csv",
    }
    recorded_run = {
        "run_id": scores["run_id"],
        "run_hash": scores["run_hash"],
        "date": scores["date"],
        "llm_used": scores["llm_used"],
        "source": "docs/revival/venture_scores.json",
        "jsonl_committed": False,
    }
    gate2_rule = {
        "state": None,
        "historical_trace": False,
        "rule_quote": readme_line,
        "rule_source": "README.md",
        "sample_replay_id": "invoice-gate2-sample",
        "why": (
            "No revive trace for this repository is committed (logs/ is gitignored and reports/ is not in git). "
            "README.md says a gate-1 verdict other than CANDIDATE ends as NOT_ATTEMPTED_GATE1 and nothing is cloned. "
            "The recorded verdict in docs/revival/venture_scores.json is ALREADY_EXISTS. "
            "invoice-gate2-sample is a SAMPLE call of revive() with that verdict, not a historical run."
        ),
    }
    invoice_verdict = {
        "verdict": ineligible["verdict"],
        "verdict_kind": "recorded_score",
        "reason": ineligible["reason"],
        "quote": prior["excerpt"],
        "quote_source_url": prior["url"],
        "quote_source_path": "data/real/prior_art.csv",
        "alternatives": [
            {
                "name": prior["alternative_id"],
                "url": prior["url"],
                "quote": prior["excerpt"],
                "origin": "data/real/prior_art.csv",
                "stars": prior["stars"],
                "pushed_at": prior["pushed_at"],
                "licence_spdx": prior["licence_spdx"],
            },
            {
                "name": blocker["alternative_id"],
                "url": blocker["url"],
                "quote": audit_quote,
                "origin": "data/eval/gate1_labels.json",
                "stars": blocker["stars"],
                "overlap": blocker.get("overlap"),
                "note": audit_alt["note"],
            },
        ],
        "acceptable": None,
        "gate2": gate2_rule,
    }
    _write_replay(
        "invoice-already-exists",
        title="Invoice generator — recorded ALREADY_EXISTS",
        repo_id=INVOICE,
        repo_url=discovery["html_url"],
        data_class="RECORDED",
        trace_class="SAMPLE",
        banner=(
            "SAMPLE hash chain. The rows are copies of committed files. "
            "This JSONL is not logs/trace-venture-019ca0071780.jsonl. "
            f"The recorded run hash {scores['run_hash']} cannot be recomputed from this file."
        ),
        summary="Deterministic score run venture-019ca0071780 refused the invoice generator. The chain is a SAMPLE seal over that record.",
        model_verdict_recorded=False,
        sources=[
            "docs/revival/venture_scores.json",
            "data/real/prior_art.csv",
            "data/real/discovery_candidates.csv",
            "data/eval/gate1_labels.json",
            "README.md",
        ],
        recorded_run=recorded_run,
        verdict=invoice_verdict,
        rescue_inputs=rescue_inputs,
        events=[
            ("RECORDED_RUN_HEADER", header),
            ("RECORDED_VERDICT", ineligible),
            ("RECORDED_QUOTE", quote_row),
            ("RECORDED_AUDIT_ALTERNATIVE", audit_alt),
            ("RESCUE_INPUTS", rescue_inputs),
        ],
    )

    hygen_evidence = next(item for item in hygen_case["evidence"] if item["role"] == "alternative")
    _write_human(
        "hygen-already-exists-quote",
        title="Hygen — human label ALREADY_EXISTS with a verbatim quote",
        case=hygen_case,
        evidence=hygen_evidence,
        summary="Human label in data/eval/gate1_labels.json. No model score for Fu93/hygen is in venture_scores.json.",
    )
    mirror_evidence = mirror_case["evidence"][0]
    _write_human(
        "mirrormate-human-label",
        title="Mirrormate — human label, not a model NEEDS_REVIEW verdict",
        case=mirror_case,
        evidence=mirror_evidence,
        summary=(
            "Human label says CANDIDATE or NEEDS_REVIEW would be acceptable. "
            "No committed file records a model verdict of NEEDS_REVIEW."
        ),
        verdict_value=None,
    )

    _write_gate2_sample(
        scores_reason=ineligible["reason"],
        repo_url=discovery["html_url"],
        quote=prior["excerpt"],
        quote_url=prior["url"],
        readme_line=readme_line,
        recorded_run=recorded_run,
    )

    manifest = {
        "replays": [
            _summary("invoice-already-exists"),
            _summary("hygen-already-exists-quote"),
            _summary("mirrormate-human-label"),
            _summary("invoice-gate2-sample"),
        ],
        "missing": [
            {
                "id": "needs-review-model-trace",
                "title": "No recorded NEEDS_REVIEW model verdict",
                "reason": (
                    "No committed JSON file contains a model verdict of NEEDS_REVIEW. "
                    "data/eval/gate1_labels.json is a human label file, and its note says it is not a live Nightshift score. "
                    "No trace jsonl is committed on any branch (logs/ is gitignored)."
                ),
            },
            {
                "id": "historical-gate2-trace",
                "title": "No historical NOT_ATTEMPTED_GATE1 trace",
                "reason": (
                    "No committed revive trace records NOT_ATTEMPTED_GATE1. "
                    "reports/ is not in git. invoice-gate2-sample is a SAMPLE produced by calling revive() locally."
                ),
            },
            {
                "id": "mismatch-and-source-error-trace",
                "title": "No committed MODEL_MISMATCH or SOURCE_ERROR trace",
                "reason": (
                    "The timeline shows MODEL_MISMATCH and SOURCE_ERROR when a trace line contains them. "
                    "No committed jsonl has those flags. The player does not invent a model name or a duration."
                ),
            },
        ],
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


def _write_human(
    replay_id: str,
    *,
    title: str,
    case: dict[str, Any],
    evidence: dict[str, Any],
    summary: str,
    verdict_value: str | None = "ALREADY_EXISTS",
) -> None:
    payload = {
        "repo_id": case["repo_id"],
        "acceptable": case["acceptable"],
        "forbidden": case.get("forbidden") or [],
        "why": case["why"],
        "evidence": [
            {
                "role": item.get("role"),
                "quote": item.get("quote"),
                "source_url": item.get("source_url"),
                "fetched_at": item.get("fetched_at"),
            }
            for item in case.get("evidence") or []
        ],
        "source": "data/eval/gate1_labels.json",
    }
    _write_replay(
        replay_id,
        title=title,
        repo_id=case["repo_id"],
        repo_url=f"https://github.com/{case['repo_id']}",
        data_class="HUMAN_LABEL",
        trace_class="SAMPLE",
        banner=(
            "SAMPLE hash chain over a human label copied from data/eval/gate1_labels.json. "
            "This is not a Nightshift trace and not a model verdict."
        ),
        summary=summary,
        model_verdict_recorded=False,
        sources=["data/eval/gate1_labels.json"],
        recorded_run=None,
        verdict={
            "verdict": verdict_value,
            "verdict_kind": "human_label",
            "reason": case["why"],
            "quote": evidence["quote"],
            "quote_source_url": evidence["source_url"],
            "quote_source_path": "data/eval/gate1_labels.json",
            "alternatives": [
                {
                    "name": item.get("role") or "evidence",
                    "url": item.get("source_url"),
                    "quote": item.get("quote"),
                    "origin": "data/eval/gate1_labels.json",
                }
                for item in case.get("evidence") or []
            ],
            "acceptable": case["acceptable"],
            "forbidden": case.get("forbidden") or [],
            "gate2": {
                "state": None,
                "historical_trace": False,
                "rule_quote": _readme_rule(),
                "rule_source": "README.md",
                "why": (
                    "No gate-2 trace is committed for this repository. "
                    "NOT_ATTEMPTED_GATE1 applies when a recorded gate-1 verdict is not CANDIDATE. "
                    "This card is a human label, so that state was not recorded here."
                ),
            },
        },
        rescue_inputs=None,
        events=[("RECORDED_HUMAN_LABEL", payload)],
    )


def _write_gate2_sample(
    *,
    scores_reason: str,
    repo_url: str,
    quote: str,
    quote_url: str,
    readme_line: str,
    recorded_run: dict[str, Any],
) -> None:
    replay_id = "invoice-gate2-sample"
    directory = OUT / replay_id
    directory.mkdir(parents=True, exist_ok=True)
    for stale in directory.glob("trace*.jsonl"):
        stale.unlink()
    trace = HackathonTrace(replay_id, log_dir=directory)
    result = revive(
        Path("."),
        None,
        trace,
        INVOICE,
        description="",
        gate_verdict="ALREADY_EXISTS",
        gate_reason=scores_reason,
    )
    trace.seal()
    written = directory / f"trace-{replay_id}.jsonl"
    (directory / "trace.jsonl").write_text(written.read_text(encoding="utf-8"), encoding="utf-8")
    written.unlink()
    ok, detail = verify(directory / "trace.jsonl")
    if not ok:
        raise SystemExit(detail)
    if result.state != "NOT_ATTEMPTED_GATE1":
        raise SystemExit(f"revive returned {result.state}")
    record = {
        "id": replay_id,
        "title": "Invoice generator — SAMPLE gate 2 blocked",
        "repo_id": INVOICE,
        "repo_url": repo_url,
        "data_class": "RECORDED",
        "trace_class": "SAMPLE",
        "banner": (
            "SAMPLE. This trace was produced by calling app.revive_loop.revive in scripts/build_replays.py "
            "with the recorded ALREADY_EXISTS verdict. Nothing was cloned. It is not a historical Nightshift run. "
            "No reports/ or logs/ revive trace for this repository is committed."
        ),
        "summary": "Local revive() short-circuit. State NOT_ATTEMPTED_GATE1 comes from the function, not from a past run.",
        "model_verdict_recorded": False,
        "sources": [
            "docs/revival/venture_scores.json",
            "data/real/prior_art.csv",
            "README.md",
            "app/revive_loop.py",
        ],
        "recorded_run": recorded_run,
        "rescue_inputs": None,
        "verdict": {
            "verdict": "ALREADY_EXISTS",
            "verdict_kind": "recorded_score",
            "reason": scores_reason,
            "quote": quote,
            "quote_source_url": quote_url,
            "quote_source_path": "data/real/prior_art.csv",
            "alternatives": [
                {
                    "name": "al1abb/invoify",
                    "url": quote_url,
                    "quote": quote,
                    "origin": "data/real/prior_art.csv",
                }
            ],
            "acceptable": None,
            "gate2": {
                "state": result.state,
                "terminal": result.terminal,
                "reason": result.reason,
                "historical_trace": False,
                "rule_quote": readme_line,
                "rule_source": "README.md",
                "why": (
                    "SAMPLE. revive() returned NOT_ATTEMPTED_GATE1 because the recorded gate-1 verdict is ALREADY_EXISTS. "
                    "The trace event is REVIVE_BLOCKED_BY_GATE1. No historical gate-2 trace is in the repository. "
                    "Nothing was cloned, installed, or patched."
                ),
            },
        },
    }
    _dump(directory / "record.json", record)


def _write_replay(
    replay_id: str,
    *,
    title: str,
    repo_id: str,
    repo_url: str,
    data_class: str,
    trace_class: str,
    banner: str,
    summary: str,
    model_verdict_recorded: bool,
    sources: list[str],
    recorded_run: dict[str, Any] | None,
    verdict: dict[str, Any],
    rescue_inputs: dict[str, Any] | None,
    events: list[tuple[str, dict[str, Any]]],
) -> None:
    directory = OUT / replay_id
    directory.mkdir(parents=True, exist_ok=True)
    for stale in directory.glob("trace*.jsonl"):
        stale.unlink()
    trace = HackathonTrace(replay_id, log_dir=directory)
    for action, payload in events:
        trace.event(
            agent_name="replay-packager",
            action=action,
            input_summary={"replay_id": replay_id, "source": "committed file copy"},
            output_summary=payload,
            status="RECORDED",
            summary_limit=100000,
        )
    trace.seal()
    written = directory / f"trace-{replay_id}.jsonl"
    target = directory / "trace.jsonl"
    target.write_text(written.read_text(encoding="utf-8"), encoding="utf-8")
    written.unlink()
    ok, detail = verify(target)
    if not ok:
        raise SystemExit(f"{replay_id}: {detail}")
    _dump(
        directory / "record.json",
        {
            "id": replay_id,
            "title": title,
            "repo_id": repo_id,
            "repo_url": repo_url,
            "data_class": data_class,
            "trace_class": trace_class,
            "banner": banner,
            "summary": summary,
            "model_verdict_recorded": model_verdict_recorded,
            "sources": sources,
            "recorded_run": recorded_run,
            "rescue_inputs": rescue_inputs,
            "verdict": verdict,
        },
    )


def _summary(replay_id: str) -> dict[str, Any]:
    record = json.loads((OUT / replay_id / "record.json").read_text(encoding="utf-8"))
    return {
        "id": record["id"],
        "title": record["title"],
        "repo_id": record["repo_id"],
        "data_class": record["data_class"],
        "trace_class": record["trace_class"],
        "summary": record["summary"],
    }


def _csv_row(path: Path, repo_id: str) -> dict[str, str]:
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["repo_id"] == repo_id:
                return row
    raise SystemExit(f"{repo_id} not in {path}")


def _case(labels: dict[str, Any], repo_id: str) -> dict[str, Any]:
    return next(case for case in labels["cases"] if case["repo_id"] == repo_id)


def _readme_rule() -> str:
    for line in (ROOT / "README.md").read_text(encoding="utf-8").splitlines():
        if "NOT_ATTEMPTED_GATE1" in line:
            return line
    raise SystemExit("README has no NOT_ATTEMPTED_GATE1 line")


def _dump(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
