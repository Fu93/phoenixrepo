"""Ask Nightshift for functional-equivalence maps and score them against the labels.

This is the runner for a machine where Nightshift is already logged in.
It does not print tokens. If Nightshift is down, it prints live_accuracy not_run
and exits 2. It does not fall back to a fabricated score.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agents.equivalence import judge_discovery  # noqa: E402
from app.llm.parsing import parse_model_json  # noqa: E402
from app.prompts import load_prompt  # noqa: E402
from config.settings import Settings  # noqa: E402

CASES = ROOT / "data" / "eval" / "cases.json"


def main() -> int:
    token = os.environ.get("METERLESS_RUNTIME_TOKEN", "").strip()
    if not token:
        _skip("METERLESS_RUNTIME_TOKEN is unset")
        return 2
    try:
        import httpx
    except ImportError:
        _skip("httpx is not installed")
        return 2
    base = os.environ.get("METERLESS_BASE_URL", "http://127.0.0.1:8088/v1").rstrip("/")
    headers = {"Authorization": f"Bearer {token}", "meterless-app": os.environ.get("METERLESS_APP", "phoenixrepo")}
    try:
        probe = httpx.get(base + "/models", headers=headers, timeout=5.0)
    except httpx.HTTPError as exc:
        _skip(f"Nightshift models request failed: {type(exc).__name__}")
        return 2
    if probe.status_code != 200:
        _skip(f"Nightshift /models returned HTTP {probe.status_code}")
        return 2
    prompt = load_prompt("equivalence")
    payload = json.loads(CASES.read_text(encoding="utf-8"))
    results = []
    for case in payload["cases"]:
        messages = [
            {"role": "system", "content": prompt.text},
            {
                "role": "user",
                "content": "ROLE: equivalence\nEVIDENCE_JSON:\n"
                + json.dumps(
                    {
                        "candidate": case["candidate"],
                        "prior_art": case["prior_art"],
                        "evidence": case["evidence"],
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        try:
            response = httpx.post(
                base + "/chat/completions",
                headers={**headers, "meterless-scope": "user"},
                json={"model": Settings().requested_model(), "messages": messages, "temperature": 0},
                timeout=120.0,
            )
        except httpx.HTTPError as exc:
            _skip(f"chat failed on {case['repo_id']}: {type(exc).__name__}")
            return 2
        if response.status_code != 200:
            _skip(f"chat HTTP {response.status_code} on {case['repo_id']}")
            return 2
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        served = body.get("model")
        try:
            mapping = parse_model_json(content, expected_keys=("capabilities", "mappings"))
        except Exception:
            mapping = None
        predicted, reason = judge_discovery(
            case["candidate"],
            case["prior_art"],
            case["demand"],
            case["evidence"],
            mapping if isinstance(mapping, dict) else None,
        )
        results.append(
            {
                "repo_id": case["repo_id"],
                "label": case["label"],
                "predicted": predicted,
                "match": predicted == case["label"],
                "served_model": served,
                "prompt_version": prompt.sha256,
                "reason": reason,
            }
        )
    correct = sum(1 for item in results if item["match"])
    total = len(results)
    json.dump(
        {
            "mode": "nightshift-functional",
            "cases": total,
            "correct": correct,
            "accuracy": correct / total if total else 0,
            "prompt_version": prompt.sha256,
            "results": results,
        },
        sys.stdout,
        indent=2,
        ensure_ascii=False,
    )
    sys.stdout.write("\n")
    return 0


def _skip(reason: str) -> None:
    json.dump({"mode": "nightshift-functional", "live_accuracy": "not_run", "reason": reason}, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())
