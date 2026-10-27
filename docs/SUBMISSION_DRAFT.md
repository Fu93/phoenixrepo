# Submission draft: Open Agent Hackathon 2026, Track 02 "The Agent That Can Explain Why"

Solo entry, team PhoenixRepo. Repository: `Fu93/phoenixrepo`. Deadline: **27 Oct 2026 23:45 UTC**. Plan: first version submitted by **12:00 UTC**, final by **21:00 UTC**. Video: 1 to 4 minutes. The shot list is cut for 3:00 in `docs/VIDEO_SCRIPT.md`. The HackOS clicks are in `docs/HACKOS_CHECKLIST.md`. Everything submitted is in English.

Rules for this draft: a measured number is written by `scripts/fill_results.py` from the live eval file named below. A slot that file cannot fill stays the visible word TBD. The slots in this draft are filled. The build-window commit count is the count on the commit where that script last wrote it; run the script again on the final commit if the count must match HEAD. This draft does not claim that an AI-written patch has fixed a real repository. It does not claim that all 16 evaluation repositories went through Zetaris.

Rubric: Impact 30, Technical 20, Innovation 15, Demo 15, Product & UX 10, Sponsor 10. Separate in-kind prize: Best use of NVIDIA Nemotron.

## State ready to paste

The scored table is one live run. The numbers below were written by:

```bash
python scripts/fill_results.py logs/eval-nemotron-v3.json --write
```

A live run on one machine, not a stored accuracy.

<!-- BEGIN numbers: eval-summary-table -->
| Measure | Result |
| --- | --- |
| Labelled repositories judged | 16 of 16 in the report |
| Verdict inside the human label's acceptable set | 10 of 16 |
| Over-refusals (ALREADY_EXISTS where the label says it should not be) | 1 of 11 |
| False passes (not refused where the label says ALREADY_EXISTS) | 4 of 5 |
| Model answers used that NVIDIA Nemotron wrote | 218 of 218 |
| Served models (all LLM call lines) | `(missing)` 3 (failed or discarded calls with no answer, not answers used), `nvidia/nemotron-3-ultra-550b-a55b` 197, `nvidia/nemotron-3.5-lightning-30b-a3b` 3, `nvidia/nemotron-3.5-super-vl-120b-a12b` 18 |

Source: `logs/eval-nemotron-v3.json`, summarised by `scripts/eval_summary.py`. A live run on one machine, not a stored accuracy.
<!-- END numbers: eval-summary-table -->

What is already fixed in the repository, with no live count attached:

