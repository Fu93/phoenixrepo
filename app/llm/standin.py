"""Deterministic stand-in used only when SAMPLE_MODE or mode=sample is in effect.

It is not a saved answer. It reads the evidence JSON in the prompt and applies
app.agents.policy. The first archaeologist pass and the first builder pass are
deliberately unsupported so the validator's critique loop has something to reject.
"""

from __future__ import annotations

from app.agents.policy import decide, explanation
from app.llm.parsing import prompt_field, prompt_json_block


class SampleStandIn:
    provider_name = "sample-standin"
    model_name = "deterministic-rule-standin"
    sample_standin = True

    def complete(self, messages: list[dict[str, str]], *, model: str | None = None) -> str:
        user = messages[-1]["content"]
        role = prompt_field(user, "ROLE")
        attempt = int(prompt_field(user, "ATTEMPT") or "1")
        payload = prompt_json_block(user)
        if role == "archaeologist":
            return _json(self._archaeologist(payload, attempt))
        if role == "market_judge":
            return _json(self._judge(payload))
        if role == "architect":
            return _json(self._architect(payload))
        if role == "builder":
            return _json(self._builder(payload, attempt))
        if role == "diagnostician":
            return _json(self._diagnostician(payload))
        if role == "planner":
            return _json(payload["deterministic_prd"])
        if role == "equivalence":
            return _json({"capabilities": [], "mappings": [], "method": "phrase/keyword"})
        raise ValueError(f"Sample stand-in has no role {role}.")

    def _archaeologist(self, payload: dict, attempt: int) -> dict:
        row = payload["row"]
        evidence_id = payload["profile_evidence_id"]
        claims = [
            {
                "id": "CLM-LICENCE",
                "statement": f"The recorded licence is {row['licence_spdx']}.",
                "evidence_ids": [evidence_id],
                "confidence": 0.9,
            },
            {
                "id": "CLM-ABANDONMENT",
                "statement": f"days_since_push is {row['days_since_push']}.",
                "evidence_ids": [evidence_id],
                "confidence": 0.9,
            },
            {
                "id": "CLM-UNIQUENESS",
                "statement": (
                    f"similar_repo_count is {row['similar_repo_count']} and unique_capability is "
                    f"{row['unique_capability'] or 'empty'}."
                ),
                "evidence_ids": [evidence_id],
                "confidence": 0.9,
            },
        ]
        if attempt == 1:
            claims.append(
                {
                    "id": "CLM-DEMAND",
                    "statement": "Independent demand signals show a large waiting user base.",
                    "evidence_ids": [],
                    "confidence": 0.2,
                }
            )
        return {"claims": claims}

    def _judge(self, payload: dict) -> dict:
        row = payload["row"]
        decision, reason = decide(row, payload["signals"])
        return {
            "decision": decision,
            "explanation": explanation(row, decision, reason),
            "evidence_ids": payload["evidence_ids"],
            "memory_ids": [item["id"] for item in payload.get("memories", [])],
        }

    def _architect(self, payload: dict) -> dict:
        row = payload["row"]
        return {
            "contract": (
                f"Revive {row['repo_id']} only if the cited rows still show {row['licence_spdx']}, "
                f"a distinct capability, tests, and ci_status success."
            ),
            "evidence_ids": [payload["profile_evidence_id"]],
        }

    def _builder(self, payload: dict, attempt: int) -> dict:
        if attempt == 1:
            return {
                "verification_result": "PASS",
                "steps": ["VALIDATION_PASS without a cited row"],
                "evidence_ids": [],
            }
        return {
            "steps": [
                f"Record policy result {payload['decision']} for {payload['row']['repo_id']} from the cited rows."
            ],
            "evidence_ids": payload["evidence_ids"],
        }

    def _diagnostician(self, payload: dict) -> dict:
        return {
            "action": "re-query phoenix.revival_signals",
            "reason": payload["validation_reason"],
            "evidence_ids": payload["evidence_ids"],
        }


def _json(payload: dict) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False)
