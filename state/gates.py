from dataclasses import dataclass


@dataclass
class GateResult:
    passed: bool
    reason: str
    evidence_ids: list[str]


class GateError(Exception):
    pass


def require_evidence(*, gate_name: str, evidence_ids: list[str]) -> GateResult:
    if not evidence_ids:
        raise GateError(f"Gate '{gate_name}' requires evidence.")
    return GateResult(
        passed=True,
        reason=f"Gate '{gate_name}' satisfied.",
        evidence_ids=evidence_ids,
    )
