# Submission draft: Open Agent Hackathon 2026, Track 02 "The Agent That Can Explain Why"

Solo entry, team PhoenixRepo. Repository: `Fu93/phoenixrepo`. Deadline: **27 Oct 2026 23:45 UTC**. Plan: first version submitted by **12:00 UTC**, final by **21:00 UTC**. Video: 1 to 4 minutes. The shot list is cut for 3:00 in `docs/VIDEO_SCRIPT.md`. The HackOS clicks are in `docs/HACKOS_CHECKLIST.md`. Everything submitted is in English.

Rules for this draft: a measured number is copied from the live eval file named below. Anything that file does not yet contain is a `<!-- TODO(numbers): ... -->` marker plus the visible word **TBD**. This draft does not claim that an AI-written patch has fixed a real repository. It does not claim that all 16 evaluation repositories went through Zetaris.

Rubric: Impact 30, Technical 20, Innovation 15, Demo 15, Product & UX 10, Sponsor 10. Separate in-kind prize: Best use of NVIDIA Nemotron.

## State ready to paste (measured numbers still open)

The scored table is one live run. Produce it on the machine that holds the Nightshift token:

```bash
PYTHONPATH=. python scripts/eval_summary.py logs/eval-nemotron-v2.json --markdown
```

A live run on one machine, not a stored accuracy.

<!-- TODO(numbers): eval-summary-table -->

**TBD.** Paste that command's stdout here verbatim, including its Source line, and paste the same block into the Results section of `README.md`.

What is already fixed in the repository, with no live count attached:

- Gate 1 has two Nemotron tiers. `MODEL_NAME` with `PHOENIX_LLM_EXTRA_BODY` (thinking off) does the bulk overlap checks. `MODEL_NAME_DEEP` with `PHOENIX_LLM_DEEP_EXTRA_BODY` writes the final verdict and keeps its reasoning.
- `PHOENIX_NEMOTRON_POLICY` is `label`, `require`, or `fallback-nvidia`. The prize path described here is `fallback-nvidia`: Nightshift is tried first; on a timeout, an HTTP 5xx, or a non-Nemotron answer, the same request goes to NVIDIA's official API and the trace records provider `nvidia-direct`.
- `served_model` is inside the hash chain. `scripts/verify_trace.py` recomputes that chain and checks `--expected-run-hash`. It does not pattern-match `nvidia/nemotron-*`. The family check is the policy, the `/ui` badge "N of M answers by NVIDIA Nemotron", and the Nemotron reasoning card.
- A blocker needs a verified verbatim quote plus a mature or official alternative. Infrastructure failures are `NEEDS_REVIEW` or `UNJUDGED`, never a silent pass. `BUDGET_EXHAUSTED` can never produce `CANDIDATE`.
- Only the 2 repositories in `data/real` went through Zetaris for gate-1 verdicts: `tharani-2006/Invoice-generator-application` and `SergioMendolia/ytkb`. The 16 evaluation repositories were fetched live with `evidence_source=csv`. `scripts/zetaris_check.py` sends read-only `SELECT`s and the trace carries a run hash.
- `DSRegoitia/CalendarSyncProject` was rejected by the older Phoenix because FullCalendar "displays public Google Calendar events". The human label calls that a WRONG over-rejection: the candidate pushes events the other way. The acceptable set is `CANDIDATE` or `NEEDS_REVIEW`. Two independent runs, with different generated queries, returned `CANDIDATE` and `NEEDS_REVIEW`. `NEEDS_REVIEW` on this case is a partial overlap with a verbatim quote handed to a human.

<!-- TODO(numbers): nemotron-answer-share -->

Answers by NVIDIA Nemotron on the scored run: **TBD** of **TBD**.

<!-- TODO(numbers): provider-split -->

Provider split, Nightshift answers used / lines traced `nvidia-direct`: **TBD** / **TBD**.

<!-- TODO(numbers): calendarsync-final-verdict -->

CalendarSyncProject verdict on the recorded run: **TBD**.

<!-- TODO(numbers): calendarsync-final-run-hash -->

CalendarSyncProject run hash: **TBD**.

The 25 Oct 2026 batch of 30 repositories, described in the README under Current status, is an earlier run. It is not this table.

## (i) Video script, 3:00

