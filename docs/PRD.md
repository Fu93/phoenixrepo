# PhoenixRepo — Product Requirements (PRD)

Status: blueprint for the build window, written 22 Oct 2026 ~16:45 BST against `main` at merge `33b15f7`.
Scope of this file: what the product must do and how we will know. `docs/ARCHITECTURE.md` says how it is built; `docs/ROADMAP.md` says who does what, day by day, until the deadline.

---

## 繁體中文摘要（給弘軒）

- **產品**：PhoenixRepo 是一組 agent，判斷一個被放棄的開源 repo「值不值得復活」，每個結論都必須指得出 Zetaris 的資料列（row）。三種結論：`NO_GO`、`RESURRECTION_FAILED`、`RESURRECTED`。
- **新方向（已核准）**：plan → act → verify。第 7 個角色 planner 為候選 repo 寫分階段復活 PRD（Stage 1 REVIVE / Stage 2 REFACTOR / Stage 3 UPGRADE），coding agent 真的在你的 fork `Fu93/hygen` 上執行，collector 用 GitHub API 把 fork 的 CI 結果讀回 `revival_attempts`。**只有 Stage 1 的 CI 在 fork 預設分支轉綠，才算 `RESURRECTED`。**
- **hygen 實測（今天在 box 上跑過）**：Node 20.19.2 / Linux 上 `yarn install --frozen-lockfile`、`tsc`、`init self`、`yarn test`（9 suites、40 tests）**全部通過**。上游 CI 失敗的真正原因是 `actions/setup-node@v2` 的 yarn cache 回傳 `Cache service responded with 400`，不是測試壞掉。所以 Stage 1 的修法是升級 workflow（checkout/setup-node v4、Node 20/22），風險在 Windows runner 尚未驗證。
- **PR #3（審核中）** 已有 planner、collector、hygen PRD（Stage 1 拿掉 Node 14/17、Stage 2 升級 actions、Stage 3 issue #450 `--version`，已重現 exit 1）。**重要修正**：失敗原因是 setup-node@v2 的 cache，所以 actions 升級必須放進 Stage 1，否則 Stage 1 很可能還是紅燈；Stage 2 改成 ejs ≥3.1.10、degit ≥2.8.6 安全升級。另外 PR #3 在「沒有 attempt」時仍保留 RESURRECTED（matterbridge），需你決定。
- **評分（官網原文，100 分）**：Impact 30、Technical 20、Innovation 15、Demo 15、Product & UX 10、Sponsor tech 10。影片 1–4 分鐘。截止 **10/27 23:45 UTC**（BST 10/25 結束，所以也是 23:45 GMT 英國時間）。
- **最重要的事**：Windows 上實際跑一次 live（Zetaris + Nightshift）並記錄真實延遲 / 429；不要寫任何沒量過的數字。

---

## 1. Problem

Abandoned open-source repositories are cheap to restart and expensive to regret. A maintainer, an engineering lead, or an individual developer who finds a dead but useful project has to answer three questions before investing a weekend or a sprint:

1. Is it worth reviving at all (licence, successor, distinct capability, real abandonment)?
2. Does it still build and test, or has the toolchain decayed?
3. If revived, what is the smallest change that makes it useful again?

Today this is done by eyeballing GitHub. Badges lie, CI history mixes security scans with build runs, and "the tests are failing" often means "the CI runner image moved on". People either waste time on a repo that has a successor or a non-portable licence, or skip a repo that only needed a workflow bump.

Evidence that the pain exists, from the repo we revive (real issue URLs, read 22 Oct 2026 with `gh api`):
- https://github.com/jondot/hygen/issues/458 "Is Hygen dead?" (15 reactions, 5 comments): "The last commit was done 2 years ago, the official webiste https://hygen.io/ is no longer working".
- https://github.com/jondot/hygen/issues/443 "Kindly asking... is this still maintained?" (5 reactions).
- https://github.com/jondot/hygen/issues/212 "More maintainers" (18 reactions).
- 86 open issues and 15 open PRs on `jondot/hygen` at read time.

We do not claim a market size or a user count. None was measured.

## 2. Users

