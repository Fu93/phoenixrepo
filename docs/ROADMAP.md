# PhoenixRepo — Roadmap to 27 Oct 2026 23:45 UTC

Written 22 Oct 2026 ~17:00 BST. Times are UK time with zone labels. **BST ends 25 Oct 02:00 → from then UK = GMT = UTC.** Hard deadline: **Tue 27 Oct 23:45 UTC (= 23:45 GMT UK)**. Our target: **submitted by Tue 27 Oct 17:45 GMT** (T-6h); ties break on submission timestamp, so earlier is better.

Owners: **U** = 弘軒 (Windows-only work: Zetaris, Nightshift, live runs, recording, form) · **CA** = Cursor cloud agent · **LC** = local Cursor on your PC (fallback for any CA task; prompts in §5).

## 繁體中文摘要

1. 今晚：合併本 PR（文件），審 PR #3，在 Windows **第一次跑 live**（§3 的指令），量測延遲 / 429。
2. 週五：hygen fork 先跑一次「沒改動的紅燈 CI」，再做 Stage 1（**actions 升級要放進 Stage 1**）；provider fallback 實作。
3. 週六：Stage 2/3、collector 讀回、Zetaris 重新載入、hygen 變 `RESURRECTED`；晚上功能凍結。
4. 週日（改冬令時間）：README、簡報、影片腳本、錄第一版。
5. 週一：乾淨環境演練、重錄、填表單存草稿。週二 17:45 GMT 前送出。
6. 額度用完時：§5 有每個任務可直接貼進本機 Cursor 的提示詞。

---

## 1. Task list

