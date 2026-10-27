# Video script: 3:00 demo

Open Agent Hackathon 2026, Track 02. Solo team PhoenixRepo. Record at 1080p, large type, English only. Keep the cut at 3:00 (the form allows 1 to 4 minutes).

Show `http://127.0.0.1:8000/ui` and a terminal. The same page with no install is <https://fu93.github.io/phoenixrepo/>. Do not show `.env`, tokens, or the Nightshift Console. If a command fails on camera, say so and show the next step.

Every number said on camera is read from a file on screen. The Results slots in the README are filled (218 of 218, Nightshift 168, nvidia-direct 50, 0 of 16 final verdicts with a reasoning field). Do not invent a number that is not on screen. The build-window commit count is the count on the commit where `scripts/fill_results.py` last wrote it.

## Shot list

| Time | Narration | On screen |
| --- | --- | --- |
| 0:00–0:20 | "Abandoned GitHub repositories are cheap to clone and expensive to finish. Agents revive them blindly. They patch and open a pull request before anyone asks whether the idea already exists. PhoenixRepo asks that first, and writes down why." | Title card: PhoenixRepo, Track 02. Then the two-gate diagram in the README. |
| 0:20–0:45 | "Gate 1: is the idea worth rescuing? It searches GitHub, npm and the web, and refuses only when a mature or official alternative does the same job, and the quote it relies on appears word for word on that page. A search error or a cut-short search is NEEDS_REVIEW, never a silent pass. Gate 2, the revive loop, runs only on CANDIDATE." | README verdict table. Point at `CANDIDATE`, `ALREADY_EXISTS`, `NEEDS_REVIEW`. |
| 0:45–1:25 | "This is the case that changed the design. The old PhoenixRepo refused CalendarSyncProject because FullCalendar 'displays public Google Calendar events'. A human audit called that wrong: CalendarSync pushes events the other way, into Google Calendar. Since the rebuild it has never been refused. Across three runs it said CANDIDATE, NEEDS_REVIEW with FullCalendar's docs as a partial overlap for a human, and on the scored run NEEDS_PRIOR_ART, because that search found nothing to compare. That last one counts against us in the table, and the variance is in our limits. What it no longer does is refuse." | `/ui` with the recorded CalendarSync run: verdict card, the FullCalendar quote, the overlap level. Read the verdict from the card. (README slot `calendarsync-final-verdict`) |
| 1:25–1:45 | "A refusal that should stand: Invoice-generator-application. Gate 1 found [alternative], a maintained invoicing app, and the quote is on its own page. Nothing gets cloned." | `/ui` or the eval report row for `tharani-2006/Invoice-generator-application`: `ALREADY_EXISTS`, the alternative, the verified quote and its link. Name the alternative from the recorded row. |
| 1:45–2:15 | "The judgements are NVIDIA Nemotron, in two tiers: a fast model for the many overlap checks, and a deeper one for the verdict. The scored run kept a reasoning field on 0 of 16 final verdicts. What you can check is the quote. Nightshift is tried first; when it failed, NVIDIA's own API answered. One command checks the record against the published run hash, and that every answer used was Nemotron." | `/ui` badge "N of M answers by NVIDIA Nemotron". Terminal: `python scripts/verify_trace.py data/replays/dsregoitia-calendarsyncproject/trace.jsonl --expected-run-hash 2eb5a1e3157e3f5a148ede12fc06d4ca6f4e7a8a7262d7f2c0a5326569a56cd0 --require-nemotron` printing the run hash and the `nemotron: N of N answers used (providers: ...)` line. (README slot `nemotron-answer-share`) (README slot `provider-split`) |
| 2:15–2:35 | "Gate 1 can load its evidence from Zetaris over SQL. Here it is live and read-only on two repositories: each query has its id and a content hash, sealed into the same kind of trace. The sixteen scored evaluation repositories were fetched live from GitHub instead." | Terminal: `python scripts/zetaris_check.py` output with row counts, content hashes and `run hash`. If Zetaris is down on this take, say so. |
| 2:35–2:50 | "Anyone can check the record. Flip one character and that copy fails. A file that is re-sealed needs the run hash published in the README, passed as --expected-run-hash." | `/ui` Tamper button: PASS, then FAIL. Then the verify command from the quickstart. |
| 2:50–3:00 | "Limits, plainly: no AI-written patch has fixed a real repository yet, and a valid chain proves the record was not edited, not that every word is true. The code is open." | README Limits, then the repository link `Fu93/phoenixrepo`. |

If gate 2 ran on a real `CANDIDATE` before recording, replace 2:15–2:35 with that trace (what it tried, what failed, what passed) and keep the Zetaris sentence in the voice-over of 0:20–0:45.

## What this cut does not claim

- It does not claim that all 16 evaluation repositories went through Zetaris, or that `zetaris_check.py` wrote a verdict.
- It does not claim a CalendarSync verdict until the recorded run is on screen.
- It does not claim the revive loop fixed any repository.
- It does not read a number off a SAMPLE replay and call it the scored run.
