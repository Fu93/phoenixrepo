# Nemotron's role in the scored v3 traces

Nemotron is the model family that served the semantic steps. The final verdict is a later deterministic decision. Checks A–E in the blocker gate can replace the discovery answer. 218 of 218 means that all 218 answers used in the 16 scored v3 traces were served by a `nvidia/nemotron-*` model. An answer used is an `LLM_CALL` or `LLM_SCORE` line whose status is not `error` or `rejected` (`scripts/verify_trace.py`).

These counts were recomputed from the 16 `RECORDED` replays that `scripts/verify_nemotron_evidence.py` selects (`data/replays/manifest.json`, an `audit` object in `data/eval/gate1_labels.json`, `recorded_run.source` ending in `scores.json`, run id starting with `judge-`) and from each `trace.jsonl`. `logs/eval-nemotron-v3.json` is gitignored and was not read. There is no `data/eval/eval-nemotron-v3` manifest on `main`.

Short names below are served ids, and only served ids:

| Short name | Served id |
| --- | --- |
| Ultra | `nvidia/nemotron-3-ultra-550b-a55b` |
| Super-VL | `nvidia/nemotron-3.5-super-vl-120b-a12b` |
| Lightning | `nvidia/nemotron-3.5-lightning-30b-a3b` |

The requested fast-tier id is `nvidia/nemotron-3-super-120b-a12b`. A line is Ultra only when its `served_model` is `nvidia/nemotron-3-ultra-550b-a55b`.

## Semantic steps

`app/agents/relevance.py` asks the model for meaning. On the 208 `LLM_CALL` lines with status `ok`, `input_summary` records the step:

| Step | Lines | Tier | Served |
| --- | --- | --- | --- |
| `problem` | 16 | fast | Ultra 16 |
| `alternative` | 15 | fast | Ultra 12, Super-VL 3 |
| `alternative_batch` | 113 | fast | Ultra 106, Super-VL 7 |
| `demand` | 36 | fast | Ultra 30, Super-VL 6 |
| `capabilities` | 12 | fast | Ultra 10, Super-VL 2 |
| `discovery` | 16 | deep | Ultra 16 |

`scripts/score_candidates.py` writes a further 10 `LLM_SCORE` lines (status `ok`, no `step`, no tier) for the venture-analyst score. Served: Ultra 7, Lightning 3. The writer sets `output_summary` to `{"parsed": true}` and does not set `provider`.

## The A–E blocker gate can replace the discovery verdict

`evaluate_blocker` in `app/agents/blocker.py` sets `blocks` true only when checks A–E all hold. `judge_discovery` in `app/agents/relevance.py` then applies that decision to the discovery JSON:

- A raw `CANDIDATE` becomes `ALREADY_EXISTS` when a verified full-overlap row already passes the gate. The reason text in code ends with `overrides LLM CANDIDATE`.
- A raw `ALREADY_EXISTS` whose named alternative fails A–E can become `CANDIDATE` or `NEEDS_REVIEW`. The reason then starts with `must beat <name>: did not block`.

The scored v3 traces do not carry `GATE_DECISION` (0 lines). `docs/ARCHITECTURE.md` records that the model's raw verdict in v3 remains inferred. `docs/SCORED_RUN_AUDIT.md` reads the `must beat` reason as that path and marks the reading inferred. The trace does not store the raw model verdict.

## Requested, Served, Policy

The two tiers are a request design. `RelevanceClient.route` returns `deep` for `discovery` (`DEFAULT_DEEP_STEPS`) and `fast` for the other steps. The fast tier was served by Ultra 174 times and by Super-VL 18 times.

| Group | Requested | Served | Policy |
| --- | --- | --- | --- |
| Fast, 192 `LLM_CALL` answers used | `nvidia/nemotron-3-super-120b-a12b` | Ultra 174, Super-VL 18 | not recorded |
| Deep `discovery`, 16 `LLM_CALL` answers used | `nvidia/nemotron-3-ultra-550b-a55b` | Ultra 16 | not recorded |
| Untiered, 10 `LLM_SCORE` answers used | `nvidia/nemotron-3-super-120b-a12b` | Ultra 7, Lightning 3 | not recorded |

