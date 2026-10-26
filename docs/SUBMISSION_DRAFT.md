# Submission draft: Open Agent Hackathon 2026, Track 02 "The Agent That Can Explain Why"

Solo entry, team PhoenixRepo. Repository: `Fu93/phoenixrepo`. Deadline: **27 Oct 2026 23:45 UTC**. Plan: first version submitted by **12:00 UTC**, final by **21:00 UTC**. Video: 1 to 4 minutes. The shot list is cut for 3:00 in `docs/VIDEO_SCRIPT.md`. The HackOS clicks are in `docs/HACKOS_CHECKLIST.md`. Everything submitted is in English.

Rules for this draft: a measured number is written by `scripts/fill_results.py` from the live eval file named below. Anything that file does not yet contain is a numbers slot showing the visible word **TBD**. This draft does not claim that an AI-written patch has fixed a real repository. It does not claim that all 16 evaluation repositories went through Zetaris.

Rubric: Impact 30, Technical 20, Innovation 15, Demo 15, Product & UX 10, Sponsor 10. Separate in-kind prize: Best use of NVIDIA Nemotron.

## State ready to paste (measured numbers still open)

The scored table is one live run. Produce it on the machine that holds the Nightshift token:

```bash
python scripts/fill_results.py logs/eval-nemotron-v3.json --write
```

A live run on one machine, not a stored accuracy.

<!-- BEGIN numbers: eval-summary-table -->
**TBD.**
<!-- END numbers: eval-summary-table -->

What is already fixed in the repository, with no live count attached:

- Gate 1 has two Nemotron tiers. `MODEL_NAME` with `PHOENIX_LLM_EXTRA_BODY` (thinking off) does the bulk overlap checks. `MODEL_NAME_DEEP` with `PHOENIX_LLM_DEEP_EXTRA_BODY` writes the final verdict and keeps its reasoning.
- `PHOENIX_NEMOTRON_POLICY` is `label`, `require`, or `fallback-nvidia`. The prize path described here is `fallback-nvidia`: Nightshift is tried first; on a timeout, an HTTP 5xx, or a non-Nemotron answer, the same request goes to NVIDIA's official API and the trace records provider `nvidia-direct`.
- `served_model` is inside the hash chain. `scripts/verify_trace.py` recomputes that chain and checks `--expected-run-hash`. With `--require-nemotron` it also fails unless every used answer was served by `nvidia/nemotron-*`, and prints the provider split. The family check is the policy, the `/ui` badge "N of M answers by NVIDIA Nemotron", and the Nemotron reasoning card.
- A blocker needs a verified verbatim quote plus a mature or official alternative. Infrastructure failures are `NEEDS_REVIEW` or `UNJUDGED`, never a silent pass. `BUDGET_EXHAUSTED` can never produce `CANDIDATE`.
- Gate 1 can read its evidence from Zetaris (`EVIDENCE_SOURCE=zetaris`). In this submission Zetaris was exercised live, read-only, on the 2 repositories in `data/real`: `tharani-2006/Invoice-generator-application` and `SergioMendolia/ytkb`. `scripts/zetaris_check.py` loads their evidence rows and seals a trace; it calls no model and writes no verdict. The 16 evaluation repositories were fetched live with `evidence_source=csv`. `scripts/zetaris_check.py` sends read-only `SELECT`s and the trace carries a run hash.
- `DSRegoitia/CalendarSyncProject` was rejected by the older Phoenix because FullCalendar "displays public Google Calendar events". The human label calls that a WRONG over-rejection: the candidate pushes events the other way. The acceptable set is `CANDIDATE` or `NEEDS_REVIEW`. Two independent runs, with different generated queries, returned `CANDIDATE` and `NEEDS_REVIEW`. `NEEDS_REVIEW` on this case is a partial overlap with a verbatim quote handed to a human.

<!-- BEGIN numbers: nemotron-answer-share -->
Answers by NVIDIA Nemotron on the scored run: **TBD** of **TBD**.
<!-- END numbers: nemotron-answer-share -->

<!-- BEGIN numbers: provider-split -->
Provider split, Nightshift answers used / `nvidia-direct` answers used (`verify_trace.py --require-nemotron`): **TBD** / **TBD**. `nvidia-direct` requests `nvidia/nemotron-3-ultra-550b-a55b`, because the API answered 404 for the 120B Super model on this account; the served model per provider is counted from the traces.
<!-- END numbers: provider-split -->

<!-- BEGIN numbers: calendarsync-final-verdict -->
CalendarSyncProject verdict on the recorded run: **TBD**.
<!-- END numbers: calendarsync-final-verdict -->

