# PhoenixRepo pre-event plan

Locked 8 October 2026. Nothing here is a scored run.

PhoenixRepo does not assume abandoned software should be saved. It investigates whether it deserves to live again. Every decision must be traceable to evidence.

## Boundary

Before 22 October 2026, 00:00 UTC: prepare capability, do not execute the work.

Allowed: API and schemas, state-machine skeleton, Evidence Graph schema, adapter interfaces, Docker, logging, sample-cache shape, candidate URL and licence, known contradictions as notes, Zetaris and Meterless minimal-call notes.

Not allowed: a full resurrection run, a real evidence-ingestion path, a finished GO/NO-GO pipeline, a Builder to Validator loop, a pre-built demo.

First implementation commit after the build window opens wires Zetaris and Meterless into evidence ingestion and starts the real path. Git history should show that.

## Candidate, not a prepared answer

ai-teleprompter is the GO benchmark candidate, not a finished demo. If it is pre-existing work, declare it. Before the window, store only URL, licence, and development notes. Do not store Intent, Market, or Decision. Phoenix must reach GO by itself on 22 October.

NO-GO candidate: a commoditized repo, for example a generic QR generator. Unchosen.

- GO candidate URL:
- GO licence:
- NO-GO candidate URL:
- Known notes (not decisions):

## Six roles, two infrastructure pieces

Roles: Archaeologist/Intent, Intelligence/Judge, Architect, Builder, Validator, Diagnostician/Repairer.

Infrastructure: Evidence Graph, orchestrator/state machine. These are not agents.

Builder cannot write PASS.

## Three terminal states

- NO-GO: evidence says it should not be resurrected.
- RESURRECTION_FAILED: it was worth saving, but verification failed inside the budget.
- RESURRECTED: worth saving, rebuilt, and an independent prover showed it works.

## Sample mode

Sample mode changes the data source, not the pipeline. Cached evidence enters the same graph and the same gates. It must not return a stored final answer.

## Priority after the window opens

P0 compliance path. P1 Evidence Graph. P2 GO/NO-GO. P3 intent. P4 one real validation. P5 bounded repair. P6 UI.

A clear NO-GO outranks a resurrection that cannot prove itself.