- Gate 1 keeps three facts separate. Requested model id: two tiers, `MODEL_NAME` (Super) for bulk overlap checks and `MODEL_NAME_DEEP` (Ultra) for the final verdict. Served model id is `served_model` on the trace line, which can differ from the id requested. Policy is `PHOENIX_NEMOTRON_POLICY`, in the next bullet; it is not either model id. The scored v3 traces do not record temperature or thinking mode; neither is inferred from code defaults or later runs. The `nemotron-evidence` slot says fast-tier answers used were Ultra 174 and Super-VL 18; the 16 verdicts were served `nvidia/nemotron-3-ultra-550b-a55b`. The same slot's no-tier row is Ultra 7 and Lightning 3. That slot is not a provider split. The scored traces recorded no reasoning text for the final verdict. The counter that wrote them recognised only a separate reasoning field or a complete `<think>…</think>` pair (0 of 16 in that slot), and Nightshift's answers carry reasoning inline before a bare `</think>`, so the scored traces cannot say whether the model reasoned.
- `PHOENIX_NEMOTRON_POLICY` is `label`, `require`, or `fallback-nvidia`. The prize path described here is `fallback-nvidia`: Nightshift is tried first; on a timeout, an HTTP 5xx, or a non-Nemotron answer, the same request goes to NVIDIA's official API and the trace records provider `nvidia-direct`.
- `served_model` is inside the hash chain. `scripts/verify_trace.py` recomputes that chain. An in-place edit fails. A re-sealed file verifies unless `--expected-run-hash` is passed with a hash published outside the file. With `--require-nemotron` it also fails unless every used answer was served by `nvidia/nemotron-*`, and prints the provider split. `/ui` shows the badge "N of M answers by NVIDIA Nemotron". A reasoning field is shown only when the trace line contains one. An empty field on a scored trace is what the counter stored, not proof that none was produced. An on-screen card is headed "Reasoning field on this trace line". It is shown for the two imported Experiment E3 replays, both titled `Experiment E3 (NVIDIA direct; final verdict only: thinking on, temperature 1.0; bulk steps thinking off, temperature 0), not the scored run` (`data/replays/e3-goodreasonai-scrapeserv/` and `data/replays/e3-dsregoitia-calendarsyncproject/`). Those are not the scored run. None of the 17 scored/Zetaris RECORDED traces carries that field.
- A blocker needs a quote found as a substring of the alternative's text, plus a mature or official alternative. The substring check has no minimum length. A failure of the overlap judge is `NEEDS_REVIEW` or `UNJUDGED`. A degraded step flagged `LLM_FALLBACK` can still be `CANDIDATE` (ytkb on the Zetaris-evidence run). `BUDGET_EXHAUSTED` can never produce `CANDIDATE`.
- Gate 1 can read its evidence from Zetaris (`EVIDENCE_SOURCE=zetaris`). In this submission Zetaris was exercised live, read-only, on the 2 repositories in `data/real`: `tharani-2006/Invoice-generator-application` and `SergioMendolia/ytkb`. `scripts/zetaris_check.py` loads their evidence rows and seals a trace; it calls no model and writes no verdict. The 16 evaluation repositories were fetched live with `evidence_source=csv`. `scripts/zetaris_check.py` sends read-only `SELECT`s and the trace carries a run hash.
- `DSRegoitia/CalendarSyncProject` was rejected by the older Phoenix because FullCalendar "displays public Google Calendar events". The human label calls that a WRONG over-rejection: the candidate pushes events the other way. The acceptable set is `CANDIDATE` or `NEEDS_REVIEW`. Three runs with different generated queries returned `CANDIDATE`, `NEEDS_REVIEW` (a partial overlap, quote handed to a human) and, on the scored run, `NEEDS_PRIOR_ART` (the search checked zero alternatives). None refused it; the scored table counts the last as a disagreement.

<!-- BEGIN numbers: nemotron-answer-share -->
Answers by NVIDIA Nemotron on the scored run: **218** of **218** answers used (16 of 16 case traces verified, `logs/eval-nemotron-v3.json`). The final-verdict answer (the `discovery` step) was written by Nemotron in **16** of the **16** cases whose trace has one.
<!-- END numbers: nemotron-answer-share -->

<!-- BEGIN numbers: provider-split -->
Provider split on that run, from `verify_trace.py --require-nemotron` over each case trace: 50 explicit nvidia-direct; 158 `LLM_CALL` lines with no provider field = Nightshift route inferred from absence of the marker; 10 `LLM_SCORE` lines with no provider record. 1 of the used answer was replayed from the LLM cache: an earlier run asked the same prompt with the same input, and the cached line keeps the model and provider that answered then. Served model per provider: nightshift: `nvidia/nemotron-3-ultra-550b-a55b` 147, `nvidia/nemotron-3.5-lightning-30b-a3b` 3, `nvidia/nemotron-3.5-super-vl-120b-a12b` 18; nvidia-direct: `nvidia/nemotron-3-ultra-550b-a55b` 50.
<!-- END numbers: provider-split -->

<!-- BEGIN numbers: calendarsync-final-verdict -->
Final verdict on the recorded run (`judge-20261026202603`): **NEEDS_PRIOR_ART** (disagrees with the human label). Reason: Zero alternatives checked in prior-art search.
<!-- END numbers: calendarsync-final-verdict -->