<!-- BEGIN numbers: calendarsync-final-run-hash -->
CalendarSyncProject run hash: **TBD**.
<!-- END numbers: calendarsync-final-run-hash -->

The 25 Oct 2026 batch of 30 repositories, described in the README under Current status, is an earlier run. It is not this table.

## (i) Video script, 3:00

Full narration, timings, and on-screen directions: `docs/VIDEO_SCRIPT.md`. Record from `/ui`, the static demo at <https://fu93.github.io/phoenixrepo/>, and a terminal. Real output only. Say **TBD** for the badge and the provider split until the markers above are filled.

The draft does not repeat the shot list, so the two cannot disagree.

## (ii) Project description for the HackOS form (about 300 words)

Agents that revive abandoned GitHub repositories start by patching code. Most of those repositories should not come back: someone already ships the same idea, better maintained. PhoenixRepo asks that question first, and every answer carries evidence a reviewer can check.

Gate 1 asks whether the idea is worth rescuing. It reads the repository, searches GitHub, npm and the web, and refuses only when a mature or official alternative does the same job and the quote it relies on appears word for word on that alternative's own page. A failed search, a model outage or a search cut short by its budget is NEEDS_REVIEW or UNJUDGED, never a silent pass. Gate 2, the revive loop, runs only after gate 1 says CANDIDATE, and accepts a result only when the repository's own tests pass and an independent check finds no weakened test.

The fix that matters most is fewer wrong refusals. CalendarSyncProject was once refused because FullCalendar "displays public Google Calendar events"; a human audit says that is the opposite direction. The current gate 1 no longer refuses it.

Gate 1's judgements are asked of NVIDIA Nemotron in two tiers: a fast tier for the many overlap checks and a reasoning tier for the verdict, whose reasoning is kept next to the quote. Meterless Nightshift is tried first, and NVIDIA's own API answers when Nightshift fails. On the scored run, Nemotron wrote **TBD** of the **TBD** answers used.

Every step is a line in a hash-chained trace that records the model that actually answered; one command verifies the chain and that every used answer was Nemotron. Gate 1 can load its evidence from Zetaris over SQL; this submission ran gate 1 that way, read-only, on two repositories, with every answer verified as Nemotron's (one of the two verdicts disagrees with the human label, and the README says so). The 16 scored evaluation repositories were fetched live from GitHub instead.

Limits: no AI-written patch has fixed a real repository yet, and the code was written with AI assistance.

## (iii) "Built before the event" declaration

- **Pre-event skeleton:** everything committed up to and including `62a1540` (`chore: complete pre-opening foundation audit`, 2026-10-08 18:27:02 +0000).
- **Contents:** the FastAPI process, the Docker image, the evidence graph and pack, the integrity hash, the state machine with the three terminal names, the audit trail, secret redaction, and `FoundationOrchestrator`, which stops at `EVIDENCE_COMPLETE` with `decision: null` and calls no model.
- **Build window:** 22 to 27 Oct 2026. Everything after `62a1540` was built in it: the Zetaris REST client and gate-1 Zetaris evidence; the Meterless Runtime provider; H-MEM and Markovian; the roles and validator; gate 1 (`scripts/judge_repo.py`, `scripts/score_candidates.py`, `app/agents/relevance.py`, multi-source prior-art search); the two Nemotron tiers and `fallback-nvidia`; the batch and rerun tools; gate 2 (`app/revive_loop.py`, `app/revive_verify.py`, `scripts/revive.py`); the evidence report, trace renderer, and `/ui` replay page; the tests and this README.

<!-- BEGIN numbers: build-window-commit-count -->
Commit count after the skeleton, on the final submitted commit: **TBD**. Command: `git rev-list --count 62a1540..main`. Paste that integer here when `main` is the commit you submit. Do not reuse a count from an earlier commit.
<!-- END numbers: build-window-commit-count -->

## (iv) AI-assistance disclosure

- Most of the code was written with AI coding assistance: Cursor cloud coding agents and Claude (Anthropic) working under the author's direction. The author (solo entrant) set the design and rules, reviewed and merged the changes, and ran the live checks on a Windows machine.
- Gate 1 and gate 2 call models through Meterless Nightshift and record `requested_model` and `served_model` on every LLM trace line. Under `PHOENIX_NEMOTRON_POLICY=fallback-nvidia`, gate 1 sends the same request to NVIDIA's official API after a Nightshift timeout, HTTP 5xx, or non-Nemotron answer, and traces it as provider `nvidia-direct`. Exa and Keenable are keyless web search sources, not AI providers.
- This draft, the video script, and the HackOS checklist were written with AI assistance and checked against the repository.

