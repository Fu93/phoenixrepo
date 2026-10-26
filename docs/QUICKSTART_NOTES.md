# Quickstart notes

Notes from a fresh clone (Python 3.12, no `.env`, no API keys) so the README owner can apply doc fixes. This file is not the README. Do not describe the SAMPLE Nemotron card as a live Nightshift measurement.

## What was run

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q                  # fails: No module named pytest
python -m ruff check .               # fails: No module named ruff
python scripts/export_static_ui.py --out /tmp/ui/index.html
python scripts/verify_trace.py data/replays/invoice-already-exists/trace.jsonl
python scripts/import_replay.py data/replays/invoice-already-exists/trace.jsonl \
  --verdict data/replays/invoice-already-exists/record.json --replays-dir /tmp/replays
python scripts/compliance_check.py
python main.py                       # then open http://127.0.0.1:8000/ui
```

After `pip install -r requirements-dev.txt`, `python -m pytest -q`, `python -m ruff check .`, and `python scripts/compliance_check.py` are the CI commands.

## Works with no API key

- `pip install -r requirements.txt`, then `python main.py`. `GET /ui`, `GET /health`, and `POST /run` with `input_examples/example_1.json` stay in sample mode. No `METERLESS_RUNTIME_TOKEN`, `NVIDIA_API_KEY`, `GITHUB_TOKEN`, or `ZETARIS_API_KEY` is required. An empty environment is enough; do not copy `.env.example` unless you want the placeholders (they are treated as unset).
- `python scripts/export_static_ui.py` writes one HTML file. Open that file through any static server. It does not call Nightshift.
- `python scripts/verify_trace.py data/replays/<id>/trace.jsonl` on each shipped replay, including `nemotron-ui-fixture`.
- `python scripts/compliance_check.py` exits 0. It prints a Starlette warning: `Using httpx with starlette.testclient is deprecated; install httpx2 instead.` That warning is not a failure.
- Importing the bundled invoice trace works. Pass `--replays-dir` so the command does not write into `data/replays`.

## README edits to apply

The 30-second block installs only `requirements.txt`. The test commands are much further down and use `py -3`, which is not the Linux command.

Suggested replacement for the "Try it in 30 seconds" code block:

```bash
pip install -r requirements.txt
python main.py
# leave that process running, then open http://127.0.0.1:8000/ui
```

Suggested sentences to add next to that block:

- Tests and lint need the dev file: `pip install -r requirements-dev.txt`, then `python -m pytest -q` and `python -m ruff check .`. On Linux the interpreter is `python`, not `py -3`.
- The first card on `/ui` is `nemotron-ui-fixture`, a SAMPLE seal written by `scripts/build_replays.py`. It shows `2 of 2 answers by NVIDIA Nemotron` and the reasoning card with no token. It is not a Nightshift log and not a measured share. The other cards are SAMPLE seals over committed rows. None of them is a file from `logs/`.
- `python scripts/verify_trace.py <trace>` still checks the hash chain. It now also exits 1 when a line records a non-empty `served_model` outside `nvidia/nemotron-*`. The sample stand-in `deterministic-rule-standin` is allowed. A line with no `served_model` does not fail this check. `/ui` still shows PASS or FAIL from the hash chain only, so a recorded `MODEL_MISMATCH` can keep a valid chain. The tamper button flips one character and the chain check fails.
- A bundled import, without a private scores file:

```bash
python scripts/import_replay.py data/replays/invoice-already-exists/trace.jsonl \
  --verdict data/replays/invoice-already-exists/record.json \
  --replays-dir /tmp/replays
```

`--replays-dir` is required for a dry run. The default writes into `data/replays` and labels the copy RECORDED, which means the bytes were copied unchanged, not that Nightshift produced them.

- `python scripts/compliance_check.py` needs only `requirements.txt` (FastAPI's TestClient). `pytest` and `ruff` do not.

Suggested one-line addition under the trace section: a served model outside `nvidia/nemotron-*` makes `scripts/verify_trace.py` exit 1 after the chain matches; the message names the line and the model id.

## Code fixes in this change

- Shipped replays had no LLM lines, so the badge and the reasoning card never rendered. `data/replays/nemotron-ui-fixture` is a labelled SAMPLE fixture and is the first card.
- `scripts/import_replay.py` treated a replay `record.json` as a one-row verdict and stringified the whole verdict object into the title. It now reads `verdict.verdict`, the quote, gate 2, and `rescue_inputs`.
- The import banner no longer says the source "is not a SAMPLE seal".
- `scripts/verify_trace.py` (the command, not the hash function the page uses) rejects a non-Nemotron `served_model`. Fixtures: `tests/fixtures/traces/nemotron_clean.jsonl` and `tests/fixtures/traces/non_nemotron_served.jsonl`.
- `/ui` no longer calls the opening card "a full recorded refusal". Empty or non-http quote links are text, not `href="#"`. Catalog and tamper failures show a message instead of an unhandled rejection. `data` and `trace` are separate chips. The hash line no longer says "this page's seal is a different hash" next to PASS. The sample stand-in's reasoning is not titled "Nemotron's reasoning".
- CI runs the static-export smoke, the served-model check, and the bundled import after the suite.

## Still open

- No committed trace is a Nightshift log. Do not quote the fixture's `2 of 2` as the model's share in the submission or the video.
- The other four cards still have an empty served-model table. That is intentional: those files have no LLM lines. The "Not in the repository" box still records that no model `NEEDS_REVIEW` verdict and no historical gate-2 trace are committed.
- Invoice cards keep `data RECORDED` and `trace SAMPLE` together. The chips are labelled, and the banner says the chain is a SAMPLE seal. A judge can still misread "recorded" in the button title.
- The live judge box stays disabled until `METERLESS_RUNTIME_TOKEN` is a real token. Replay mode does not need it. `POST /judge` will not run in the no-key demo.
- GitHub Pages (`https://fu93.github.io/phoenixrepo/`) updates only after a push to `main` that matches `.github/workflows/pages.yml`. A clone should use `/ui` or `python scripts/export_static_ui.py`, not the previous Pages build.
- The page needs JavaScript. A `<noscript>` note was added. There is still no no-JS rendering of the timeline.
- `compliance_check.py` warns about Starlette's TestClient. It does not fail.
- Docker (`docker build -t phoenixrepo . && docker run --rm -p 8000:8000 phoenixrepo`) was not run in this pass.
- Re-running `python scripts/build_replays.py` rewrites every replay trace, because each seal includes a new timestamp. The fixture is included in that script. Do not run it just to refresh one card.