<!-- BEGIN numbers: calendarsync-final-run-hash -->
Final run hash: `2eb5a1e3157e3f5a148ede12fc06d4ca6f4e7a8a7262d7f2c0a5326569a56cd0`. The committed copy is `data/replays/dsregoitia-calendarsyncproject/trace.jsonl`. Check it with `python scripts/verify_trace.py data/replays/dsregoitia-calendarsyncproject/trace.jsonl --expected-run-hash 2eb5a1e3157e3f5a148ede12fc06d4ca6f4e7a8a7262d7f2c0a5326569a56cd0 --require-nemotron`. `logs/trace-judge-20261026202603.jsonl` is gitignored and is not in this checkout.
<!-- END numbers: calendarsync-final-run-hash -->

The 25 Oct 2026 batch of 30 repositories, described in the README under Current status, is an earlier run. It is not this table.

## (i) Video script, 3:00

Full narration, timings, and on-screen directions: `docs/VIDEO_SCRIPT.md`. Record from `/ui`, the static demo at <https://fu93.github.io/phoenixrepo/>, and a terminal. Real output only. Read the badge and the provider split from the markers above: 218 of 218 answers by Nemotron; 50 explicit nvidia-direct; 158 `LLM_CALL` lines with no provider field = Nightshift route inferred from absence of the marker; 10 `LLM_SCORE` lines with no provider record.

The draft does not repeat the shot list, so the two cannot disagree.

## (ii) Project description for the HackOS form (678 words)

Agents that revive abandoned GitHub repositories start by patching code. Most of those repositories should not come back: someone already ships the same idea, better maintained. PhoenixRepo asks that question first, and every answer carries evidence a reviewer can check.

Gate 1 asks whether the idea is worth rescuing. It reads the repository, searches GitHub, npm and the web, and refuses only when a mature or official alternative does the same job and the quote it relies on appears word for word on that alternative's own page. A failed search or a search cut short by its budget is NEEDS_REVIEW or UNJUDGED. A degraded step flagged LLM_FALLBACK can still be CANDIDATE; the README names ytkb on the Zetaris-evidence run. Gate 2 runs only after gate 1 says CANDIDATE. It first writes a rescue plan: who it is for, the gap it can own against the alternatives it must beat, and the work in three stages (revive, optimise, upgrade), each item tied to evidence gate 1 recorded. Gate 2 part 1 only: `docs/rescue/trace-rescue-0xmh__fichero-printer-20261027T001117Z.jsonl` (fichero-printer, not the scored run) has requested model id `nvidia/nemotron-3-ultra-550b-a55b`, served model id `nvidia/nemotron-3-ultra-550b-a55b`, and `provider: nvidia-direct` with `fallback_reason` `nightshift HTTP 502` (policy `PHOENIX_NEMOTRON_POLICY=fallback-nvidia`, not stored as its own field). Verified execution has never run on a real CANDIDATE. The quote check proves a quote occurs in the cited text, not that it supports the item. Where the repository has a test command, the loop patches and accepts a result only when those tests pass and an independent check finds no weakened test.

The fix that matters most is fewer wrong refusals. CalendarSyncProject was once refused because FullCalendar "displays public Google Calendar events"; a human audit says that is the opposite direction. The current gate 1 has not refused it in three runs.

Gate 1's judgements are three separate facts. Requested model id: two tiers (Super for bulk checks, Ultra for the verdict). Served model id is the trace field `served_model`, not the requested id. Policy is `PHOENIX_NEMOTRON_POLICY=fallback-nvidia` on the path described here: Nightshift is tried first, and a failed Nightshift call is traced `provider: nvidia-direct` (a committed example is `data/replays/dsregoitia-calendarsyncproject/trace.jsonl`, `alternative_batch`, `fallback_reason` `nightshift HTTP 502`). The 50 nvidia-direct answers in the sentence below are the provider-split slot, not that one file. The scored v3 traces do not record temperature or thinking mode; neither is inferred from code defaults or later runs. The nemotron-evidence slot says fast-tier answers used were Ultra 174 and Super-VL 18; the 16 verdicts were served `nvidia/nemotron-3-ultra-550b-a55b`. The same slot's no-tier row is Ultra 7 and Lightning 3, which is why the provider split's Ultra count is 197 rather than 190. The scored traces recorded no reasoning text for the final verdict. The counter that wrote them recognised only a separate reasoning field or a complete think pair (0 of 16 in that slot), and Nightshift's answers carry reasoning inline before a bare closing think tag, so the scored traces cannot say whether the model reasoned. Meterless Nightshift is tried first, and NVIDIA's own API answers when Nightshift fails. On the scored run, Nemotron wrote 218 of the 218 answers used (50 explicit nvidia-direct; 158 `LLM_CALL` lines with no provider field = Nightshift route inferred from absence of the marker; 10 `LLM_SCORE` lines with no provider record; 1 cached; 53 fallback calls, 50 answered and 3 failed, all 50 answered calls after HTTP 502).

