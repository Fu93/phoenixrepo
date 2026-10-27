# Judge card: Best use of NVIDIA Nemotron

Six questions. Each answer cites a file on `main`. Read a model line as Requested, Served, and Policy. Those are three facts. Served is the `served_model` on the line. Say `nvidia-direct` only when the line stores `provider` `nvidia-direct`.

The scored v3 traces are the 16 RECORDED replays under `data/replays/` other than `zetaris-gate1-tharani-2006-invoice-generator-application`, `e3-goodreasonai-scrapeserv`, and `e3-dsregoitia-calendarsyncproject`. They are separate from the Zetaris gate-1 trace.

## 1. How do I reproduce the 27-of-27 Nemotron answers?

The 27-of-27 count is the Zetaris gate-1 trace, not a scored v3 trace.

The committed file is `data/replays/zetaris-gate1-tharani-2006-invoice-generator-application/trace.jsonl`. `git log` shows that file was added in pull request #46 (`15bb62a`). Pull request #58 corrected the path in `README.md` so it names this file. The original path `logs/trace-zetaris-gate1.jsonl` is gitignored (`README.md`).

Check that file with the same flags the README quickstart uses:

```bash
python scripts/verify_trace.py data/replays/zetaris-gate1-tharani-2006-invoice-generator-application/trace.jsonl --expected-run-hash 4f340332cd1349884bb5355d471f03e89d5b778cf378ccb20ccda4b74472ebad --require-nemotron
```

That command is the one in the Zetaris section of `README.md`. On this checkout it exits 0 and prints:

```text
4f340332cd1349884bb5355d471f03e89d5b778cf378ccb20ccda4b74472ebad
request_settings: present on 0 of 27 LLM_CALL lines (informational; absence is not a failure)
nemotron: 27 of 27 answers used (providers: nightshift 21, nvidia-direct 6; discarded by policy: 0)
```

The word `nightshift` in that print is the name `scripts/verify_trace.py` uses when the line has no `provider` field. It is not a stored value. `discarded by policy` in that print is the verifier's count. Policy is not a field on these lines.

The quickstart itself runs the flags on a different file, the scored SelfDB trace:

```bash
python scripts/verify_trace.py data/replays/selfdb-io-selfdb/trace.jsonl --expected-run-hash 147b7b32cbf0141079a58ffe71dc832d1092e81dfd42e512332ad4579e7fd711 --require-nemotron
```

`README.md` records the SelfDB result as 10 of 10 answers used. That file is not the 27-of-27 trace.

`scripts/verify_nemotron_evidence.py` is on this checkout. It was added in merged pull request #57 (`fd19999`). It takes no network and no model call. It recomputes the scored v3 figures and requires 19 RECORDED replays to pass, which includes the Zetaris replay. It does not print the 27-of-27 line.

`docs/SCORED_RUN_AUDIT.md` states this about the Zetaris gate-1 trace, not about the scored v3 traces: 27 of 27 answers used were Nemotron's, 21 through Nightshift and 6 through nvidia-direct.

Counted on that file: 27 `LLM_CALL` lines with status `ok`. The 21 have no `provider` field and carry a routing block with `winning_provider`. The 6 store `provider` `nvidia-direct` and `routing.fallback_reason` `nightshift HTTP 502`. Served on those 6 is `nvidia/nemotron-3-ultra-550b-a55b`. Across the 27, Served is `nvidia/nemotron-3-ultra-550b-a55b` on 24 lines and `nvidia/nemotron-3.5-super-vl-120b-a12b` on 3. Requested is `nvidia/nemotron-3-super-120b-a12b` on 25 lines and `nvidia/nemotron-3-ultra-550b-a55b` on 2. Policy is not a field on these lines. `_observe` in `app/agents/relevance.py` writes `provider` only when it is not `nightshift`. `scripts/verify_trace.py` prints the word `nightshift` for a missing `provider` field. That print is not a stored value (`README.md`).

The run hash on the `RUN_SEAL` line is `4f340332cd1349884bb5355d471f03e89d5b778cf378ccb20ccda4b74472ebad`.

## 2. Where is the why of each verdict?

None of the 16 scored trace files contains a `reasoning` field. `README.md` Limitations says a reasoning field is present on 0 of 16 scored traces. `docs/RUNBOOK_WINDOWS.md` says the counter that wrote them recognised only a separate reasoning field or a complete `<think>…</think>` pair (0 of 16 in that slot), so those traces cannot say whether the model reasoned.

The scored v3 traces do not record temperature or thinking mode; neither is inferred from code defaults or later runs.