| ID | Task | Owner | Depends | Done when |
| --- | --- | --- | --- | --- |
| T1 | Merge this docs PR; read PR #3 | U | — | merged |
| T2 | First Windows live run + provider measurement | U | T1 | `logs/demo-live.md` + `logs/provider-measurements.md` exist with real numbers |
| T3 | Zetaris scripts: confirm PR #3's compose/path fix; make `load_real.ps1` treat HTTP 400 + "already exists" as exists; JSON-null cell test | CA (reply on PR #3) | — | re-run of `load_real.ps1` exits 0; test added |
| T4 | Provider lanes/fallback/budget/cache/backoff + degrade path + refuse direct NVIDIA in live + Markovian limits (ARCHITECTURE §4, §7, §8) | CA (new branch) | T2 numbers help | tests for 429/timeout/401/all-lanes-down; trace has `LLM_CALL`/`LLM_FALLBACK` |
| T5 | hygen fork: baseline red run, then Stage 1 (Node matrix + checkout/setup-node v4) | CA on `Fu93/hygen` or LC | — | fork `CI` green on `master` (ubuntu + windows) |
| T6 | PR #3 follow-ups: Stage 1 content (PRD §8.0), no-attempt ≠ RESURRECTED decision (PRD §5.2), branch/head check, failed_step, `ci_log` source | CA (reply on PR #3) | user decision | PR #3 tests green; merged by U |
| T7 | hygen Stage 2 (ejs ≥3.1.10, degit ≥2.8.6) and Stage 3 (#450 `--version`) | CA on fork or LC | T5 | both green on fork; acceptance in PR #3's PRD + PRD §8 |
| T8 | Close the loop: collector → reload Zetaris → re-run → `RESURRECTED`; render trace | U | T5, T6 | live `POST /run jondot/hygen` = RESURRECTED citing the fork run URL |
| T9 | Sponsor spikes S1 (GitHub REST table), S3 (audit DB), N4 (`/v1/meterless/classes`) — keep only what works | U | T2 | each marked verified/unverified in ARCHITECTURE §13 |
| T10 | README + `metadata.json` refresh: live results, RESURRECTED meaning, declarations | CA or LC | T8 | no "not verified" text for things now verified; no fabricated numbers |
| T11 | Video: script (§4.1), record, subtitles, upload | U | T8 | 1–4 min, English, captions, public/unlisted link works logged out |
| T12 | Deck (§4.2) | U (+ LC) | T8 | covers every item the rules list |
| T13 | Written submission + form (PRD Appendix A/B) | U | T11, T12 | submitted, confirmation seen |
| T14 | Clean-clone QA (§6) | U | T10 | checklist all ticked |
| T15 | Bonus: n8n weekly monitor (ARCHITECTURE §15) | U/LC | T8 | only if time; never blocks |

## 2. Day by day

### Day 0 — Thu 22 Oct (evening, BST)
- [ ] T1 merge docs PR (U).
- [ ] T6: reply to the PR #3 cloud agent with the Stage 1 correction (PRD §8.0) and the no-attempt decision (U decides; CA implements).
- [ ] T2: Windows live run (§3.1–3.4). Expect problems; write them down in `logs/demo-live.md` notes.
- [ ] T2: provider measurement (§3.5) once the live run works.
- [ ] T3 to CA if `load_real.ps1` still exits 1 on re-run.
- Cut line: if the live run fails tonight, Day 1 morning is only fixing it.

### Day 1 — Fri 23 Oct (BST)
- [ ] T5 hygen baseline red run + Stage 1 → green (CA/LC; U clicks "enable workflows" on the fork Actions tab if asked).
- [ ] T4 provider fallback (CA), using T2's real 429/latency numbers.
- [ ] Open the hackathon submission form; note every field and limit into `docs/SUBMISSION_FIELDS.md` (U, R20). Select **Track 02**. Create the draft (solo team: roster lock is harmless).
- [ ] T9 spikes S1, S3, N4 (U, ≤1 h total; stop at the time box).

### Day 2 — Sat 24 Oct (BST)
- [ ] T7 Stage 2 and Stage 3 on the fork (CA/LC).
- [ ] T6 merged (U), then T8 end-to-end live (U): hygen flips to RESURRECTED.
- [ ] Re-run all three demo repos live; save traces + HTML renders.
- [ ] **Feature freeze 22:00 BST.** After this: bug fixes, docs, assets only.

### Day 3 — Sun 25 Oct (clocks go back 02:00; now GMT)
- [ ] T10 README/metadata refresh (CA/LC).
- [ ] T12 deck draft.
- [ ] T11 script final, record v1, subtitle (.srt), watch it once as a judge.

### Day 4 — Mon 26 Oct (GMT)
- [ ] T14 clean-clone QA.
- [ ] Re-record anything broken (v2). Upload video early.
- [ ] T13 fill all form fields; save draft; check character limits.
- [ ] Judge Q&A prep (§7).

### Day 5 — Tue 27 Oct (GMT = UTC)
- [ ] Morning: last re-read of rules (https://hackathon.genai.works/event/open-agent-hackathon-2026), links open logged out.
- [ ] **Submit by 17:45 GMT.** Reopen the public page; screenshot the confirmation.
- [ ] 23:45 GMT hard deadline. No pushes to `main` after submitting unless the rules allow it.

## 3. Exact commands — Windows live run

Never paste secret values into files, chats, or the video. The commands below read them into the current shell only.

### 3.1 WSL: Zetaris with the allowed-path mount
```bash
cd ~/zetaris/zetaris-platform
docker compose ps                                  # expect services api, driver, postgres, ui
docker compose exec driver printenv ZETARIS_LOCAL_FILE_SYTEM_ROOT_PATH
# expect: /home/zetaris/customer-data/shared   (verified 22 Oct 16:30 BST)

# Path of the repo's data/real as WSL sees it (example; use yours):
export PHOENIX_REAL_DATA=/mnt/c/Users/<you>/<path>/phoenixrepo/data/real

# Opt-in compose file. With PR #3 merged it is in the repo; on main use an untracked copy:
#   services:
#     driver:
#       volumes:
#         - ${PHOENIX_REAL_DATA:?PHOENIX_REAL_DATA must be set inside WSL}:/home/zetaris/customer-data/shared/phoenix_real:ro
# Use the same -f files you normally start the platform with, plus this one:
docker compose -f docker-compose.yml -f /mnt/c/Users/<you>/<path>/phoenixrepo/scripts/zetaris/phoenix-real-data.compose.yml up -d driver
docker compose exec driver ls /home/zetaris/customer-data/shared/phoenix_real
# expect repo_evidence.csv, revival_signals.csv (+ revival_attempts.csv, revival_sources.csv after PR #3)
```
After the collector rewrites `data/real/*.csv`, the bind mount sees the new files immediately; Zetaris FILESTORE tables re-read on query (if a stale result appears, re-run `load_real.ps1`; caching behaviour not verified).

### 3.2 PowerShell terminal A — environment + API (live)
```powershell
cd C:\path\to\phoenixrepo
Set-ExecutionPolicy -Scope Process Bypass -Force
.\.venv\Scripts\Activate.ps1          # first time: py -3 -m venv .venv; then pip install -r requirements.txt
$env:SAMPLE_MODE = "false"
$env:LLM_PROVIDER = "meterless"
$env:METERLESS_BASE_URL = "http://127.0.0.1:8088/v1"
$env:MODEL_NAME = "nvidia/nemotron-3-super-120b-a12b"     # or leave empty for meterless/auto
$env:METERLESS_RUNTIME_TOKEN = (Get-Content "$env:LOCALAPPDATA\ai.meterless.runtime\proxy-token" -Raw).Trim()
$env:ZETARIS_API_KEY = [Environment]::GetEnvironmentVariable("ZETARIS_API_KEY", "User")   # if stored as a user env var
python scripts\test_meterless_connection.py        # do NOT use --auto-detect output on port 11434 (Ollama) as the base URL
python smoke_test.py
python run.py                                      # leave running
```
(Use `python` from the activated venv; `py -3` may pick the global interpreter instead of the venv.)

### 3.3 PowerShell terminal B — load Zetaris, run the demo
```powershell
cd C:\path\to\phoenixrepo
Set-ExecutionPolicy -Scope Process Bypass -Force
.\.venv\Scripts\Activate.ps1
$env:METERLESS_RUNTIME_TOKEN = (Get-Content "$env:LOCALAPPDATA\ai.meterless.runtime\proxy-token" -Raw).Trim()
$env:ZETARIS_API_KEY = [Environment]::GetEnvironmentVariable("ZETARIS_API_KEY", "User")
powershell -ExecutionPolicy Bypass -File scripts\zetaris\load_real.ps1 -ContainerPath /home/zetaris/customer-data/shared/phoenix_real
# Known bug on main: a re-run exits 1 because Zetaris answers HTTP 400 "already exists". If so, verify in the UI (localhost:3000):
#   SELECT repo_id, data_origin FROM phoenix.repo_evidence     -> 5 rows
#   SELECT COUNT(*) FROM phoenix.revival_signals               -> 19
powershell -ExecutionPolicy Bypass -File scripts\demo_live.ps1
# expect lines: docker ok / nightshift_models 200 / api_sample_mode_false false / zetaris_select_1 200,
# then golang/dep, jondot/hygen, 42wim/matterbridge with HTTP status + decision. Output: logs\demo-live.md
```

### 3.4 One repo by hand + trace render
```powershell
'{"repository":"jondot/hygen","mode":"live","max_repair_iterations":0}' | Set-Content -Encoding ascii logs\hygen.json
curl.exe -s -X POST http://127.0.0.1:8000/run -H "Content-Type: application/json" --data-binary "@logs/hygen.json" -o logs\hygen-response.json
python -c "import json;r=json.load(open('logs/hygen-response.json'));print(r.get('decision'),r.get('log_file'));print(r.get('metadata',{}).get('llm'))"
python scripts\render_trace.py <log_file from above> --format html --output logs\trace-hygen.html
```
If `demo_live.ps1` reports a timeout on `/run`, the PowerShell request timeout may be the cause (R18): run the curl form above, which has no default timeout, and ask CA/LC to add `-TimeoutSec 900` in `demo_live.ps1`.

### 3.5 Provider measurement (T2) — prints status and latency only
```powershell
$h = @{ Authorization = "Bearer $env:METERLESS_RUNTIME_TOKEN"; "meterless-app" = "phoenixrepo"; "meterless-scope" = "user" }
(Invoke-WebRequest -Uri http://127.0.0.1:8088/v1/models -Headers $h -UseBasicParsing).Content | python -c "import sys,json;print([m['id'] for m in json.load(sys.stdin)['data']])"
# Is a local (Ollama) model or a Groq-routed model listed? Write the answer down: L3 / Groq verified or not.
$lanes = @("nvidia/nemotron-3-super-120b-a12b", "meterless/auto")   # add the local id if listed
foreach ($m in $lanes) { foreach ($i in 1..10) {
  $body = @{ model = $m; max_tokens = 256; messages = @(@{ role = "user"; content = 'Reply with {"ok":true}' }) } | ConvertTo-Json -Depth 4
  $sw = [Diagnostics.Stopwatch]::StartNew(); $code = 0; $served = ""
  try { $r = Invoke-WebRequest -Uri http://127.0.0.1:8088/v1/chat/completions -Method Post -Headers $h -Body $body -ContentType "application/json" -UseBasicParsing -TimeoutSec 180; $code = [int]$r.StatusCode; $served = ($r.Content | ConvertFrom-Json).model }
  catch { if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode } else { $code = -1 } }
  "{0},{1},{2},{3},{4}" -f (Get-Date -Format o), $m, $code, $sw.ElapsedMilliseconds, $served | Add-Content logs\provider-measurements.csv
}}
```
Then summarise p50/p90 latency, count of 429/5xx/timeouts (-1), served models per lane, into `logs/provider-measurements.md`. Small prompts understate real role latency; also record the per-call latency from the three `demo_live` traces (`LLM_JUDGE` lines carry `latency_ms`).

### 3.6 hygen fork commands (T5/T7; any machine with Node ≥18 and git)
```bash
git clone https://github.com/Fu93/hygen && cd hygen
git commit --allow-empty -m "ci: baseline run before revival" && git push origin master   # real red baseline on the fork
# check: https://github.com/Fu93/hygen/actions  (enable workflows if GitHub asks)
npx -y yarn@1.22.22 install --frozen-lockfile && npx -y yarn@1.22.22 build:lib && node dist/bin.js init self && npx -y yarn@1.22.22 test
node dist/bin.js --version; echo $?          # today: prints the generator error and exits 1 (issue #450)
```
Then collect (after PR #3 merges): `python scripts/collect_revival_attempt.py --fork Fu93/hygen --upstream jondot/hygen --issue 450 --output data/real` (optional `GITHUB_TOKEN` in env; never written), then §3.3 `load_real.ps1`, then §3.4.

## 4. Assets (OUTLINES — fill only with real results)

### 4.1 Demo video script (~3:00, English, burn-in or .srt subtitles)

| Time | On screen | Voice-over (draft — rewrite in your own words) |
| --- | --- | --- |
| 0:00–0:20 | hygen README; upstream Actions page with the red run 37141993934 | "Your team's code generator, hygen, hasn't shipped in years. Its CI is red. Issue #458 asks 'Is Hygen dead?'. Do you fork it, or walk away?" |
| 0:20–0:35 | Title card: PhoenixRepo · Track 02 | "PhoenixRepo is a team of agents that answers that question with evidence — and then proves it." |
| 0:35–1:05 | Zetaris UI: `phoenix` views (raw → signals → evidence); one SELECT | "Every fact comes from Zetaris: a snapshot of GitHub facts, curated signals, and later our own fork's CI — all as SQL views. The agents can't see anything else." |
| 1:05–1:40 | Terminal `POST /run` for golang/dep (NO_GO) and jondot/hygen (RESURRECTION_FAILED); trace HTML showing validator rejection + retry, requested vs served model | "golang/dep: NO_GO — its README names a successor. hygen: worth saving, but CI failed — and the agent says it's the toolchain setup step, not the tests. The validator rejected an unsupported claim and forced a retry; the LLM judge on Nightshift is advisory, the rows decide." [RESULT FROM LIVE RUN] |
| 1:40–2:05 | Planner PRD (`docs/revival/jondot__hygen.prd.md`): 3 stages with evidence ids + issue #450 | "For a candidate, the planner writes a staged revival plan. Stage one is the only gate: green CI on our fork." |
| 2:05–2:35 | Fork Actions: baseline red → Stage 1 green; Stage 2/3 commits; `hygen --version` before/after | "A coding agent executed it on Fu93/hygen. Red, then green. Then a security bump and a real user issue fixed." [RESULT FROM FORK CI] |
| 2:35–2:50 | Re-run → RESURRECTED citing the run URL; H-MEM cites the prior verdict | "Our collector reads the fork's CI back into Zetaris. Re-run: RESURRECTED — and it remembers why it failed before." [RESULT FROM LIVE RUN] |
| 2:50–3:00 | Architecture slide; repo URL | "Zetaris for evidence, Meterless H-MEM and Markovian for memory and long runs, Nightshift with Nemotron for every model call. github.com/Fu93/phoenixrepo." |

Recording notes: hide tokens/keys; pre-load Zetaris and Nightshift; if a live call is slow, cut the wait in editing but never splice in a different run's result; say "cached" on screen if a cached response is shown.

### 4.2 Deck outline (≤10 slides; every item the rules require)
1. Problem + target users (hygen issues #458/#443 as evidence). 2. Solution in one sentence + three verdicts. 3. Agent workflow (7 roles, plan → act → verify diagram). 4. Architecture (ARCHITECTURE §1 diagram). 5. Technical stack. 6. How Zetaris was used (views, REST/FILESTORE, any verified spike). 7. How Meterless was used (H-MEM, Markovian, Nightshift lanes, Nemotron; requested vs served). 8. What was unique about our sponsor use (verdict gated on an experiment read back through Zetaris; guard beats LLM). 9. Product visuals (trace HTML, CI red→green, RESURRECTED response). 10. Future enhancements (batch dependency monitoring, n8n schedule, more forges) + honesty box + declarations.

### 4.3 Written submission
Use PRD Appendix B (outline) and Appendix A (declarations). Required explanation per the rules: Zetaris + Meterless integration, how Cursor was used (cloud agents + local Cursor), where NVIDIA contributes, what was unique.

## 5. If you are continuing alone in local Cursor

General rules to paste at the top of any Cursor chat:
> You are working in the PhoenixRepo repo. Read README.md, docs/PRD.md, docs/ARCHITECTURE.md and docs/ROADMAP.md first. Do not invent results or numbers. Never print or commit secrets (ZETARIS_API_KEY, METERLESS_RUNTIME_TOKEN, any provider key). Keep the deterministic policy as the verdict authority; the LLM judge stays advisory. All model calls go through Meterless Nightshift. Run `python -m pytest -q` and `python scripts/compliance_check.py` before you say you're done, and show the output. Small commits, one branch per task, open a PR, don't merge.

**T3 — load_real.ps1 400 fix**
> In scripts/zetaris/load_real.ps1, Zetaris returns HTTP 400 (not 200) when a database, table, container or view already exists. Make that case count as "exists" (and "view-exists" for views) when the response body contains "already exists" anywhere, while any other 400 is still a failure. Keep printing only status tokens, never bodies or the key. Make the default -ContainerPath /home/zetaris/customer-data/shared/phoenix_real. Add a Python test with a fake server for the decision logic if you can port it, and a test in tests/adapters/test_zetaris_contract.py that JSON null cells parse (blank text, false bools) and that a null days_since_push raises ZetarisSchemaError. Update README's "Real snapshot and local Zetaris" section to the verified steps in docs/ARCHITECTURE.md §3.2.

**T4 — provider lanes and degrade path**
> Implement docs/ARCHITECTURE.md §8 and §7 "proposed" rows. Wrap BoundLLM.complete in app/llm/provider.py with: an ordered lane list from METERLESS_LANES (default "nvidia/nemotron-3-super-120b-a12b,meterless/auto"), all sent to METERLESS_BASE_URL through MeterlessRuntime; retries on 429/5xx/timeout/connection error after 2s,4s,8s (respect Retry-After), then next lane; no retry and no fallback on 401/403; a per-run call budget METERLESS_MAX_CALLS_PER_RUN (default 24); an optional response cache LLM_CACHE=off|read|readwrite keyed by sha256(model+canonical messages+max_tokens) under logs/llm-cache/ (add to .gitignore), caching only replies that parsed. Trace LLM_CALL and LLM_FALLBACK events with role, lane, lane_index, requested_model, served_model, http_status, latency_ms, attempt, cache_hit, budget_left. In app/investigation.py, when every lane fails for a role call, do not return 502: market_judge falls back to the policy explanation, archaeologist to deterministic claims that quote the row and cite EV-PROFILE, architect/builder are skipped, and metadata.llm.degraded records why; the verdict is always produced. Refuse LLM_PROVIDER=nvidia in live mode unless ALLOW_DIRECT_PROVIDER_DEV=true. Also fix the Markovian limits in ARCHITECTURE §4 (max_chunks and chunk timeout vs chat timeout). Test everything with a fake Nightshift (httpx MockTransport) — 429 then 200, timeout on lane 1 then lane 2 ok, 401, all lanes down, budget exhausted, cache hit.

**T5 — hygen Stage 1 (in the Fu93/hygen repo)**
> In this hygen fork, make .github/workflows/ci.yml green on push to master without changing product code: use actions/checkout@v4 and actions/setup-node@v4 (keep cache: 'yarn'), node-version matrix [20.x, 22.x] on ubuntu-latest and windows-latest, `yarn install --frozen-lockfile`. The old failure was "Cache service responded with 400" inside setup-node@v2 (run https://github.com/jondot/hygen/actions/runs/37141993934). Locally on Node 20, `yarn install --frozen-lockfile && yarn build:lib && node dist/bin.js init self && yarn test` passes (9 suites, 40 tests). If Node 22 or Windows fails, show the log, try [18.x, 20.x], and explain in the PR rather than silently dropping a platform. Open a PR to master in Fu93/hygen.

**T6 — PR #3 follow-ups (in phoenixrepo)**
> On branch cursor/phoenix-revival-prd: (1) update the hygen planner output so Stage 1 includes the action bumps (checkout/setup-node v4) because the decisive log line is "Cache service responded with 400" in setup-node@v2; add that line as a revival_sources row of kind ci_log and cite it; Stage 2 becomes the production dependency security bump (ejs >=3.1.10, degit >=2.8.6, advisories GHSA-phwq-j96m-2c2q, GHSA-ghr5-ch3p-vcr6, GHSA-77c7-pq4r-6mcq). (2) Apply the user's decision on "no attempt row" (docs/PRD.md §5.2). (3) In _revive_success, require the run to be on default_branch and commit_sha == head_sha (or prove the collector guarantees it, with a test). (4) Record failed_step for failed attempts. Regenerate docs/revival/jondot__hygen.prd.md with scripts/write_revival_prd.py and update tests.

**T7 — hygen Stage 2 and 3 (in Fu93/hygen)**
> Stage 2: bump ejs to >=3.1.10 and degit to >=2.8.6, regenerate yarn.lock, keep tests and snapshots green (explain any snapshot change); `yarn audit --groups dependencies` must show no ejs/degit advisories. Stage 3: fix https://github.com/jondot/hygen/issues/450 so `node dist/bin.js --version` and `-v` exit 0, print `Hygen v<version>`, and do not print "Error: please specify a generator."; add a jest spec; keep `yarn test:require` working (it runs bin.timed.js with no args). One PR per stage, each green on CI.

**T10 — docs refresh**
> Update README.md and metadata.json to match what is now verified (logs/demo-live.md, logs/provider-measurements.md, fork CI run URLs, the new meaning of RESURRECTED). Replace "not verified" wording only where there is evidence. Keep the pre-existing-work declaration (through commit 62a1540) and the AI-assistance declaration. Do not add any number that is not in a log file in this repo.

**T12 — deck**
> Create a 10-slide deck from docs/ROADMAP.md §4.2 using only facts from docs/PRD.md, docs/ARCHITECTURE.md and logs/*.md. Put [PLACEHOLDER] where a result is missing. Include the architecture mermaid as an image.

**T15 — n8n (bonus)**
> Draft an n8n workflow JSON (scripts/n8n/weekly-monitor.json) per docs/ARCHITECTURE.md §15: weekly schedule, POST http://127.0.0.1:8000/run for each repo in a list, compare decision with metadata.hmem.prior_verdicts[0], notify only on change. No credentials in the JSON.

## 6. QA checklist (Day 4)
- [ ] Fresh clone on Windows; sample mode: `python -m pytest -q` passes; `python run.py` + `curl.exe ... example_1.json` returns RESURRECTED (sample).
- [ ] Live: §3 commands work from scratch; `logs/demo-live.md` regenerated.
- [ ] README covers setup, usage, dependencies (rules §6).
- [ ] Secret scan: `git grep -n -I -E "(nvapi-|gsk_|ghp_|gho_|github_pat_)"` → nothing; `.env` not tracked.
- [ ] Every number in README/deck/video/submission traces to a file in `logs/` or a URL.
- [ ] Video 1–4 min, English, subtitles, shows main workflow and how Zetaris and Meterless are integrated; plays logged out.
- [ ] Deck covers: problem/users, solution, workflow/architecture, stack, Zetaris+Meterless use, uniqueness, visuals, future.
- [ ] Form: Track 02, repo URL, video, deck, pre-existing declaration, AI/Cursor/NVIDIA explanation; submitted (not draft).

## 7. Judge Q&A (one-liners to prepare)
- What did you build in the window? → everything after `62a1540`; git history from 22 Oct.
- Is RESURRECTED real? → yes, only when the fork's build/test CI on `master` is green, read back through Zetaris; link the run.
- Isn't Stage 1 trivial? → the insight is the diagnosis (cache backend vs test failure); the agent refused to call it a test failure, then proved it.
- What if the LLM is wrong? → deterministic guard wins; judge is advisory; missing evidence id beats an LLM accept.
- Why Zetaris? → one governed SQL surface over files, APIs and our own experiment results; agents have no other data path.
- Which model? → requested Nemotron 3 Super via Nightshift; Nightshift served Ultra on 22 Oct; traces record both.
- What breaks? → Nightshift down → degraded run, verdict still produced (after T4); Zetaris down → no verdict by design.