Every step is a line in a hash-chained trace; one command verifies the chain and that every model answer used was Nemotron. Gate 1 can load its evidence from Zetaris over SQL; the evidence queries on this run are read-only SELECTs, on two repositories. The 27 model answers used were all Nemotron-served. One analysis step (ytkb venture_analyst) fell back to a deterministic baseline after a Nightshift 502, which is flagged LLM_FALLBACK in the trace (one of the two verdicts disagrees with the human label, and the README says so). The 16 scored evaluation repositories were fetched live from GitHub instead.

Limits: no AI-written patch has fixed a real repository yet, and the code was written with AI assistance.

## (iii) "Built before the event" declaration

- **Pre-event skeleton:** `git log` shows 17 commits on 2026-10-08 and none from 9 Oct through 21 Oct. They run from `54d52c1` (`chore: initialize PhoenixRepo foundation`, 2026-10-08 17:42:19 +0000) through `62a1540` (`chore: complete pre-opening foundation audit`, 2026-10-08 18:27:02 +0000). The subjects are: initialize PhoenixRepo foundation; compliance foundation without scored flow; evidence-gated state machine without repo analysis; synthetic evidence graph engine; evidence provenance and verification; auditable state transitions; sample foundation pipeline without a decision; verify claims without making a repository decision; persist replayable evidence packs; attach every run to a reproducible context; compliance harness for the foundation pipeline; lock sponsor adapters to interface-only contracts; evaluator smoke check without a decision; freeze foundation schema 1.0; lock sample mode away from secrets and network; rehearse evaluator sample runs without docker here; complete pre-opening foundation audit. The build window in the rules quoted below is 22 Oct 00:00 UTC to 27 Oct 23:45 UTC. The next commit is `7f01da0` at 2026-10-22 13:13:06 +0000.
- **Contents:** the FastAPI process, the Docker image, the evidence graph and pack, the integrity hash, the state machine with the three terminal names, the audit trail, secret redaction, and `FoundationOrchestrator`, which stops at `EVIDENCE_COMPLETE` with `decision: null` and calls no model.
- **Build window:** 22 to 27 Oct 2026. Everything after `62a1540` was built in it: the Zetaris REST client and gate-1 Zetaris evidence; the Meterless Runtime provider; H-MEM and Markovian; the roles and validator; gate 1 (`scripts/judge_repo.py`, `scripts/score_candidates.py`, `app/agents/relevance.py`, multi-source prior-art search); the two Nemotron tiers and `fallback-nvidia`; the batch and rerun tools; gate 2 (`app/revive_loop.py`, `app/revive_verify.py`, `scripts/revive.py`); the evidence report, trace renderer, and `/ui` replay page; the tests and this README.

<!-- BEGIN numbers: build-window-commit-count -->
Commit count after the skeleton, on the commit these numbers were written from: **124** (`git rev-list --count 62a1540..HEAD`). Run `scripts/fill_results.py` again on the final commit.
<!-- END numbers: build-window-commit-count -->

## (iv) AI-assistance disclosure

- Most of the code was written with AI coding assistance: Cursor cloud coding agents and Claude (Anthropic) working under the author's direction. The author (solo entrant) set the design and rules, reviewed and merged the changes, and ran the live checks on a Windows machine.
- Gate 1 and gate 2 call models through Meterless Nightshift and record `requested_model` and `served_model` on every LLM trace line. Under `PHOENIX_NEMOTRON_POLICY=fallback-nvidia`, gate 1 sends the same request to NVIDIA's official API after a Nightshift timeout, HTTP 5xx, or non-Nemotron answer, and traces it as provider `nvidia-direct`. Exa and Keenable are keyless web search sources, not AI providers.
- This draft, the video script, and the HackOS checklist were written with AI assistance and checked against the repository.

