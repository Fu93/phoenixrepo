"""Read-only audit of v3 disclosures and an optional separate eval16 snapshot.

No settings, environment files, model client, cache migration, or network calls.
Cache entries can be inspected only from an explicitly supplied copy. A timestamp
or a similar answer is never enough to link a cache entry to a scored call.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.rerun_unjudged import _verdict_of, is_infra_review
from scripts.verify_nemotron_evidence import audit_figures, load_scored, recorded_field
from scripts.verify_trace import nemotron_report, verify


def obj(value: Any) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def inferred_override(verdict: str, reason: str) -> bool:
    return verdict == "CANDIDATE" and bool(re.match(r"^must beat .+: did not block", reason))


def cache_link(call: dict, entries: list[dict]) -> dict:
    """Historical response lookup only; never claim compatibility with today's key."""
    prompt_hash, _ = recorded_field(call, "prompt_sha256")
    input_hash, _ = recorded_field(call, "input_sha256")
    requested, _ = recorded_field(call, "requested_model")
    served, _ = recorded_field(call, "served_model")
    if not all((prompt_hash, input_hash, requested, served)):
        return {"status": "not_linkable", "reason": "scored call lacks prompt/input identity; no client call attempted"}
    matches = [entry for entry in entries if (
        entry.get("prompt_sha256") == prompt_hash and entry.get("input_sha256") == input_hash
        and entry.get("requested_model") == requested and entry.get("served_model") == served
    )]
    if len(matches) != 1:
        return {"status": "miss" if not matches else "ambiguous", "reason": "no unique exact historical response"}
    # This is not an API retry or a migrated cache hit. Keep raw text out of the report.
    from app.agents.relevance import extract_json_object

    raw = extract_json_object(str(matches[0].get("response_text") or "")) or {}
    return {"status": "linked_response", "raw_verdict": raw.get("verdict"), "cache_sha256": matches[0]["_sha256"]}


def v3_audit(root: Path, cache_copy: Path | None = None) -> dict:
    scored, problems = load_scored(root)
    if problems:
        raise ValueError("; ".join(problems))
    labels = json.loads((root / "data/eval/gate1_labels.json").read_text(encoding="utf-8"))
    label_by_id = {case["repo_id"]: case for case in labels["cases"]}
    entries = []
    if cache_copy is not None:
        for path in sorted(cache_copy.rglob("*.json")):
            raw = path.read_bytes()
            entry = obj(raw.decode("utf-8"))
            if "response_text" in entry:
                entries.append({**entry, "_sha256": hashlib.sha256(raw).hexdigest()})
    rows = []
    for item in scored:
        before = item.trace.read_bytes()
        ok, detail = verify(item.trace, expected_run_hash=item.run_hash)
        if not ok:
            raise ValueError(f"{item.repo_id}: {detail}")
        records = [json.loads(line) for line in before.decode("utf-8").splitlines() if line.strip()]
        finals = [row for row in records if row.get("action") == "DISCOVERY_FILTER"]
        if len(finals) != 1:
            raise ValueError(f"{item.repo_id}: expected one final decision")
        final = obj(finals[0].get("output_summary"))
        saved = item.record["verdict"]
        if final.get("verdict") != saved.get("verdict"):
            raise ValueError(f"{item.repo_id}: record/trace verdict mismatch")
        verdict = str(final.get("verdict") or "")
        reason = str(final.get("reason") or "")
        calls = [row for row in records if row.get("action") in {"LLM_CALL", "LLM_SCORE"}]
        discovery = [row for row in calls if obj(row.get("input_summary")).get("step") == "discovery" and row.get("status") not in {"error", "rejected"}]
        linked = cache_link(discovery[-1], entries) if discovery and cache_copy else {"status": "not_attempted"}
        if item.trace.read_bytes() != before:
            raise ValueError(f"{item.repo_id}: trace changed during audit")
        rows.append({
            "repo_id": item.repo_id, "verdict": verdict,
            "acceptable": label_by_id[item.repo_id]["acceptable"],
            "false_pass": verdict == "CANDIDATE" and set(label_by_id[item.repo_id]["acceptable"]) == {"ALREADY_EXISTS"},
            "override_inferred_from_reason": inferred_override(verdict, reason),
            "raw_verdict_recorded": any(row.get("action") == "GATE_DECISION" for row in records),
            "reason": reason, "cache_check": linked,
            "request_settings_lines": sum(bool(obj(row.get("request_settings")) or obj(obj(row.get("output_summary")).get("request_settings"))) for row in calls),
            "error_or_rejected_calls": sum(row.get("status") in {"error", "rejected"} for row in calls),
            "trace_sha256": hashlib.sha256(before).hexdigest(), "run_hash": item.run_hash,
        })
    return {
        "data_class": "LIVE_EVAL_V3_READ_ONLY_AUDIT", "claim_scope": "16-case regression audit; not general accuracy",
        "figures": audit_figures(scored, labels), "candidate_count": sum(row["verdict"] == "CANDIDATE" for row in rows),
        "inferred_override_candidates": sum(row["override_inferred_from_reason"] for row in rows),
        "false_pass_override_evidence": "reason-format inference unless a unique historical response is explicitly linked",
        "cache_entries_inspected": len(entries), "network_calls": 0, "model_calls": 0, "cases": rows,
    }