## (vi) Sponsor technology explanation (HackOS form)

**Meterless Nightshift (Runtime).** The model route for gate 1 and gate 2, tried first on every call (OpenAI-compatible chat with `meterless-app` and `meterless-scope` headers). Each `LLM_CALL` trace line stores the requested model, the model Nightshift served, latency and Nightshift's routing detail, inside the hash chain. `scripts/nightshift_probe.py` measures which Nemotron ids and settings Nightshift actually serves.

**NVIDIA Nemotron through Nightshift.** Problem extraction, every overlap check and the final verdict are Nemotron answers in two tiers: a fast model for the many short checks and a reasoning model for the verdict, whose reasoning is kept next to the quote it must agree with. `PHOENIX_NEMOTRON_POLICY=fallback-nvidia` sends a call to NVIDIA's API only when Nightshift fails, and traces it as `nvidia-direct`. Proof: `python scripts/verify_trace.py <trace> --require-nemotron`; measured share and provider split in the README.

**Meterless Engines: H-MEM and Markovian.** Implemented from the published specs in `app/engines/`, used by the evidence-policy track (`POST /run`), not by gate 1. H-MEM stores evidence claims and verdicts, recalls them into the judge step, promotes verdicts to long-term memory and supersedes without deleting. Markovian runs the investigation as a chunk loop with a bounded carryover (1200 characters, 24 chunks). Tests: `tests/engines/test_hmem_conformance.py` (store, ranking formula, promotion, decay, supersede-without-delete) and `tests/engines/test_markovian.py`, run in CI on every pull request. They are project tests modelled on the spec; the official TypeScript conformance suite was not run.

**Zetaris.** Gate 1 reads its evidence views over the REST SQL contract with `EVIDENCE_SOURCE=zetaris`; each statement is a trace line with its SQL, `query_id` and `content_hash`, and a read-only preflight stops the run if Zetaris does not answer. Exercised live on the 2 `data/real` repositories (evidence load, and one gate-1 run); the 16 scored repositories used `evidence_source=csv`.

## (v) Remaining steps, in order

See `docs/RUNBOOK_WINDOWS.md` for the Windows commands. Pull requests #17, #18, and the Nemotron, `/ui`, and eval-summary work through #24 are already on `main`.

- [ ] On the Windows machine: run `scripts/nightshift_probe.py`. Put the printed `MODEL_NAME`, `MODEL_NAME_DEEP`, and `PHOENIX_LLM_*` lines into `.env`. Set `PHOENIX_NEMOTRON_POLICY=fallback-nvidia` and `NVIDIA_API_KEY` for the prize run.
- [ ] `python scripts/zetaris_check.py --snapshot data/real` with Zetaris running. Read-only. Keep the printed run hash. The command covers the snapshot's discovery rows, which are the two repositories named above, not the 16 evaluation repositories.
- [ ] Live eval, one commit for all 16 cases, new file (no `--resume` from a run of older code): `python scripts/eval_gate1.py --audit-only --deadline <seconds to 08:00 UTC> --out logs/eval-nemotron-v3.json`. If it is cut short, continue with `--resume logs/eval-nemotron-v3.json` on the same commit.
- [ ] Optional, with Zetaris running and nothing else judging: `python scripts/score_candidates.py --snapshot data/real --llm --output-json logs/zetaris-gate1.json --output-md logs/zetaris-gate1.md`. Its verdicts are the only gate-1 verdicts that read Zetaris; fill `zetaris-gate1-run` in the README from it, or delete that paragraph.
- [ ] For each scored trace: `python scripts/verify_trace.py logs/trace-<run_id>.jsonl --require-nemotron`. Its `nemotron:` line is the provider split.
- [ ] `python scripts/fill_results.py logs/eval-nemotron-v3.json --write` and commit. Check the printed warnings: a missing or failing trace stays TBD.
- [ ] Optional: `python scripts/import_replay.py` for one real trace so `/ui` can show RECORDED. Until that import, say SAMPLE on camera.
- [ ] **12:00 UTC 27 Oct: submit the first version** (Track 02, description, repository link, declarations). The video may follow.
- [ ] Record `docs/VIDEO_SCRIPT.md`. Check that no token or `.env` is visible, and that the Zetaris sentence names only the two `data/real` repositories.
- [ ] Final commit: `python -m ruff check .`, `python -m pytest -q`, `python scripts/compliance_check.py`. Paste the build-window commit count.
- [ ] **21:00 UTC: final submission**, with the video.
- [ ] Hard deadline **23:45 UTC**.