## (vi) Sponsor technology explanation (HackOS form)

**Meterless Nightshift (Runtime).** The model route for gate 1, tried first on every call, and the route gate 2 requests by design (gate 2 has not run on a real CANDIDATE here) (OpenAI-compatible chat with `meterless-app` and `meterless-scope` headers). Each `LLM_CALL` trace line stores the requested model, the model Nightshift served, latency and Nightshift's routing detail, inside the hash chain. `scripts/nightshift_probe.py` measures which Nemotron ids and settings Nightshift actually serves.

**NVIDIA Nemotron through Nightshift.** Three facts. Requested model id: problem extraction, every overlap check and the final verdict are asked in two tiers (Super for bulk checks, Ultra for the verdict). Served model id: on the scored run the nemotron-evidence slot says fast-tier answers used were Ultra 174 and Super-VL 18; the 16 verdicts were served `nvidia/nemotron-3-ultra-550b-a55b`. The scored traces recorded no reasoning text for the final verdict. The counter that wrote them recognised only a separate reasoning field or a complete think pair (0 of 16 in that slot), and Nightshift's answers carry reasoning inline before a bare closing think tag, so the scored traces cannot say whether the model reasoned. The quote next to the verdict is the check a reviewer can read. Policy: `PHOENIX_NEMOTRON_POLICY=fallback-nvidia` sends a call to NVIDIA's API only when Nightshift fails, and traces it as `provider: nvidia-direct`. A committed example is `data/replays/dsregoitia-calendarsyncproject/trace.jsonl` (`alternative_batch`, `fallback_reason` `nightshift HTTP 502`). Proof: `python scripts/verify_trace.py <trace> --require-nemotron --expected-run-hash <published hash>`; measured share and provider split in the README.

**Meterless Engines: H-MEM and Markovian.** Implemented in this repository from the published specs in `app/engines/`. On gate 1, H-MEM is the verdict memory (`app/verdict_memory.py`): `MemoryStoreService.add` writes each verdict as a long-term memory, `MemoryStoreService.find_verdicts` reads prior verdicts (not `MemoryRetrievalService`), `MemoryStoreService.supersede` keeps the previous one, and `TrustLedgerService` appends `conflict_detected` when they differ. The trace line is `HMEM_VERDICT_MEMORY` with `remembered_before`. It never changes a verdict; the scored v3 run predates the hook. The test is `tests/test_verdict_memory.py`. No trace file in this repository shows two live gate-1 runs of that line. In the evidence-policy track (`POST /run`) H-MEM stores evidence claims and verdicts, recalls them into the judge step, promotes verdicts to long-term memory and supersedes without deleting. Markovian, not on the gate-1 path, runs the investigation as a chunk loop with a bounded carryover (1200 characters, 24 chunks). Tests: `tests/engines/test_hmem_spec_model.py` (store, ranking formula, promotion, decay, supersede-without-delete) and `tests/engines/test_markovian.py`, run in CI on every pull request. They are project tests modelled on the spec; the official TypeScript conformance suite was not run.

**Zetaris.** Gate 1 reads its evidence views over the REST SQL contract with `EVIDENCE_SOURCE=zetaris`; each statement is a trace line with its SQL, `query_id` and `content_hash`, and a read-only preflight stops the run if Zetaris does not answer. Exercised live on the 2 `data/real` repositories (evidence load, and one gate-1 run); the 16 scored repositories used `evidence_source=csv`.

## HackOS rule checklist

The sentences in quotes are the rule text this draft is mapping. The window is 22 Oct 00:00 UTC to 27 Oct 23:45 UTC.

