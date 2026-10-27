# Video script: 3:00 demo

Open Agent Hackathon 2026, Track 02. Solo team PhoenixRepo. Record at 1080p, large type, English only. Keep the cut at 3:00 (the form allows 1 to 4 minutes).

Show `http://127.0.0.1:8000/ui` and a terminal. The same page with no install is <https://fu93.github.io/phoenixrepo/>. Do not show `.env`, tokens, headers, account ids, or the Nightshift Console. If a command fails on camera, say so and show the next step.

Every number said on camera is read from a file or a command on screen. Do not invent a number. The 218 of 218 figures are README slots filled by `scripts/fill_results.py` from a file that is not in the checkout. Prefer the command output of a committed trace.

## Shot list

| Time | Narration | On screen |
| --- | --- | --- |
| 0:00–0:20 | "Abandoned GitHub repositories are cheap to clone and expensive to finish. Agents revive them blindly. PhoenixRepo asks whether the idea already exists, and writes down why." | Title card: PhoenixRepo, Track 02. Then the two-gate diagram in the README. |
| 0:20–0:45 | "Gate 1 searches GitHub, npm and the web. It refuses only when a mature or official alternative does the same job and the quote appears word for word on that page. A search error is NEEDS_REVIEW, never a silent pass. Gate 2 runs only on CANDIDATE." | README verdict table. Point at `CANDIDATE`, `ALREADY_EXISTS`, `NEEDS_REVIEW`. |
| 0:45–1:25 | "The old PhoenixRepo refused CalendarSyncProject because FullCalendar 'displays public Google Calendar events'. A human audit called that wrong: CalendarSync pushes events the other way, into Google Calendar. Later runs said CANDIDATE, NEEDS_REVIEW, and on the scored run NEEDS_PRIOR_ART, because that search found nothing to compare. Read the verdict and the served model from the card." | `/ui` with the recorded CalendarSync replay. Point at the served-model id on the card. Do not show a reasoning card on this replay. The scored trace holds no reasoning text, which is not proof that none was produced. |
| 1:25–1:45 | "An agreeing refusal, to be chosen after #46 merges. The intended replay is SelfDB: the Results table marks it ALREADY_EXISTS and agrees with the label. Do not use the Invoice-generator-application replay that is on main: that recorded verdict is CANDIDATE, a disagreement." | After #46 merges, `/ui` on the SelfDB RECORDED replay, and read the verdict the card shows. Until then, show the SelfDB row in the README Results table and say the trace is not in the checkout. |
| 1:45–2:05 | "Nemotron, twenty seconds. The id on the card is the model that answered, which is not always the id requested. This command checks the file against the published run hash and that every answer used was Nemotron. Read the line it prints." | Terminal: `python scripts/verify_trace.py data/replays/dsregoitia-calendarsyncproject/trace.jsonl --expected-run-hash 2eb5a1e3157e3f5a148ede12fc06d4ca6f4e7a8a7262d7f2c0a5326569a56cd0 --require-nemotron`. Leave the served-model id and the printed `nemotron:` line on screen. |
| 2:05–2:25 | "Meterless Nightshift is the route tried first. The provider split in that line is Nightshift versus nvidia-direct, which is the fallback after Nightshift fails. Meterless Engine, H-MEM and Markovian, is in the other track. Gate 1 does not call it." | README sponsor table: the Nightshift row and the H-MEM row. The verify line stays visible. |
| 2:25–2:50 | "Zetaris is the SQL evidence source for gate 1. It ran live, read-only, on two repositories in data/real: Invoice-generator-application and ytkb. zetaris_check loads those rows and writes no verdict. The sixteen scored repositories used evidence_source=csv." | README Zetaris scope, with the two repository names. If Zetaris is up, run `python scripts/zetaris_check.py` and say it writes no verdict. If it is down, say so. |
| 2:50–3:00 | "Limits: no AI-written patch has fixed a real repository, gate 2's verified execution has not run on a real CANDIDATE, and a valid chain proves the record was not edited. The code is open." | README Limits, then the repository link `Fu93/phoenixrepo`. |

## What this cut does not claim

- It does not claim that all 16 evaluation repositories went through Zetaris, or that `zetaris_check.py` wrote a verdict.
- It does not claim a CalendarSync verdict until the recorded run is on screen.
- It does not claim the revive loop fixed any repository.
- It does not read a number off a SAMPLE replay and call it the scored run.
- It does not say the scored run produced no reasoning. The 0 of 16 figure counts a separate reasoning field or a complete `<think>…</think>` pair. The scored traces hold no reasoning text. The scored run used temperature 0 and did not record a thinking setting. It does not say thinking was on or off for the scored run.
- A reasoning card is shown on screen only from the labelled RECORDED replay whose title is `Experiment E3: NVIDIA direct, thinking on, temperature 1.0, not the scored run`. That replay is not in `data/replays` yet. Do not show a reasoning card from a scored-run replay, and do not read an E3 verdict as the scored run. The committed rescue-plan trace also has a reasoning excerpt; do not show that card as the scored run or as E3.
- It does not call the Invoice replay on main a refusal. That shot waits until #46 merges.