The recorded why is the `reason` string on the `DISCOVERY_FILTER` line, together with the verbatim quote check `quote_in_text` in `app/agents/relevance.py`. Whether an alternative may refuse the repository is `evaluate_blocker` in `app/agents/blocker.py`. Its docstring says `blocks` is true only when A-E all hold.

`docs/SCORED_RUN_AUDIT.md`: eight of the twelve v3 `CANDIDATE` reasons start with `must beat <name>: did not block`. That string is written in `app/agents/relevance.py` when the model verdict is `ALREADY_EXISTS` and the named alternative fails A-E. The audit marks this **inferred** from the reason format. The v3 trace does not record the raw model verdict. All 4 false passes take this path. The other four `CANDIDATE` reasons do not. A count of the 16 `DISCOVERY_FILTER` lines finds no `raw_verdict` field, and 8 reasons start with `must beat`.

Pull request #56 is merged. `reasoning_excerpt` in `app/agents/relevance.py` can also copy text before a lone `</think>` when a JSON object follows. That code is post-v3. It did not rewrite these 16 traces. No committed file is a re-score of the 16. It is not recorded here that a later live run used that function.

`README.md` Limitations also says the scored v3 traces do not record `PHOENIX_NEMOTRON_POLICY`. Policy is not a field on those lines.

## 3. Has gate 2 been run on a real CANDIDATE with Nemotron?

No, not verified execution. `README.md` Limitations says gate 2 verified execution has not run on a real `CANDIDATE` here. `docs/GATES.md` says the same for install and tests on the host. The words "mocked answers" are not in those Limits.

The gate-2 tests script the model. `tests/test_revive_prd_verify.py` says the model is scripted and pytest on the fixture is real. `tests/test_revive_loop.py` uses a `complete_json` stand-in that replays scripted objects.

One committed rescue plan is not verified execution. The file is `docs/rescue/trace-rescue-0xmh__fichero-printer-20261027T001117Z.jsonl`. Its `LLM_CALL` stores Requested and Served `nvidia/nemotron-3-ultra-550b-a55b`, `provider` `nvidia-direct`, and `routing.fallback_reason` `nightshift HTTP 502`. `README.md` says `verify_trace.py --require-nemotron` with run hash `51e828e538ed8f05dda15648fc673c3461ca310a87c0af77c53482e1f0a086c4` prints 1 of 1 answers used, providers nvidia-direct 1. It is a rescue plan, not a gate-1 verdict.

`app/revive_verify.py` says the verifier rejects a diff that deletes, skips, or loosens tests. `verify_revival` sets `resurrection_allowed` false when tests were weakened, final checks are worse than the baseline, no kept item has a passing acceptance check, the baseline was not broken or outdated, or the diff scope failed. `weakened_test_diff` also fails a change to `scripts.test` or `scripts.pretest` in `package.json`. `tests/test_revive_prd_verify.py` (`test_verifier_rejects_a_deleted_or_loosened_test_even_when_the_run_is_green`) shows a green run still fails when an assertion is replaced with `assert True`, and when a test file is removed.

## 4. Zetaris evidence versus the CSV evidence source

Each of the 16 scored traces has an `evidence_source` line whose output is `evidence_source=csv` and `prior_rows=0`. `docs/SCORED_RUN_AUDIT.md` says no scored verdict used a Zetaris query or a `prior_art` row.

The 2-repository gate-1 run is a different file. Its `evidence_source` line is `evidence_source=zetaris` and `prior_rows=6`. `README.md` records the command as `EVIDENCE_SOURCE=zetaris` and `python scripts/score_candidates.py --snapshot data/real --llm`, on 26 Oct 2026, run hash `4f340332cd1349884bb5355d471f03e89d5b778cf378ccb20ccda4b74472ebad`. The two `DISCOVERY_FILTER` lines are `tharani-2006/Invoice-generator-application` `ALREADY_EXISTS` and `SergioMendolia/ytkb` `CANDIDATE`, both with `evidence_source` `zetaris`. `README.md` says this run is not part of the scored table. The scored Invoice trace is `CANDIDATE`.

The Invoice `prior_art` query in the Zetaris trace selects `licence_spdx`, `stars`, and `pushed_at` and records `row_count` 1. The ytkb `prior_art` query records `row_count` 5. `data/real/prior_art.csv` has the `al1abb/invoify` row: MIT, 6376 stars, pushed `2026-08-24T02:55:34Z`. Those digits are not in the trace file. The scored Invoice trace has `prior_rows=0` and does not contain `invoify`. The Zetaris `DISCOVERY_FILTER` for Invoice names alternative `al1abb/invoify`.