Full narration, timings, and on-screen directions: `docs/VIDEO_SCRIPT.md`. Record from `/ui`, the static demo at <https://fu93.github.io/phoenixrepo/>, and a terminal. Real output only. Say **TBD** for the badge and the provider split until the markers above are filled.

| Time | Say | Show |
| --- | --- | --- |
| 0:00–0:25 | Dead repositories, and agents that revive them blindly. PhoenixRepo asks whether the idea already exists, and writes down why. | Title, then the two-gate diagram. |
| 0:25–0:50 | Gate 1 versus gate 2. Gate 2 runs only on `CANDIDATE`. Infrastructure failures are `NEEDS_REVIEW` or `UNJUDGED`. `BUDGET_EXHAUSTED` can never produce `CANDIDATE`. | Verdict table. |
| 0:50–1:15 | The trace, the run hash, and `scripts/verify_trace.py`. The script checks the chain that contains `served_model`. It does not pattern-match the model prefix. Tamper one character and the check fails. | `verify_trace.py` or the `/ui` Tamper button. |
| 1:15–1:40 | Zetaris honesty: only the 2 repositories in `data/real` (`tharani-2006/Invoice-generator-application` and `SergioMendolia/ytkb`) went through Zetaris for gate-1 verdicts. The 16 evaluation repositories were fetched live with `evidence_source=csv`. `scripts/zetaris_check.py` is read-only `SELECT`s, and the trace carries a run hash. | `scripts/zetaris_check.py` output, including the run hash. |
| 1:40–2:10 | Two Nemotron tiers (`MODEL_NAME` + `PHOENIX_LLM_EXTRA_BODY`, `MODEL_NAME_DEEP` + `PHOENIX_LLM_DEEP_EXTRA_BODY`). `fallback-nvidia` tries Nightshift first and traces `nvidia-direct` on failure. Badge **TBD** of **TBD**. Provider split **TBD** / **TBD**. | `/ui` badge and Nemotron reasoning card. |
| 2:10–2:30 | Static demo <https://fu93.github.io/phoenixrepo/> and local `/ui`. SAMPLE versus RECORDED. | Both pages. Say SAMPLE if that flag is showing. |
| 2:30–2:48 | hygen: technically able to come back (tests reported passing on current Node; upstream CI failed on Node 14 setup), rejected because the idea already exists. Mature alternatives: Plop and Yeoman. The revive loop has not been run on hygen. | Human label `ALREADY_EXISTS` for `Fu93/hygen`. |
| 2:48–3:00 | Limits: no AI-written patch has fixed a real repository; the chain is integrity, not truth; the 16 evaluation repositories were not a Zetaris run; scored figures stay **TBD** until pasted. | README Limits, then the repository link. |

## (ii) Project description for the HackOS form (about 250 words)

PhoenixRepo decides whether an abandoned GitHub repository deserves a second life, and explains every refusal with evidence a reviewer can check.

Gate 1 asks whether the idea is worth rescuing. It searches GitHub, npm, and the web. A blocker needs a verified verbatim quote plus a mature or official alternative. A failed search, a model outage, or a cut-short search is `NEEDS_REVIEW` or `UNJUDGED`, never a silent pass. `BUDGET_EXHAUSTED` can never produce `CANDIDATE`.

Gate 2 runs only when gate 1 says `CANDIDATE`. It accepts a result only when the repository's own tests exit 0 and an independent verifier finds no weakened tests.

Only the 2 repositories in `data/real` went through Zetaris for gate-1 verdicts: `tharani-2006/Invoice-generator-application` and `SergioMendolia/ytkb`. The 16 evaluation repositories were fetched live with `evidence_source=csv`. `scripts/zetaris_check.py` sends read-only `SELECT`s, and the trace carries a run hash.

Every step is a hash-chained trace. `scripts/verify_trace.py` recomputes the chain, including `served_model` and the run hash. The same page with no install is <https://fu93.github.io/phoenixrepo/>.

Gate 1 asks NVIDIA Nemotron in two tiers, Nightshift first: `MODEL_NAME` with `PHOENIX_LLM_EXTRA_BODY` (thinking off) for overlap checks, and `MODEL_NAME_DEEP` with `PHOENIX_LLM_DEEP_EXTRA_BODY` for the verdict. With `fallback-nvidia`, a timeout, a 5xx, or a non-Nemotron answer goes to NVIDIA's API and is traced as `nvidia-direct`. `/ui` shows an "N of M answers by NVIDIA Nemotron" badge and a Nemotron reasoning card. That share is **TBD**.

