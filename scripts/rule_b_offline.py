"""Run the actual opt-in Rule B guard on reconstructed, pinned v3 facts.

No model, GitHub lookup, or full judge rerun. Missing web metadata is reconstructed
as the old web-row placeholders; snippets come only from an explicit cache copy.
This is a post-v3 sensitivity check, not an accuracy score or exact run replay.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agents.relevance import PHOENIX_UNKNOWN_BLOCKER_REVIEW, _unknown_blocker_review
from scripts.verify_nemotron_evidence import load_scored
from scripts.verify_trace import verify


def summary(rows: list[dict]) -> dict:
    changed = [r for r in rows if r["on"] != r["off"]]
    return {
        "cases": len(rows), "agreement_off": sum(r["off"] in r["acceptable"] for r in rows),
        "agreement_on": sum(r["on"] in r["acceptable"] for r in rows),
        "automatic_false_passes_off": sum(r["off"] == "CANDIDATE" and r["acceptable"] == ["ALREADY_EXISTS"] for r in rows),
        "automatic_false_passes_on": sum(r["on"] == "CANDIDATE" and r["acceptable"] == ["ALREADY_EXISTS"] for r in rows),
        "refusal_label_errors_on": sum(r["on"] != "ALREADY_EXISTS" and r["acceptable"] == ["ALREADY_EXISTS"] for r in rows),
        "additional_reviews_all": len(changed),
        "additional_reviews_previously_agreeing": sum(r["off"] in r["acceptable"] for r in changed),
        "review_verdicts_off": sum(r["off"] == "NEEDS_REVIEW" for r in rows),
        "review_verdicts_on": sum(r["on"] == "NEEDS_REVIEW" for r in rows),
        "transitions": dict(Counter(f"{r['off']} -> {r['on']}" for r in rows)),
    }


def run(root: Path, cache_copy: Path, as_of: date, identity_evidence: list[dict] | None = None) -> dict:
    scored, problems = load_scored(root)
    if problems:
        raise ValueError("; ".join(problems))
    cache = {}
    inputs = []
    for path in sorted(cache_copy.glob("*.json")):
        raw = path.read_bytes()
        data = json.loads(raw)
        inputs.append({"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()})
        for item in data.get("items") or []:
            url = str(item.get("url") or "")
            cache.setdefault(url, (str(item.get("title") or ""), str(item.get("snippet") or "")))
    label_path = root / "data/eval/gate1_labels.json"
    raw_labels = label_path.read_bytes()
    labels = {c["repo_id"]: c for c in json.loads(raw_labels)["cases"]}
    inputs.append({"path": str(label_path), "sha256": hashlib.sha256(raw_labels).hexdigest()})
    overlays = {item["source_url"]: item["page_text"] for item in identity_evidence or []}
    rows = []
    for item in scored:
        raw = item.trace.read_bytes()
        ok, detail = verify(item.trace, expected_run_hash=item.run_hash)
        if not ok:
            raise ValueError(detail)
        inputs.append({"path": str(item.trace), "sha256": hashlib.sha256(raw).hexdigest()})
        events = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]

        def output(action):
            event = next((e for e in reversed(events) if e.get("action") == action), {})
            value = event.get("output_summary") or {}
            return json.loads(value) if isinstance(value, str) else value

        final = output("DISCOVERY_FILTER")
        candidate = dict(labels[item.repo_id].get("audit", {}).get("candidate") or {})
        candidate["repo_id"] = item.repo_id
        for row in final.get("must_beat") or []:
            match = re.search(r"candidate_stars=(\d+)", row.get("reason", ""))
            if match:
                candidate["stars"] = int(match[1])
                break
        alternatives = []
        for old in output("ALTERNATIVES_VERIFIED").get("alternatives") or []:
            row = {**old, "same_problem": True, "quote_verified": True, "stars": 0, "licence_spdx": "", "pushed_at": ""}
            url = str(row.get("url") or str(row.get("alternative_id") or "").removeprefix("web:"))
            title, snippet = cache.get(url, ("", ""))
            row.update(description=f"{title} - {snippet}" if title and snippet else title or snippet, readme=snippet)
            if url in overlays:
                row["page_text"] = overlays[url]
            alternatives.append(row)
        old_flag = os.environ.get(PHOENIX_UNKNOWN_BLOCKER_REVIEW)
        try:
            os.environ.pop(PHOENIX_UNKNOWN_BLOCKER_REVIEW, None)
            off_hit = _unknown_blocker_review(candidate, alternatives, None, as_of=as_of)
            assert off_hit is None
            os.environ[PHOENIX_UNKNOWN_BLOCKER_REVIEW] = "1"
            hit = _unknown_blocker_review(candidate, alternatives, None, as_of=as_of) if final["verdict"] == "CANDIDATE" else None
        finally:
            if old_flag is None:
                os.environ.pop(PHOENIX_UNKNOWN_BLOCKER_REVIEW, None)
            else:
                os.environ[PHOENIX_UNKNOWN_BLOCKER_REVIEW] = old_flag
        rows.append({"repo_id": item.repo_id, "acceptable": labels[item.repo_id]["acceptable"],
                     "off": final["verdict"], "on": "NEEDS_REVIEW" if hit else final["verdict"],
                     "reason": hit["reason"] if hit else "unchanged", "run_hash": item.run_hash,
                     "trace_sha256": hashlib.sha256(raw).hexdigest()})
    return {
        "data_class": "POST_V3_RULE_B_SENSITIVITY", "as_of": as_of.isoformat(),
        "mode": "new_identity_evidence_overlay" if overlays else "reconstructed_frozen_facts",
        "claim_scope": "actual Rule B guard on reconstructed inputs; not the full pipeline or a held-out accuracy result",
        "reconstruction": "trace-verified alternatives plus old stars/date/licence placeholders; first URL snippet in sorted copied cache files",
        "identity_evidence_sha256": hashlib.sha256(json.dumps(identity_evidence or [], sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest(),
        "model_calls": 0, "summary": summary(rows), "cases": rows, "input_hashes": inputs,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--web-cache-copy", type=Path, required=True)
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--identity-evidence", type=Path)
    args = parser.parse_args(argv)

    def no_network(*args, **kwargs):
        raise RuntimeError("network disabled for offline Rule B audit")

    socket.create_connection = no_network
    socket.socket.connect = no_network
    socket.getaddrinfo = no_network
    evidence = json.loads(args.identity_evidence.read_text(encoding="utf-8")) if args.identity_evidence else None
    print(json.dumps(run(args.root, args.web_cache_copy, args.as_of, evidence), ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
