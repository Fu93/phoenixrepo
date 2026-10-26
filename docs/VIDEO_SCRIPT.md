# Video script: 3:00 demo

Open Agent Hackathon 2026, Track 02. Solo team PhoenixRepo. Record at 1080p, large type, English only. Keep the cut at 3:00 (the form allows 1 to 4 minutes).

Show `http://127.0.0.1:8000/ui` and a terminal. The same page with no install is <https://fu93.github.io/phoenixrepo/>. Do not show `.env`, tokens, or the Nightshift Console. If a command fails on camera, say so and show the next step.

Say **TBD** out loud for every unfilled number. Do not invent a count.

## Shot list

| Time | Narration | On screen |
| --- | --- | --- |
| 0:00–0:25 | "Abandoned GitHub repositories are cheap to clone and expensive to finish. Agents revive them blindly: they patch, install, and open a pull request before anyone asks whether the idea already exists. PhoenixRepo stops at that question and writes down why." | Title card: PhoenixRepo, Track 02. Then the README sentence under the title, and the two-gate diagram. |
| 0:25–0:50 | "Two gates. Gate 1 asks whether the idea is worth rescuing. It reads the repository, searches GitHub, npm, and the web, and refuses only when a mature or official alternative does the same job and the quote appears verbatim in that alternative's own text. Gate 2, the revive loop, runs only after gate 1 says CANDIDATE. A search error, a model outage, or a cut-short search is NEEDS_REVIEW or UNJUDGED, never a silent pass. BUDGET_EXHAUSTED can never produce CANDIDATE." | README verdict table. Point at `CANDIDATE`, `ALREADY_EXISTS`, `NEEDS_REVIEW`, `UNJUDGED`. |
| 0:50–1:15 | "Every step is one line in a hash-chained trace. The last line is RUN_SEAL, and its line hash is the run hash. verify_trace recomputes the chain. The served model sits inside that hash, so editing it fails the check. The script checks the chain and the run hash. It does not pattern-match the model name. Change one character and the check fails." | Terminal: `python scripts/verify_trace.py logs/trace-<run>.jsonl` printing the run hash, then the same command with `--expected-run-hash` and a wrong hash, or the `/ui` Tamper button going from PASS to FAIL. Say the run id on screen. |
| 1:15–1:40 | "Zetaris is the SQL evidence store, and the scope is narrow. Only the 2 repositories in data/real went through Zetaris for gate-1 verdicts: tharani-2006/Invoice-generator-application and SergioMendolia/ytkb. The 16 evaluation repositories were fetched live with evidence_source=csv. This demo does not claim that all 16 went through Zetaris. zetaris_check.py sends read-only SELECTs and seals a trace. The trace carries a run hash." | Terminal output of `python scripts/zetaris_check.py` (or `--snapshot data/real`): row counts, content hashes, and the line `run hash`. If Zetaris is down, show the exit and say the check did not pass on this take. Do not imply the 16 rows came from this command. |
| 1:40–2:10 | "The judgements are NVIDIA Nemotron, in two tiers. A small model, MODEL_NAME, with thinking off via PHOENIX_LLM_EXTRA_BODY, does the bulk overlap checks. A larger reasoning model, MODEL_NAME_DEEP, with PHOENIX_LLM_DEEP_EXTRA_BODY, writes the final verdict. Policy fallback-nvidia tries Nightshift first. On a timeout, a 5xx, or a non-Nemotron answer, the same request goes to NVIDIA's official API and the trace says provider nvidia-direct. The badge reads TBD of TBD answers by NVIDIA Nemotron. Nightshift versus nvidia-direct is TBD / TBD." | `/ui` served-model table, the badge "N of M answers by NVIDIA Nemotron", and the Nemotron reasoning card. Leave the TBD figures as the words TBD. <!-- TODO(numbers): nemotron-answer-share --> <!-- TODO(numbers): provider-split --> |
| 2:10–2:30 | "Anyone can open the same page with no token. The static demo is fu93.github.io/phoenixrepo. Locally it is /ui. SAMPLE means the replay was sealed in this repository. RECORDED means a trace file was copied byte for byte. The reasoning card is the model's account. The verdict stands on the verbatim quote." | Browser: <https://fu93.github.io/phoenixrepo/> then `http://127.0.0.1:8000/ui`. Show the badge, the reasoning card, and the SAMPLE or RECORDED flag. If the flag is SAMPLE, say so. |
| 2:30–2:48 | "Contrast case: hygen. The code generator can be brought back technically. An independent check reported its tests passing on current Node, and upstream CI failed on a Node 14 setup step. Gate 1 still rejects the idea. The human label is ALREADY_EXISTS. Mature alternatives already do this job: Plop and Yeoman. PhoenixRepo's revive loop has not been run on hygen." | `/ui` or the README human-label line for `Fu93/hygen`, with Plop's quote visible. Do not show a green revive loop for hygen. |
| 2:48–3:00 | "Limits, said plainly. No AI-written patch in this project has fixed a real repository. The chain shows the record was not edited afterwards. It does not, by itself, show the words are true. The scored agreement, the Nemotron share, and the provider split are TBD until the live table is pasted. The 16 evaluation repositories were not a Zetaris run." | README Limits, then the repository link `Fu93/phoenixrepo`. |

## What this cut does not claim

- It does not claim that all 16 evaluation repositories went through Zetaris.
- It does not claim a final verdict or run hash for `DSRegoitia/CalendarSyncProject`. Two earlier runs, with different generated queries, returned `CANDIDATE` and `NEEDS_REVIEW`. The recorded run is still **TBD**.
- It does not claim `scripts/verify_trace.py` checks the prefix `nvidia/nemotron-*`. The script checks the hash chain, which includes `served_model`, and the run hash.
- It does not claim the revive loop resurrected hygen, or that any AI-written patch has fixed a real repository.
- It does not read a number off a SAMPLE replay and call it the scored run.

## Fill before recording

<!-- TODO(numbers): nemotron-answer-share -->

Say the badge as **TBD** of **TBD** answers by NVIDIA Nemotron, unless the Results table has been pasted. Then read that row and no other figure.

<!-- TODO(numbers): provider-split -->

Say the provider split as **TBD** / **TBD** (Nightshift answers used / `nvidia-direct` lines), unless the trace for the scored run has been counted. Then say that count.
