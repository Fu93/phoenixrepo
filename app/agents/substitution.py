"""Opt-in quoted substitution contract. Quotes prove provenance, not semantics.

The model still decides whether the user, operation, direction, and deployment
match. Code requires explicit booleans, two source quotes, and a concrete product.
No hostname or case-name rule decides whether two products are substitutes.
"""
from __future__ import annotations

import os
from typing import Any, Callable

FLAG = "PHOENIX_STRICT_SUBSTITUTION"
AXES = ("same_user", "same_operation", "same_direction", "same_deployment")

INSTRUCTION = """

QUOTED SUBSTITUTION CONTRACT (experimental strict mode):
For every same_problem result, also return a substitution object:
{"candidate_quote": "verbatim from Candidate Source Text", "alternative_quote": "verbatim from THIS alternative",
 "concrete_product": true, "same_user": true, "same_operation": true, "same_direction": true, "same_deployment": true}.
Each boolean must be true or false, never a string. Use null when not established.
Full overlap requires an identifiable concrete product and all four context matches.
A directory listing several providers is a discovery lead, not one proven substitute.
A hosted service is not automatically a self-hosted server. Reading/importing events
does not prove writing/exporting them. General advice that anyone can build something
does not prove a maintained implementation. Do not invent source quotes or facts.
If a core context differs, use partial overlap. If evidence is missing, keep the
uncertainty explicit instead of asserting a match. Do not return stars or licences.
"""


def enabled() -> bool:
    return os.environ.get(FLAG, "").strip().casefold() in {"1", "true", "yes", "on", "enabled"}


def source_text(row: dict[str, Any]) -> str:
    return "\n".join(str(row.get(k) or "") for k in ("excerpt", "description", "text", "readme"))


def assess(proof: Any, candidate_text: str, alternative_text: str, quote_check: Callable[[str, str], bool]) -> str:
    if not isinstance(proof, dict):
        return "unverified"
    for key, text in (("candidate_quote", candidate_text), ("alternative_quote", alternative_text)):
        quote = proof.get(key)
        if not isinstance(quote, str) or not quote.strip() or not text.strip() or not quote_check(quote, text):
            return "unverified"
    if any(type(proof.get(key)) is not bool for key in (*AXES, "concrete_product")):
        return "unverified"
    if not proof["concrete_product"]:
        return "unverified"  # A directory is not proof of absence or of a substitute.
    return "quoted_match" if all(proof[key] for key in AXES) else "quoted_mismatch"


def contract_fields(entry: dict[str, Any], problem: dict[str, Any], alternative_text: str, quote_check) -> dict[str, Any]:
    if not enabled():
        return {}
    proof = entry.get("substitution")
    if not isinstance(proof, dict):
        proof = {}
    # Preserve only the actual contract, not arbitrary model-added keys.
    kept = {key: proof.get(key) for key in ("candidate_quote", "alternative_quote", "concrete_product", *AXES)}
    return {"substitution": kept, "substitution_status": assess(kept, str(problem.get("candidate_evidence") or ""), alternative_text, quote_check)}