def eval16_snapshot(directory: Path, cases: list[dict]) -> dict:
    """Only stable, sealed, matching Zetaris artifacts can enter an independent result."""
    rows = []
    for case in cases:
        repo = case["repo_id"]
        slug = re.sub(r"[^a-z0-9]+", "-", repo.lower()).strip("-")
        score_path = directory / f"scores-{slug}.json"
        trace_path = directory / f"trace-zetaris-eval16-{slug}.jsonl"
        row = {"repo_id": repo, "status": "pending"}
        rows.append(row)
        if not score_path.is_file() or not trace_path.is_file():
            continue
        try:
            raw_score, raw_trace = score_path.read_bytes(), trace_path.read_bytes()
            score = json.loads(raw_score)
            run_hash = str(score.get("run_hash") or "")
            if not run_hash:
                raise ValueError("scores have no pinned run hash")
            ok, detail = verify(trace_path, expected_run_hash=run_hash)
            if not ok:
                raise ValueError(detail)
            nemotron = nemotron_report(trace_path)
            if nemotron["not_nemotron"] or not nemotron["answers_used"]:
                raise ValueError("Nemotron provenance check did not pass")
            records = [json.loads(line) for line in raw_trace.decode("utf-8").splitlines() if line.strip()]
            modes = {obj(record.get("output_summary")).get("evidence_source") for record in records if record.get("action") == "evidence_source"}
            if modes != {"zetaris"}:
                raise ValueError("trace does not record exclusively Zetaris evidence")
            queries = [record for record in records if record.get("action") == "zetaris_query"]
            tables = set()
            for query in queries:
                payload = obj(query.get("output_summary"))
                match = re.search(r"\bFROM\s+([\w.]+)", str(payload.get("sql") or ""), re.IGNORECASE)
                if not match or not match[1].lower().startswith("phoenix_eval16."):
                    raise ValueError("query is not scoped to phoenix_eval16")
                if query.get("status") == "ok" and payload.get("query_id") and payload.get("content_hash") and not payload.get("error"):
                    tables.add(match[1].lower())
            required = {f"phoenix_eval16.{name}" for name in ("discovery_candidates", "prior_art", "demand_signals")}
            if not required.issubset(tables):
                raise ValueError("missing successful eval16 evidence queries")
            verdict, reason = _verdict_of(score, repo)
            finals = [obj(record.get("output_summary")) for record in records if record.get("action") == "DISCOVERY_FILTER"]
            if len(finals) != 1 or finals[0].get("verdict") != verdict:
                raise ValueError("scores/final trace decision mismatch")
            if score_path.read_bytes() != raw_score or trace_path.read_bytes() != raw_trace:
                raise ValueError("run artifacts changed during snapshot")
            judged = bool(verdict) and not is_infra_review({"verdict": verdict, "reason": reason})
            row.update(status="verified", verdict=verdict, judged=judged,
                       agreed=judged and verdict in case["acceptable"], run_hash=run_hash,
                       acceptable=case["acceptable"],
                       scores_sha256=hashlib.sha256(raw_score).hexdigest(), trace_sha256=hashlib.sha256(raw_trace).hexdigest(),
                       query_tables=sorted(tables), query_count=len(queries),
                       model_mismatch_count_recorded=score.get("model_mismatch_count"),
                       llm_fallback_events=sum(record.get("action") == "LLM_FALLBACK" for record in records),
                       failed_model_calls=sum(record.get("action") in {"LLM_CALL", "LLM_SCORE"} and record.get("status") == "error" for record in records),
                       answers_used=nemotron["answers_used"], answers_by_nemotron=nemotron["answers_by_nemotron"])
        except (OSError, ValueError, KeyError) as exc:
            row.update(status="unverified", detail=str(exc))
    complete = bool(rows) and all(row["status"] == "verified" for row in rows)
    result = {"data_class": "POST_V3_ZETARIS_EVAL16", "status": "complete" if complete else "incomplete",
              "claim_scope": "same repositories with freshly collected evidence; separate from frozen v3; not held-out validation",
              "verified_runs": sum(row["status"] == "verified" for row in rows), "expected_runs": len(rows), "cases": rows}
    if complete:
        result["summary"] = {"judged": sum(row["judged"] for row in rows), "agreed": sum(row["agreed"] for row in rows)}
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--cache-copy", type=Path)
    parser.add_argument("--eval16-dir", type=Path)
    args = parser.parse_args(argv)
    report = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "v3": v3_audit(args.root, args.cache_copy)}
    if args.eval16_dir:
        labels = json.loads((args.root / "data/eval/gate1_labels.json").read_text(encoding="utf-8"))
        report["eval16"] = eval16_snapshot(args.eval16_dir, [case for case in labels["cases"] if case.get("audit")])
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