Served totals for the 218 answers used: Ultra 197 (174 fast, 16 deep discovery, 7 untiered), Super-VL 18, Lightning 3.

`PHOENIX_NEMOTRON_POLICY` is not a field on these traces. The code accepts `label`, `require`, or `fallback-nvidia` (`app/agents/relevance.py`). This page does not state which of those values the scored run used.

## Provider fields

The 16 traces contain 211 `LLM_CALL` lines and 10 `LLM_SCORE` lines.

- 158 `LLM_CALL` lines (status `ok`) carry no `provider` field. That absence is the Nightshift record: `_observe` omits `provider` when the value is `nightshift`. The string `provider: nightshift` is not stored. Each of the 158 carries the Nightshift routing block `mode`, `requested_model`, `routed_model`, `winning_model`, `winning_provider`. `winning_provider` is `nvidia` on all 158. That key is Nightshift's label for the upstream it picked. It is not the trace field `provider`. One of the 158 is a `cache_hit`; it has the same routing block and no `provider` field. Served on the 158: Ultra 140 (124 tier `fast`, 16 tier `deep`) and Super-VL 18 (tier `fast`). The 124 tier `fast` Ultra lines have `requested_model` `nvidia/nemotron-3-super-120b-a12b`.
- 53 `LLM_CALL` lines store `provider` `nvidia-direct`. 50 are status `ok` and 3 are status `error`.
- The 50 `ok` lines are served Ultra. Each has `routing.fallback_reason` `nightshift HTTP 502` and no other routing key. Those 50 are the NVIDIA-direct answers the traces show.
- The 3 `error` lines store an empty `served_model`, tier `fast`, requested id `nvidia/nemotron-3-ultra-550b-a55b`, flag `MODEL_MISMATCH`, and no `fallback_reason`. They are `0xMH/fichero-printer` line 37, `Selfdb-io/SelfDB` line 40, and `samwho/spacer` line 42. They are not answers used. The traces do not show a served model on them.
- 10 `LLM_SCORE` lines have no `provider` field. They are the venture-analyst lines above, not the Nightshift omission in `_observe`.

218 answers used = 158 `LLM_CALL` with no provider field + 50 `nvidia-direct` `ok` + 10 `LLM_SCORE`. All 218 served ids start with `nvidia/nemotron-`.

## Temperature and reasoning

Scored v3 traces do not record thinking or temperature. Neither is inferred from code defaults or later runs. A recount of the 16 files finds no `temperature`, `top_p`, `max_tokens`, `enable_thinking`, `thinking`, `reasoning_effort`, or `request_settings` on the model lines, and no `reasoning` field. The v3 eval's 0/16 reasoning figure is not recorded.

`data/replays/e3-goodreasonai-scrapeserv/trace.jsonl` is not one of the 16 scored v3 traces. It has 17 `LLM_CALL` lines. `request_settings.enable_thinking` is true on 1 line (step `discovery`, `temperature` 1.0) and false on 16 lines (`temperature` 0).

## Eligibility conditions for Best use of NVIDIA Nemotron

The condition text is the wording in `README.md` under this same title. Each row is a condition that file states, with the committed file and trace that show it.

| Condition | File | Trace |
| --- | --- | --- |
| At least one Nemotron model, through Meterless Nightshift | `app/agents/relevance.py`: `_do_post` posts to `self.base_url`. `scripts/score_candidates.py` sets that from `settings.meterless_base_url` (`METERLESS_BASE_URL`). A kept HTTP 200 goes through `_store` with provider `nightshift`, and `_observe` then omits the field. | The 158 scored `LLM_CALL` lines with no `provider` field and the Nightshift routing block. Example: `data/replays/selfdb-io-selfdb/trace.jsonl`. |
| In a working feature | The gate-1 judge in `app/agents/relevance.py` (`problem`, `alternative`, `demand`, `capabilities`, `discovery`), called from `scripts/score_candidates.py`. | The 16 scored traces under `data/replays/`. Gate 2 verified execution has not run on a real `CANDIDATE` in this submission (`README.md`). |
| Shows which model handled the request | `_observe` writes `requested_model` and `served_model` on the `LLM_CALL` line. `scripts/verify_trace.py --require-nemotron` checks that every answer used has a served id in `nvidia/nemotron-*`. | The Requested / Served / Policy table on this page. Policy stays not recorded. |

