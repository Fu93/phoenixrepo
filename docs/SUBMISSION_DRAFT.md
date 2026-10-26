# Submission draft: Open Agent Hackathon 2026, Track 02 "The Agent That Can Explain Why"

Solo entry, team PhoenixRepo. Repository: `Fu93/phoenixrepo`, branch `main`. Deadline: **27 Oct 2026 23:45 UTC** (London is on GMT, so the same clock time). Plan: first version submitted by **12:00 UTC**, final by **21:00 UTC**. Video: 1 to 4 minutes. Everything submitted is in English.

Rules for this draft: every number is copied from a file. A number that lives only on the author's Windows machine says so and names the file. Anything not known yet is a `[TODO: ...]`. Nothing here claims that an AI-written patch has fixed a real repository, or that Nemotron answered a call, unless a committed trace shows it.

Rubric: Impact 30, Technical 20, Innovation 15, Demo 15, Product & UX 10, Sponsor 10. Separate in-kind prize: Best use of NVIDIA Nemotron.

## State on 26 Oct 2026 (author's report)

These come from the author's Windows runs. The files are under `logs/` there and are not committed.

- Latest gate-1 batch (`r5`, 5 repositories): 0 `CANDIDATE`, 1 `ALREADY_EXISTS` (`[TODO: owner]/pagerbeauty`), 2 `UNJUDGED`, 2 `NEEDS_REVIEW`. [TODO: scores file path and run id.]
- Requested model `nvidia/nemotron-3-super-120b-a12b`; served model `gemini-3.1-flash-lite`, so every LLM line is `MODEL_MISMATCH`. `scripts/nightshift_probe.py` (PR #17) measures whether any Nemotron id is actually served. [TODO: probe result.]
- Nightshift calls took 45 to 345 seconds; HTTP 502 arrived after about 5.5 minutes; one repository took 5 to 21 minutes.
- `/ui` replays are all labelled SAMPLE until a real trace is imported with `scripts/import_replay.py`.
- Gate 2 has one engineering trial on pwgen. [TODO: outcome and trace path.]

Fill the numbers below only from the files named. Do not estimate.

## (i) Video script, 3:00 to 3:30

Theme: **what it refused, and why**. Record at 1080p from `http://127.0.0.1:8000/ui` and a terminal with a large font. Real output only.

| Time | Say (voice-over) | Show on screen |
| --- | --- | --- |
| 0:00 to 0:20 | "Reviving an abandoned repository is cheap to start and expensive to regret. Most should not be revived. PhoenixRepo asks first whether the idea deserves a second life, and every 'no' points at the row that proves it." | Title card, then the README's two-gate summary. |
| 0:20 to 0:40 | "Two gates. Gate 1: is the idea worth rescuing? It reads stored evidence from Zetaris, then searches GitHub, npm and the web. Gate 2 runs only if gate 1 says candidate. Every model call goes through Meterless Nightshift, and every step goes into a hash-chained trace." | README gate tables. If PR #18 is merged: a `zetaris_query` line with its `content_hash`. |
| 0:40 to 1:25 | "A real refusal. [pagerbeauty] [one-line job]. PhoenixRepo found [alternative], checked that it does the same job for the same user, that it is maintained and mature, and that the quote it relies on appears word for word on that page." | `/ui`, the RECORDED pagerbeauty replay: timeline, verdict card, the alternative, the verified quote and its link. [TODO: alternative name and quote from the imported record.] |
| 1:25 to 1:50 | "It also shows which model answered. We requested Nemotron. [If probe found Nemotron: these lines were served by Nemotron.] [Else: Nightshift served gemini-3.1-flash-lite, and every such line is flagged MODEL_MISMATCH rather than hidden.]" | The served-model table and a flagged line. Say the true one. |
| 1:50 to 2:15 | "When the evidence is incomplete, it does not guess. These came back NEEDS_REVIEW or UNJUDGED because Nightshift returned 502; finished calls are cached and the rerun picks them up." | A `NEEDS_REVIEW` or `UNJUDGED` row and its reason; the `rerun_unjudged.py` command. |
| 2:15 to 2:40 | "A refused idea is never built: gate 2 is not attempted and nothing is cloned. [If a CANDIDATE appeared and gate 2 ran: here is what it did, including the failures.]" | `NOT_ATTEMPTED_GATE1` replay (SAMPLE label visible) or a real revive trace. Never `--gate-verdict` for a CANDIDATE shown on camera. |
| 2:40 to 3:05 | "Anyone can check the record. The chain verifies. Change one character and it fails." | `/ui` verification: OK, then the Tamper button: FAILED. Or `verify_trace.py` before and after editing a copy. |
| 3:05 to 3:30 | "The limits are in the README: Nightshift is slow and the served model varies, no AI-written patch has fixed a real repository yet, and this code was written with AI assistance." | README "Limits", then the repository link. |

Notes:

- Do not show `.env`, tokens or the Nightshift Console.
- If a command fails on camera, say so and show the next step.
- Keep the final cut under 4:00.

## (ii) Project description for the HackOS form (about 230 words)

PhoenixRepo decides whether an abandoned GitHub repository deserves a second life, and explains every refusal with evidence a reviewer can check.

Gate 1 asks whether the idea is worth rescuing. It loads the stored evidence for the repository from Zetaris (candidate row, recorded prior art, demand signals; each query is in the trace with its content hash), then reads the repository's own text and searches GitHub, npm and the web for prior art, including official vendor features. An alternative blocks only when it does the same job for the same user, is maintained and mature, and the quote it relies on appears verbatim in its own text. A failed search is a source error, never "no competition", and the repository's own stars are never used as demand.

Gate 2 runs only when gate 1 says CANDIDATE. It runs the repository's own tests, lets a model draft a plan and patches, and accepts a result only when the repository's own test command exits 0 and an independent verifier finds no weakened tests.

Every step is a line in a hash-chained trace that records the model requested and the model that actually answered. A token-free page replays recorded runs and shows the chain failing when one character changes.

Limits: Nightshift can be slow and may serve a different model than requested (flagged, not hidden). No AI-written patch has yet fixed a real repository. The code was written with AI coding assistance.

[TODO: replace "may serve a different model" with the probe result.]

## (iii) "Built before the event" declaration

- **Pre-event skeleton:** everything committed up to and including `62a1540` (`chore: complete pre-opening foundation audit`, 2026-10-08 18:27:02 +0000).
- **Contents:** the FastAPI process, the Docker image, the evidence graph and pack, the integrity hash, the state machine with the three terminal names, the audit trail, secret redaction, and `FoundationOrchestrator`, which stops at `EVIDENCE_COMPLETE` with `decision: null` and calls no model.
- **Build window:** 22 to 27 Oct 2026. Everything after `62a1540` was built in it: Zetaris REST client and gate-1 Zetaris evidence; Meterless Runtime provider; H-MEM and Markovian; the roles and validator; gate 1 (`scripts/judge_repo.py`, `scripts/score_candidates.py`, `app/agents/relevance.py`, multi-source prior-art search); the batch and rerun tools; gate 2 (`app/revive_loop.py`, `app/revive_verify.py`, `scripts/revive.py`); the evidence report, trace renderer and `/ui` replay page; the tests and this README.
- Count: `git rev-list --count 62a1540..main` on the final commit. [TODO: paste the number.]

## (iv) AI-assistance disclosure

- Most of the code was written with AI coding assistance: Cursor cloud coding agents and an AI assistant working under the author's direction. The author (solo entrant) set the design and rules, reviewed and merged the changes, and ran the live checks on a Windows machine.
- The product uses AI models only through Meterless Nightshift and records `requested_model` and `served_model` on every LLM trace line. Exa and Keenable are used only as keyless web search sources, not as AI providers.
- This draft was written with AI assistance and checked against the repository.

## (v) Remaining steps, in order

See `docs/RUNBOOK_WINDOWS.md` for the exact commands.

- [ ] Merge PR #17 (Nightshift probe and controls) and PR #18 (Zetaris evidence with preflight) after CI is green.
- [ ] Windows: run `scripts/nightshift_probe.py`; set `.env` from its output, or keep `MODEL_MISMATCH` and say so.
- [ ] Windows: `scripts/zetaris_check.py` with Zetaris running (read-only).
- [ ] Windows: rerun the 4 unfinished `r5` repositories one at a time with `--deadline`.
- [ ] Import the pagerbeauty trace into `/ui` with `scripts/import_replay.py`; open a PR with `data/replays/`.
- [ ] Fill every `[TODO]` above from the files named.
- [ ] **12:00 UTC 27 Oct: submit the first version** (Track 02, description, repository link, declarations; video may follow).
- [ ] Record the video from `/ui`; check no token or `.env` is visible.
- [ ] Final commit: `python -m ruff check .`, `python -m pytest -q`, `python scripts/compliance_check.py`.
- [ ] **21:00 UTC: final submission** with the video.