No AI-written patch has yet fixed a real repository. The scored table is a live run on one machine, not a stored accuracy. The code was written with AI assistance.

## (iii) "Built before the event" declaration

- **Pre-event skeleton:** everything committed up to and including `62a1540` (`chore: complete pre-opening foundation audit`, 2026-10-08 18:27:02 +0000).
- **Contents:** the FastAPI process, the Docker image, the evidence graph and pack, the integrity hash, the state machine with the three terminal names, the audit trail, secret redaction, and `FoundationOrchestrator`, which stops at `EVIDENCE_COMPLETE` with `decision: null` and calls no model.
- **Build window:** 22 to 27 Oct 2026. Everything after `62a1540` was built in it: the Zetaris REST client and gate-1 Zetaris evidence; the Meterless Runtime provider; H-MEM and Markovian; the roles and validator; gate 1 (`scripts/judge_repo.py`, `scripts/score_candidates.py`, `app/agents/relevance.py`, multi-source prior-art search); the two Nemotron tiers and `fallback-nvidia`; the batch and rerun tools; gate 2 (`app/revive_loop.py`, `app/revive_verify.py`, `scripts/revive.py`); the evidence report, trace renderer, and `/ui` replay page; the tests and this README.

<!-- TODO(numbers): build-window-commit-count -->

Commit count after the skeleton, on the final submitted commit: **TBD**. Command: `git rev-list --count 62a1540..main`. Paste that integer here when `main` is the commit you submit. Do not reuse a count from an earlier commit.

## (iv) AI-assistance disclosure

- Most of the code was written with AI coding assistance: Cursor cloud coding agents and an AI assistant working under the author's direction. The author (solo entrant) set the design and rules, reviewed and merged the changes, and ran the live checks on a Windows machine.
- Gate 1 and gate 2 call models through Meterless Nightshift and record `requested_model` and `served_model` on every LLM trace line. Under `PHOENIX_NEMOTRON_POLICY=fallback-nvidia`, gate 1 sends the same request to NVIDIA's official API after a Nightshift timeout, HTTP 5xx, or non-Nemotron answer, and traces it as provider `nvidia-direct`. Exa and Keenable are keyless web search sources, not AI providers.
- This draft, the video script, and the HackOS checklist were written with AI assistance and checked against the repository.

## (v) Remaining steps, in order

See `docs/RUNBOOK_WINDOWS.md` for the Windows commands. Pull requests #17, #18, and the Nemotron, `/ui`, and eval-summary work through #24 are already on `main`.

- [ ] On the Windows machine: run `scripts/nightshift_probe.py`. Put the printed `MODEL_NAME`, `MODEL_NAME_DEEP`, and `PHOENIX_LLM_*` lines into `.env`. Set `PHOENIX_NEMOTRON_POLICY=fallback-nvidia` and `NVIDIA_API_KEY` for the prize run.
- [ ] `python scripts/zetaris_check.py --snapshot data/real` with Zetaris running. Read-only. Keep the printed run hash. The command covers the snapshot's discovery rows, which are the two repositories named above, not the 16 evaluation repositories.
- [ ] Live eval, resumable: `python scripts/eval_gate1.py --audit-only --deadline 7200 --out logs/eval-nemotron-v2.json`. Then the summary command at the top of this file.
- [ ] Paste the summary into `README.md` Results and into the table slot above. Fill `nemotron-answer-share`, `provider-split`, `calendarsync-final-verdict`, and `calendarsync-final-run-hash` from those files. Leave **TBD** where the file has no such number.
- [ ] Optional: `python scripts/import_replay.py` for one real trace so `/ui` can show RECORDED. Until that import, say SAMPLE on camera.
- [ ] **12:00 UTC 27 Oct: submit the first version** (Track 02, description, repository link, declarations). The video may follow.
- [ ] Record `docs/VIDEO_SCRIPT.md`. Check that no token or `.env` is visible, and that the Zetaris sentence names only the two `data/real` repositories.
- [ ] Final commit: `python -m ruff check .`, `python -m pytest -q`, `python scripts/compliance_check.py`. Paste the build-window commit count.
- [ ] **21:00 UTC: final submission**, with the video.
- [ ] Hard deadline **23:45 UTC**.