## Verification

`scripts/verify_trace.py` recomputes the hash chain and compares `RUN_SEAL` `line_hash` to `--expected-run-hash`. `--require-nemotron` also requires every answer used to have `served_model` in `nvidia/nemotron-*`. That checks the stored record and the served model family. It does not check that the verdict is correct, and it does not check that a new run would match.

```bash
python scripts/verify_trace.py data/replays/selfdb-io-selfdb/trace.jsonl --require-nemotron --expected-run-hash 147b7b32cbf0141079a58ffe71dc832d1092e81dfd42e512332ad4579e7fd711
```

On this checkout that command exits 0. It prints the hash above, `request_settings: present on 0 of 11 LLM_CALL lines`, and `nemotron: 10 of 10 answers used (providers: nightshift 6, nvidia-direct 4; discarded by policy: 0)`. In that line, `nightshift 6` is the script's label for a missing `provider` field (`_provider` in `scripts/verify_trace.py`). The same command, with each trace's `recorded_run.run_hash`, applies to the other 15 scored traces.

`scripts/verify_nemotron_evidence.py` recomputes the 218, the tier rows, and the missing-provider remainder from the committed traces. It does not call a model. Temperature, thinking mode, and policy stay not recorded in that script as well.

## Limits

The scored v3 set is a regression and audit set of 16 labelled cases (`scripts/eval_gate1.py --audit-only` keeps cases that have an `audit` object). It is not an accuracy benchmark. Recounted from `data/eval/gate1_labels.json` and each replay `record.json`: 10 of 16 verdicts sit inside the human `acceptable` list, and 4 false passes are must-reject cases (`acceptable` is `ALREADY_EXISTS`) judged `CANDIDATE`: `tharani-2006/Invoice-generator-application`, `goodreasonai/ScrapeServ`, `rianvdm/raindrop-wordpress-linkblog-sync`, and `Shabi-x/go-file-sync`. The per-case reading is `docs/SCORED_RUN_AUDIT.md`.

H-MEM is this repository's implementation of the public Meterless spec. `app/engines/hmem/__init__.py` points at that spec. `docs/EVIDENCE_POLICY_TRACK.md` says it is implemented from the open-source spec and not imported as a package, and that the official TypeScript conformance suite was not run.

`scripts/nvidia_live_smoke.py` makes one chat request and writes a sealed trace. Its docstring limits that run to the Nemotron-family trace path. A smoke run is provenance plumbing only. This checkout has no committed smoke trace.

`scripts/zetaris_check.py` sends the catalog `SELECT` statements and writes a sealed trace. The script calls no model.

## Single-repo demo

The single-repo demo trace is [`docs/evidence/single-repo-demo/`](evidence/single-repo-demo/). `trace.jsonl` in that folder has 10 `LLM_CALL` lines. `request_settings.chat_template_kwargs.enable_thinking` is true on 1 line (step `discovery`, `temperature` 0) and false on 9 lines (`temperature` 0).

## What this page does not claim

- A served id other than Ultra was generated by Ultra. Super-VL accounts for 18 answers used. Lightning accounts for 3.
- NVIDIA-direct success on the 3 `error` lines. The traces show 50 `ok` answers served Ultra, and 3 `error` lines with an empty `served_model`.
- A stored policy value, a stored temperature, or a stored thinking mode for the scored run.
- That 0/16 reasoning is anything other than not recorded.
- That the hash check is a verdict check or a rerun check.
- That v3 is an accuracy benchmark, that H-MEM passed official conformance, that a smoke run is an evaluation, or that `scripts/zetaris_check.py` called a model.
