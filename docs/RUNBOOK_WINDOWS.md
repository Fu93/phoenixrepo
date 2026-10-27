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

## 1a. Replace the GitHub token first (2 minutes)

The current `GITHUB_TOKEN` gets HTTP 401. With it, every GitHub search failed and every repository became `NEEDS_REVIEW`; the code now drops a rejected token and continues, but unauthenticated GitHub allows only 60 requests per hour, which one or two judgements use up. Create a fine-grained token at github.com → Settings → Developer settings → Personal access tokens, with **public repositories, read-only** and no other permission, and put it in `.env` as `GITHUB_TOKEN=`. Check it:

```powershell
py -3 -c "import os,urllib.request as u; from app.console import load_env_file; load_env_file(); r=u.urlopen(u.Request('https://api.github.com/rate_limit', headers={'Authorization':'Bearer '+os.environ['GITHUB_TOKEN']})); print(r.status)"
```

`200` is good. The token is never printed or written to a trace.

## 1b. Make every verdict a Nemotron verdict (for the NVIDIA prize)

The goal is a trace where every answer that informed a verdict says `served_model: nvidia/nemotron-*`. TODO(maintainer): the "18 of 18" Nightshift probe count is not in a committed probe file (`docs/NIGHTSHIFT_PROBE.md` says no redacted probe output is in this repository). Do not state that count until `logs/nightshift_probe-*.json` is committed. Use `require` when a probe you can show says Nemotron is served most of the time: it guarantees the family, and the served size is recorded per line.

Recommended `.env` for the first real run (then tighten from the measured latencies):

```
PHOENIX_NEMOTRON_POLICY=require
PHOENIX_LLM_ATTEMPT_TIMEOUT_S=180
PHOENIX_LLM_TOTAL_TIMEOUT_S=360
PHOENIX_ALTERNATIVES_BUDGET_S=900
PHOENIX_LLM_DEEP_EXTRA_BODY={"temperature":1.0,"top_p":0.95}
```

The last line is what NVIDIA's Ultra model card suggests (temperature 1.0, top_p 0.95), applied only when `MODEL_NAME_DEEP` is set. It is not the scored run. The scored-run .env is not in the repo, and the scored v3 traces do not record temperature or thinking mode. No claim about thinking mode or temperature is made for the scored v3 run. `app/agents/relevance.py` sets a code default of temperature 0 and then merges the tier extra body; that default is not a record of what the scored run sent. TODO(maintainer): commit the scored-run .env, or a trace that records request_settings, before stating temperature or enable_thinking for the scored v3 run. Experiment E3's discovery lines record temperature 1.0 and top_p 1.0, which is not the model-card pair. The line does nothing unless `MODEL_NAME_DEEP` is set.

180 s per attempt is three times the slowest probe reply (57 s) and below the ~330 s at which Nightshift's 502 used to arrive, so a hung call is cut sooner. After the pagerbeauty run, the report's served-model table shows the median and maximum seconds per model; set the attempt timeout to about twice that maximum.

1. Copy the probe's lines into `.env`. When it found a second, larger Nemotron it also prints `MODEL_NAME_DEEP`. Two requested tiers: Super for bulk checks, Ultra for the verdict. On the scored run the `nemotron-evidence` slot says fast-tier answers used were Ultra 174 and Super-VL 18; the 16 verdicts were served `nvidia/nemotron-3-ultra-550b-a55b`. The same slot's no-tier row is Ultra 7 and Lightning 3. That slot is not a provider split. The scored traces recorded no reasoning text for the final verdict. The counter that wrote them recognised only a separate reasoning field or a complete `<think>…</think>` pair (0 of 16 in that slot), and Nightshift's answers carry reasoning inline before a bare `</think>`, so the scored traces cannot say whether the model reasoned. The scored-run .env is not in the repo, and the scored v3 traces do not record temperature or thinking mode. No claim about thinking mode or temperature is made for the scored v3 run. `app/agents/relevance.py` sets a code default of temperature 0 and then merges the tier extra body; that default is not a record of what the scored run sent. TODO(maintainer): commit the scored-run .env, or a trace that records request_settings, before stating temperature or enable_thinking for the scored v3 run.
2. Choose the policy in `.env`:
   - `PHOENIX_NEMOTRON_POLICY=require`: a non-Nemotron answer is discarded and asked again through Nightshift. Use it when the probe shows Nemotron is served most of the time.
   - `PHOENIX_NEMOTRON_POLICY=fallback-nvidia`: a non-Nemotron answer, a timeout or a 5xx is replaced by the same Nemotron model at the NVIDIA API. Needs `NVIDIA_API_KEY` in `.env` (the key you added to Nightshift's Providers tab). This line is marked `provider: nvidia-direct` in the trace, so it is visible that it bypassed Meterless.
3. Judge one repository and check the share:

```powershell
py -3 scripts/judge_repo.py https://github.com/<owner>/pagerbeauty
```

The JSON it prints ends with `answers_used` and `answers_by_nemotron`; the report page shows the same line.

4. Measure accuracy on the human labels, capped and resumable:

```powershell
py -3 scripts/eval_gate1.py --audit-only --deadline 7200 --out logs/eval-nemotron.json
py -3 scripts/eval_gate1.py --audit-only --deadline 7200 --resume logs/eval-nemotron.json --out logs/eval-nemotron.json
```

The report is written after every case. `agreement`, `live_error_rates` and `models` (answers by Nemotron) are the numbers for the README and the video. A rerun with `--resume` asks again only for cases that are missing or ended in an infrastructure error.

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

## 5b. Publish the hosted demo (once, 2 minutes)

GitHub → the repository → Settings → Pages → Source: **GitHub Actions**. Then Actions → `pages` → Run workflow (or push to `main`). The page is at https://fu93.github.io/phoenixrepo/. After importing a real trace into `data/replays/`, the next push to `main` republishes it.

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
