#!/usr/bin/env python3
"""Write the scored numbers into the judge-facing docs from one eval report and its traces.

    python scripts/fill_results.py logs/eval-nemotron-v3.json            # print what would be written
    python scripts/fill_results.py logs/eval-nemotron-v3.json --write    # write README.md and the draft

Every number comes from the report written by scripts/eval_gate1.py and from the trace file
of each judged case (logs/trace-<run_id>.jsonl), checked with scripts/verify_trace.py. A slot
is the text between ``<!-- BEGIN numbers: NAME -->`` and ``<!-- END numbers: NAME -->``; the
markers stay, so the command can be run again on the final commit. A number the files do not
hold is written as TBD, never estimated. The Zetaris slot is not touched: it comes from a
different command.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.eval_gate1 import LABELS  # noqa: E402
from scripts.eval_summary import markdown, summarise  # noqa: E402
from scripts.rerun_unjudged import is_infra_review  # noqa: E402
from scripts.verify_trace import nemotron_report, verify  # noqa: E402

PROTAGONIST = "DSRegoitia/CalendarSyncProject"
SKELETON = "62a1540"
TARGETS = (ROOT / "README.md", ROOT / "docs" / "SUBMISSION_DRAFT.md")


def _cell(text: Any, limit: int = 110) -> str:
    one = " ".join(str(text or "").split()).replace("|", "\\|")
    return one if len(one) <= limit else one[: limit - 1].rstrip() + "…"


def _served_by_provider(path: Path) -> dict[str, dict[str, int]]:
    """provider -> served model -> used answers, from the trace lines themselves."""
    out: dict[str, dict[str, int]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if "served_model" not in record or str(record.get("status") or "") in {"error", "rejected"}:
            continue
        provider = "nvidia-direct" if '"provider": "nvidia-direct"' in str(record.get("output_summary") or "") else "nightshift"
        model = str(record.get("served_model") or "(missing)")
        out.setdefault(provider, {})[model] = out.setdefault(provider, {}).get(model, 0) + 1
    return out


def _cache_hits(path: Path) -> int:
    hits = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if "served_model" in record and str(record.get("status") or "") not in {"error", "rejected"}:
            if '"cache_hit": true' in str(record.get("output_summary") or ""):
                hits += 1
    return hits


def _verdict_answer(path: Path) -> dict[str, str] | None:
    """The used answer of the last discovery (final-verdict) call in the trace."""
    found = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if "served_model" not in record or str(record.get("status") or "") in {"error", "rejected"}:
            continue
        inp = record.get("input_summary")
        if isinstance(inp, str):
            try:
                inp = json.loads(inp)
            except json.JSONDecodeError:
                inp = {"step": "discovery"} if '"step": "discovery"' in inp else {}
        if isinstance(inp, dict) and inp.get("step") == "discovery":
            out = str(record.get("output_summary") or "")
            found = {"served_model": str(record.get("served_model") or ""),
                     "provider": "nvidia-direct" if '"provider": "nvidia-direct"' in out else "nightshift"}
    return found


def trace_facts(run_id: str | None, logs: Path) -> dict[str, Any]:
    if not run_id:
        return {"found": False}
    path = logs / f"trace-{run_id}.jsonl"
    if not path.exists():
        return {"found": False, "path": path}
    ok, detail = verify(path)
    facts: dict[str, Any] = {"found": True, "path": path, "chain_ok": ok}
    if ok:
        facts["run_hash"] = detail
        facts.update(nemotron_report(path))
        facts["cache_hits"] = _cache_hits(path)
        facts["verdict_answer"] = _verdict_answer(path)
        facts["served_by_provider"] = _served_by_provider(path)
    else:
        facts["chain_error"] = detail
    return facts


def collect(report: dict[str, Any], labels: dict[str, Any], logs: Path) -> dict[str, Any]:
    cases = {case["repo_id"]: case for case in labels.get("cases") or []}
    rows = [r for r in report.get("results") or [] if isinstance(r, dict) and r.get("repo_id") in cases]
    per_case = []
    totals = {"traces": 0, "chain_ok": 0, "answers_used": 0, "answers_by_nemotron": 0, "cache_hits": 0,
              "providers": {}, "not_nemotron": [], "verdicts": 0, "verdicts_by_nemotron": 0, "served_by_provider": {}}
    for row in rows:
        facts = trace_facts(row.get("run_id"), logs)
        if facts.get("found"):
            totals["traces"] += 1
        if facts.get("chain_ok"):
            totals["chain_ok"] += 1
            totals["answers_used"] += facts["answers_used"]
            totals["answers_by_nemotron"] += facts["answers_by_nemotron"]
            totals["cache_hits"] += facts["cache_hits"]
            for name, count in facts["providers"].items():
                totals["providers"][name] = totals["providers"].get(name, 0) + count
            totals["not_nemotron"] += [f"{row['repo_id']} {item}" for item in facts["not_nemotron"]]
            for prov, models in facts["served_by_provider"].items():
                bucket = totals["served_by_provider"].setdefault(prov, {})
                for model, count in models.items():
                    bucket[model] = bucket.get(model, 0) + count
            answer = facts.get("verdict_answer")
            if answer:
                totals["verdicts"] += 1
                totals["verdicts_by_nemotron"] += int(answer["served_model"].startswith("nvidia/nemotron-"))
        infra = not row.get("verdict") or is_infra_review({"verdict": row.get("verdict"), "reason": row.get("reason") or ""})
        acceptable = cases[row["repo_id"]].get("acceptable") or []
        per_case.append({
            "repo_id": row["repo_id"],
            "acceptable": acceptable,
            "verdict": row.get("verdict") or "",
            "outcome": "not judged" if infra else ("agrees" if row.get("verdict") in acceptable else "disagrees"),
            "reason": row.get("reason") or row.get("error") or "",
            "run_id": row.get("run_id") or "",
            "trace": facts,
        })
    return {
        "summary": summarise(report, labels),
        "per_case": per_case,
        "totals": totals,
        "rows": len(rows),
        "protagonist": next((c for c in per_case if c["repo_id"] == PROTAGONIST), None),
    }


def _commit_count() -> str:
    try:
        out = subprocess.run(["git", "rev-list", "--count", f"{SKELETON}..HEAD"], cwd=ROOT,
                             capture_output=True, text=True, check=True, timeout=30)
        return out.stdout.strip() or "TBD"
    except (OSError, subprocess.SubprocessError):
        return "TBD"


def _summary_fields(record: dict[str, Any]) -> dict[str, Any]:
    raw = record.get("output_summary")
    if isinstance(raw, dict):
        return raw
    text = str(raw or "")
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    # A long reasoning excerpt can truncate the JSON string; read the short keys that precede it.
    fields: dict[str, Any] = {}
    for key in ("provider", "tier"):
        m = re.search(r'"' + key + r'": "([^"]*)"', text)
        if m:
            fields[key] = m.group(1)
    m = re.search(r'"elapsed_s": ([0-9.]+)', text)
    if m:
        fields["elapsed_s"] = float(m.group(1))
    if '"cache_hit": true' in text:
        fields["cache_hit"] = True
    if '"reasoning": "' in text:
        fields["reasoning"] = "(truncated)"
    m = re.search(r'"fallback_reason": "([^"]*)"', text)
    if m:
        fields["routing"] = {"fallback_reason": m.group(1)}
    return fields


def _reason_label(reason: str) -> str:
    """'nightshift HTTP 502' -> 'HTTP 502'; 'nightshift served x' -> 'non-Nemotron answer'."""
    r = reason.strip()
    if r.startswith("nightshift served"):
        return "non-Nemotron answer"
    return r.replace("nightshift ", "", 1) or "unknown"


def nemotron_evidence(data: dict[str, Any]) -> str:
    """Per-tier use, latency per served model and provider failures, from the verified traces."""
    tiers: dict[str, dict[str, int]] = {}
    reasoning_kept = deep_used = 0
    seconds: dict[str, list[float]] = {}
    nightshift_error = nightshift_rejected = direct_used = direct_failed = direct_rejected = 0
    reasons: dict[str, int] = {}
    for case in data["per_case"]:
        t = case["trace"]
        if not t.get("chain_ok"):
            continue
        for line in Path(t["path"]).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if "served_model" not in record:
                continue
            fields = _summary_fields(record)
            provider = str(fields.get("provider") or "nightshift")
            status = str(record.get("status") or "")
            if status == "error":
                if provider == "nightshift":
                    nightshift_error += 1
                else:
                    direct_failed += 1
                continue
            if status == "rejected":
                if provider == "nightshift":
                    nightshift_rejected += 1
                else:
                    direct_rejected += 1
                continue
            model = str(record.get("served_model") or "(missing)")
            tier = str(fields.get("tier") or "no tier (scoring calls outside the two-tier client)")
            tiers.setdefault(tier, {})[model] = tiers.setdefault(tier, {}).get(model, 0) + 1
            if tier == "deep":
                deep_used += 1
                reasoning_kept += int(bool(fields.get("reasoning")))
            if provider == "nvidia-direct":
                direct_used += 1
                routing = fields.get("routing") if isinstance(fields.get("routing"), dict) else {}
                label = _reason_label(str(routing.get("fallback_reason") or ""))
                reasons[label] = reasons.get(label, 0) + 1
            if not fields.get("cache_hit") and isinstance(fields.get("elapsed_s"), (int, float)):
                seconds.setdefault(model, []).append(float(fields["elapsed_s"]))
    if not tiers:
        return "**TBD** (no verified case trace)."
    lines = ["| Tier | Answers used, by served model |", "| --- | --- |"]
    for tier in sorted(tiers):
        lines.append(f"| {tier} | " + ", ".join(f"`{m}` {c}" for m, c in sorted(tiers[tier].items())) + " |")
    lines += ["", "| Served model | Live answers | Median seconds | Max seconds |", "| --- | --- | --- | --- |"]
    for model, values in sorted(seconds.items()):
        ordered = sorted(values)
        mid = ordered[len(ordered) // 2] if len(ordered) % 2 else (ordered[len(ordered) // 2 - 1] + ordered[len(ordered) // 2]) / 2
        lines.append(f"| `{model}` | {len(ordered)} | {mid:.0f} | {ordered[-1]:.0f} |")
    lost = sum(1 for c in data["per_case"] if c["outcome"] == "not judged")
    lines += [
        "",
        f"Final-verdict (deep tier) answers that carried a separate reasoning field or `<think>` block: **{reasoning_kept}** of **{deep_used}**. "
        f"Calls sent to NVIDIA's API after Nightshift failed or served a non-Nemotron answer: **{direct_used + direct_failed + direct_rejected}** "
        f"(answered **{direct_used}**, failed **{direct_failed}**, rejected **{direct_rejected}**)"
        + (f"; the recorded reason on the answered ones: {', '.join(f'{k} {v}' for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]))}" if reasons else "")
        + f". Nightshift answers discarded as non-Nemotron: **{nightshift_rejected}**; other Nightshift errors traced: **{nightshift_error}**. "
        "A Nightshift failure that was retried on Nightshift itself writes no trace line, so the failure counts are lower bounds. "
        f"Cases left not judged on this run: **{lost}** of **{len(data['per_case'])}**. Cached answers are counted as used but not timed.",
    ]
    return "\n".join(lines)


def _committed_trace_for_hash(run_hash: str) -> str | None:
    """Replay path whose record.json stores this run hash, when that trace is committed."""

    root = ROOT / "data" / "replays"
    manifest = root / "manifest.json"
    if not manifest.is_file():
        return None
    try:
        catalog = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    for row in catalog.get("replays") or []:
        if row.get("trace_class") != "RECORDED":
            continue
        replay_id = str(row.get("id") or "")
        record_path = root / replay_id / "record.json"
        trace_path = root / replay_id / "trace.jsonl"
        if not replay_id or not record_path.is_file() or not trace_path.is_file():
            continue
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        recorded = record.get("recorded_run") or {}
        if recorded.get("run_hash") == run_hash:
            return f"data/replays/{replay_id}/trace.jsonl"
    return None


def per_case_table(data: dict[str, Any]) -> str:
    lines = [
        "| Repository | Human label | Verdict | Outcome | Verdict answer by | Nemotron answers used | Providers | Chain | Reason (shortened) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for case in data["per_case"]:
        t = case["trace"]
        answer = t.get("verdict_answer") if t.get("chain_ok") else None
        by = f"`{answer['served_model']}` ({answer['provider']})" if answer else "none"
        if t.get("chain_ok"):
            nemo = f"{t['answers_by_nemotron']} of {t['answers_used']}"
            prov = ", ".join(f"{k} {v}" for k, v in t["providers"].items()) or "none"
            chain = f"ok `{t['run_hash'][:12]}`"
        elif t.get("found"):
            nemo, prov, chain = "TBD", "TBD", "FAILED"
        else:
            nemo, prov, chain = "TBD", "TBD", "trace not found"
        lines.append(
            f"| `{case['repo_id']}` | {' or '.join(case['acceptable'])} | {case['verdict'] or 'none'} | {case['outcome']} "
            f"| {by} | {nemo} | {prov} | {chain} | {_cell(case['reason'])} |"
        )
    refuse = [c for c in data["per_case"] if set(c["acceptable"]) == {"ALREADY_EXISTS"} and c["outcome"] != "not judged"]
    if refuse:
        tally: dict[str, int] = {}
        for c in refuse:
            tally[c["verdict"] or "none"] = tally.get(c["verdict"] or "none", 0) + 1
        parts = ", ".join(f"{n} `{v}`" for v, n in sorted(tally.items(), key=lambda kv: (-kv[1], kv[0])))
        lines.append("")
        lines.append(f"Of the {len(refuse)} judged cases the label says to refuse: {parts}. A `CANDIDATE` among them is a false pass that gate 2 would act on.")
    lines.append("")
    lines.append(
        "Each row was written as `logs/trace-<run_id>.jsonl` (`logs/` is gitignored). "
        "A committed copy, when one is in `data/replays/`, is the file to check. "
        "The chain column is `scripts/verify_trace.py` on that file. "
        "`(missing)` in a served-model count is a call that failed or was discarded before any answer, not an answer used."
    )
    return "\n".join(lines)


def slots(data: dict[str, Any], source: str, readme: bool) -> dict[str, str]:
    t = data["totals"]
    out: dict[str, str] = {}
    out["eval-summary-table"] = markdown(data["summary"], source).rstrip()
    if readme:
        out["per-case-table"] = per_case_table(data)
        out["nemotron-evidence"] = nemotron_evidence(data)
    traced = f"{t['chain_ok']} of {data['rows']} case traces verified"
    if t["chain_ok"]:
        share = f"**{t['answers_by_nemotron']}** of **{t['answers_used']}** answers used ({traced}, `{source}`)."
        if t["verdicts"]:
            share += (f" The final-verdict answer (the `discovery` step) was written by Nemotron in **{t['verdicts_by_nemotron']}** "
                      f"of the **{t['verdicts']}** cases whose trace has one.")
        if t["not_nemotron"]:
            share += " Not Nemotron: " + "; ".join(t["not_nemotron"]) + "."
        split = ", ".join(f"{name} **{count}**" for name, count in sorted(t["providers"].items()))
        cached = (
            f" {t['cache_hits']} of the used answers were replayed from the LLM cache: an earlier run asked the same "
            "prompt with the same input, and the cached line keeps the model and provider that answered then."
            if t["cache_hits"] else ""
        )
        out["provider-split"] = (
            f"Provider split on that run, from `verify_trace.py --require-nemotron` over each case trace: {split}.{cached} "
            "Served model per provider: " + "; ".join(
                f"{prov}: " + ", ".join(f"`{model}` {count}" for model, count in sorted(models.items()))
                for prov, models in sorted(t["served_by_provider"].items())
            ) + "."
        )
    else:
        share = f"**TBD** ({traced})."
        out["provider-split"] = "Provider split on that run: **TBD** (no verified case trace)."
    out["nemotron-answer-share"] = "Answers by NVIDIA Nemotron on the scored run: " + share
    hero = data["protagonist"]
    if hero and hero["verdict"]:
        out["calendarsync-final-verdict"] = (
            f"Final verdict on the recorded run (`{hero['run_id']}`): **{hero['verdict']}** ({hero['outcome']} with the human label). "
            f"Reason: {_cell(hero['reason'], 300)}"
        )
        if hero["trace"].get("chain_ok"):
            h = hero["trace"]["run_hash"]
            committed = _committed_trace_for_hash(h)
            if committed:
                out["calendarsync-final-run-hash"] = (
                    f"Final run hash: `{h}`. The committed copy is `{committed}`. Check it with "
                    f"`python scripts/verify_trace.py {committed} --expected-run-hash {h} --require-nemotron`. "
                    f"`logs/trace-{hero['run_id']}.jsonl` is gitignored and is not in this checkout."
                )
            else:
                out["calendarsync-final-run-hash"] = (
                    f"Final run hash: `{h}`. Check it with "
                    f"`python scripts/verify_trace.py logs/trace-{hero['run_id']}.jsonl --expected-run-hash {h} --require-nemotron`."
                )
        else:
            out["calendarsync-final-run-hash"] = "Final run hash: **TBD** (trace not found or not verified)."
    if not readme:
        out["build-window-commit-count"] = (
            f"Commit count after the skeleton, on the commit these numbers were written from: **{_commit_count()}** "
            f"(`git rev-list --count {SKELETON}..HEAD`). Run `scripts/fill_results.py` again on the final commit."
        )
    return out


def apply(text: str, values: dict[str, str]) -> tuple[str, list[str]]:
    done = []
    for name, body in values.items():
        pattern = re.compile(
            r"(<!-- BEGIN numbers: " + re.escape(name) + r" -->\n)(.*?)(\n<!-- END numbers: " + re.escape(name) + r" -->)", re.S
        )
        text, n = pattern.subn(lambda m: m.group(1) + body + m.group(3), text)
        if n:
            done.append(name)
    return text, done


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("report", type=Path)
    ap.add_argument("--labels", type=Path, default=LABELS)
    ap.add_argument("--logs", type=Path, default=ROOT / "logs")
    ap.add_argument("--write", action="store_true", help="Write README.md and docs/SUBMISSION_DRAFT.md.")
    args = ap.parse_args(argv)
    report = json.loads(args.report.read_text(encoding="utf-8"))
    labels = json.loads(args.labels.read_text(encoding="utf-8"))
    data = collect(report, labels, args.logs)
    try:
        source = args.report.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        source = args.report.as_posix()
    for target in TARGETS:
        values = slots(data, source, readme=target.name == "README.md")
        if args.write:
            text, done = apply(target.read_text(encoding="utf-8"), values)
            with open(target, "w", encoding="utf-8", newline="\n") as handle:  # LF on Windows too
                handle.write(text)
            print(f"{target.relative_to(ROOT)}: wrote {', '.join(done) or 'nothing'}")
        else:
            print(f"===== {target.relative_to(ROOT)}")
            for name, body in values.items():
                print(f"--- {name}\n{body}\n")
    t = data["totals"]
    if t["not_nemotron"]:
        print("warning: some used answers were not Nemotron: " + "; ".join(t["not_nemotron"]), file=sys.stderr)
    if t["chain_ok"] < data["rows"]:
        print(f"warning: only {t['chain_ok']} of {data['rows']} case traces verified (--logs {args.logs})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    from app.console import prepare_cli

    prepare_cli()
    raise SystemExit(main())