`scripts/zetaris_check.py` calls no model. The module docstring says "No model call." The file loads rows, prints query ids and content hashes, and seals a trace. It writes no verdict. `README.md` says that sealed trace is not in this checkout.

H-MEM in this repository is its own implementation of the public Meterless spec (`app/engines/hmem/`, spec URL in `metadata.json`). `metadata.json` says the official TypeScript conformance suite was not run. The Zetaris gate-1 trace has no `HMEM_VERDICT_MEMORY` line. The scored 16 traces have none either (`README.md`).

## 5. CalendarSyncProject gave different verdicts across runs

`docs/SCORED_RUN_AUDIT.md` does not name CalendarSyncProject. A cause for the difference is not recorded there.

What is recorded:

- Scored trace `data/replays/dsregoitia-calendarsyncproject/trace.jsonl`: `DISCOVERY_FILTER` verdict `NEEDS_PRIOR_ART`, reason `Zero alternatives checked in prior-art search.` Run hash `2eb5a1e3157e3f5a148ede12fc06d4ca6f4e7a8a7262d7f2c0a5326569a56cd0` (`README.md` and `record.json`).
- Separate Experiment E3 replay `data/replays/e3-dsregoitia-calendarsyncproject/trace.jsonl`: `DISCOVERY_FILTER` verdict `CANDIDATE`. `README.md` says this replay is not the scored run. Its reason starts with `must beat lexancalled/ontask: did not block`.
- `README.md` says a `NEEDS_REVIEW` trace for CalendarSyncProject is not in `data/replays/`. This checkout has no third CalendarSync trace.
- Human label in `data/eval/gate1_labels.json`: acceptable set `CANDIDATE` or `NEEDS_REVIEW`. The `why` field is `WRONG. FullCalendar displays public Google Calendar events. The candidate pushes events the other way.`

The scored table in `README.md` marks `NEEDS_PRIOR_ART` as a disagreement with that label. This card does not add a cause.

## 6. Four of five must-reject cases passed as CANDIDATE

`README.md` calls the scored v3 set a regression and audit set: 16 cases, 10 agree, 5 must-reject, 4 false passes. It is not an accuracy benchmark.

The 4 false passes are `tharani-2006/Invoice-generator-application`, `goodreasonai/ScrapeServ`, `rianvdm/raindrop-wordpress-linkblog-sync`, and `Shabi-x/go-file-sync`, each `CANDIDATE` where the human `acceptable` set is `ALREADY_EXISTS`. The fifth must-reject case, `Selfdb-io/SelfDB`, is `ALREADY_EXISTS` and agrees (`README.md`).

`docs/SCORED_RUN_AUDIT.md`: the gate overturned the model's answer on that path. That is **inferred** from the reason format. The v3 trace does not record the raw model verdict. The audit says nothing below it is marked fixed.

The 10/25 batch is human audit labels only, no traces. `README.md` gives 5 of 16, 11 of 11, and 0 of 5. Those counts are recomputed from `data/eval/gate1_labels.json`: 16 `audit` objects dated 2026-10-25, classes LEGIT 5, WEAK 8, WRONG 3. The file stores no model id. A case in that row agrees when `acceptable` contains `ALREADY_EXISTS` (5 of 16). Should-pass cases that do not list `ALREADY_EXISTS` are 11, all class WEAK or WRONG, so over-refusals are 11 of 11. Must-reject cases are 5, all class LEGIT, so false passes are 0 of 5.

Open pull-request status below is as of 27 Oct 2026 20:20 UTC.

Pull requests #59, #62, #65, and #66 are open and not merged. `README.md` and `docs/SCORED_RUN_AUDIT.md` say each switch is default off (`1`, `true`, `yes`, `on`, or `enabled`; unset stays off) and is not part of any reported number. The names `PHOENIX_UNKNOWN_BLOCKER_REVIEW`, `PHOENIX_WEB_GITHUB_API_LOOKUP`, `PHOENIX_QUOTE_FAIL_FLAG`, and `PHOENIX_COMMODITY_CATEGORY_RULE` are not in the Python on this checkout. No mechanism-level change from those pull requests is merged here.

Pull request #63 is open and not merged. It is not on this checkout.

Pull request #67 (`GATE_DECISION`) was merged at 18:00 UTC (`37ab838`). It records a `GATE_DECISION` line when the blocker gate keeps or overturns the model.