| Rule text | Where this repo meets it | Gap |
| --- | --- | --- |
| "Every submission must integrate Zetaris and Meterless." | Zetaris: `app/adapters/zetaris_evidence.py`, live read-only on the 2 repositories in `data/real`, trace `data/replays/zetaris-gate1-tharani-2006-invoice-generator-application/`. Meterless Nightshift: tried first on every gate-1 model call (`app/agents/relevance.py`). Meterless Engine: H-MEM on gate 1 (`app/verdict_memory.py`) and, with Markovian, on `POST /run`. | The 16 scored repositories used `evidence_source=csv`, not Zetaris. The scored v3 run predates the gate-1 H-MEM hook, so those traces have no `HMEM_VERDICT_MEMORY` line. World Model and Scout Intent are not implemented. |
| "how Zetaris and Meterless are integrated" | `docs/VIDEO_SCRIPT.md` shots at 2:05 and 2:25. README sections "Sponsor technology", "Zetaris and Meterless: integrated, live, or offline", and "Meterless Engine: what is implemented and what test shows it works". | The video file is not in the repository. The shot list is the script, not a recording. |
| "implement at least one Meterless Engine (H-MEM, Markovian, World Model or Scout Intent) in a working feature, and use Meterless Nightshift as its primary AI provider, with NVIDIA connected inside Nightshift" | H-MEM is the working gate-1 verdict memory. Nightshift is the first route for every gate-1 call. The scored-run `provider-split` slot says 50 explicit nvidia-direct; 158 `LLM_CALL` lines with no provider field = Nightshift route inferred from absence of the marker; 10 `LLM_SCORE` lines with no provider record. NVIDIA's API is the `fallback-nvidia` path, traced `nvidia-direct`. | This checkout has no Nightshift probe file, so it does not show NVIDIA configured inside the Nightshift console. The traces show Nightshift serving `nvidia/nemotron-*` ids, and they show direct NVIDIA calls after HTTP 502. Markovian is not on the gate-1 path. |
| "which Meterless Engine you implemented and the test that shows it works" | H-MEM. Gate 1 exercises `MemoryStoreService.add`, `find_verdicts`, and `supersede`, and `TrustLedgerService` `conflict_detected`. The test is `tests/test_verdict_memory.py`, which checks the `HMEM_VERDICT_MEMORY` line and `remembered_before`. | The official TypeScript conformance suite was not run. Limit: no committed trace shows two live runs. |
| "should show one real request passing through Meterless Nightshift, with all keys and tokens hidden" | Committed scored traces have model lines with no `provider` field, which `scripts/verify_trace.py` counts as Nightshift (24 such lines on the CalendarSync and Invoice traces). The video script says not to show `.env`, tokens, headers, or account ids. | A live request is not re-run from this checkout. No redacted probe output is committed. The E3 replays were sent to the NVIDIA API because Nightshift was pointed at a closed port; do not use those shots as the Nightshift request. |
| "Work committed before the window opens is not eligible, except for clearly declared pre-existing components." | Section (iii) above. `git log` shows 17 commits on 2026-10-08, from `54d52c1` through `62a1540`, and no commits from 9 Oct through 21 Oct 2026. The next commit, `7f01da0`, is 2026-10-22 13:13:06 +0000, inside the window. | The build-window commit count in the marker below is the number `scripts/fill_results.py` last wrote. It is not the count at HEAD. Run that script on the final commit. |

## (v) Remaining steps, in order

See `docs/RUNBOOK_WINDOWS.md` for the Windows commands. Pull requests #17, #18, and the Nemotron, `/ui`, and eval-summary work through #24 are already on `main`.