| User | Situation | What they need from PhoenixRepo |
| --- | --- | --- |
| Primary: an engineer/tech lead deciding whether to adopt or fork an abandoned dependency | A dependency their product uses (e.g. a code generator) stopped getting releases | A verdict with reasons they can check, and a concrete revival plan with acceptance criteria |
| Secondary: an OSS contributor looking for a revival to adopt | Wants a project where effort will show | Which candidates are cheap to bring back, and the first issues to fix |
| Judge (hackathon) | Watches a 1–4 min video, reads the repo | One flow where the agent explains *why* and then proves it by experiment |

## 3. Value proposition

"PhoenixRepo will not issue a verdict it cannot point at a row — and it will not say RESURRECTED until a real CI run on a real fork says so."

Unlike a repo-health dashboard, PhoenixRepo:
- separates *value* (should it live?) from *viability* (does it build?) and reports both with row-level citations from Zetaris;
- distinguishes CI *decay* (toolchain setup failed) from test failure (`app/agents/policy.py`, `validation_block_reason`);
- turns a candidate into a staged plan, executes it on a fork, reads the result back, and only then changes the verdict (plan → act → verify, §7).

Track: **Track 02 confirmed by the user (22 Oct 2026).** Select it in the form before submitting.

Demo story (confirmed): a team depends on hygen and it broke (upstream CI red since the toolchain decayed); PhoenixRepo judges it, writes the staged PRD, a coding agent actually revives **and** refactors/upgrades the fork `Fu93/hygen`, CI goes red → green, and PhoenixRepo re-judges it `RESURRECTED`. About 3 minutes.

Track fit: Track 02 "The Agent That Can Explain Why" — "Build agents that investigate complex questions across multiple data sources, connect findings, and produce evidence-backed answers or recommendations." (hackathon.genai.works event page, fetched 22 Oct 2026).

## 4. Scope

### In scope (must ship by 27 Oct 2026 23:45 UTC)
1. The existing six-role investigation over Zetaris views, live on Windows with Nightshift as the model provider (already coded; live run pending).
2. A seventh role, **planner**, that writes a staged revival PRD for candidates (Stage 1/2/3), with every item citing evidence (row ids, issue URLs).
3. One real revival executed on `https://github.com/Fu93/hygen` (fork of `jondot/hygen`, default branch `master`).
4. A **collector** that reads the fork's build/test CI via the GitHub REST API into `revival_attempts` (CSV → Zetaris view), so the verdict is driven by an experiment.
5. Provider/fallback strategy through Nightshift only (ARCHITECTURE §8).
6. Demo video (English, subtitles), slide deck, README, written submission with sponsor-tech explanation, AI-assistance and pre-existing-work declarations.

### Non-goals
- No web UI beyond what exists (`scripts/render_trace.py` HTML/markdown render of the trace is the visual). Product & UX is 10 points; the trace render plus a clean CLI flow is the plan.
- No automatic PRs to upstream `jondot/hygen`. We work on the fork only. Opening an upstream PR is the user's choice after the event.
- No neural embeddings in H-MEM (hash embedder by design, `app/engines/hmem/`).
- No more than one revived repository end to end. Matterbridge, setcover, etc. stay evidence-only.
- No claim that the curated GitHub columns are a census (see `data/real/provenance.json` `column_rules`).
- No direct LLM calls that bypass Nightshift in the live demo (ARCHITECTURE §8).

## 5. Verdicts and policy rules

### 5.1 Current policy (code on `main`, `app/agents/policy.py`, constants in `app/sql_catalog.py`)

Evaluation order in `decide(row, signals)`:

1. **Value gate** (`blocking_value_reason`) → `NO_GO` if any of:
   - `licence_spdx` not in `{MIT, Apache-2.0, BSD-3-Clause, BSD-2-Clause, ISC, MPL-2.0}`;
   - `successor_url` non-empty;
   - `days_since_push < 180` (`ABANDONED_AFTER_DAYS`);
   - `unique_capability` empty.
2. **Validation gate** (`validation_block_reason`) → `RESURRECTION_FAILED` if any of:
   - `has_tests` false; `has_license_file` false;
   - `ci_status` in `{failure, cancelled, timed_out, startup_failure}` (reason names a toolchain setup step when a `ci_failure` signal says so);
   - a `tests_absent` or `ci_failure` signal with `supports_revival=false`.
3. Otherwise → `RESURRECTED` (with CI success or CI unknown).

Always reported, never decisive: `alternatives_factor` low (≤5) / moderate (≤30) / high, from `similar_repo_count`, with an uncertainty sentence. `archived=true` is recorded, not a pass or block.

