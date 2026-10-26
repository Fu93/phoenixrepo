#!/usr/bin/env python3
"""Re-judge UNJUDGED rows, and NEEDS_REVIEW rows caused by infrastructure, from a scores file.

Reads a ``*_scores.json`` written by ``scripts/score_candidates.py`` (or the merged file from
``scripts/run_sharded.py``). It does not re-judge a refusal that already has a verified
alternative. Finished Nightshift calls are read from the snapshot's ``.cache/relevance``
directory, the same cache ``score_candidates.py`` writes, so a call that already succeeded
is not sent again.

Repos are judged one at a time. The process sleeps between repos and backs off further
when a repo is still unjudged, so a 502 burst is not followed by another immediate burst.
Every new call still goes through Meterless Nightshift. This script does not call a model
any other way, and it does not print the runtime token.

Without a configured Nightshift client the script exits 2 and does not rewrite verdicts
with the phrase/keyword fallback. That fallback is not a judgement of the failed call.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.evidence_report import format_decision_lines  # noqa: E402

# Phrases that mean the row was not a finished judgement.
_INFRA_MARKERS = (
    "infra:",
    "http 5",
    "http 429",
    "timed out",
    "timeout",
    "nightshift request",
    "invalid json",
    "not valid json",
    "exceeded total timeout",
    "llm_unavailable",
    "unjudged",
    "source_error",
    "source error",
    "infra: zetaris",
    "tool budget exhausted",
)
# A human-review reason that already names an overlap is a finished judgement.
_JUDGEMENT_MARKERS = (
    "alternative_overlap",
    "mainstream_overlap",
)


def is_infra_review(row: dict[str, Any]) -> bool:
    """True for UNJUDGED, and for NEEDS_REVIEW whose reason is an infrastructure failure."""
    verdict = str(row.get("verdict") or "")
    reason = str(row.get("reason") or "")
    low = reason.lower()
    if verdict == "UNJUDGED":
        return True
    if verdict != "NEEDS_REVIEW":
        return False
    if any(marker in low for marker in _JUDGEMENT_MARKERS):
        return False
    if "not found in" in low and "not valid json" not in low and "invalid json" not in low:
        return False
    return any(marker in low for marker in _INFRA_MARKERS)


def rows_to_rerun(scores: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in (scores.get("ineligible") or []) if is_infra_review(row)]


def _verdict_of(result: dict[str, Any], repo_id: str) -> tuple[str, str]:
    for bucket in ("candidates", "ineligible"):
        for row in result.get(bucket) or []:
            if row.get("repo_id") == repo_id:
                verdict = "CANDIDATE" if bucket == "candidates" else str(row.get("verdict") or "")
                return verdict, str(row.get("reason") or "")
    return "", str(result.get("error") or "")


def merge_rerun(original: dict[str, Any], replacements: dict[str, dict[str, Any]], meta: dict[str, Any]) -> dict[str, Any]:
    """Replace only the rerun repos. Leave every other row as it was."""
    rerun_ids = set(replacements)
    merged = dict(original)
    old_ineligible = {str(r.get("repo_id")): r for r in (original.get("ineligible") or [])}
    candidates = [c for c in (original.get("candidates") or []) if c.get("repo_id") not in rerun_ids]
    ineligible = [r for r in (original.get("ineligible") or []) if r.get("repo_id") not in rerun_ids]
    for repo_id, result in replacements.items():
        placed = False
        if result.get("error") and not (result.get("candidates") or result.get("ineligible")):
            kept = dict(old_ineligible.get(repo_id) or {"repo_id": repo_id, "verdict": "UNJUDGED", "reason": ""})
            kept["rerun_error"] = str(result["error"])
            ineligible.append(kept)
            continue
        for row in result.get("candidates") or []:
            if row.get("repo_id") == repo_id:
                candidates.append(row)
                placed = True
        for row in result.get("ineligible") or []:
            if row.get("repo_id") == repo_id:
                ineligible.append(row)
                placed = True
        if not placed:
            kept = dict(old_ineligible.get(repo_id) or {"repo_id": repo_id, "verdict": "UNJUDGED", "reason": ""})
            kept["rerun_error"] = "rerun produced no row for this repository"
            ineligible.append(kept)
    merged["candidates"] = candidates
    merged["ineligible"] = ineligible
    merged["rerun"] = meta
    old_ids = {c.get("repo_id") for c in (original.get("candidates") or [])}
    new_ids = {c.get("repo_id") for c in candidates}
    if old_ids != new_ids:
        merged["protagonist_note"] = (
            "The eligible set changed on rerun. The protagonist from the source file was not recomputed."
        )
    return merged


def render_rerun_md(data: dict[str, Any]) -> str:
    lines = ["# Rerun of unjudged repositories", ""]
    meta = data.get("rerun") or {}
    lines.append(f"- Source: `{meta.get('source', '')}`")
    lines.append(f"- Repos retried: {', '.join(meta.get('repos') or []) or 'none'}")
    lines.append(f"- Backoff seconds: {meta.get('backoff_seconds')}")
    lines.append(f"- Still infrastructure failures: {', '.join(meta.get('still_infra') or []) or 'none'}")
    lines.append("")
    lines.append("| Repository | Verdict | Reason |")
    lines.append("| :--- | :---: | :--- |")
    for row in data.get("ineligible") or []:
        reason = str(row.get("reason") or "").replace("|", "/").replace("\n", " ")
        lines.append(f"| `{row.get('repo_id')}` | `{row.get('verdict')}` | {reason[:300]} |")
    lines.append("")
    lines.append("## Why a repository was refused or sent to review")
    lines.append("")
    shown = False
    for row in data.get("ineligible") or []:
        block = format_decision_lines(
            str(row.get("repo_id") or ""),
            str(row.get("verdict") or ""),
            str(row.get("reason") or ""),
            row.get("decision_evidence"),
        )
        if block:
            shown = True
            lines.extend(block)
    if not shown:
        lines.append("No ALREADY_EXISTS or NEEDS_REVIEW row was recorded.")
        lines.append("")
    lines.append("## Eligible after rerun")
    lines.append("")
    for row in data.get("candidates") or []:
        lines.append(f"- `{row.get('repo_id')}`")
    if not data.get("candidates"):
        lines.append("None.")
    lines.append("")
    return "\n".join(lines)


def write_subset(snapshot: Path, repo_id: str, dest: Path) -> None:
    """A one-repository snapshot. The LLM cache stays on the original snapshot."""
    dest.mkdir(parents=True, exist_ok=True)
    found = False
    for name in ("discovery_candidates.csv", "prior_art.csv", "demand_signals.csv"):
        src = snapshot / name
        if not src.exists():
            raise FileNotFoundError(f"snapshot is missing {name}")
        with src.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = reader.fieldnames or []
            rows = [row for row in reader if row.get("repo_id") == repo_id]
        if name == "discovery_candidates.csv":
            found = bool(rows)
        with (dest / name).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
    if not found:
        raise FileNotFoundError(f"{repo_id} is not in {snapshot / 'discovery_candidates.csv'}")


def make_relevance_client(snapshot: Path):
    """Client pointed at the snapshot cache. Finished calls are cache hits."""
    from config.settings import Settings

    from app.agents.relevance import RelevanceClient

    settings = Settings()
    return RelevanceClient(
        base_url=settings.meterless_base_url,
        token=settings.meterless_runtime_token,
        model=settings.requested_model(),
        cache_dir=snapshot / ".cache" / "relevance",
    )


def zetaris_preflight_error(snapshot: Path, client: Any | None = None) -> str:
    """One read-only SELECT before any repository when Zetaris is the evidence source.

    Without it a stopped Zetaris stack would fail every repository, each after its retries
    and backoff. Returns "" when the source is csv or Zetaris answered.
    """
    from app.adapters.zetaris_evidence import (
        ZetarisPreflightError,
        build_live_client,
        cohort_repo_ids,
        preflight,
        resolve_evidence_source,
    )

    if resolve_evidence_source() != "zetaris":
        return ""
    try:
        if client is None:
            client = build_live_client()
        preflight(client, cohort_repo_ids(snapshot))
    except ZetarisPreflightError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001 — building the client failed
        return f"Zetaris client could not be built: {type(exc).__name__}: {exc}"
    return ""


def default_runner(snapshot: Path, client, log_dir: Path) -> Callable[[str], dict[str, Any]]:
    from app.agents.repo_facts import RepoFactsClient
    from scripts.score_candidates import score_all

    facts = RepoFactsClient(cache_dir=snapshot / ".cache" / "repo_facts")

    def run(repo_id: str) -> dict[str, Any]:
        dest = log_dir / "rerun-snaps" / repo_id.replace("/", "__")
        write_subset(snapshot, repo_id, dest)
        safe = repo_id.replace("/", "-")
        return score_all(
            snapshot=dest,
            output_json=dest / "scores.json",
            output_md=dest / "scores.md",
            use_llm=False,
            run_id=f"rerun-{safe}",
            log_dir=log_dir,
            relevance_client=client,
            facts_client=facts,
            command_str="scripts/rerun_unjudged.py",
        )

    return run


def rerun(
    scores: dict[str, Any],
    *,
    source: str,
    runner: Callable[[str], dict[str, Any]] | None,
    sleep: Callable[[float], None] = time.sleep,
    backoff_s: float = 30.0,
    max_attempts: int = 3,
    deadline_s: float | None = None,
    only: list[str] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Re-judge the infrastructure rows and return the merged scores document.

    ``runner`` is called once per attempt for one repository. Tests pass a fake runner.
    ``sleep`` is called between repositories and before a retry. Tests pass a recorder.
    ``deadline_s`` stops starting a repository or a retry once that many seconds have passed;
    a repository already running is not interrupted, and the ones not started keep their old
    rows and are listed in ``not_started``. ``only`` limits the rerun to those repo ids.
    """
    started = clock()
    selected = rows_to_rerun(scores)
    if only:
        wanted = {item.strip() for item in only if item.strip()}
        selected = [row for row in selected if str(row.get("repo_id") or "") in wanted]
    meta: dict[str, Any] = {
        "source": source,
        "repos": [str(row.get("repo_id") or "") for row in selected],
        "backoff_seconds": backoff_s,
        "max_attempts": max_attempts,
        "attempts": {},
        "still_infra": [],
        "not_started": [],
        "deadline_seconds": deadline_s,
        "live_ran": runner is not None and bool(selected),
    }

    def out_of_time() -> bool:
        return deadline_s is not None and clock() - started >= deadline_s

    if not selected or runner is None:
        meta["live_ran"] = False
        merged = merge_rerun(scores, {}, meta)
        return merged
    replacements: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(selected):
        repo_id = str(row.get("repo_id") or "")
        if index:
            sleep(backoff_s)
        if out_of_time():
            meta["not_started"] = [str(r.get("repo_id") or "") for r in selected[index:]]
            break
        attempt = 0
        result: dict[str, Any] = {}
        while True:
            attempt += 1
            try:
                result = runner(repo_id)
            except Exception as exc:  # noqa: BLE001 — one repo must not abort the others
                result = {"error": str(exc), "candidates": [], "ineligible": []}
            verdict, reason = _verdict_of(result, repo_id)
            if not is_infra_review({"verdict": verdict, "reason": reason}) or attempt >= max_attempts or out_of_time():
                break
            sleep(min(120.0, backoff_s * (2 ** attempt)))
        meta["attempts"][repo_id] = attempt
        verdict, reason = _verdict_of(result, repo_id)
        if is_infra_review({"verdict": verdict, "reason": reason}):
            meta["still_infra"].append(repo_id)
        replacements[repo_id] = result
    return merge_rerun(scores, replacements, meta)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scores", type=Path, required=True, help="A *_scores.json from score_candidates or run_sharded.")
    parser.add_argument("--snapshot", type=Path, required=True, help="Discovery snapshot the scores were computed from.")
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--log-dir", type=Path, default=ROOT / "logs")
    parser.add_argument("--backoff", type=float, default=30.0, help="Seconds to wait between repositories.")
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--deadline", type=float, default=None, help="Stop starting repositories or retries after this many seconds (the one running finishes).")
    parser.add_argument("--repo", action="append", default=[], help="Only rerun this repo id (repeatable).")
    args = parser.parse_args()

    scores = json.loads(args.scores.read_text(encoding="utf-8"))
    selected = rows_to_rerun(scores)
    runner = None
    if selected:
        from app.agents.relevance import is_client_configured

        client = make_relevance_client(args.snapshot)
        if not is_client_configured(client):
            sys.stderr.write(
                "METERLESS_RUNTIME_TOKEN is missing. Refusing to rewrite verdicts without Nightshift. "
                "Finished calls would not be reused and the phrase fallback is not a rerun.\n"
            )
            return 2
        error = zetaris_preflight_error(args.snapshot)
        if error:
            sys.stderr.write(f"Rerun not started: {error}\n")
            return 2
        args.log_dir.mkdir(parents=True, exist_ok=True)
        runner = default_runner(args.snapshot, client, args.log_dir)
    merged = rerun(
        scores,
        source=str(args.scores),
        runner=runner,
        backoff_s=args.backoff,
        max_attempts=args.max_attempts,
        deadline_s=args.deadline,
        only=args.repo or None,
    )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    args.output_md.write_text(render_rerun_md(merged), encoding="utf-8")
    print(json.dumps({
        "repos": (merged.get("rerun") or {}).get("repos"),
        "still_infra": (merged.get("rerun") or {}).get("still_infra"),
        "not_started": (merged.get("rerun") or {}).get("not_started"),
        "output_json": str(args.output_json),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    from app.console import prepare_cli

    prepare_cli()
    raise SystemExit(main())
