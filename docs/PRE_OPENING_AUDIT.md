# Pre-opening audit

Status: OPENING-READY for foundation only. Scored resurrection has not started.

## G1 Repository integrity

Tracked tree has no `.env`, API keys, or credentials. Runtime packs stay in `run_artifacts/` and are ignored. `.gitignore` covers `.env`, `__pycache__/`, `.pytest_cache/`, `run_artifacts/`, and `logs/*.jsonl`.

## G2 Hackathon compliance

| Requirement | Status |
| --- | --- |
| Zetaris boundary | INTERFACE_ONLY / NOT_YET_CONNECTED |
| Meterless boundary | INTERFACE_ONLY / NOT_YET_CONNECTED |
| POST /run | PASS |
| JSON input/output | PASS |
| 0.0.0.0 | PASS in `main.py` |
| JSONL logs | PASS |
| SAMPLE_MODE | PASS |
| Docker file | PASS |
| CPU-only | PASS |
| env secrets | PASS |

Do not mark the sponsors integrated.

## G3 Evidence foundation

Evidence, claim, relation, graph, provenance, and verification exist. No evidence means no verified claim. A verified claim is not a value decision. `decision` stays null.

## G4 Reproducibility

A run links `run_id` to `run-context.json`, `evidence-pack.json`, and the trace. Tampering the pack fails `verify_integrity`.

## G5 Security boundary

Sample mode uses synthetic evidence and does not require the network. Secrets stay in the environment. Trace metadata redacts secret-like fields.

## G6 Sponsor boundary

Zetaris and Meterless contracts exist. Real integration is not connected. Workshops are 12 and 14 October 2026. Scored development opens 22 October 2026 00:00 UTC.

## G7 Evaluator readiness

`pytest` covers `/health`, sample `/run`, live rejection, artifacts, and traces. Docker build was not executed in the audit environment. Run it locally before relying on the image.

## G8 Pre-opening restriction

Done: foundation, compliance, evidence graph, provenance, claim verification, audit, replay reload, integrity, security boundary, Dockerfile, evaluator contract, adapter contracts.

Not done: real repository ingestion, market research, GO/NO-GO, real sponsor calls, builder, validator, repair loop, resurrection, demo result.