Current verdicts on the committed real snapshot (asserted by `tests/data/test_curation.py`):

| Repo | Verdict | First policy reason |
| --- | --- | --- |
| golang/dep | NO_GO | successor URL `https://golang.org/ref/mod` next to "in favor of" |
| jxnblk/rgx | NO_GO | no SPDX licence id |
| discreteoptimization/setcover | RESURRECTION_FAILED | no test path in tree |
| jondot/hygen | RESURRECTION_FAILED | build/test workflow `CI` failed at toolchain step `Use Node.js 14.x` (CI decay) |
| 42wim/matterbridge | RESURRECTED | evidence accepts revival; CI unknown |

### 5.2 New rule set (plan → act → verify; approved direction, to be implemented)

The meaning of `RESURRECTED` changes from "the evidence policy accepted a revival contract" (README §Problem) to "a revival was executed and proven". Concretely:

| Verdict | New definition |
| --- | --- |
| `NO_GO` | Unchanged: the value gate blocks. Planner is not called. |
| `RESURRECTION_FAILED` | The value gate passes, and either (a) no Stage 1 attempt has been proven green inside the budget, or (b) the latest Stage 1 attempt's build/test CI on the fork default branch concluded failure/cancelled/timed_out/startup_failure. The reason says which, and names the failed step when the jobs API gave one. |
| `RESURRECTED` | The value gate passes **and** `phoenix.revival_attempts` has a row for this repo with `stage=REVIVE`, `branch` = the fork's default branch, a build/test workflow (not CodeQL/security, not `action_required`, not a fork-PR run), `conclusion=success`, and `head_sha` equal to the fork default-branch head at `collected_at`. **This is the only gate for RESURRECTED.** |

Consequences to accept explicitly (decision for the user, recommended "yes"):
- `42wim/matterbridge` becomes `RESURRECTION_FAILED` with reason "no proven revival attempt" unless someone revives it. Its evidence-policy acceptance stays visible as `metadata.revival.status = "candidate"`.
- `jondot/hygen` is `RESURRECTION_FAILED` today (upstream CI decayed) and becomes `RESURRECTED` only after the fork's CI is green and collected. That flip is the demo.
- Stage 2 and Stage 3 results are reported in `metadata.revival` and the trace but do **not** gate `RESURRECTED`.
- Proposed (not yet in code) `metadata.revival.status` values: `not_applicable` (NO_GO), `candidate` (value gate passed, no attempt), `planned` (planner PRD accepted by validator), `attempted` (attempt row exists, not green), `proven` (gate above satisfied).

**In-review implementation: PR #3** (https://github.com/Fu93/phoenixrepo/pull/3, draft, branch `cursor/phoenix-revival-prd`, commit `8c95cd4`, not merged at 16:55 BST 22 Oct). It adds `app/agents/planner.py`, `apply_revival_attempts()` in `app/agents/policy.py`, `scripts/collect_revival_attempt.py`, `scripts/write_revival_prd.py`, views `phoenix.revival_attempts` and `phoenix.revival_sources`, tests `tests/agents/test_planner.py`, `tests/agents/test_revival_policy.py`, `tests/data/test_revival_attempts.py`, and the Zetaris path fix. What it does today (read from the branch):
- stage `revive` row with `status=success`, `ci_status=success`, non-empty `commit_sha` and `ci_run_url` → `RESURRECTED` (cannot override `NO_GO`);
- stage `revive` row with `status=failure` and a run URL → `RESURRECTION_FAILED` citing that run;
- **no attempt row → the old `decide()` verdict stays, with a "not yet attempted" note.** So `42wim/matterbridge` would still be `RESURRECTED` without any experiment.

Gaps between PR #3 and the rule above (add before merge, or consciously accept):
1. "No proven attempt" must not yield `RESURRECTED` (decision for the user; this PRD recommends `RESURRECTION_FAILED` + `metadata.revival.status=candidate`, matching `PRE_EVENT_PLAN.md`: "worth saving, but verification failed inside the budget").
2. `_revive_success` trusts the collector for branch/eligibility; add an explicit check that `default_branch` matches the run's branch and that `commit_sha == head_sha` at collection (or document that the collector guarantees it, with a test).
3. Record the failed step for a failed attempt (jobs API), as `curate_real_repos.py` already does for upstream.

## 6. Agent roles

