# Post-v3 repair run (flag on, not the official scoring record)

If the rerun is incomplete, this file must not be linked from the README.

## Statement

v3 (16 cases, 10 agree, 4 false passes) remains the official record and is not re-scored. This section is a rerun on the same 16 labelled cases with `PHOENIX_GATE1_EVIDENCE_REPAIR` on. The repair was designed on these same 16 cases, so results are in-sample and are not generalisation accuracy. Web search is live and varies between runs. The LLM cache was empty. The policy is `fallback-nvidia` (`PHOENIX_NEMOTRON_POLICY`). That names the policy. NVIDIA-direct success is recorded only when a trace shows it. No trace is stored in this file, so that result is TBD.

## Per-case

| case | human label | v3 verdict | repair verdict | changed vs v3 | run hash | verify_trace exit code | Requested / Served / Policy |
| --- | --- | --- | --- | --- | --- | --- | --- |
| TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |

## Totals

Agree, over-refusals, and false passes use the v3 definitions in [v3 eval](../README.md#v3-eval-scored-regression-and-audit-set-not-a-generalisable-accuracy-benchmark) and in the summary table under [Before and after](../README.md#before-and-after-the-same-16-repositories). `NEEDS_REVIEW` and `UNJUDGED` use the definitions in [Gate 1](GATES.md#gate-1-is-the-idea-worth-rescuing).

| Measure | Result |
| --- | --- |
| agree n/16 | TBD |
| over-refusals | TBD |
| false passes | TBD |
| NEEDS_REVIEW | TBD |
| UNJUDGED | TBD |

### Cases the repair made worse (previously correct verdict now wrong)

- TBD

## Reproduction command

TBD

## Environment variable names

- `PHOENIX_GATE1_EVIDENCE_REPAIR`
- `PHOENIX_NEMOTRON_POLICY`

`PHOENIX_NEMOTRON_POLICY` is `fallback-nvidia` (the policy). Strict-substitution stays off. Rule B stays off.

## Verification

```bash
python scripts/verify_trace.py <trace> --require-nemotron --expected-run-hash <hash>
```

This verifies the stored record and model identity, not verdict correctness or rerun consistency.
