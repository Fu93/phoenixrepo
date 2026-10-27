# Single-repo live demo trace (gate 1)

`trace.jsonl` in this folder is one live gate-1 run on one repository, `Selfdb-io/SelfDB`, run id `delivery-demo-20261027T133659Z`. It is kept as a demonstration of the workflow: a real run, a hash-chained trace, and a checker that reads it.

## What it is not

- It is not the scored v3 run. The scored v3 traces are the 16 under `data/replays/` described in `docs/SCORED_RUN_AUDIT.md`.
- It is not an accuracy result. It is one repository. No label comparison was made and no agreement figure is claimed.
- It is not a Gate 2 repair. No Gate 2 change, switch or verdict rule is exercised or claimed here.
- It is not the Zetaris run. Its `evidence_source` line records `csv` with `prior_rows` 0.

This folder claims no verdict for the repository.

## File

`trace.jsonl` is a byte-identical copy of the file as it was produced: 59 lines, 59538 bytes, SHA-256 `32e7ecc5f586254bb07bc044b7869fee0584746226dee0b2763dcc97de8d8b58`. Every line ends in CRLF. `.gitattributes` marks `*.jsonl` as `-text`, so git does not change those bytes. Do not re-save or reformat it: the checker below recomputes each line hash.

The run hash is the `line_hash` of the last line (`RUN_SEAL`): `b8233e543dd520f4614db707fbda303512321a8240d7c85c57e355aa1b57e472`.

## Verify

```
python scripts/verify_trace.py docs/evidence/single-repo-demo/trace.jsonl --require-nemotron --expected-run-hash b8233e543dd520f4614db707fbda303512321a8240d7c85c57e355aa1b57e472
```

Expected output (exit code 0):

```
b8233e543dd520f4614db707fbda303512321a8240d7c85c57e355aa1b57e472
request_settings: present on 10 of 10 LLM_CALL lines (informational; absence is not a failure)
nemotron: 10 of 10 answers used (providers: nightshift 4, nvidia-direct 6; discarded by policy: 0)
```

## Counts in the trace

Read from the 10 `LLM_CALL` lines. Reproducible with a few lines of Python over `requested_model`, `served_model`, `output_summary` and `request_settings`.

| | Count |
| --- | --- |
| Requested `nvidia/nemotron-3-super-120b-a12b` | 9 |
| Requested `nvidia/nemotron-3-ultra-550b-a55b` | 1 |
| Served `nvidia/nemotron-3-ultra-550b-a55b` | 8 |
| Served `nvidia/nemotron-3.5-super-vl-120b-a12b` | 2 (lines 49 and 51) |
| Provider Nightshift | 4 (no `provider` field; the line carries a `routing` block) |
| Provider nvidia-direct | 6 (`provider` is `nvidia-direct`; `fallback_reason` is `nightshift HTTP 502`) |
| Tier `fast` | 9 |
| Tier `deep` | 1 (the `discovery` step) |

The checker reports Nightshift for any line with no `provider` field, so the 4 is "no `provider` field", which is also what the Nightshift routing block lines look like.

## Thinking setting

`request_settings.chat_template_kwargs.enable_thinking` is `true` on 1 of the 10 `LLM_CALL` lines (line 56, the `discovery` step, requested and served model `nvidia/nemotron-3-ultra-550b-a55b`) and `false` on the other 9. This is the request the client sent. It does not show how the provider applied it. The trace does not record that.

## Reasoning excerpt

Line 56 also has a `reasoning` key inside its `output_summary` JSON string. Its value is 1205 characters long (Python `len` of the decoded string). It is an excerpt, not the full reasoning: `reasoning_excerpt` in `app/agents/relevance.py` keeps the first 600 and the last 600 characters (`REASONING_EXCERPT_CHARS` is 1200) and joins them with `"\n...\n"`, which is 5 characters. 1200 plus 5 is 1205. The length of the model's full reasoning is not in the trace. The other 9 `LLM_CALL` lines have no `reasoning` key.
