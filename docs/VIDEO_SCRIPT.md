# Video script: 3:00 demo

Open Agent Hackathon 2026, Track 02. Solo team PhoenixRepo. Record at 1080p, large type, English only. Keep the cut at 3:00 (the form allows 1 to 4 minutes).

Show `http://127.0.0.1:8000/ui` and a terminal. The same page with no install is <https://fu93.github.io/phoenixrepo/>. Do not show `.env`, tokens, headers, account ids, cache paths, or the Nightshift Console. If a command fails on camera, say so and show the next step.

Every number said on camera is read from a file or a command on screen. The v3 eval (16 repos), a single-repo demo, and a smoke run are three artifacts. Never show them as one result. The scored v3 traces do not record temperature or thinking mode; neither is inferred from code defaults or later runs.

Four layers, in order: Nemotron account of the overlap, then the verified quote from the source, then deterministic maturity/overlap checks, then the verdict plus the sealed hash trace. In the 0:45–1:25 shot those four are on screen together: model output with requested model id and served model id, the verified quote, the maturity/overlap rule that decides, and the trace seal plus `python scripts/verify_trace.py <trace> --require-nemotron`. Policy is a separate on-screen line, `PHOENIX_NEMOTRON_POLICY=fallback-nvidia` (`metadata.json`); the trace does not store that value.

## Pre-recording checklist

- fast LLM_CALL present; deep LLM_CALL present
- requested_model and served_model both present; tier=deep
- RUN_SEAL present; verify_trace --require-nemotron passes
- reasoning card only if non-empty; otherwise use the labelled non-scored Experiment E3 replay
- no token, .env or cache path visible on screen

## Shot list

| Time | Narration | On screen |
| --- | --- | --- |
| 0:00–0:20 | "Abandoned GitHub repositories are cheap to clone and expensive to finish. Agents revive them blindly. PhoenixRepo asks whether the idea already exists, and writes down why." | Title card: PhoenixRepo, Track 02. Then the two-gate diagram in the README. |
| 0:20–0:45 | "Gate 1 searches GitHub, npm and the web. It refuses only when a mature or official alternative does the same job and the quote appears word for word on that page. A search error is NEEDS_REVIEW, never a silent pass. Gate 2 runs only on CANDIDATE." | README verdict table. Point at `CANDIDATE`, `ALREADY_EXISTS`, `NEEDS_REVIEW`. |
| 0:45–1:25 | "Nemotron proposes the account of the overlap. PhoenixRepo then verifies the quote against the source and applies deterministic maturity rules. The trace records both the model requested and the model that actually served the response. A valid trace proves record integrity; it does not turn an unverified model statement into evidence. The old PhoenixRepo refused CalendarSyncProject because FullCalendar displays public Google Calendar events. Later runs said CANDIDATE, NEEDS_REVIEW, and on the scored run NEEDS_PRIOR_ART. Read those from the card." | Same frame: CalendarSync model output (requested id and served id), the verified quote, the maturity/overlap rule, `RUN_SEAL`, and `python scripts/verify_trace.py data/replays/dsregoitia-calendarsyncproject/trace.jsonl --expected-run-hash 2eb5a1e3157e3f5a148ede12fc06d4ca6f4e7a8a7262d7f2c0a5326569a56cd0 --require-nemotron`. Policy line beside them: `PHOENIX_NEMOTRON_POLICY=fallback-nvidia`. This single-repo trace is not the 16-case score. |
| 1:25–1:45 | "An agreeing refusal. SelfDB: the Results table marks it ALREADY_EXISTS and agrees with the label. Do not use the Invoice-generator-application replay: that recorded verdict is CANDIDATE, a disagreement." | `/ui` on the SelfDB RECORDED replay (`selfdb-io-selfdb`). #46 is merged; the trace is in the catalog. |
| 1:45–2:05 | "Read the printed line. Requested id, served id, and policy stay three facts. A reasoning card is shown only if that trace line's reasoning field is non-empty. This scored discovery line has none, so the card comes from the Experiment E3 replay, which is not the scored run. The scored v3 traces do not record temperature or thinking mode; neither is inferred from code defaults or later runs." | Verify output stays up. Open `e3-goodreasonai-scrapeserv` or `e3-dsregoitia-calendarsyncproject`, titled `Experiment E3 (NVIDIA direct; final verdict only: thinking on, temperature 1.0; bulk steps thinking off, temperature 0), not the scored run`. Do not read that verdict as the scored run. |
| 2:05–2:25 | "Meterless Nightshift is the route tried first. Name nvidia-direct only for a line whose trace has provider nvidia-direct, such as the CalendarSync alternative_batch lines. H-MEM records a verdict and never decides one. The scored run predates it." | README sponsor table. The verify line stays visible. |
| 2:25–2:50 | "Gate 1 can read its evidence from Zetaris. It ran live, read-only, on two repositories in data/real: Invoice-generator-application and ytkb. zetaris_check loads those rows and writes no verdict. The sixteen scored repositories used evidence_source=csv. A smoke run shows provider and hash-chain plumbing only, not Gate 1 quality." | README Zetaris scope. If Zetaris is up, run `python scripts/zetaris_check.py` and say it writes no verdict. If it is down, say so. Do not show smoke output as a score. |
| 2:50–3:00 | "Limits: no AI-written patch has fixed a real repository, gate 2's verified execution has not run on a real CANDIDATE, and a valid chain proves the record was not edited. The code is open." | README Limits, then the repository link `Fu93/phoenixrepo`. |

## What this cut does not claim

- It does not claim that all 16 evaluation repositories went through Zetaris, or that `zetaris_check.py` wrote a verdict.
- It does not show the v3 eval, a single-repo demo, and a smoke run as one result.
- It does not claim a CalendarSync verdict until the recorded run is on screen.
- It does not claim the revive loop fixed any repository.
- It does not read a number off a SAMPLE replay and call it the scored run.
- It does not say the scored run produced no reasoning, and it does not say the model did not reason. The 0 of 16 figure counts a separate reasoning field or a complete `<think>…</think>` pair. The scored traces recorded no reasoning text for the final verdict. The scored v3 traces do not record temperature or thinking mode; neither is inferred from code defaults or later runs.
- A reasoning card is shown only if the trace's reasoning field is non-empty. Otherwise use the clearly labelled non-scored Experiment E3 replay (`e3-goodreasonai-scrapeserv`, `e3-dsregoitia-calendarsyncproject`). Do not show that card from a scored-run replay, and do not read an E3 verdict as the scored run. The committed rescue-plan trace also has a reasoning excerpt; do not show that card as the scored run or as E3.
- It does not call the Invoice replay a refusal. That recorded verdict is CANDIDATE, a disagreement. #46 is merged.