- [ ] On the Windows machine: run `scripts/nightshift_probe.py`. Put the printed `MODEL_NAME`, `MODEL_NAME_DEEP`, and `PHOENIX_LLM_*` lines into `.env`. Set `PHOENIX_NEMOTRON_POLICY=fallback-nvidia` and `NVIDIA_API_KEY` for the prize run.
- [ ] `python scripts/zetaris_check.py --snapshot data/real` with Zetaris running. Read-only. Keep the printed run hash. The command covers the snapshot's discovery rows, which are the two repositories named above, not the 16 evaluation repositories.
- [ ] Live eval, one commit for all 16 cases, new file (no `--resume` from a run of older code): `python scripts/eval_gate1.py --audit-only --deadline <seconds to 08:00 UTC> --out logs/eval-nemotron-v3.json`. If it is cut short, continue with `--resume logs/eval-nemotron-v3.json` on the same commit.
- [ ] Optional, with Zetaris running and nothing else judging: `python scripts/score_candidates.py --snapshot data/real --llm --output-json logs/zetaris-gate1.json --output-md logs/zetaris-gate1.md`. Its verdicts are the only gate-1 verdicts that read Zetaris; fill `zetaris-gate1-run` in the README from it, or delete that paragraph.
- [ ] For each scored trace: `python scripts/verify_trace.py data/replays/<id>/trace.jsonl --expected-run-hash <recorded_run.run_hash> --require-nemotron`. The original `logs/trace-<run_id>.jsonl` path is gitignored. Its `nemotron:` line is the provider split.
- [ ] `python scripts/fill_results.py logs/eval-nemotron-v3.json --write` and commit. Check the printed warnings: a missing or failing trace is reported and that slot is not given a number.
- [x] 19 RECORDED traces and 4 SAMPLE traces are in `data/replays/` (23 in the manifest): the 16 v3 case traces, the Zetaris-backed gate 1 trace, and two Experiment E3 replays titled `Experiment E3 (NVIDIA direct; final verdict only: thinking on, temperature 1.0; bulk steps thinking off, temperature 0), not the scored run`. SelfDB (`selfdb-io-selfdb`) is the first RECORDED entry, and `/ui` opens on it. On camera, name a replay SAMPLE or RECORDED from the label on the page. The E3 replays are not the scored run.
- [ ] **12:00 UTC 27 Oct: submit the first version** (Track 02, description, repository link, declarations). The video may follow.
- [ ] Record `docs/VIDEO_SCRIPT.md`. Check that no token or `.env` is visible, and that the Zetaris sentence names only the two `data/real` repositories.
- [ ] Final commit: `python -m ruff check .`, `python -m pytest -q`, `python scripts/compliance_check.py`. Paste the build-window commit count.
- [ ] **21:00 UTC: final submission**, with the video.
- [ ] Hard deadline **23:45 UTC**.

## Limits and honest numbers

The README Results table is 10 of 16 verdicts inside the human label and 6 of 16 outside it. The six are `goodreasonai/ScrapeServ`, `rianvdm/raindrop-wordpress-linkblog-sync`, and `Shabi-x/go-file-sync` (judged `CANDIDATE`, label `ALREADY_EXISTS`), `Dhaiwat10/x402-sovereign` (judged `ALREADY_EXISTS`, label `CANDIDATE` or `NEEDS_REVIEW`), plus the two already recorded on main: `tharani-2006/Invoice-generator-application` (`CANDIDATE` vs label `ALREADY_EXISTS`) and `DSRegoitia/CalendarSyncProject` (`NEEDS_PRIOR_ART` vs label `CANDIDATE` or `NEEDS_REVIEW`). Gate 2 has one committed rescue plan (`0xMH/fichero-printer`). Verified execution has not run on a real `CANDIDATE`. Those commands use `shell=True` and are not sandboxed (`app/revive_loop.py`).

`model_mismatch` in `config/settings.py` treats a served id as a mismatch unless it starts with `nvidia/nemotron-`. The direct NVIDIA API once served `private/nvidia/nemotron-3.5-super-120b-a12b` for the Super-VL id. `fallback-nvidia` would reject that reply although it is Nemotron. This errs on the safe side: it can wrongly reject a real Nemotron, never accept a non-Nemotron. The rule is unchanged. `MODEL_MISMATCH` on a trace line means the served id is missing or outside the Nemotron family on a call whose answer was used. Failed attempts are flagged `CALL_FAILED`. The scored v3 traces predate this and may show `MODEL_MISMATCH` on failed lines.

On experiment E3, not the scored run, the direct NVIDIA API returned HTTP 429 on 36 of the 112 calls across the five runs. The repository's retries completed every call. None failed permanently. That is a rate limit, not a change to the retry code.
