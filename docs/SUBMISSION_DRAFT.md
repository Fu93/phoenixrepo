# Submission draft: Open Agent Hackathon 2026, Track 02 "The Agent That Can Explain Why"

Solo entry, team PhoenixRepo. Repository: `Fu93/phoenixrepo` (branch `cursor/idea-filter-13a8`, PR #6). Deadline: **27 Oct 2026 23:45 UK time**. Video length allowed: 1 to 4 minutes. Everything here is in English.

Rules for this draft: every statement was read from the repository or from a real file on 25 Oct 2026. Anything not yet known is a `[TODO: ...]`. Nothing here claims that the revive loop has fixed a real repository with an AI-written patch, because it has not.

Rubric reminder: Impact 30, Technical 20, Innovation 15, Demo 15, Product & UX 10, Sponsor 10.

## Source of truth for numbers

- `logs/curated30d_scores.md` (merged scorecard of the `curated30d` batch from `scripts/run_sharded.py`). **It did not exist when this draft was written** (the batch was still running; only per-shard files `logs/curated30d-s0..s3_scores.json` existed, and they were from an unfinished first round). Every count below that depends on it is a TODO.
- [TODO: when `logs/curated30d_scores.md` exists, fill: repositories judged, count per verdict (CANDIDATE / ALREADY_EXISTS / NO_GO / NEEDS_REVIEW / NEEDS_PRIOR_ART / UNJUDGED), model calls per served model. Copy the numbers from the file, do not estimate.]

## (i) Video script, 3:00 to 3:30

Theme: **what it refused, and why**, with a human reviewing at every step.

Record the screen at 1080p. Terminal font large. One take per segment, cut together. Show real output only; if a command fails on camera, say so and show the next step.

| Time | Say (voice-over) | Show on screen |
| --- | --- | --- |
| 0:00 to 0:20 | "Thousands of abandoned repositories look revivable. Most should not be revived, and an agent that only says yes is useless. PhoenixRepo is built to say no, and to show its evidence." | Title card "PhoenixRepo, Track 02: The Agent That Can Explain Why". Then the README top section "What it does now: two gates". |
| 0:20 to 0:45 | "Two gates. Gate 1: is the idea worth rescuing? Gate 2: can it get a green test run? Gate 2 only runs if gate 1 says candidate. Everything goes through Nightshift, and every step goes into a hash-chained trace." | README tables for the gate-1 verdicts and the gate-2 steps (scroll slowly). Highlight "Gate 2 only runs if gate 1 said CANDIDATE". |
| 0:45 to 1:30 | "First refusal: an invoice generator. PhoenixRepo reads the repository, extracts the problem, searches GitHub, npm, PyPI and the web, and finds a maintained open-source invoicing tool that already solves it. The verdict is already exists. The quote it relies on is checked word for word against that tool's own page. Its own star count is never used." | Terminal: `python scripts/judge_repo.py https://github.com/tharani-2006/Invoice-generator-application` (or the saved run). Then open `reports/<run_id>/report.html` and zoom on the verdict, the alternative's name and URL, and the verbatim quote. Interim fact from an unfinished shard file: `logs/curated30d-s0_scores.json` says `tharani-2006/Invoice-generator-application` is `ALREADY_EXISTS` because of `InvoicePlane/InvoicePlane`, served by `gemini-3.1-flash-lite`. [TODO: confirm in the final `logs/curated30d_scores.md` or in a fresh judge run, and use the alternative named there.] |
| 1:30 to 1:55 | "Second case: ytkb, a reinvented wheel." | [TODO: ytkb verdict. In the unfinished shard file (`logs/curated30d-s1_scores.json`) `SergioMendolia/ytkb` is `UNJUDGED` because Nightshift returned HTTP 502. That is not a verdict. Replace this row with the final verdict and reason from the re-judge, or drop the segment if it is still unjudged.] Show the report page with its reason and sources. |
| 1:55 to 2:25 | "And one it did not refuse." | [TODO: protagonist chosen from curated30d results]. Show its gate-1 report: the stated problem, the alternatives checked, why none fully overlaps, and the human confirmation of the remaining gap. If no repository in the final results is `CANDIDATE`, replace this segment with an honest statement that gate 1 refused or sent everything to review, and show the `NEEDS_REVIEW` reasons. |
| 2:25 to 3:00 | "Gate 2 builds, and only the repository's own tests can accept. Here is what it does on this repository, including the failures." | Terminal: `python scripts/revive.py https://github.com/<protagonist>`. Show the trace events `REVIVE_INSTALL`, `REVIVE_BASELINE`, `REVIVE_PRD`, `REVIVE_PATCH`, `REVIVE_VALIDATE` and `REVIVE_RESULT` with `python scripts/render_trace.py logs/trace-<run_id>.jsonl --format md`. State the real outcome on camera. If it is `RESURRECTION_FAILED`, say so and show the real test output that went back to the builder. If it is `RESURRECTED`, say "verified locally by the repository's own test command, not yet on CI". [TODO: outcome from the real run.] |
| 3:00 to 3:15 | "A refused idea is never built. Here is gate 2 on the invoice generator: not attempted, nothing cloned." | Terminal: `python scripts/revive.py https://github.com/tharani-2006/Invoice-generator-application --gate-verdict ALREADY_EXISTS`. Show state `NOT_ATTEMPTED_GATE1` and the `REVIVE_BLOCKED_BY_GATE1` trace event. (Using `--gate-verdict` reuses the earlier verdict instead of judging again; say that on camera.) |
| 3:15 to 3:30 | "Every line is hash-chained and records which model actually answered. A human can verify the chain and read the timeline. The limits are on the README: Nightshift is slow and the model varies, no AI-written patch has fixed a real repository yet, and this code was written with AI assistance." | Terminal: `python scripts/verify_trace.py logs/trace-<run_id>.jsonl` (prints the run hash), then the rendered markdown timeline with the `served_model` column. Final card with the repository link and the "Limits" heading from the README. |

Notes for the recording:

- A real, honest fallback for gate 2 if the protagonist run is not ready: the local trial `reports/revtry-orangekame3mirrormate/revive.json` and `logs/trace-revtry-orangekame3mirrormate.jsonl` show the loop on a real repository: baseline failed (exit 1), a PRD was drafted, four patch iterations edited `package.json` only, the repository's test command still exited 1 each time, and the state was `RESURRECTION_FAILED` ("the test command still fails after 4 iterations"). Served models: `gemini-3.1-flash-lite`, `gemini-3.5-flash-lite`. **Disclose on camera** that this trial was run with a manually supplied gate-1 verdict of `NEEDS_REVIEW`, which the current code no longer lets proceed (it would be `NOT_ATTEMPTED_GATE1`), and that it is a failure, not a success.
- Do not show `.env`, tokens or Nightshift console pages with the runtime token.
- Keep under 4:00 in the final cut.

## (ii) Project description for the HackOS form (248 words)

PhoenixRepo is an agent that decides whether an abandoned GitHub repository deserves a second life, and explains every refusal. Most revival tools say yes to everything. PhoenixRepo has two gates and is built to say no with evidence.

Gate 1 asks whether the idea is worth rescuing. It reads the repository, extracts the problem and the target user, then searches prior art on GitHub, npm, PyPI and the web, including mainstream products and official built-in features. Every quote the model gives must appear verbatim in the source text, a search failure is never read as "no competition", and the repository's own stars are never used as demand. A verified full overlap turns an optimistic model answer into ALREADY_EXISTS.

Gate 2 runs only when gate 1 says CANDIDATE. It runs the repository's own tests, lets a model draft a plan and patches, and accepts a result only when the repository's own test command exits 0. Failures go back to the builder with the real output. Success is verified locally, not on CI.

Every step is written to a hash-chained trace with the model that actually answered, and a renderer turns it into a timeline a human reviewer can check. All model calls go through Meterless Nightshift; Zetaris is the SQL evidence layer of the evidence-policy track; Exa is only a web search source.

Limits: Nightshift can be slow; the served model varies. No AI-written patch has yet fixed a real repository. The code was written with AI coding assistance.

## (iii) "Built before the event" declaration

- **Pre-event skeleton:** everything committed up to and including `62a1540` (`chore: complete pre-opening foundation audit`, committed 2026-10-08 18:27:02 +0000). The commit exists in the repository (`git cat-file -t 62a1540` returns `commit`).
- **What the skeleton contains** (as declared in the README, "Pre-existing components"): the FastAPI process, the Docker image, the evidence graph and pack, the integrity hash, the state machine with the three terminal names, the audit trail, secret redaction, and `FoundationOrchestrator`. The foundation orchestrator stops at `EVIDENCE_COMPLETE` with `decision: null`: it makes no verdict and calls no model.
- **What was built in the build window** (after 62a1540; 45 commits as of 25 Oct 2026 before the three documentation commits of that day, `git rev-list --count 62a1540..HEAD`): the Zetaris REST client and sample SQL cache; the Meterless Runtime provider with lane retry and cooldown, and the NVIDIA dev fallback; H-MEM and the Markovian loop; the seven roles and the validator; the evidence policy and the hackathon trace format; the real-repository snapshot; the idea filter and gate 1 (`scripts/judge_repo.py`, `scripts/score_candidates.py`, `app/agents/relevance.py`, multi-source prior-art search in `app/tools/builtin.py`); the batch runner `scripts/run_sharded.py`; the revive loop (`app/revive_loop.py`, `scripts/revive.py`) and its hard gate-1 precondition (`aa48ca7`); the HTML evidence report and trace renderer; the tests and the README.
- [TODO: add the exact event start date and time and confirm that the first build-window commit is after it.]

## (iv) AI-assistance disclosure

- Most of the code in this repository was written with AI coding assistance: Cursor cloud coding agents and an AI assistant working under the author's direction (the README names a Cursor cloud agent as the author of the earlier build-window core). The author (solo entrant) chose the design and the rules, reviewed the changes, and ran the live checks on a Windows machine.
- The submitted product uses AI models only through Meterless Nightshift, and records which model answered each call (`requested_model` and `served_model` in every LLM trace line). Exa is used only as a keyless web search source, not as an AI provider. [TODO: confirm with the organiser form whether Cursor must be declared as a tool or a sponsor.]
- This documentation was also drafted with AI assistance and checked against the repository.
- Known limits (also in the README under "Limits"): Nightshift is slow and 502-prone and the served model varies between runs; alternatives are not checked for popularity or maintenance, so the overlap rule may over-refuse; the revive loop is verified locally only; no AI-written fix has yet succeeded on a real repository.

## (v) Remaining submission steps

- [ ] Confirm the `curated30d` batch finished and `logs/curated30d_scores.md` exists. Copy the real counts into this file and the form. (Do not touch the batch while it runs.)
- [ ] Pick the protagonist from the merged ranking (a human decision) and re-run `scripts/judge_repo.py` for it so the report and trace exist.
- [ ] Run `scripts/revive.py` on the protagonist and keep the real outcome, including a failure.
- [ ] Re-judge ytkb (or drop it) and fill the TODO rows in the script.
- [ ] Verify every trace shown on screen: `python scripts/verify_trace.py logs/trace-<run_id>.jsonl`.
- [ ] Record the video, 1 to 4 minutes (target 3:00 to 3:30); check that no token or `.env` is visible.
- [ ] Decide what the repository link points to. PR #6 is not merged and its base is `cursor/hygen-upgrade-prd-13a8`, not `main`; see `docs/PR_SPLIT_PLAN.md`. [TODO: owner decides branch or merge; this draft does not merge anything.]
- [ ] Run `python -m pytest -q` on the final commit and `python scripts/compliance_check.py`.
- [ ] On HackOS: select **Track 02, "The Agent That Can Explain Why"**.
- [ ] On HackOS: add a submission and paste the description from section (ii).
- [ ] On HackOS: upload the video.
- [ ] On HackOS: add the repository link, the "built before the event" declaration and the AI-assistance disclosure.
- [ ] Submit before **27 Oct 2026 23:45 UK time**; do not leave it to the last hour.
