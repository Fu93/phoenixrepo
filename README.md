# PhoenixRepo
Before anyone spends a weekend reviving an abandoned GitHub repository, PhoenixRepo checks whether that idea already exists in a maintained form and records the check in a hash-chained trace. Solo Open Agent Hackathon 2026 entry, Track 02, "The Agent That Can Explain Why", for the prize Best use of NVIDIA Nemotron.
The evidence is the committed traces in `data/replays/`, the case audit in `docs/SCORED_RUN_AUDIT.md`, and `scripts/verify_nemotron_evidence.py` (offline checker, added in pull request #57). Check a recorded trace with `scripts/verify_trace.py --require-nemotron`.
The scored v3 set is a regression and audit set: 16 cases, 10 agree, 5 must-reject, 4 false passes. The traces are what make that audit possible. It is not an accuracy benchmark.
Read each model line as Requested, Served, and Policy. Say Ultra only when Served is `nvidia/nemotron-3-ultra-550b-a55b`. Say nvidia-direct only when the line stores `provider` `nvidia-direct`.

## Quickstart

Install the same dependencies CI installs (`.github/workflows/ci.yml` uses Python 3.12):

```bash
python -m pip install -r requirements-dev.txt
```

`requirements-dev.txt` includes `requirements.txt`, pytest, and ruff.

Run the tests:

```bash
python -m pytest -q
```

On this checkout, after this README rewrite, that command ended with:

```text
738 passed, 4 skipped, 1 warning in 192.61s
```

The warning is `StarletteDeprecationWarning` from FastAPI's test client (`httpx` versus `httpx2`). It is not a failure. The stable result is `738 passed, 4 skipped`. The seconds change between runs.

Check one scored trace. `scripts/verify_trace.py` requires a trace path; the flag alone does not run. SelfDB is the replay `GET /ui` opens on.

```bash
python scripts/verify_trace.py data/replays/selfdb-io-selfdb/trace.jsonl --expected-run-hash 147b7b32cbf0141079a58ffe71dc832d1092e81dfd42e512332ad4579e7fd711 --require-nemotron
```

```text
147b7b32cbf0141079a58ffe71dc832d1092e81dfd42e512332ad4579e7fd711
request_settings: present on 0 of 11 LLM_CALL lines (informational; absence is not a failure)
nemotron: 10 of 10 answers used (providers: nightshift 6, nvidia-direct 4; discarded by policy: 0)
```

Exit code 0. In that last line, `nightshift` is the name `scripts/verify_trace.py` prints when the line has no `provider` field (`_provider` in that file). It is not a value stored on the line. On an `LLM_CALL` line that omission is the Nightshift route, shown under [Nemotron use](#nemotron-use). On this SelfDB file the 6 are `LLM_CALL` lines with no `provider` field, and the 4 are `LLM_CALL` lines whose summary has `provider` `nvidia-direct`. One further `LLM_CALL` has status `error` and is not an answer used (11 lines, 10 used).

Check every RECORDED replay:

```bash
python scripts/verify_all_replays.py
```

```text
replay                                                    verdict          run_hash                                                          answers                                         served_models                                                                                                            result
--------------------------------------------------------  ---------------  ----------------------------------------------------------------  ----------------------------------------------  -----------------------------------------------------------------------------------------------------------------------  ------
selfdb-io-selfdb                                          ALREADY_EXISTS   147b7b32cbf0141079a58ffe71dc832d1092e81dfd42e512332ad4579e7fd711  10/10 nemotron; nightshift 6, nvidia-direct 4   nvidia/nemotron-3-ultra-550b-a55b 10                                                                                     PASS  
dsregoitia-calendarsyncproject                            NEEDS_PRIOR_ART  2eb5a1e3157e3f5a148ede12fc06d4ca6f4e7a8a7262d7f2c0a5326569a56cd0  10/10 nemotron; nightshift 7, nvidia-direct 3   nvidia/nemotron-3-ultra-550b-a55b 10                                                                                     PASS  
tharani-2006-invoice-generator-application                CANDIDATE        cfbe3cc16adb981ee3d7e5ec240424505eb9fe32fe10f1f54b3110f3c56f340f  20/20 nemotron; nightshift 17, nvidia-direct 3  nvidia/nemotron-3-ultra-550b-a55b 15, nvidia/nemotron-3.5-lightning-30b-a3b 1, nvidia/nemotron-3.5-super-vl-120b-a12b 4  PASS  
goodreasonai-scrapeserv                                   CANDIDATE        8ebf13c116d5f780fe7aab40d696b82367ee6564ff39746a19897358933e4609  14/14 nemotron; nightshift 11, nvidia-direct 3  nvidia/nemotron-3-ultra-550b-a55b 14                                                                                     PASS  
samuelarogbonlo-knetvis                                   CANDIDATE        26bab56e6000ffabea1a35917075e255bcd0c058c812bf3cec9ca91df106f338  13/13 nemotron; nightshift 8, nvidia-direct 5   nvidia/nemotron-3-ultra-550b-a55b 13                                                                                     PASS  
rianvdm-raindrop-wordpress-linkblog-sync                  CANDIDATE        51210214ec75aae6853e085264c9d07034c608ee101b56446c3d14ef8e0a5808  14/14 nemotron; nightshift 13, nvidia-direct 1  nvidia/nemotron-3-ultra-550b-a55b 12, nvidia/nemotron-3.5-super-vl-120b-a12b 2                                           PASS  
carvenjizaks-gc-sync-webhook                              CANDIDATE        459b10a988b87c68d9c15bb846073a20e793a8b92e891a74f24f54afb840c27b  13/13 nemotron; nightshift 11, nvidia-direct 2  nvidia/nemotron-3-ultra-550b-a55b 12, nvidia/nemotron-3.5-super-vl-120b-a12b 1                                           PASS  
aa12gq-stack-file-sync-vscode                             NEEDS_REVIEW     54c66dd2653dc9b68eb087dfd434088abc76f8b57f6a8782bb1a1389b99d3b82  10/10 nemotron; nightshift 5, nvidia-direct 5   nvidia/nemotron-3-ultra-550b-a55b 10                                                                                     PASS  
ri-char-pwatch                                            CANDIDATE        ccd9ee9d98ee1e9ad96b9871f3c6e3e8a8b9241a2b7b8530284f16c9750c5ffd  15/15 nemotron; nightshift 12, nvidia-direct 3  nvidia/nemotron-3-ultra-550b-a55b 14, nvidia/nemotron-3.5-super-vl-120b-a12b 1                                           PASS  
dayank-linkedinsynccontacts                               CANDIDATE        536118df205fc09cab0b7ce47f930c3d791b19435421618e135c9a1f9ed9ebb2  18/18 nemotron; nightshift 16, nvidia-direct 2  nvidia/nemotron-3-ultra-550b-a55b 16, nvidia/nemotron-3.5-super-vl-120b-a12b 2                                           PASS  
shabi-x-go-file-sync                                      CANDIDATE        0842db1647fd4496a4b543da270b2e2b3e2de6ed8fef2883d9805edb95a836c1  18/18 nemotron; nightshift 17, nvidia-direct 1  nvidia/nemotron-3-ultra-550b-a55b 13, nvidia/nemotron-3.5-lightning-30b-a3b 1, nvidia/nemotron-3.5-super-vl-120b-a12b 4  PASS  
samwho-spacer                                             CANDIDATE        6f1c4392af970dc02709593ad928606aed267a60626a0a9e2c20c5cc10001a52  13/13 nemotron; nightshift 9, nvidia-direct 4   nvidia/nemotron-3-ultra-550b-a55b 10, nvidia/nemotron-3.5-lightning-30b-a3b 1, nvidia/nemotron-3.5-super-vl-120b-a12b 2  PASS  
oleksiiluchnikov-obsidian-localtable                      CANDIDATE        2ae4aab419eb8f9a0a1d5c9a7d760d1ee09e00838ab06dfcf5badbacfa8199bc  14/14 nemotron; nightshift 13, nvidia-direct 1  nvidia/nemotron-3-ultra-550b-a55b 13, nvidia/nemotron-3.5-super-vl-120b-a12b 1                                           PASS  
sealdice-qq-group-files-sync                              CANDIDATE        833fd205dac5cc32019a05fd92f3455a1a0856046d6a055d431ff97e4507cceb  14/14 nemotron; nightshift 11, nvidia-direct 3  nvidia/nemotron-3-ultra-550b-a55b 13, nvidia/nemotron-3.5-super-vl-120b-a12b 1                                           PASS  
dhaiwat10-x402-sovereign                                  ALREADY_EXISTS   37f20b4250facfc3dd6fb9a3624641c482428448a544b15033d48359a5811514  10/10 nemotron; nightshift 6, nvidia-direct 4   nvidia/nemotron-3-ultra-550b-a55b 10                                                                                     PASS  
0xmh-fichero-printer                                      CANDIDATE        78643307b096ebbb1f3ea0a1c4acc3f91c0ee0d571facfa496db0a88449848ad  12/12 nemotron; nightshift 6, nvidia-direct 6   nvidia/nemotron-3-ultra-550b-a55b 12                                                                                     PASS  
zetaris-gate1-tharani-2006-invoice-generator-application  ALREADY_EXISTS   4f340332cd1349884bb5355d471f03e89d5b778cf378ccb20ccda4b74472ebad  27/27 nemotron; nightshift 21, nvidia-direct 6  nvidia/nemotron-3-ultra-550b-a55b 24, nvidia/nemotron-3.5-super-vl-120b-a12b 3                                           PASS  
e3-goodreasonai-scrapeserv                                ALREADY_EXISTS   ad556177edc4bb186251ecbb5a5dde256a255e4fc4742935677716bb2401480b  10/10 nemotron; nvidia-direct 10                nvidia/nemotron-3-ultra-550b-a55b 10                                                                                     PASS  
e3-dsregoitia-calendarsyncproject                         CANDIDATE        1fbf3ae09de5c72eaa02c866aa9e867d025b579e42b4038f72beda94681f3052  19/19 nemotron; nvidia-direct 19                nvidia/nemotron-3-ultra-550b-a55b 19                                                                                     PASS  
19 passed, 0 failed, 19 RECORDED
```

Exit code 0. The same `nightshift` label means a missing `provider` field. For the 16 scored traces, read the 50 / 158 / 10 split in [Nemotron use](#nemotron-use), not the word `nightshift` in this table. Invoice's `nightshift 17` is 16 `LLM_CALL` lines with no `provider` field plus 1 `LLM_SCORE` line. `scripts/score_candidates.py` writes `LLM_SCORE` with `output_summary` `{"parsed": true}` and never sets `provider`. Those `LLM_SCORE` lines are not the Nightshift omission.

Compliance (sample mode, no token):

```bash
python scripts/compliance_check.py
```

The stable lines from a passing run are:

```text
PhoenixRepo Compliance Check
PASS  Docker / CPU-only
PASS  API contract
PASS  SAMPLE_MODE
PASS  Trace logging
PASS  Evidence artifacts
PASS  Audit trail
PASS  Evidence Graph
PASS  Secret configuration
PASS  Validation boundaries
RESULT: PASS
```

Between `API contract` and `SAMPLE_MODE` the script prints a sample `POST /run` trace. That trace's `run_id` and hashes change on every execution, so they are not a fixed expected output. Exit code 0, last line `RESULT: PASS`.

`scripts/verify_nemotron_evidence.py` is named in the opening as the evidence checker. It was added in pull request #57 and is on `main`.

Token-free replay page: `python main.py`, then open `http://127.0.0.1:8000/ui`. The page opens on `selfdb-io-selfdb`. A static copy is published by `.github/workflows/pages.yml` at <https://fu93.github.io/phoenixrepo/> (HTTP 200 on 27 Oct 2026; the HTML contains `selfdb-io-selfdb`, `zetaris-gate1`, and `e3-goodreasonai`). `docker build -t phoenixrepo . && docker run --rm -p 8000:8000 phoenixrepo` serves the same app. `main.py` binds `0.0.0.0` and `$PORT` (default 8000).

`/ui` and the static export (`scripts/export_static_ui.py`) pass `recorded_run.run_hash` from `record.json` into `scripts.verify_trace.verify` when the manifest `trace_class` is RECORDED and that field is set. A re-sealed RECORDED file with a different hash fails on the page. A RECORDED replay with no recorded hash is shown as no recorded hash to compare, not as a pass. A SAMPLE replay says sample, not a recorded run: hash not compared, and is not shown as a failure when that hash differs. The static copy shows the export commit and `exported_at` (`docs/replay-export.md`).

## The decision, read as VRIO

The two gates follow the four VRIO questions. The trace records what was checked. A missing or failed search does not answer a question: it is `NEEDS_REVIEW` or `UNJUDGED`.

| Question | What is recorded | If it does not hold |
| --- | --- | --- |
| Valuable | A problem quote found in the repository's own text | `NO_GO` |
| Rare | An alternative that passes the maturity checks with a verified quote | `ALREADY_EXISTS` |
| Inimitable | Alternatives that did not block, kept as `must_beat` with the reason | `CANDIDATE`, with that gap stated. A famous commercial page in that list does not by itself refuse the repository. |
| Organised | Gate 2 runs only after `CANDIDATE`. Verified execution has not run on a real `CANDIDATE` in this submission. | Any other gate-1 verdict is `NOT_ATTEMPTED_GATE1` unless a human approval is attached for `NEEDS_REVIEW` or `NEEDS_PRIOR_ART`. |

## Nemotron use

Gate 1 asks for two NVIDIA Nemotron ids. The served id on the trace line is the only record of which model answered. Say Ultra only when that line's `served_model` is `nvidia/nemotron-3-ultra-550b-a55b`.

| Column | What it is | What the scored v3 traces show |
| --- | --- | --- |
| Requested | `requested_model` | `nvidia/nemotron-3-super-120b-a12b` on 202 used lines, `nvidia/nemotron-3-ultra-550b-a55b` on 16 used lines. The code default for the fast tier is `MODEL_NAME`, else `config/settings.py` `DEFAULT_NIGHTSHIFT_MODEL` (`nvidia/nemotron-3-super-120b-a12b`). The deep step defaults to `discovery` via `MODEL_NAME_DEEP` / `PHOENIX_DEEP_MODEL`. |
| Served | `served_model`, stored as returned, not copied from the request | `nvidia/nemotron-3-ultra-550b-a55b` 197, `nvidia/nemotron-3.5-super-vl-120b-a12b` 18, `nvidia/nemotron-3.5-lightning-30b-a3b` 3. By tier on `LLM_CALL`: fast Ultra 174, fast Super-VL 18, deep Ultra 16. The 10 `LLM_SCORE` lines have no tier field: Ultra 7, Lightning 3. |
| Policy | `PHOENIX_NEMOTRON_POLICY` | Not stored on the scored v3 traces. The code accepts `label` (default), `require`, or `fallback-nvidia` (`app/agents/relevance.py`). `provider` `nvidia-direct` is written only inside `_direct`, which returns without calling NVIDIA unless the policy is `fallback-nvidia` and `NVIDIA_API_KEY` is set. |

Scored v3 traces do not record temperature or thinking mode. Neither is inferred from the code default or from later runs. `request_settings` is present on 0 `LLM_CALL` lines in those 16 files. The string `temperature` does not occur in those 16 files.

Provider, counted from the 16 scored files under `data/replays/` (status `error` and `rejected` skipped):

- **50** `LLM_CALL` lines record `provider` `nvidia-direct`. Each of those 50 has `routing.fallback_reason` `nightshift HTTP 502` and no other routing key. All 50 were served `nvidia/nemotron-3-ultra-550b-a55b`. `_direct` in `app/agents/relevance.py` is the only writer of `provider` `nvidia-direct`, and it returns without calling NVIDIA unless the policy is `fallback-nvidia` and `NVIDIA_API_KEY` is set.
- **158** `LLM_CALL` lines have no `provider` field. Each of those 158 carries Nightshift's routing block (`mode`, `requested_model`, `routed_model`, `winning_model`, `winning_provider`) and no `fallback_reason`. `routing_meta` copies that block from a Nightshift response. The Nightshift HTTP 200 path passes `provider="nightshift"` into `_store` (`app/agents/relevance.py` around line 1113). `_observe` omits `provider` when the value is `nightshift` (the default argument, around lines 685 and 722). `winning_provider` on that block is `nvidia`: Nightshift's label for the upstream it picked, not the trace field `provider`. One of the 158 is a `cache_hit`. A cache hit copies the cached provider, so a cached `nvidia-direct` answer would still show the field. This cache hit has the Nightshift routing block and no `provider` field. Served on these 158 lines: Ultra 140 (124 fast, 16 deep) and Super-VL 18 (fast). Requested on the fast lines is Super.
- **10** `LLM_SCORE` lines have no `provider` field. `scripts/score_candidates.py` writes `LLM_SCORE` with `output_summary` `{"parsed": true}` and never sets `provider`, on any route. Those 10 are not the Nightshift omission. Served: Ultra 7, Lightning 3. They are the rest of the 168 lines with no `provider` field (218 used answers − 50 explicit `nvidia-direct`).

The blocks between `BEGIN numbers` and `END numbers` are the `scripts/fill_results.py` slots already in the file. They were not regenerated. The writer is `scripts/fill_results.py logs/eval-nemotron-v3.json --write`. That command was not run for this page. `logs/eval-nemotron-v3.json` is gitignored.

<!-- BEGIN numbers: nemotron-answer-share -->
Answers by NVIDIA Nemotron on the scored run: **218** of **218** answers used (16 of 16 case traces verified, `logs/eval-nemotron-v3.json`). The final-verdict answer (the `discovery` step) was written by Nemotron in **16** of the **16** cases whose trace has one.
<!-- END numbers: nemotron-answer-share -->

<!-- BEGIN numbers: provider-split -->
Provider split on that run, from `verify_trace.py --require-nemotron` over each case trace: 50 explicit nvidia-direct; 158 `LLM_CALL` lines with no provider field = Nightshift route inferred from absence of the marker; 10 `LLM_SCORE` lines with no provider record. 1 of the used answer was replayed from the LLM cache: an earlier run asked the same prompt with the same input, and the cached line keeps the model and provider that answered then. Served model per provider: nightshift: `nvidia/nemotron-3-ultra-550b-a55b` 147, `nvidia/nemotron-3.5-lightning-30b-a3b` 3, `nvidia/nemotron-3.5-super-vl-120b-a12b` 18; nvidia-direct: `nvidia/nemotron-3-ultra-550b-a55b` 50.
<!-- END numbers: provider-split -->

<!-- BEGIN numbers: nemotron-evidence -->
| Tier | Answers used, by served model |
| --- | --- |
| deep | `nvidia/nemotron-3-ultra-550b-a55b` 16 |
| fast | `nvidia/nemotron-3-ultra-550b-a55b` 174, `nvidia/nemotron-3.5-super-vl-120b-a12b` 18 |
| no tier (scoring calls outside the two-tier client) | `nvidia/nemotron-3-ultra-550b-a55b` 7, `nvidia/nemotron-3.5-lightning-30b-a3b` 3 |

| Served model | Live answers | Median seconds | Max seconds |
| --- | --- | --- | --- |
| `nvidia/nemotron-3-ultra-550b-a55b` | 189 | 42 | 280 |
| `nvidia/nemotron-3.5-super-vl-120b-a12b` | 18 | 7 | 90 |

Final-verdict (deep tier) answers that stored a separate reasoning field or a complete `<think>…</think>` pair: **0** of **16**. The scored traces recorded no reasoning text for the final verdict. The counter that wrote them recognised only a separate reasoning field or a complete `<think>…</think>` pair, and Nightshift's answers carry reasoning inline before a bare `</think>`, so the scored traces cannot say whether the model reasoned. Calls sent to NVIDIA's API after Nightshift failed or served a non-Nemotron answer: **53** (answered **50**, failed **3**, rejected **0**); the recorded reason on the answered ones: HTTP 502 50. Nightshift answers discarded as non-Nemotron: **0**; other Nightshift errors traced: **0**. A Nightshift failure that was retried on Nightshift itself writes no trace line, so the failure counts are lower bounds. Cases left not judged on this run: **0** of **16**. Cached answers are counted as used but not timed.
<!-- END numbers: nemotron-evidence -->


Read the `provider-split` slot with the three bullets above. Its "served model per provider: nightshift" clause still groups the 158 `LLM_CALL` lines and the 10 `LLM_SCORE` lines together (147 + 3 + 18 = 168) and calls that group nightshift. The 3 Lightning lines are the 3 `LLM_SCORE` lines served `nvidia/nemotron-3.5-lightning-30b-a3b`. The 218 and the tier rows in `nemotron-evidence` match a recount of the 16 committed traces. The latency medians in that slot (Ultra 189 live answers, median 42 seconds, max 280; Super-VL 18, median 7, max 90) come from the slot; this checkout's recount did not recompute the medians. `logs/eval-nemotron-v3.json` is gitignored. The answer counts above are in the committed traces.

Two RECORDED replays are Experiment E3, not the scored run: `data/replays/e3-goodreasonai-scrapeserv/` and `data/replays/e3-dsregoitia-calendarsyncproject/`. Both titles say NVIDIA direct, discovery thinking on at temperature 1.0, bulk steps thinking off at temperature 0. Their used lines record `provider` `nvidia-direct`. Their discovery lines record `enable_thinking` true, temperature 1.0, `top_p` 1.0, `max_tokens` 16384, and a reasoning excerpt. The other E3 traces and the 36-of-112 HTTP 429 figure are not files in this checkout, so they are not stated here.

One committed rescue-plan trace, not part of the 218: `docs/rescue/trace-rescue-0xmh__fichero-printer-20261027T001117Z.jsonl`. `verify_trace.py --require-nemotron` with run hash `51e828e538ed8f05dda15648fc673c3461ca310a87c0af77c53482e1f0a086c4` prints `nemotron: 1 of 1 answers used (providers: nvidia-direct 1; discarded by policy: 0)`. Requested and served on that line are `nvidia/nemotron-3-ultra-550b-a55b`, `fallback_reason` `nightshift HTTP 502`. It is a rescue plan, not a gate-1 verdict. Gate 2 verified execution (install and the repository's own tests on the host) has not run on a real `CANDIDATE` in this submission.

## Why Nemotron

### Eligibility conditions for Best use of NVIDIA Nemotron

Three conditions. Each row names the file and the trace that shows it.

| Condition | File | Trace / evidence |
| --- | --- | --- |
| At least one Nemotron model, through Meterless Nightshift | Nightshift is posted first (`_do_post` to `METERLESS_BASE_URL` in `app/agents/relevance.py`). An HTTP 200 that is kept calls `_store` with `provider="nightshift"` (around line 1113). `_observe` omits `provider` when the value is `nightshift` (default argument, around lines 685 and 722). | Scored v3: the 158 `LLM_CALL` lines in [Nemotron use](#nemotron-use). Zetaris gate-1 trace `data/replays/zetaris-gate1-tharani-2006-invoice-generator-application/trace.jsonl`: 27 of 27 answers used were Nemotron's, 21 through Nightshift and 6 through nvidia-direct. The 21 have the Nightshift routing block. The 6 store `provider` `nvidia-direct` and `fallback_reason` `nightshift HTTP 502`. |
| In a working feature | The gate-1 judge in `app/agents/relevance.py` (`problem`, `alternative`, `coverage`, `demand`, `discovery`), called from `scripts/score_candidates.py`. | The 16 scored traces and the Zetaris trace above are that feature. Gate 2 verified execution has not run on a real `CANDIDATE`. The committed rescue plan `docs/rescue/trace-rescue-0xmh__fichero-printer-20261027T001117Z.jsonl` is one answer: Requested and Served `nvidia/nemotron-3-ultra-550b-a55b`, `provider` `nvidia-direct`, `fallback_reason` `nightshift HTTP 502`. |
| Shows which model handled the request | `_observe` writes `requested_model` and `served_model`. `scripts/verify_trace.py --require-nemotron` prints the served family. `/ui` shows the served-model table. | Requested / Served / Policy, read separately. Requested is `requested_model`. Served is `served_model`. Policy is not a field on these traces. On the scored Nightshift `LLM_CALL` lines, Served is Ultra on 140 and Super-VL on 18. The 50 scored `nvidia-direct` lines are Served Ultra. The 10 `LLM_SCORE` lines are Served Ultra 7 and Lightning 3, and they do not record a provider. |

On the scored v3 traces, 218 of 218 answers used have a Served id under `nvidia/nemotron-*`. That is which model answered on one labelled audit. It is not a quality ranking and it is not a generalisable accuracy figure. `.env.example` comments that the NVIDIA API answered 404 for `nvidia/nemotron-3-super-120b-a12b` on the author's account. No trace in this checkout records that 404. The `nvidia-direct` lines that are answers record `fallback_reason` `nightshift HTTP 502` and Served Ultra.

## Results

Three artifacts. They are not one result.

### Smoke runs: provenance plumbing only

`scripts/nvidia_live_smoke.py` makes one chat request, writes a sealed trace under `logs/` (gitignored), and prints the requested id and the served id. Its docstring says the run verifies the Nemotron-family trace path only, and that it is not an evaluation result, a model-quality comparison, or a Gate 2 repair claim. This checkout has no committed smoke trace. A smoke run is provenance plumbing only.

### Single-repo demo: workflow only

`python scripts/judge_repo.py <github-url>` writes `reports/<run_id>/report.html` for one repository fetched live from GitHub and records `evidence_source=csv` (`scripts/judge_repo.py`). `GET /ui` shows one catalog entry at a time. Either one is the workflow for that repository: the trace, the served id, and the quote check. It is not a 16-case score and it is not a generalisable accuracy number.

### v3 eval: scored regression and audit set, not a generalisable accuracy benchmark

The tables below are one live gate-1 run of the 16 cases in `data/eval/gate1_labels.json` that have an `audit` object (`scripts/eval_gate1.py --audit-only` keeps `case.get("audit")`; 16 of the 19 cases in that file). It is a regression audit of cases that were already labelled. It is not a held-out benchmark and it is not a generalisable accuracy figure.

Counted from those 16 human labels and from each trace's `DISCOVERY_FILTER` verdict:

| Figure | Count |
| --- | --- |
| Repositories | 16 |
| Label agreements (verdict inside `acceptable`) | 10 |
| Must-reject (human `acceptable` is `ALREADY_EXISTS`) | 5 |
| False passes (must-reject, verdict `CANDIDATE`) | 4 |

The 4 false passes are `tharani-2006/Invoice-generator-application`, `goodreasonai/ScrapeServ`, `rianvdm/raindrop-wordpress-linkblog-sync`, and `Shabi-x/go-file-sync`. The fifth must-reject case, `Selfdb-io/SelfDB`, is `ALREADY_EXISTS` and agrees. The other two disagreements are not false passes: `DSRegoitia/CalendarSyncProject` is `NEEDS_PRIOR_ART` (acceptable is `CANDIDATE` or `NEEDS_REVIEW`), and `Dhaiwat10/x402-sovereign` is `ALREADY_EXISTS` where the label allows `CANDIDATE` or `NEEDS_REVIEW` (1 over-refusal).

The cause for each case is in [What the audit of our own run found](docs/SCORED_RUN_AUDIT.md). The gate currently cannot refuse a repo because of a famous commercial product.

### Before and after: the same 16 repositories

The v3 row has traces that can be verified. The 10/25 batch has only human audit labels, so it is a different evidence level. It is not a model comparison and it is not an accuracy benchmark.

The 10/25 counts are recomputed from `data/eval/gate1_labels.json`. Sixteen cases have an `audit` object dated 2026-10-25. Classes are LEGIT 5, WEAK 8, WRONG 3. The file stores no model id and no batch verdict string. The row states that all 16 were refused. A case agrees when `acceptable` contains `ALREADY_EXISTS`: 5 of 16, and those 5 are class LEGIT. A should-pass case does not list `ALREADY_EXISTS`: 11 cases, class WEAK 8 and WRONG 3, all refused, so over-refusals are 11 of 11. A must-reject case has `acceptable` equal to `ALREADY_EXISTS`: 5 cases, all class LEGIT, so false passes are 0 of 5. The scored cells match the `eval-summary-table` slot and the 16 `DISCOVERY_FILTER` lines.

| Run | What is recorded | Verdicts inside the human label | Over-refusals | False passes |
| --- | --- | --- | --- | --- |
| 10/25 batch (all 16 refused; human audit recorded in data/eval/gate1_labels.json) | Human audit labels only. No model id is in the file. | 5 of 16 | 11 of 11 | 0 of 5 |
| Scored v3 run | Traces in `data/replays/`. Served ids are in [Nemotron use](#nemotron-use). | 10 of 16 | 1 of 11 | 4 of 5 |

The v3 row has traces that can be verified. The 10/25 batch has only human audit labels, so it is a different evidence level. It is not a model comparison and it is not an accuracy benchmark. The cause of each scored false pass is the per-case table in [What the audit of our own run found](docs/SCORED_RUN_AUDIT.md).

Agree, 5 of 16:

```bash
python -c "import json;a=[c for c in json.load(open('data/eval/gate1_labels.json'))['cases'] if c.get('audit')];print(sum('ALREADY_EXISTS' in c['acceptable'] for c in a),len(a))"
```

Classes, LEGIT 5, WEAK 8, WRONG 3:

```bash
python -c "import json;from collections import Counter;a=[c for c in json.load(open('data/eval/gate1_labels.json'))['cases'] if c.get('audit')];print(dict(Counter(c['audit']['class'] for c in a)))"
```

Over-refusals, 11 of 11 (should-pass cases whose audit class is WEAK or WRONG):

```bash
python -c "import json;a=[c for c in json.load(open('data/eval/gate1_labels.json'))['cases'] if c.get('audit')];sp=[c for c in a if 'ALREADY_EXISTS' not in c['acceptable']];print(sum(c['audit']['class'] in ('WEAK','WRONG') for c in sp), len(sp))"
```

False passes, 0 of 5 (must-reject cases whose audit class is not LEGIT):

```bash
python -c "import json;a=[c for c in json.load(open('data/eval/gate1_labels.json'))['cases'] if c.get('audit')];mr=[c for c in a if set(c['acceptable'])=={'ALREADY_EXISTS'}];print(sum(c['audit']['class']!='LEGIT' for c in mr), len(mr))"
```

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

<!-- BEGIN numbers: per-case-table -->
| Repository | Human label | Verdict | Outcome | Verdict answer by | Nemotron answers used | Providers | Chain | Reason (shortened) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `tharani-2006/Invoice-generator-application` | ALREADY_EXISTS | CANDIDATE | disagrees | `nvidia/nemotron-3-ultra-550b-a55b` (nightshift) | 20 of 20 | nightshift 17, nvidia-direct 3 | ok `cfbe3cc16adb` | all guards passed |
| `DSRegoitia/CalendarSyncProject` | CANDIDATE or NEEDS_REVIEW | NEEDS_PRIOR_ART | disagrees | `nvidia/nemotron-3-ultra-550b-a55b` (nightshift) | 10 of 10 | nightshift 7, nvidia-direct 3 | ok `2eb5a1e3157e` | Zero alternatives checked in prior-art search. |
| `goodreasonai/ScrapeServ` | ALREADY_EXISTS | CANDIDATE | disagrees | `nvidia/nemotron-3-ultra-550b-a55b` (nightshift) | 14 of 14 | nightshift 11, nvidia-direct 3 | ok `8ebf13c116d5` | all guards passed |
| `Selfdb-io/SelfDB` | ALREADY_EXISTS | ALREADY_EXISTS | agrees | `nvidia/nemotron-3-ultra-550b-a55b` (nightshift) | 10 of 10 | nightshift 6, nvidia-direct 4 | ok `147b7b32cbf0` | verified full-overlap alternative https://github.com/Erudika/para already solves the same problem (quote: 'Mu… |
| `samuelarogbonlo/knetvis` | CANDIDATE or NEEDS_REVIEW | CANDIDATE | agrees | `nvidia/nemotron-3-ultra-550b-a55b` (nightshift) | 13 of 13 | nightshift 8, nvidia-direct 5 | ok `26bab56e6000` | all guards passed |
| `rianvdm/raindrop-wordpress-linkblog-sync` | ALREADY_EXISTS | CANDIDATE | disagrees | `nvidia/nemotron-3-ultra-550b-a55b` (nightshift) | 14 of 14 | nightshift 13, nvidia-direct 1 | ok `51210214ec75` | all guards passed |
| `Carvenjizaks/gc-sync-webhook` | CANDIDATE or NEEDS_REVIEW | CANDIDATE | agrees | `nvidia/nemotron-3-ultra-550b-a55b` (nightshift) | 13 of 13 | nightshift 11, nvidia-direct 2 | ok `459b10a988b8` | all guards passed |
| `aa12gq/stack-file-sync-vscode` | CANDIDATE or NEEDS_REVIEW | NEEDS_REVIEW | agrees | `nvidia/nemotron-3-ultra-550b-a55b` (nightshift) | 10 of 10 | nightshift 5, nvidia-direct 5 | ok `54c66dd2653d` | unverified LLM quote: alternative_repo 'espenalbert/path-sync' not found in candidate prior-art. Method: llm+… |
| `ri-char/pwatch` | CANDIDATE or NEEDS_REVIEW | CANDIDATE | agrees | `nvidia/nemotron-3-ultra-550b-a55b` (nightshift) | 15 of 15 | nightshift 12, nvidia-direct 3 | ok `ccd9ee9d98ee` | all guards passed |
| `DayanK/LinkedinSyncContacts` | CANDIDATE or NEEDS_REVIEW | CANDIDATE | agrees | `nvidia/nemotron-3-ultra-550b-a55b` (nightshift) | 18 of 18 | nightshift 16, nvidia-direct 2 | ok `536118df205f` | all guards passed |
| `Shabi-x/go-file-sync` | ALREADY_EXISTS | CANDIDATE | disagrees | `nvidia/nemotron-3-ultra-550b-a55b` (nightshift) | 18 of 18 | nightshift 17, nvidia-direct 1 | ok `0842db1647fd` | all guards passed |
| `samwho/spacer` | CANDIDATE or NEEDS_REVIEW | CANDIDATE | agrees | `nvidia/nemotron-3-ultra-550b-a55b` (nightshift) | 13 of 13 | nightshift 9, nvidia-direct 4 | ok `6f1c4392af97` | all guards passed |
| `oleksiiluchnikov/obsidian-localtable` | CANDIDATE or NEEDS_REVIEW | CANDIDATE | agrees | `nvidia/nemotron-3-ultra-550b-a55b` (nightshift) | 14 of 14 | nightshift 13, nvidia-direct 1 | ok `2ae4aab419eb` | all guards passed |
| `sealdice/qq-group-files-sync` | CANDIDATE or NEEDS_REVIEW | CANDIDATE | agrees | `nvidia/nemotron-3-ultra-550b-a55b` (nightshift) | 14 of 14 | nightshift 11, nvidia-direct 3 | ok `833fd205dac5` | all guards passed |
| `Dhaiwat10/x402-sovereign` | CANDIDATE or NEEDS_REVIEW | ALREADY_EXISTS | disagrees | `nvidia/nemotron-3-ultra-550b-a55b` (nightshift) | 10 of 10 | nightshift 6, nvidia-direct 4 | ok `37f20b4250fa` | verified full-overlap alternative https://docs.x402.org/dev-tools/facilitators already solves the same proble… |
| `0xMH/fichero-printer` | CANDIDATE or NEEDS_REVIEW | CANDIDATE | agrees | `nvidia/nemotron-3-ultra-550b-a55b` (nightshift) | 12 of 12 | nightshift 6, nvidia-direct 6 | ok `78643307b096` | all guards passed |

Of the 5 judged cases the label says to refuse: 4 `CANDIDATE`, 1 `ALREADY_EXISTS`. A `CANDIDATE` among them is a false pass that gate 2 would act on.

Each row was written as `logs/trace-<run_id>.jsonl` (`logs/` is gitignored). The committed copy of each of the 16 is under `data/replays/`. The chain column is `scripts/verify_trace.py` and shows the first 12 hex characters of the run hash. `(missing)` in a served-model count is a call that failed or was discarded before any answer, not an answer used.
<!-- END numbers: per-case-table -->


The per-case slot's `nightshift N` figures are `verify_trace.py`'s label for a missing `provider` field, including `LLM_SCORE` lines where that repo has one. Use the 50 / 158 / 10 split above for the route.

<!-- BEGIN numbers: calendarsync-final-verdict -->
Final verdict on the recorded run (`judge-20261026202603`): **NEEDS_PRIOR_ART** (disagrees with the human label). Reason: Zero alternatives checked in prior-art search.
<!-- END numbers: calendarsync-final-verdict -->

<!-- BEGIN numbers: calendarsync-final-run-hash -->
Final run hash: `2eb5a1e3157e3f5a148ede12fc06d4ca6f4e7a8a7262d7f2c0a5326569a56cd0`. The committed copy is `data/replays/dsregoitia-calendarsyncproject/trace.jsonl`. Check it with `python scripts/verify_trace.py data/replays/dsregoitia-calendarsyncproject/trace.jsonl --expected-run-hash 2eb5a1e3157e3f5a148ede12fc06d4ca6f4e7a8a7262d7f2c0a5326569a56cd0 --require-nemotron`. `logs/trace-judge-20261026202603.jsonl` is gitignored and is not in this checkout.
<!-- END numbers: calendarsync-final-run-hash -->


The scored CalendarSync trace verdict is `NEEDS_PRIOR_ART`. The separate Experiment E3 replay of that repository is `CANDIDATE` and is not this run. A `NEEDS_REVIEW` trace for CalendarSyncProject is not in `data/replays/`. The human label in `data/eval/gate1_labels.json` says the older refusal was wrong because FullCalendar displays public Google Calendar events, and the candidate pushes events the other way. The acceptable set on that label is `CANDIDATE` or `NEEDS_REVIEW`.

**Gate 2 only runs if gate 1 said `CANDIDATE`.** This is a hard precondition (commit `aa48ca7`). For any other gate-1 verdict the state is `NOT_ATTEMPTED_GATE1`, the trace records `REVIVE_BLOCKED_BY_GATE1`, and nothing is cloned, installed or patched. `revival_allowed` in `app/revive_loop.py` also lets `NEEDS_REVIEW` and `NEEDS_PRIOR_ART` proceed when a human approval is attached. `ALREADY_EXISTS`, `NO_GO`, and `UNJUDGED` never proceed. Without that approval, a non-`CANDIDATE` verdict returns `NOT_ATTEMPTED_GATE1`.

A search whose `source_status` includes `BUDGET_EXHAUSTED` sets coverage blocked, and `judge_discovery` then changes a `CANDIDATE` to `NEEDS_REVIEW` (`app/agents/relevance.py`). `BUDGET_EXHAUSTED` can never produce `CANDIDATE`.

To add a real trace to the replay catalog:

```bash
python scripts/import_replay.py logs/trace-<run_id>.jsonl --verdict path/to/scores.json
```

## Zetaris and Meterless

Zetaris was exercised live, read-only, on 2 repositories in `data/real`: `tharani-2006/Invoice-generator-application` and `SergioMendolia/ytkb`. Both ids are in `data/real/discovery_candidates.csv`. The committed gate-1 trace is `data/replays/zetaris-gate1-tharani-2006-invoice-generator-application/trace.jsonl`.

That file has 7 `zetaris_query` events: 1 `preflight` and 6 `ok` (3 per repository, on `phoenix.discovery_candidates`, `phoenix.prior_art`, and `phoenix.demand_signals`). Each stored SQL text is a single `SELECT`. `evidence_source` on those events is `zetaris`. The 27 `LLM_CALL` lines are status `ok` and served `nvidia/nemotron-*`: Ultra 24, Super-VL 3. Requested ids are Super on 25 lines and Ultra on 2. Tiers are fast 25 and deep 2. 27 of 27 answers used were Nemotron's, 21 through Nightshift and 6 through nvidia-direct. The 21 have no `provider` field and carry the Nightshift routing block (`winning_provider` `nvidia`, plus `mode`, `requested_model`, `routed_model`, `winning_model`). The 6 store `provider` `nvidia-direct` and `routing.fallback_reason` `nightshift HTTP 502`, and all 6 were Served Ultra. The same writer proof as the scored 158 is in [Nemotron use](#nemotron-use). One of the 21 is a `cache_hit` with that routing block and no `provider` field. `request_settings` is present on 0 of the 27. `HMEM_VERDICT_MEMORY` does not occur. ytkb `venture_analyst` at `2026-10-26T20:09:01Z` is `LLM_FALLBACK` after `Meterless chat failed with HTTP 502`, then `INITIAL_SCORECARD` total 13.33. `PROTAGONIST_SELECTION` says no eligible candidate met the feasibility threshold. Verdicts: Invoice `ALREADY_EXISTS`, ytkb `CANDIDATE` (human label `ALREADY_EXISTS`, so ytkb is a false pass on this run). Tool lines include 10 with status text `GitHub HTTP 404`, 1 `GitHub HTTP 422`, and 4 `BUDGET_EXHAUSTED`. There are 2 `UNVERIFIED_ALTERNATIVE_QUOTE` events. `scripts/zetaris_check.py` loads rows and seals a trace; it calls no model and writes no verdict. Its sealed trace is not in this checkout. `scripts/zetaris/load_real.ps1` issues `CREATE` statements. This checkout has no record that the load was run.

The 16 scored repositories were fetched live with `evidence_source=csv` on each `DISCOVERY_FILTER` line. They did not go through Zetaris.

### Separate post-v3 Zetaris eval16 run

This is an independent re-run of the 16 labelled cases, with Zetaris `evidence_source` and freshly collected evidence for the same repository names. It is separate from the scored v3 run, which used `evidence_source=csv`. It is not the official v3 record. It is not an accuracy benchmark, and it is not a held-out validation set. The run finished at 18:11 UTC on 27 Oct 2026. This checkout has no committed eval16 traces, pinned run hashes, or aggregate file, so no case results are stated here (see logs). A result recorded later belongs in this section with its own artifacts and hashes. It must not replace the scored v3 table (10 of 16 inside the human label) or its traces.

The earlier Zetaris gate-1 trace judged Invoice `ALREADY_EXISTS` with evidence alternative `al1abb/invoify`. That run's `prior_art` query for Invoice returned 1 row and selected `licence_spdx`, `stars`, and `pushed_at`. The committed `data/real/prior_art.csv` row is MIT, 6376 stars, pushed `2026-08-24T02:55:34Z`. The scored v3 Invoice trace has `prior_rows=0` and does not name that row.

<!-- BEGIN numbers: zetaris-gate1-run -->
Gate 1 was then run with its evidence read from Zetaris (`EVIDENCE_SOURCE=zetaris`, `python scripts/score_candidates.py --snapshot data/real --llm`) on those 2 repositories, on 26 Oct 2026. Run hash `4f340332cd1349884bb5355d471f03e89d5b778cf378ccb20ccda4b74472ebad`. The original path `logs/trace-zetaris-gate1.jsonl` is gitignored. The committed copy is `data/replays/zetaris-gate1-tharani-2006-invoice-generator-application/trace.jsonl`. Check it with `python scripts/verify_trace.py data/replays/zetaris-gate1-tharani-2006-invoice-generator-application/trace.jsonl --expected-run-hash 4f340332cd1349884bb5355d471f03e89d5b778cf378ccb20ccda4b74472ebad --require-nemotron`. That command on the committed copy reports 27 of 27 answers used were Nemotron's (Nightshift 21, `nvidia-direct` 6; served `nvidia/nemotron-3-ultra-550b-a55b` 24, `nvidia/nemotron-3.5-super-vl-120b-a12b` 3, the latter chosen by Nightshift). `scripts/zetaris_check.py` is a separate read-only evidence load and still writes no verdict; its sealed trace is also not in this checkout.

| Repository | Verdict with Zetaris evidence | Human label |
| --- | --- | --- |
| `tharani-2006/Invoice-generator-application` | `ALREADY_EXISTS` (maintained free alternatives such as Invoify) | `ALREADY_EXISTS` |
| `SergioMendolia/ytkb` | `CANDIDATE`, flagged `LLM_FALLBACK` | `ALREADY_EXISTS` |

ytkb is a false pass on this run. An earlier trial returned `NEEDS_REVIEW` on a partial overlap with JetBrains/youtrack-cli, and the human label names kd-cli. Zetaris supplied the evidence rows with their query ids and content hashes; it does not make a verdict more accurate. This run is not part of the scored table above.
<!-- END numbers: zetaris-gate1-run -->


The slot's phrase "Nightshift 21" is the same split, in `verify_trace.py`'s wording: 27 of 27 answers used were Nemotron's, 21 through Nightshift and 6 through nvidia-direct. The slot was not regenerated.

Meterless Nightshift is the first route for a gate-1 model call: OpenAI-compatible `POST /v1/chat/completions` with `meterless-app` and `meterless-scope` headers (`app/agents/relevance.py`). Tests mock that HTTP transport. No redacted Nightshift probe output is committed (`docs/NIGHTSHIFT_PROBE.md` describes where one would go).

H-MEM is this repository's own implementation of the public Meterless spec (`app/engines/hmem/`, spec URL in `metadata.json`: `https://github.com/Meterless/Meterless/tree/main/engines/hmem`, HTTP 200 on 27 Oct 2026). The official TypeScript conformance suite was not run. `tests/engines/test_hmem_spec_model.py` and `tests/test_verdict_memory.py` are project tests modelled on that spec. On current gate 1, `app/verdict_memory.py` writes each verdict to an H-MEM store before the seal and traces `HMEM_VERDICT_MEMORY`. It does not change the verdict. The scored 16 traces have no `HMEM_VERDICT_MEMORY` line; that hook was added after the scored run. `PHOENIX_HMEM=off` turns the hook off. Markovian (`app/engines/markovian.py`) is the bounded loop of `POST /run`. Gate 1 does not call it.

## Limitations

**Known limits of the judge.** Per-item invalid JSON is dropped and the chunk still counts as judged (`app/agents/relevance.py` around 2540, 2842, and 3158). Only error text containing 502, 503, 504, 429, timeout, timed out, or `http 5` leaves the chunk unjudged. A failed verbatim quote is `UNVERIFIED_ALTERNATIVE_QUOTE` and is not unjudged. Unjudged chunks never return `CANDIDATE` (`judge_partial_alternatives`: `ALREADY_EXISTS` if a verified full overlap already blocks, else `NEEDS_REVIEW`). That hold does not cover those two drops. The four scored false passes (Invoice-generator-application, ScrapeServ, raindrop-wordpress-linkblog-sync, go-file-sync) have no invalid-JSON drop and 2, 1, 2, and 0 quote drops. Those events omit overlap, so a verdict change is not established. In each, several verified full-overlap rows did not block. Their `must_beat` lines cite no push or release date and/or `stars=0`; other lines cite a stale push date or only a missing free tier. A reasoning field is present on 0 of 16 scored traces; the recorded check is the verbatim quote. `trace_class` (`RECORDED` or `SAMPLE`) sits in `manifest.json` and `record.json`, outside the JSONL hash. A re-sealed trace verifies unless `--expected-run-hash` is set.

- The v3 table is one labelled audit run. It is not a generalisable accuracy benchmark. There is no held-out set in this repository.
- Scored v3 traces do not record temperature, thinking mode, or `PHOENIX_NEMOTRON_POLICY`.
- `verify_trace.py` prints `nightshift` for a missing `provider`. That print is not a stored field. On an `LLM_CALL` line the omission is the Nightshift route in [Nemotron use](#nemotron-use). `LLM_SCORE` never sets `provider`.
- `quote_in_text` (`app/agents/relevance.py`) is a case-insensitive substring check after whitespace and Markdown normalisation. There is no minimum length. A match shows the quote occurs. It does not show that the quote supports the verdict.
- A web row is created with `stars` set to `0` (`app/agents/relevance.py`). `RepoFactsClient.fetch_alternative` does call the GitHub API for a `github.com/owner/name` URL, including a `web:` prefix (`app/agents/repo_facts.py`), and `scripts/score_candidates.py` calls it on problem alternatives. It fills a field only when the current value is `None` or `""`, so the preset `0` is not replaced by the star count the API returns. A web-found GitHub repository still cannot meet the 100-star bar from that lookup.
- Gate 2 verified execution runs the target repository's install and test commands with `subprocess.Popen(..., shell=True)` (`app/revive_loop.py`). There is no sandbox. It has not run on a real `CANDIDATE` here.
- The one committed rescue plan is a plan. The quote check shows the quote occurs in the cited text. It does not show that the quote supports the item. No AI-written patch in this repository has fixed a real repository's tests.
- CI (`.github/workflows/ci.yml`) does not call a live Zetaris, Nightshift, or NVIDIA API. Live runs were done outside CI.
- H-MEM on gate 1 records verdicts. It does not decide them. The official conformance suite was not run. The scored run predates the hook.
- `model_mismatch` in `config/settings.py` is true unless the served id starts with `nvidia/nemotron-`. An empty id is a mismatch. The sample stand-in name is not. `MODEL_MISMATCH` means the served id is missing or outside the Nemotron family on a call whose answer was used. Failed attempts are flagged `CALL_FAILED`. The scored v3 traces predate this and may show `MODEL_MISMATCH` on failed lines.

## Pre-existing components

`git log` shows 17 commits on 8 Oct 2026, from `54d52c1` (`chore: initialize PhoenixRepo foundation`, 2026-10-08 17:42:19 +0000) through `62a1540` (`chore: complete pre-opening foundation audit`, 2026-10-08 18:27:02 +0000), and no commits from 9 Oct through 21 Oct 2026. The next commit is `7f01da0` at 2026-10-22 13:13:06 +0000.

That skeleton includes the FastAPI process, the Docker image, the evidence graph and pack, the integrity hash, the state machine and its terminal names, the audit trail, secret redaction, and `FoundationOrchestrator`. The foundation orchestrator stops at `EVIDENCE_COMPLETE` with no decision. `POST /run` uses `InvestigationOrchestrator`, which is later work. The same declaration is in `metadata.json` under `pre_existing`.

## Repo map

| Path | What it is |
| --- | --- |
| `app/agents/relevance.py` | Gate-1 model client, quote check, Nightshift then `nvidia-direct` |
| `app/agents/blocker.py` | Maturity rule for an alternative |
| `app/verdict_memory.py`, `app/engines/hmem/` | H-MEM, including the gate-1 verdict memory |
| `app/engines/markovian.py` | `POST /run` chunk loop |
| `app/investigation.py` | Evidence-policy track |
| `app/adapters/zetaris_evidence.py`, `app/sql_catalog.py` | Zetaris reads for gate 1 |
| `scripts/score_candidates.py` | Gate-1 batch |
| `scripts/judge_repo.py` | One repository, HTML report, `evidence_source=csv` |
| `scripts/verify_trace.py`, `scripts/verify_all_replays.py` | Hash chain and Nemotron check |
| `scripts/compliance_check.py` | Sample-mode compliance |
| `scripts/nvidia_live_smoke.py` | One-call provenance smoke |
| `scripts/fill_results.py` | Writes the `BEGIN numbers` slots. Not run for this page |
| `data/replays/` | 19 RECORDED and 4 SAMPLE replays (`data/replays/manifest.json`) |
| `data/eval/gate1_labels.json` | Human labels. 16 cases carry `audit` |
| `docs/GATES.md` | Gate command notes moved out of this file |
| `docs/SCORED_RUN_AUDIT.md` | Cause per scored case. Not a new run |
| `docs/EVIDENCE_POLICY_TRACK.md` | `POST /run` track moved out of this file |
| `PRE_EVENT_PLAN.md`, `OPENING_READY.md` | 8 Oct 2026 skeleton notes |

## In review

These pull requests are open. They are not merged, and they are not the behaviour of this branch. Pull request #58 is on `main`.

**Pull requests #59, #62, #65, and #66 (not merged).** Each switch is default off. It runs only when set to `1`, `true`, `yes`, `on`, or `enabled`. Unset stays off. None of them are on this branch, and none are part of any number on this page. The names are `PHOENIX_UNKNOWN_BLOCKER_REVIEW` (#59), `PHOENIX_WEB_GITHUB_API_LOOKUP` (#62), `PHOENIX_QUOTE_FAIL_FLAG` (#65), and `PHOENIX_COMMODITY_CATEGORY_RULE` (#66). An offline reading of Rule B on the recorded `must_beat` lines is in [What the audit of our own run found](docs/SCORED_RUN_AUDIT.md). It is not a live run.

## Tests and licence

CI on Python 3.12 runs `python -m ruff check .`, `python -m pytest -q`, and `python scripts/compliance_check.py` (`.github/workflows/ci.yml`).

MIT, see `LICENSE`. Data files under `data/` keep the licences listed in [Data sources and licences](docs/EVIDENCE_POLICY_TRACK.md#data-sources-and-licences).