The orchestrator (`app/investigation.py`, `InvestigationOrchestrator`), evidence graph (`evidence/graph.py`) and state machine (`state/machine.py`) are pre-existing infrastructure, not agents. Every role call goes through `_role_chunk` → Markovian `ChunkManager.run` → `BoundLLM.complete` and must return one JSON object (parsed by `app/llm/parsing.py`, last JSON object after any reasoning prose / `<think>`).

| # | Role | Input (EVIDENCE_JSON) | Output keys | Evidence rule (who enforces) | Authority |
| --- | --- | --- | --- | --- | --- |
| 1 | Archaeologist | profile row, `profile_evidence_id` | `claims[]` {id, statement, evidence_ids, confidence} | Every claim needs ≥1 known evidence id; claims together must quote `licence_spdx`, `days_since_push`, `similar_repo_count` (`critique_archaeologist`, `app/agents/validate.py`). Live: plus advisory LLM judge. | None. Max 2 attempts. |
| 2 | Market judge | row, signals, known evidence ids, H-MEM recalled memories | `decision`, `explanation`, `evidence_ids` | `decision` must equal `decide()`; ids must be known; explanation must quote licence and `days_since_push` (`critique_judge`). On failure the policy text is used. | None over the verdict. |
| 3 | Architect | row, `profile_evidence_id` | `contract` | Not validated today (output only traced). Skipped on NO_GO. | None. |
| 4 | Builder | row, decision, known ids | `steps`, `evidence_ids`, optional `verification_result` | `PASS` forbidden; ids must resolve; steps non-empty (`critique_builder`). Failure after 2 attempts → 422 `ValidatorBlocked`. | None. |
| 5 | Validator | every payload above | critiques, `CLAIM_VERIFIED` | Deterministic guard first; LLM judge (`app/agents/llm_judge.py`) is advisory: a parsed reject can force one retry; an accept never clears a missing/unknown evidence id (`guard_override`). | Only role that can accept. |
| 6 | Diagnostician | row, validation reason, known ids | `action`, `claims`, `decision` | Re-queries `phoenix.revival_signals`; cannot declare success. Runs only while `max_repair_iterations` remains. | None. |
| 7 | **Planner (PR #3, `app/agents/planner.py`)** | row, signals, known ids, `phoenix.revival_sources` rows (workflow/package/issue/compare/actions excerpts), `phoenix.revival_attempts` rows | `stages[]`: {stage: REVIVE\|REFACTOR\|UPGRADE, items[]: {title, rationale, evidence_ids, issue_urls, acceptance[]}} | Each item cites ≥1 known evidence id; every UPGRADE item cites ≥1 real open issue URL present in `phoenix.revival_sources`; REVIVE acceptance must be "build/test CI green on fork default branch". Validator rejects unknown URLs/ids and retries once. | None. Called only when the value gate passes. |
| — | Coding agent (outside the run) | the planner PRD | commits/PRs on `Fu93/hygen` | Must not edit upstream; one PR per stage; CI must run on push to `master`. | Not part of the verdict; its result is only seen through the collector. |
| — | Collector (script, not an LLM role) | fork owner/name | `revival_attempts.csv` rows | Only build/test workflows count; same eligibility rules as `scripts/curate_real_repos.py`. | Its rows are the only RESURRECTED evidence. |

## 7. Plan → act → verify loop

1. Investigate (existing six roles) → value gate result.
2. If the value gate passes: planner writes the staged PRD; validator checks citations.
3. Act: a coding agent (Cursor cloud agent or local Cursor) executes Stage 1 on the fork, then 2 and 3.
4. Collect: `scripts/collect_revival_attempt.py` (PR #3) reads the fork's Actions runs via the GitHub REST API and writes `data/real/revival_attempts.csv` (+ `revival_attempts_provenance.json`), loaded into Zetaris as `phoenix.revival_attempts` by `load_real.ps1`.
5. Re-run the investigation: policy reads attempts through Zetaris; verdict flips only if the gate in §5.2 holds. H-MEM recalls the earlier `RESURRECTION_FAILED` verdict as `CLM-PRIOR-*`, so the explanation can say "previously failed because CI decayed; now proven by run <url>".

Sequence diagrams are in `docs/ARCHITECTURE.md` §6.

## 8. hygen revival plan — additions to PR #3's PRD

The canonical staged PRD is PR #3's generated file `docs/revival/jondot__hygen.prd.md` (and `.prd.json`), produced by `scripts/write_revival_prd.py`. Its stages: Stage 1 drop Node 14.x/17.x from `ci.yml` + `yarn install/build/test` + eligible fork CI green; Stage 2 replace `actions/checkout@v2`/`actions/setup-node@v2`; Stage 3 issue #450 `hygen --version` treated as error. This section does not repeat it. It adds independent research (22 Oct 2026, on the box) and **one correction that matters for the demo**.

### 8.0 Correction: the action bump belongs in Stage 1

The upstream failing job's log shows `##[error]Cache service responded with 400` inside step `Use Node.js 14.x` (`actions/setup-node@v2` with `cache: 'yarn'`), right after Node 14.21.3 was downloaded successfully. The failure is the v2 action's cache backend, not the Node version. If Stage 1 only drops 14.x/17.x and keeps `setup-node@v2` + `cache: 'yarn'`, the new Node version will most likely hit the same cache error and Stage 1 CI stays red. **Recommendation:** Stage 1 = Node matrix change **and** `actions/checkout@v4` + `actions/setup-node@v4` (or, minimally, remove `cache: 'yarn'`). Then Stage 2 needs a different item — use the dependency security bump (§8.3). Feed this back to PR #3 (ROADMAP T6) — the planner's evidence should include the log line (e.g. as a `revival_sources` row of kind `ci_log`).

Useful honest beat for the video: run the fork CI once *before* any change to get a real red run on `Fu93/hygen` (baseline), then Stage 1 → green.

### 8.1 Facts gathered (all reproducible)

| Fact | Source / how checked |
| --- | --- |
| Fork `Fu93/hygen`, `isFork=true`, parent `jondot/hygen`, default branch `master`, issues disabled on the fork, Actions permissions `enabled: true, allowed_actions: all`, 0 workflow runs so far | `gh repo view Fu93/hygen`, `gh api repos/Fu93/hygen/actions/permissions`, `.../actions/runs` |
| HEAD `26f76d8` (2023-03-15, "Merge pull request #418") | `git log` on a clone |
| `package.json`: hygen 6.2.11; scripts `build:lib`=`tsc`, `test`=`yarn test:code && yarn test:require`, `test:win32` skips `metaverse-conditional`; jest 27 + ts-jest 27; TypeScript ^4.5.5; `pkg` targets node17 | `package.json` |
| `.github/workflows/ci.yml` "CI": push to master, pull_request, monthly schedule, workflow_call; matrix node `[14.x, 17.x]` × `[ubuntu-latest, windows-latest]`; `actions/checkout@v2`, `actions/setup-node@v2` with `cache: 'yarn'`; steps `yarn install`, `yarn build`, `yarn hygen:build -- init self`, `yarn test` (ubuntu) / `yarn test:win32` (windows) | file |
| `cd.yml` (release on `v*` tags, calls ci.yml, node 17.x, setup-node@v2) and `docs.yml` (on `docs-*` tags, node 16.x) | files; out of scope (tag-only) |
| `.tool-versions-e` says `nodejs 7.7.2`, `ruby 2.5.0` (stale, odd filename) | file |
| Upstream failing run https://github.com/jondot/hygen/actions/runs/37141993934 (schedule, master, created 2026-10-03T17:50:35Z): job `build (14.x, ubuntu-latest)` failed at step `Use Node.js 14.x`; the log line is `##[error]Cache service responded with 400` right after `yarn --version`. Other matrix jobs were cancelled. | `gh api .../runs/37141993934/jobs`, `gh run view --log-failed` |
| **Local reproduction on the box, Node v20.19.2, yarn 1.22.22, Linux**: `yarn install --frozen-lockfile` OK (20 s); `yarn build:lib` OK; `node dist/bin.js init self` OK; `yarn test` OK — 9 suites passed, 40 tests passed, 39 snapshots; a Jest "did not exit one second after the test run" warning (open handles), exit code 0 | commands in ROADMAP §3.6 |
| `yarn audit --groups dependencies --summary`: 3 low, 10 moderate, 33 high, 1 critical; includes ejs 3.1.6 → GHSA-phwq-j96m-2c2q (critical, patched ≥3.1.7) and GHSA-ghr5-ch3p-vcr6 (moderate, ≥3.1.10); degit → GHSA-77c7-pq4r-6mcq (high, ≥2.8.6) | run on the box 22 Oct 2026 |
| Issue #433 reproduced: in a `"type":"module"` project, a generator `index.cjs` exporting `params` is ignored (output `hello unnamed` instead of the param value); `src/prompt.ts` `hooksfiles` = `index.js, index.ts, prompt.cjs, prompt.js, prompt.ts` (no `index.cjs`) | `/tmp/t433` repro, `src/prompt.ts` lines 6–12 |
| Issue #275 reproduced: empty `_templates/` → `hygen init self` exits 1 with "I can't find action 'self' for generator 'init'" | `/tmp/t275` repro |

Interpretation (honest): the upstream "CI failure" is infrastructure decay in the workflow (deprecated cache backend reached through `setup-node@v2`), not a code failure. That is exactly what the policy calls "CI has decayed". It also means Stage 1 is likely small — which is fine to say in the video; the point is that PhoenixRepo *diagnosed* it correctly and *proved* it.

### 8.2 Stage 1 — REVIVE (recommended content)

Goal: build/test CI green on `Fu93/hygen` `master`.

Items:
1. `ci.yml`: `actions/checkout@v4`, `actions/setup-node@v4` (keep `cache: 'yarn'`), node matrix `[20.x, 22.x]` (drop EOL 14.x/17.x), keep `ubuntu-latest` and `windows-latest`. Evidence: upstream run 37141993934 failed step + log line; `signal ci_failure` row for `jondot/hygen` in `data/real/revival_signals.csv`.
2. `yarn install --frozen-lockfile` in CI (lockfile already installs cleanly on Node 20).
3. Do **not** change source code in Stage 1 unless CI proves it is needed.

Acceptance criteria:
- [ ] A push to `Fu93/hygen` `master` triggers workflow `CI`; every matrix job concludes `success` (link the run URL).
- [ ] `yarn test` (ubuntu) and `yarn test:win32` (windows) steps ran (not skipped).
- [ ] Collector writes a `revival_attempts` row: `stage=REVIVE`, `branch=master`, `workflow_name=CI`, `conclusion=success`, `head_sha` = `master` head.
- [ ] Re-run of PhoenixRepo on `jondot/hygen` returns `RESURRECTED` citing that row's evidence id and run URL.

Risks: Windows runner untested locally (line endings handled by the workflow's `core.autocrlf false`, but `test:win32` may still fail on Node 22); Node 22 + ts-jest 27/jest 27 untested (fallback: matrix `[18.x, 20.x]`); forked repos sometimes require a one-time "enable workflows" click in the Actions tab even when the API says enabled; scheduled workflows are disabled on forks (irrelevant: we trigger on push).

### 8.3 Stage 2 — REFACTOR/OPTIMISE (recommended replacement for PR #3's S2 once the action pins move to Stage 1)

1. **Security dependency bumps** (production dependencies): `ejs` to ≥3.1.10, `degit` to ≥2.8.6; regenerate `yarn.lock`. Evidence: the advisories above (GHSA-phwq-j96m-2c2q, GHSA-ghr5-ch3p-vcr6, GHSA-77c7-pq4r-6mcq). Acceptance: `yarn audit --groups dependencies` shows 0 critical and no ejs/degit advisories; CI green; snapshot tests unchanged (if a snapshot changes, explain why in the PR).
2. **Toolchain declaration**: remove `.tool-versions-e` (nodejs 7.7.2), add `"engines": {"node": ">=18"}` (or the lowest green matrix version) and `.nvmrc` matching CI. Acceptance: CI green; README "requirements" line matches.

### 8.4 Stage 3 — UPGRADE

PR #3 chose **#450 "hygen --version treated as error"** — https://github.com/jondot/hygen/issues/450 (open, 0 reactions, 0 comments). Verified on the box with hygen 6.2.11 / Node 20.19.2: `node dist/bin.js --version` prints `Error: please specify a generator.`, `Hygen v6.2.11`, the action list, and **exits 1**. PR #3's acceptance (exit 0, stdout has `Hygen v`, no error line, same for `-v`) is good and demo-visible. Add: a jest spec for `--version`/`-v` so CI proves it, and the change must not break `test:require` (which runs `node dist/bin.timed.js` with no args and expects the usage path).

Alternates with more user demand, both reproduced on the box (use only if #450 is blocked):
- #433 "Project with type Modules skips index.cjs" — https://github.com/jondot/hygen/issues/433 (2 reactions; related #406; upstream PR #434 open). In a `"type":"module"` project a generator `index.cjs` exporting `params` is ignored (output `hello unnamed`); `src/prompt.ts` `hooksfiles` lacks `index.cjs`.
- #275 "hygen init self fails if _templates directory is present" — https://github.com/jondot/hygen/issues/275 (3 reactions, 7 comments): empty `_templates/` → `hygen init self` exits 1.

Other demand signals the planner may cite but we will not implement: #96 append a line during inject (8 reactions), #106 inject replace (7), #455 hygen.io domain expired.

## 9. Success metrics (measured, not assumed)

| Metric | How measured | Target |
| --- | --- | --- |
| Offline tests | `py -3 -m pytest -q` | 138 passing today (box, 22 Oct); must stay green as code grows |
| Live run completes per verdict | `scripts/demo_live.ps1` → `logs/demo-live.md` | 3/3 HTTP 200 with expected verdicts [RESULT FROM LIVE RUN] |
| Live latency and 429 rate per lane | measurement task in ROADMAP Day 1 | recorded, whatever it is [RESULT FROM LIVE RUN] |
| hygen proof | fork CI run URL + `revival_attempts` row + flipped verdict | 1 proven revival [RESULT FROM FORK CI] |
| Every claim cites evidence | `metadata.claims[].evidence[]` non-empty | 100% (enforced by validator) |

## 10. Requirements from the hackathon (quoted)

Source: https://hackathon.genai.works/event/open-agent-hackathon-2026, rules sections 4–7, fetched 22 Oct 2026 16:40 BST (rule text is inside the page's embedded data; the collapsed sections do not render in a plain fetch).

- Build window: "Building starts 22 October 2026 at 00:00 UTC and ends 27 October 2026 at 23:45 UTC." "Work committed before the window opens is not eligible, except for clearly declared pre-existing components." "Time zones do not extend the window; all deadlines are UTC."
- What you can use: "Every submission must integrate Zetaris and Meterless, and teams must use Cursor for development." "Integrate NVIDIA technology wherever it contributes…" "Pre-existing product code must be declared in the submission form." "Generated code is allowed and expected, you own the result and are accountable for it." (Note: our README/metadata cite a 22 Oct organiser update making Cursor optional; the public rules text still says "must use Cursor". We use Cursor — cloud agents and local — so we satisfy both readings. Declare it.)
- Submission requirements: "A demo video of 1-4 minutes that shows the main workflow, how the agent solves the selected challenge, its key features, and how Zetaris and Meterless are integrated." "A slide deck covering the problem and target users, the solution, the agent workflow or architecture, the technical stack, how Zetaris and Meterless were used, what was unique about your use of the sponsor technologies, product visuals, and future enhancements." "A GitHub repository with all source code and a README covering setup, usage and dependencies." "A clear explanation of how Zetaris and Meterless were integrated, how Cursor was used during development, where NVIDIA technology contributes, and what was unique about your use of the sponsor technologies."
- Judging: "Scoring out of 100: Impact 30, Technical 20, Innovation 15, Demo 15, Product & UX 10, Sponsor tech 10." "Each submission is reviewed by at least three judges." "Ties are resolved on the Impact score, then on submission timestamp." (→ submit early.)
- Criterion descriptions shown on the page: Impact — "how effectively the solution addresses a meaningful real-world problem for its intended users"; Technical — "How well the solution is built, including its technical implementation, reliability and architecture"; Innovation — "How original or creative the approach to solving the problem is"; Demo — "How clearly the demo shows the solution, its functionality, and the value it provides to its intended users"; Product & UX — "How intuitive, accessible, and easy to use the product is for its intended users"; Sponsor tech — "How effectively the solution uses the sponsors' technology to address the problem and enhance the overall product."
- Teams: "Team composition locks when your first submission is created". Tracks: "Every submission selects exactly one primary track… You may change track any time before the submission deadline".
- Prizes: Track 02 $1,600 / $1,200 / $900; in-kind "Best use of NVIDIA Nemotron" (NVIDIA DGX Spark) — a stretch target because our model lane is Nemotron via Nightshift.
- Judges listed include Michael Hay, CTO of Zetaris — Zetaris depth matters.
- Not found publicly: the exact submission form fields and character limits (behind login). Fill this in from the form on Day 2 (ROADMAP).

### Rubric → proof map

| Criterion (pts) | Proof we ship |
| --- | --- |
| Impact (30) | Named user + hygen issues #458/#443 as demand evidence; a real fork brought back to green; honest "what RESURRECTED means" |
| Technical (20) | Deterministic policy beats LLM; critique/retry; H-MEM recall; Markovian loop; 138+ tests; collector proof loop; provider fallback with budget/backoff |
| Innovation (15) | Verdict flipped by experiment (plan → act → verify), CI decay vs test failure distinction |
| Demo (15) | ~3 min English video, subtitles, one flow: NO_GO → RESURRECTION_FAILED(hygen) → fork CI → RESURRECTED |
| Product & UX (10) | One-command `demo_live.ps1`, trace HTML render, readable verdict text |
| Sponsor tech (10) | Zetaris = only evidence source incl. attempts; Meterless H-MEM + Markovian; Nightshift primary with Nemotron lane; Cursor used and declared |

## Appendix A — Declarations (draft text, edit in your own voice)

**Pre-existing work.** Everything on `main` before 22 Oct 2026 00:00 UTC is pre-existing: the 8 Oct 2026 skeleton, commits `54d52c1` through `62a1540` (API/schemas, state machine, evidence graph, adapter interfaces, sample-cache shape, Docker, logging, compliance tests). `PRE_EVENT_PLAN.md` and `OPENING_READY.md` are the historical notes. All investigation logic, policy, roles, H-MEM, Markovian loop, Zetaris/Nightshift adapters' live paths, real snapshot, planner, collector and hygen revival were built in the window (first window commit `7f01da0`, 22 Oct 2026).

**AI assistance.** Code in the window was written mostly by Cursor cloud agents (branches `cursor/hackathon-judge-core-13a8`, `cursor/phoenix-real-data-live-judge`, `cursor/phoenix-revival-prd`, plus any later ones) directed and reviewed by me, and by local Cursor on my Windows machine. A general-purpose assistant wrote this blueprint and research. I reviewed, ran and merged the changes and am accountable for them. The product's runtime model calls go through Meterless Nightshift; no coding-assistant credit is the product's model route.

## Appendix B — Written submission outline (OUTLINE — no results yet)

1. **Title**: PhoenixRepo: evidence-backed revival verdicts for abandoned repos, proven on a real fork.
2. **Short description**: Engineers deciding whether to adopt or fork a dead dependency get a verdict that cites Zetaris rows, a staged revival plan, and a CI-proven result — powered by Zetaris, Meterless H-MEM/Markovian and Nightshift (Nemotron).
3. **Problem / users** — §1–2.
4. **What it does** — 7 roles, 3 verdicts, plan → act → verify.
5. **Live result** — "On [DATE/TIME], jondot/hygen was RESURRECTION_FAILED (CI decay at `Use Node.js 14.x`). After Stage 1 on Fu93/hygen, run [RUN URL] concluded [RESULT FROM FORK CI]; PhoenixRepo re-ran and returned [RESULT FROM LIVE RUN]." Latency: [RESULT FROM LIVE RUN].
6. **How Zetaris is used** — only evidence source; REST SQL editor API; FILESTORE tables over `/home/zetaris/customer-data/shared/...`; SchemaStore views `phoenix.repo_evidence`, `phoenix.revival_signals`, `phoenix.revival_attempts`(+issues); agent SQL is single-statement and whitelisted (`app/sql_catalog.py`).
7. **How Meterless is used** — Engine: H-MEM (store/retrieval/trust ledger, prior verdict recall) + Markovian chunk loop, self-implemented from the open-source spec; Runtime: Nightshift primary provider, lanes, served-model tracing.
8. **Where NVIDIA contributes** — Nemotron 3 Super lane via Nightshift; note Nightshift served `nvidia/nemotron-3-ultra-550b-a55b` in the 22 Oct check; traces record both.
9. **How Cursor was used** — cloud agents for the build-window core; local Cursor for Windows-only work.
10. **What was unique** — verdict gated on an experiment; deterministic guard beats LLM; CI decay vs failure.
11. **Challenges** — Zetaris path allow-list; 400 on "already exists"; reasoning-prose replies; served-model rerouting.
12. **Honesty box** — what is real vs sample; RESURRECTED means [definition]; what was not run live.
13. **Declarations** — Appendix A.
