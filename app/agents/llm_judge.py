"""Live critique of one role payload.

Sample mode does not call this. A parsed reject can add a rejection. A parsed
accept cannot clear a deterministic rejection, and it cannot clear a missing
evidence id. An unparsed reply leaves the deterministic result unchanged.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from app.agents.validate import Critique
from app.errors import InvestigationError
from app.llm.parsing import parse_model_json
from app.prompts import load_prompt

_REVIEWER = load_prompt("reviewer")
JUDGE_SYSTEM = _REVIEWER.text
PROMPT_VERSION = _REVIEWER.sha256


@dataclass
class LlmJudgeOutcome:
    parsed: bool
    decision: str
    reason: str
    claim_ids: list[str]
    raw: str
    requested_model: str
    served_model: str | None
    latency_ms: int
    prompt_version: str = PROMPT_VERSION


def call_llm_judge(llm, *, subject: dict) -> LlmJudgeOutcome:
    messages = [
        {"role": "system", "content": JUDGE_SYSTEM + "\nReasoning before the JSON is allowed. The last value must be that object."},
        {
            "role": "user",
            "content": "ROLE: llm_judge\nSUBJECT:\n" + json.dumps(subject, ensure_ascii=False, default=str),
        },
    ]
    started = time.perf_counter()
    raw = llm.complete(messages)
    latency_ms = int((time.perf_counter() - started) * 1000)
    requested = str(getattr(llm, "requested_model", "") or "")
    served = getattr(llm, "served_model", None)
    try:
        payload = parse_model_json(raw, expected_keys=("decision", "reason", "claim_ids"))
    except InvestigationError:
        return LlmJudgeOutcome(False, "unparsed", "", [], raw, requested, served, latency_ms)
    decision = str(payload.get("decision") or "").strip().lower()
    if decision not in {"accept", "reject"}:
        return LlmJudgeOutcome(False, "unparsed", str(payload.get("reason") or ""), [], raw, requested, served, latency_ms)
    claim_ids = payload.get("claim_ids") or []
    if not isinstance(claim_ids, list):
        claim_ids = []
    return LlmJudgeOutcome(
        True,
        decision,
        str(payload.get("reason") or ""),
        [str(item) for item in claim_ids],
        raw,
        requested,
        served,
        latency_ms,
    )


def missing_evidence(critique: Critique) -> bool:
    text = critique.reason.lower()
    return (
        "no evidence" in text
        or "unknown evidence" in text
        or "cited evidence that is not" in text
        or "no resolved evidence" in text
    )


def combine(guard: Critique, outcome: LlmJudgeOutcome | None) -> tuple[Critique, str]:
    """Return the critique that wins, and a note when the evidence guard overrides an accept."""

    if outcome is None or not outcome.parsed:
        return guard, ""
    if guard.rejected:
        if outcome.decision == "accept" and missing_evidence(guard):
            note = "The evidence guard overrode an LLM accept."
            return Critique(True, f"{guard.reason} {note}", list(guard.claim_ids), guard.target_agent), note
        return guard, ""
    if outcome.decision == "reject":
        reason = outcome.reason or "LLM judge rejected the claim."
        claim_ids = outcome.claim_ids or list(guard.claim_ids)
        return Critique(True, reason, claim_ids, guard.target_agent), ""
    return guard, ""
