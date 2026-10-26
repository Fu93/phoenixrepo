# Runbook: the last live runs on Windows (26 to 27 Oct 2026)

Exact commands for the author's Windows machine, in order. Each step says how long it takes, what it changes, and what to do if it fails. PowerShell, from the repository root, on an up-to-date `main`.

## 0. Once per window (2 minutes)

```powershell
chcp 65001
$env:PYTHONUTF8 = "1"
git pull
py -3 -m pip install -r requirements-dev.txt   # also installs ruff
py -3 -m ruff check .
```

`chcp 65001` and `PYTHONUTF8` stop the Chinese-locale console from garbling UTF-8 output. The entry points also switch their own output to UTF-8.

## 1. What does Nightshift actually serve? (10 to 15 minutes, changes nothing)

```powershell
py -3 scripts/nightshift_probe.py
```

It lists the `nvidia/nemotron-*` ids Nightshift offers, calls each with three request variants, and prints status, latency and served model per call. The report is `logs/nightshift_probe-<utc>.json`.

- **Exit 0**: some variant was served by Nemotron. Copy the printed `MODEL_NAME=` and `PHOENIX_LLM_*` lines into `.env`.
- **Exit 1**: nothing was served by Nemotron. In the Nightshift Console, Providers tab, temporarily turn off the non-NVIDIA providers (Gemini, OpenRouter), then run the probe again. If it still exits 1, keep `MODEL_MISMATCH` and write the probe result in the README and the video. Do not hide it.
- **Exit 2**: token missing or Nightshift not reachable.

Changing `MODEL_NAME` changes the relevance cache key, so earlier finished calls are not reused. Plan reruns for that.

Shorter timeouts help only once a faster lane is found. With healthy calls of up to 345 s and 502s at about 330 s, a cap cannot tell them apart. Without a faster lane, keep the defaults and bound each repository instead: `$env:PHOENIX_ALTERNATIVES_BUDGET_S = "900"`.

## 2. Zetaris read-only check (5 minutes, sends only SELECT)

Start Docker and Zetaris first. Then:

```powershell
py -3 scripts/zetaris_check.py --snapshot data/real
```

- **Exit 0**: every candidate row came back. The printed `content_hash` values are what the trace records.
- **Exit 1**: Zetaris answered, but some rows are missing: the views were not loaded with this snapshot. Loading them (`scripts/zetaris/load_real.ps1`) runs `CREATE` statements. That is a write, so it is a separate decision.
- **Exit 2**: Zetaris did not answer. For runs while Docker is off, set `$env:EVIDENCE_SOURCE = "csv"`. The trace and every verdict row then say `evidence_source=csv`.

With `ZETARIS_URL` set and Zetaris stopped, a batch now stops at once with one error (PR #18). Before that change, every repository came back `NEEDS_REVIEW`.

## 3. Rerun the unfinished repositories (one at a time, capped)

```powershell
py -3 scripts/rerun_unjudged.py `
  --scores logs/<r5 run>_scores.json --snapshot <r5 snapshot dir> `
  --output-json logs/r5-rerun_scores.json --output-md logs/r5-rerun_scores.md `
  --backoff 30 --max-attempts 2 --deadline 5400
```

This reruns only `UNJUDGED` rows and infrastructure `NEEDS_REVIEW` rows, one repository at a time. `--deadline 5400` stops starting new ones after 90 minutes, and repositories not started keep their old rows (`not_started` in the output). `--repo owner/name` limits the rerun to one repository.

Skip the 30-repository before/after rerun. At 5 to 21 minutes per repository it does not fit before the deadline.

## 4. Put a real run into `/ui` (5 minutes)

```powershell
py -3 scripts/verify_trace.py logs/trace-<run id>.jsonl
py -3 scripts/import_replay.py logs/trace-<run id>.jsonl --verdict logs/<run id>_scores.json --repo <owner>/pagerbeauty
py -3 main.py   # then open http://127.0.0.1:8000/ui
```

The import copies the trace byte for byte and refuses a trace that does not verify or that contains a token-shaped string or a credential from this machine. Commit `data/replays/` on a branch and open a PR.

## 5. Gate 2, only if a CANDIDATE appears (optional, up to 1 hour)

```powershell
py -3 scripts/revive.py https://github.com/<owner>/<repo> --deadline 3600
```

Do not pass `--gate-verdict` for a repository you will show as a candidate; let gate 1 judge it live. This is not on the critical path.

## 6. Housekeeping (2 minutes)

Empty report folders:

```powershell
Get-ChildItem reports -Directory -Filter "judge*" | Where-Object { -not (Get-ChildItem $_.FullName -Recurse -Force) } | Remove-Item
```

Open PRs to close on GitHub: #14 (superseded by #18), #12 (superseded by #13), and #4 (blueprint docs from 22 Oct; merge or close, so the PR list is clean for the judges).

## 7. Before each submission

```powershell
py -3 -m ruff check .
py -3 -m pytest -q
py -3 scripts/compliance_check.py
```

Then follow `docs/SUBMISSION_DRAFT.md` section (v).
