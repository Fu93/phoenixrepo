#!/usr/bin/env python3
"""Recompute committed Nemotron evidence offline.

One command, no network, no model calls, and no read of .env. Exit 0 only when
every check passes. Absent fields are printed as "not recorded" and are not
inferred (an omitted provider is not counted as nightshift).

    python scripts/verify_nemotron_evidence.py
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
from collections import Counter
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.trace_log import kept_request_settings, request_settings_sha256
from config.settings import is_nemotron_family
from scripts.eval_summary import summarise
from scripts.verify_all_replays import main as verify_all_replays_main
from scripts.verify_trace import _NOT_USED, line_has_request_settings, verify

# README figures this command recomputes. They are constants, not parsed out of README.md.
EXPECTED_ANSWERS = 218
EXPECTED_NEMOTRON = 218
EXPECTED_NIGHTSHIFT = 168
EXPECTED_NVIDIA_DIRECT = 50
EXPECTED_RECORDED_PASS = 19
EXPECTED_CASES = 16
EXPECTED_AGREEMENTS = 10
EXPECTED_MUST_REJECT = 5
EXPECTED_FALSE_PASSES = 4
EXPECTED_TIERS = (
    ("fast", "nvidia/nemotron-3-ultra-550b-a55b", "Ultra", 174),
    ("fast", "nvidia/nemotron-3.5-super-vl-120b-a12b", "Super-VL", 18),
    ("deep", "nvidia/nemotron-3-ultra-550b-a55b", "Ultra", 16),
    ("no tier", "nvidia/nemotron-3-ultra-550b-a55b", "Ultra", 7),
    ("no tier", "nvidia/nemotron-3.5-lightning-30b-a3b", "Lightning", 3),
)
_MODEL_ACTIONS = {"LLM_CALL", "LLM_SCORE"}
_SAMPLING_KEYS = ("temperature", "top_p", "max_tokens", "enable_thinking", "thinking", "reasoning_effort")
_SAMPLING_SENTENCE = "Scored v3 traces do not record temperature or thinking mode; neither is inferred."
_NOT_SCORED_LABEL = "not the scored v3 run"


@dataclass
class Answer:
    line: int
    action: str
    requested: str | None
    requested_at: str
    served: str | None
    served_at: str
    provider: str | None
    provider_at: str
    tier: str | None
    tier_at: str


@dataclass
class Scored:
    replay_id: str
    repo_id: str
    trace: Path
    record: dict[str, Any]
    run_hash: str


def _ascii(text: str) -> str:
    return text.encode("ascii", "backslashreplace").decode("ascii")


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _object_summary(record: dict[str, Any]) -> dict[str, Any]:
    raw = record.get("output_summary")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def recorded_field(record: dict[str, Any], name: str) -> tuple[str | None, str]:
    """Value of a field that is actually on the line. Never a default."""

    if name in record:
        text = str(record.get(name) if record.get(name) is not None else "").strip()
        if text:
            return text, "top-level"
    summary = _object_summary(record)
    if name in summary:
        text = str(summary.get(name) if summary.get(name) is not None else "").strip()
        if text:
            return text, "output_summary"
    return None, "absent"


def _is_model_line(record: dict[str, Any]) -> bool:
    action = str(record.get("action") or record.get("event") or "")
    return action in _MODEL_ACTIONS or "served_model" in record


def iter_records(path: Path) -> list[tuple[int, dict[str, Any]]]:
    rows: list[tuple[int, dict[str, Any]]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        parsed = json.loads(line)
        if isinstance(parsed, dict):
            rows.append((index, parsed))
    return rows


def used_answers(path: Path) -> list[Answer]:
    """LLM_CALL / LLM_SCORE lines that scripts.verify_trace counts as answers used.

    Status error or rejected is the same skip as scripts.verify_trace._NOT_USED.
    """

    answers: list[Answer] = []
    for index, record in iter_records(path):
        if not _is_model_line(record):
            continue
        if str(record.get("status") or "") in _NOT_USED:
            continue
        requested, requested_at = recorded_field(record, "requested_model")
        served, served_at = recorded_field(record, "served_model")
        provider, provider_at = recorded_field(record, "provider")
        tier, tier_at = recorded_field(record, "tier")
        answers.append(
            Answer(
                line=index,
                action=str(record.get("action") or record.get("event") or ""),
                requested=requested,
                requested_at=requested_at,
                served=served,
                served_at=served_at,
                provider=provider,
                provider_at=provider_at,
                tier=tier,
                tier_at=tier_at,
            )
        )
    return answers


def skipped_model_lines(path: Path) -> int:
    total = 0
    for _index, record in iter_records(path):
        if _is_model_line(record) and str(record.get("status") or "") in _NOT_USED:
            total += 1
    return total


def audit_trace(path: Path, *, expected_run_hash: str | None = None) -> list[str]:
    """Fail on a broken chain, a non-Nemotron used answer, or a missing answer field.

    A missing provider is a failure. It is not filled in as nightshift.
    A missing tier is not a failure here: the caller counts that line as no tier.
    """

    problems: list[str] = []
    ok, detail = verify(path, expected_run_hash=expected_run_hash)
    if not ok:
        problems.append(f"FAIL hash chain: {detail}")
    try:
        rows = used_answers(path)
    except (OSError, json.JSONDecodeError) as exc:
        problems.append(f"FAIL could not read model lines: {exc}")
        return problems
    for row in rows:
        where = f"{path} line {row.line}"
        if row.served is None:
            problems.append(f"FAIL Served not recorded ({where})")
        elif not is_nemotron_family(row.served):
            problems.append(f"FAIL Served {row.served} does not start with nvidia/nemotron- ({where})")
        if row.requested is None:
            problems.append(f"FAIL Requested not recorded ({where})")
        if row.provider is None:
            problems.append(f"FAIL provider not recorded ({where})")
    return problems


def _served_short(model: str) -> str:
    lowered = model.lower()
    if "super-vl" in lowered:
        return "Super-VL"
    if "lightning" in lowered:
        return "Lightning"
    if "ultra" in lowered:
        return "Ultra"
    return model


def _tier_bucket(tier: str | None) -> str:
    return tier if tier else "no tier"


def _check(label: str, got: int, expected: int, failures: list[str]) -> str:
    ok = got == expected
    if not ok:
        failures.append(label)
    return f"  {label}: recomputed {got}, expected {expected}, {'PASS' if ok else 'FAIL'}"


def _load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data


def _is_scored_v3(entry: dict[str, Any], record: dict[str, Any], audit_ids: set[str]) -> bool:
    """A RECORDED audit replay that does not label itself as another run.

    Scored copies store recorded_run.source ending in scores.json and a judge- run id.
    A title, summary, or banner that says "not the scored run" stays out of the 16.
    """

    if entry.get("trace_class") != "RECORDED":
        return False
    repo_id = str(record.get("repo_id") or entry.get("repo_id") or "")
    if repo_id not in audit_ids:
        return False
    text = " ".join(str(entry.get(key) or "") for key in ("title", "summary"))
    text += " " + " ".join(str(record.get(key) or "") for key in ("title", "summary", "banner"))
    if "not the scored run" in text.lower():
        return False
    recorded = record.get("recorded_run") if isinstance(record.get("recorded_run"), dict) else {}
    source = str(recorded.get("source") or "").replace("\\", "/")
    run_id = str(recorded.get("run_id") or "")
    return source.endswith("scores.json") and run_id.startswith("judge-")


def load_scored(root: Path) -> tuple[list[Scored], list[str]]:
    problems: list[str] = []
    labels_path = root / "data" / "eval" / "gate1_labels.json"
    manifest_path = root / "data" / "replays" / "manifest.json"
    if not labels_path.is_file():
        return [], [f"MISSING file {_rel(labels_path, root)}"]
    if not manifest_path.is_file():
        return [], [f"MISSING file {_rel(manifest_path, root)}"]
    labels = _load_json(labels_path)
    audit_ids = {str(case.get("repo_id") or "") for case in labels.get("cases") or [] if isinstance(case, dict) and case.get("audit") and case.get("repo_id")}
    manifest = _load_json(manifest_path)
    replays_root = manifest_path.parent
    scored: list[Scored] = []
    for entry in manifest.get("replays") or []:
        if not isinstance(entry, dict):
            continue
        replay_id = str(entry.get("id") or "")
        record_path = replays_root / replay_id / "record.json"
        trace_path = replays_root / replay_id / "trace.jsonl"
        if not replay_id or not record_path.is_file():
            continue
        try:
            record = _load_json(record_path)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            problems.append(f"FAIL {_rel(record_path, root)}: {exc}")
            continue
        if not _is_scored_v3(entry, record, audit_ids):
            continue
        if not trace_path.is_file():
            problems.append(f"MISSING file {_rel(trace_path, root)} field trace.jsonl")
            continue
        recorded = record.get("recorded_run") if isinstance(record.get("recorded_run"), dict) else {}
        run_hash = str(recorded.get("run_hash") or "")
        if not run_hash:
            problems.append(f"MISSING field recorded_run.run_hash in {_rel(record_path, root)}")
        scored.append(
            Scored(
                replay_id=replay_id,
                repo_id=str(record.get("repo_id") or entry.get("repo_id") or ""),
                trace=trace_path,
                record=record,
                run_hash=run_hash,
            )
        )
    found = {item.repo_id for item in scored}
    missing = sorted(audit_ids - found)
    extra = sorted(found - audit_ids)
    if missing:
        problems.append("MISSING scored v3 trace for repo_id: " + ", ".join(missing))
    if extra:
        problems.append("unexpected scored v3 repo_id: " + ", ".join(extra))
    dupes = [repo for repo, count in Counter(item.repo_id for item in scored).items() if count > 1]
    if dupes:
        problems.append("duplicate scored v3 repo_id: " + ", ".join(sorted(dupes)))
    scored.sort(key=lambda item: item.repo_id)
    return scored, problems


def audit_figures(scored: list[Scored], labels: dict[str, Any]) -> dict[str, int]:
    """16 / 10 / 5 / 4 from committed verdicts and the label file.

    False pass follows the README audit table: a must-reject case judged CANDIDATE.
    """

    results = []
    false_passes = 0
    cases_by_id = {str(case.get("repo_id") or ""): case for case in labels.get("cases") or [] if isinstance(case, dict)}
    for item in scored:
        verdict_obj = item.record.get("verdict") if isinstance(item.record.get("verdict"), dict) else {}
        verdict = str(verdict_obj.get("verdict") or "")
        results.append(
            {
                "repo_id": item.repo_id,
                "verdict": verdict,
                "reason": str(verdict_obj.get("reason") or ""),
            }
        )
        case = cases_by_id.get(item.repo_id) or {}
        acceptable = set(case.get("acceptable") or [])
        if case.get("audit") and acceptable == {"ALREADY_EXISTS"} and verdict == "CANDIDATE":
            false_passes += 1
    summary = summarise({"results": results}, labels)
    return {
        "cases": int(summary["cases_in_report"]),
        "agreements": int(summary["agreed"]),
        "must_reject": int(summary["should_refuse_judged"]),
        "false_passes": false_passes,
        "summary_false_passes": len(summary["false_pass"]),
    }


def _settings_object(record: dict[str, Any]) -> dict[str, Any] | None:
    top = record.get("request_settings")
    if isinstance(top, dict) and top:
        return top
    nested = _object_summary(record).get("request_settings")
    if isinstance(nested, dict) and nested:
        return nested
    return None


def _has_sampling(record: dict[str, Any]) -> bool:
    if line_has_request_settings(record):
        return True
    blobs: list[dict[str, Any]] = [record, _object_summary(record)]
    return any(key in blob for blob in blobs for key in _SAMPLING_KEYS)


def _replay_traces(root: Path) -> list[Path]:
    replay_root = root / "data" / "replays"
    if not replay_root.is_dir():
        return []
    return sorted(path for path in replay_root.glob("*/trace.jsonl") if path.is_file())


def _append_settings(lines: list[str], groups: list[tuple[str, str, str, int]], *, label: str | None) -> None:
    current = ""
    for rel, rendered, digest, count in groups:
        if rel != current:
            current = rel
            lines.append(f"{label}: {rel}" if label else rel)
        lines.append(f"  sanitized request_settings: {rendered}")
        lines.append(f"  sha256: {digest}")
        lines.append(f"  lines: {count}")


def _recorded_pass_count(manifest: Path) -> tuple[int | None, int | None, list[str]]:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = verify_all_replays_main(["--manifest", str(manifest)])
    text = buffer.getvalue()
    match = re.search(r"(\d+) passed, (\d+) failed, (\d+) RECORDED", text)
    notes = [line for line in text.splitlines() if "FAIL" in line]
    if match is None:
        notes.append("MISSING verify_all_replays pass count")
        notes.append(f"verify_all_replays exit {code}")
        return None, code, notes
    passed = int(match.group(1))
    if code != 0:
        notes.append(f"verify_all_replays exit {code}")
    return passed, code, notes


def build_report(root: Path | None = None) -> tuple[int, str]:
    root = root or ROOT
    failures: list[str] = []
    lines: list[str] = [
        "Nemotron evidence (offline, read-only, no network, no model calls)",
        "",
    ]
    try:
        scored, locate_problems = load_scored(root)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        text = _ascii(f"FAIL could not locate scored v3 traces: {exc}\nRESULT: FAIL\n")
        return 1, text
    for problem in locate_problems:
        failures.append(problem)
        lines.append(_ascii(problem))
    lines.append(f"Scored v3 traces: {len(scored)}")
    for item in scored:
        lines.append(f"  {_rel(item.trace, root)}")
    lines.append("")

    answers: list[tuple[Scored, Answer]] = []
    skipped = 0
    read_errors: list[str] = []
    for item in scored:
        try:
            for answer in used_answers(item.trace):
                answers.append((item, answer))
            skipped += skipped_model_lines(item.trace)
        except (OSError, json.JSONDecodeError) as exc:
            read_errors.append(f"FAIL could not read {_rel(item.trace, root)}: {exc}")
    for problem in read_errors:
        failures.append(problem)
        lines.append(_ascii(problem))

    requested_counts: Counter[str] = Counter()
    served_counts: Counter[str] = Counter()
    provider_counts: Counter[str] = Counter()
    tier_counts: Counter[tuple[str, str]] = Counter()
    requested_at: Counter[str] = Counter()
    served_at: Counter[str] = Counter()
    provider_at: Counter[str] = Counter()
    tier_at: Counter[str] = Counter()
    nemotron = 0
    non_nemotron: list[str] = []
    missing_provider: Counter[str] = Counter()
    for item, answer in answers:
        rel = _rel(item.trace, root)
        requested_at[answer.requested_at] += 1
        served_at[answer.served_at] += 1
        provider_at[answer.provider_at] += 1
        tier_at[answer.tier_at] += 1
        requested_counts[answer.requested or "not recorded"] += 1
        served_name = answer.served or "not recorded"
        served_counts[served_name] += 1
        if answer.served is not None and is_nemotron_family(answer.served):
            nemotron += 1
        else:
            non_nemotron.append(f"{rel} line {answer.line}: {served_name}")
        provider_name = answer.provider or "not recorded"
        provider_counts[provider_name] += 1
        if answer.provider is None:
            missing_provider[rel] += 1
        tier_counts[(_tier_bucket(answer.tier), served_name)] += 1
        if answer.requested is None:
            failures.append(f"MISSING field requested_model in {rel}")
        if answer.served is None:
            failures.append(f"MISSING field served_model in {rel}")

    lines.append("Requested")
    lines.append("  field location: " + ", ".join(f"{name} {count}" for name, count in sorted(requested_at.items())))
    for name, count in sorted(requested_counts.items()):
        lines.append(f"  {name}: {count}")
    if requested_counts.get("not recorded"):
        lines.append("  not recorded means the requested_model field is absent. It is not inferred.")
    lines.append("")

    lines.append("Served")
    lines.append("  field location: " + ", ".join(f"{name} {count}" for name, count in sorted(served_at.items())))
    lines.append(_check("total answers", len(answers), EXPECTED_ANSWERS, failures))
    lines.append(_check("nvidia/nemotron- prefix", nemotron, EXPECTED_NEMOTRON, failures))
    lines.append(f"  not counted (status error or rejected): {skipped}")
    for name, count in sorted(served_counts.items()):
        lines.append(f"  {name}: {count}")
    if non_nemotron:
        failures.append("non-nemotron served id")
        for item in non_nemotron:
            lines.append(f"  FAIL Served {item}")
    expected_keys = {(tier, model) for tier, model, _short, _count in EXPECTED_TIERS}
    for tier, model, short, expected in EXPECTED_TIERS:
        got = tier_counts[(tier, model)]
        label = f"{tier} {short} ({model})"
        lines.append(_check(label, got, expected, failures))
    for key, count in sorted(tier_counts.items()):
        if key not in expected_keys:
            failures.append(f"unexpected tier {key[0]} {key[1]}")
            lines.append(f"  FAIL unexpected {key[0]} {key[1]}: {count}")
    absent_tier = tier_at.get("absent", 0)
    lines.append(f"  tier field not recorded: {absent_tier} (counted in the no tier rows; not inferred)")
    lines.append("")

    lines.append("Policy")
    lines.append("  PHOENIX_NEMOTRON_POLICY: not recorded")
    lines.append("  provider field location: " + ", ".join(f"{name} {count}" for name, count in sorted(provider_at.items())))
    lines.append(_check("provider nvidia-direct", provider_counts.get("nvidia-direct", 0), EXPECTED_NVIDIA_DIRECT, failures))
    lines.append(_check("provider nightshift", provider_counts.get("nightshift", 0), EXPECTED_NIGHTSHIFT, failures))
    not_recorded = provider_counts.get("not recorded", 0)
    lines.append(f"  provider not recorded: {not_recorded}")
    if not_recorded:
        failures.append("MISSING field provider")
        lines.append("  provider is absent on those lines. It is not inferred as nightshift.")
        for rel, count in sorted(missing_provider.items()):
            lines.append(f"  MISSING field provider: {rel}: {count} used answers")
    other_providers = sorted(name for name in provider_counts if name not in {"nvidia-direct", "nightshift", "not recorded"})
    for name in other_providers:
        failures.append(f"unexpected provider {name}")
        lines.append(f"  FAIL unexpected provider {name}: {provider_counts[name]}")
    lines.append("")

    lines.append("Hash chain (scripts.verify_trace.verify)")
    for item in scored:
        if not item.run_hash:
            lines.append(f"  {_rel(item.trace, root)} FAIL missing recorded_run.run_hash")
            continue
        ok, detail = verify(item.trace, expected_run_hash=item.run_hash)
        if ok:
            lines.append(f"  {_rel(item.trace, root)} PASS {detail}")
        else:
            failures.append(f"hash chain {item.replay_id}")
            lines.append(f"  {_rel(item.trace, root)} FAIL {detail}")
    lines.append("")

    manifest = root / "data" / "replays" / "manifest.json"
    lines.append("RECORDED replays (scripts.verify_all_replays)")
    if manifest.is_file():
        passed, code, notes = _recorded_pass_count(manifest)
        if passed is None:
            failures.append("RECORDED pass count")
            lines.append("  FAIL pass count not recorded in verify_all_replays output")
        else:
            lines.append(_check("passed", passed, EXPECTED_RECORDED_PASS, failures))
            if code not in (0, None) and passed == EXPECTED_RECORDED_PASS:
                failures.append("verify_all_replays exit")
                lines.append(f"  FAIL verify_all_replays exit {code}")
        for note in notes:
            if note not in lines:
                lines.append(f"  {_ascii(note)}")
    else:
        failures.append("MISSING manifest")
        lines.append(f"  MISSING file {_rel(manifest, root)}")
    lines.append("")

    scored_paths = {item.trace.resolve() for item in scored}
    scored_sampling = False
    scored_settings: list[tuple[str, str, str, int]] = []
    other_settings: list[tuple[str, str, str, int]] = []
    for trace_path in _replay_traces(root):
        try:
            records = iter_records(trace_path)
        except (OSError, json.JSONDecodeError) as exc:
            failures.append(f"request_settings {trace_path.name}")
            lines.append(f"  FAIL could not read {_rel(trace_path, root)}: {exc}")
            continue
        groups: Counter[tuple[str, str]] = Counter()
        is_scored = trace_path.resolve() in scored_paths
        for _index, record in records:
            if is_scored and _has_sampling(record):
                scored_sampling = True
            raw = _settings_object(record)
            if not raw:
                continue
            sanitized = kept_request_settings(raw)
            digest = request_settings_sha256(sanitized)
            rendered = json.dumps(sanitized, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            groups[(rendered, digest)] += 1
        bucket = scored_settings if is_scored else other_settings
        for (rendered, digest), count in sorted(groups.items()):
            bucket.append((_rel(trace_path, root), rendered, digest, count))

    if scored_sampling:
        failures.append("scored v3 sampling fields")
        lines.append("Scored v3 traces record sampling fields. The absence sentence is not printed.")
        _append_settings(lines, scored_settings, label=None)
    else:
        lines.append(_SAMPLING_SENTENCE)
    lines.append("")
    if other_settings:
        _append_settings(lines, other_settings, label=_NOT_SCORED_LABEL)
    else:
        lines.append(f"{_NOT_SCORED_LABEL}: no replay trace carries request_settings")
    lines.append("")

    labels_path = root / "data" / "eval" / "gate1_labels.json"
    if not labels_path.is_file() or not scored:
        failures.append("v3 summary")
        lines.append(
            "v3 summary (regression/audit set, not an accuracy benchmark): "
            "MISSING committed verdicts or data/eval/gate1_labels.json. "
            "logs/eval-nemotron-v3.json is not a committed file and was not read."
        )
    else:
        try:
            figures = audit_figures(scored, _load_json(labels_path))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            failures.append("v3 summary")
            lines.append(f"FAIL v3 summary: {exc}")
            figures = None
        if figures is not None:
            figures_ok = (
                figures["cases"] == EXPECTED_CASES
                and figures["agreements"] == EXPECTED_AGREEMENTS
                and figures["must_reject"] == EXPECTED_MUST_REJECT
                and figures["false_passes"] == EXPECTED_FALSE_PASSES
                and figures["summary_false_passes"] == figures["false_passes"]
            )
            if figures_ok:
                lines.append(
                    "v3 summary (regression/audit set, not an accuracy benchmark): "
                    f"{figures['cases']} cases, {figures['agreements']} label agreements, "
                    f"{figures['must_reject']} must-reject, {figures['false_passes']} false passes"
                )
            else:
                failures.append("v3 summary")
                lines.append(
                    "v3 summary (regression/audit set, not an accuracy benchmark): "
                    f"recomputed {figures['cases']} cases, {figures['agreements']} label agreements, "
                    f"{figures['must_reject']} must-reject, {figures['false_passes']} false passes; "
                    f"expected {EXPECTED_CASES} cases, {EXPECTED_AGREEMENTS} label agreements, "
                    f"{EXPECTED_MUST_REJECT} must-reject, {EXPECTED_FALSE_PASSES} false passes FAIL"
                )
                if figures["summary_false_passes"] != figures["false_passes"]:
                    lines.append(f"  FAIL false-pass definitions differ: CANDIDATE count {figures['false_passes']}, scripts.eval_summary false_pass {figures['summary_false_passes']}")
            lines.append(
                "recomputed from data/eval/gate1_labels.json and data/replays record.json verdicts "
                "via scripts.eval_summary.summarise. "
                "logs/eval-nemotron-v3.json is not a committed file and was not read."
            )
    lines.append("")
    lines.append("RESULT: PASS" if not failures else "RESULT: FAIL")
    text = _ascii("\n".join(lines) + "\n")
    return (0 if not failures else 1), text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recompute committed Nemotron evidence offline. No network and no model calls.")
    parser.parse_args(argv)
    code, text = build_report(ROOT)
    sys.stdout.write(text)
    return code


if __name__ == "__main__":
    from app.console import ensure_utf8_stdio

    ensure_utf8_stdio()
    sys.exit(main())
