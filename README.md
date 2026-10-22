# PhoenixRepo

PhoenixRepo does not assume an abandoned repository should be saved. It asks whether the evidence says it deserves another life, and it will not issue a verdict it cannot point at a row.

Track 02 of the Open Agent Hackathon 2026, "The Agent That Can Explain Why". Solo entry under the team name PhoenixRepo.

This README describes the build-window core. `PRE_EVENT_PLAN.md` and `OPENING_READY.md` are the 8 October 2026 skeleton notes. They are historical. Where they disagree with this file, this file is the current behaviour.

## Problem

Abandoned repositories are cheap to restart and expensive to regret. A revival wastes time when the idea is a commodity clone, the licence will not travel, or nobody can show that the code still has a test signal. PhoenixRepo runs a small team of agents over one SQL surface and stops at one of three evidence verdicts:

| Verdict | Meaning in this core |
| --- | --- |
| `NO_GO` | The Zetaris rows fail the value rules (licence, abandonment, successor, or an empty distinct capability). A high close-substitute score does not, by itself, do this. |
| `RESURRECTION_FAILED` | The rows say it is worth reviving, and the recorded tests are absent or the latest CI conclusion failed. Missing CI does not do this. |
| `RESURRECTED` | The cited rows satisfy the revival rules: permissive licence, abandoned, a distinct capability, a licence file, tests, and CI that succeeded or is unknown. A recorded stage-revive success on a fork can also reach this verdict after an upstream CI failure. |

`RESURRECTED` does **not** mean this program cloned, compiled, tested, or republished the repository. The pre-event plan used that word for a future prover. This core reuses the state-machine token. With no stage-revive attempt, it still means the row policy accepted a revival contract, and the verdict text says revival has not yet been attempted. A stage-revive success means the fork's eligible build/test CI went green after an attempt commit. The response field `metadata.scope` says the same thing.

## Agents

The orchestrator, the evidence graph, and the state machine are infrastructure from the 8 October skeleton. They are not agents. Seven roles share that graph.

| Role | What it does | Can block or force a retry |
| --- | --- | --- |
| Archaeologist | Asks Zetaris for `phoenix.repo_evidence` and `phoenix.revival_signals` and drafts claims. | No. The validator can reject its claims. |
| Market judge | Writes the explanation of the evidence-policy verdict. | No. An explanation that disagrees with the rows is rejected and retried once. The rows keep the verdict. In live mode the validator also asks the model, as a separate judge call, to accept or reject the claim. |
| Architect | Drafts a revival contract after a value pass. Skipped on `NO_GO`. | No. |
| Builder | Proposes the next step. It is not allowed to mark verification as `PASS`. | No. A `PASS` or a step with no evidence id is rejected. |
| Validator | Checks evidence ids, quoted row values, and the builder rule. In live mode it also calls Nightshift as an LLM judge that can reject a claim. | Yes. It blocks unsupported claims and forces one retry. A missing evidence id wins even when the LLM judge accepts. It is the only role that may accept a revival. |
| Diagnostician | On a failed validation, and only while repair budget remains, asks Zetaris for `phoenix.revival_signals` again. | It cannot declare success. A second failure becomes `RESURRECTION_FAILED`. |
| Planner | For a repository that is not `NO_GO`, writes a three-stage revival PRD. Every finding and task cites a Zetaris evidence id and the query that produced it. | No. The validator rejects a line with no evidence id. A live draft that fails that check is replaced by the deterministic PRD. |

Collaboration that is actually in the run:

- Shared state: one evidence graph and one H-MEM store.
- Critique loop: the validator rejects the archaeologist's first unsupported demand claim, and rejects a builder `PASS`.
- Retry: one bounded retry for those rejections, plus at most `max_repair_iterations` signal re-queries.
- Role authority: the builder cannot write `PASS`. `NO_GO` does not call the architect, builder, diagnostician, or planner.
- Non-linear flow: a failed validation returns to Zetaris through the diagnostician instead of continuing forward.
- Markovian carryover: each role sees a bounded summary of the previous step, not the raw transcript.

The sample stand-in is written to emit one unsupported archaeologist claim and one builder `PASS` on the first attempt, so the critique loop is visible without a live model. That is a property of the stand-in, not a recorded trace being replayed. A live model that does not make those mistakes is not forced to fail. The validator still rejects them if they appear.

The terminal state itself is an evidence policy in `app/agents/policy.py`, not a free-form opinion:

- permissive SPDX licence (`MIT`, `Apache-2.0`, `BSD-3-Clause`, `BSD-2-Clause`, `ISC`, `MPL-2.0`)
- no successor URL
- `days_since_push` at least 180
- a non-empty `unique_capability`
- a close-substitute search total, scored as `alternatives_factor` low, moderate, high, or unknown. The score is reported with the uncertainty that a GitHub search total is not a census of substitutes. It does not, by itself, decide the verdict. There is no hard cut at 40.
- for `RESURRECTED`: `has_tests`, `has_license_file`, and a build or test CI conclusion that is `success` or unknown. A conclusion of `failure`, `cancelled`, `timed_out`, or `startup_failure` on that workflow is `RESURRECTION_FAILED`. When the failing step is toolchain setup, the reason names that step and says CI has decayed. It does not say the tests failed. A security or analysis scan (CodeQL and similar), a run still awaiting approval (`action_required`), and a fork pull request are CI unknown, never success. Missing build/test CI is unknown, not a failure and not an automatic block. `archived=true` is recorded and is not a pass or a block.
- Revival attempts, when the orchestrator loaded `phoenix.revival_attempts`: stage `revive` with status `success`, `ci_status` `success`, a commit SHA, and a CI run URL overrides an upstream CI failure and is `RESURRECTED`. Stage `revive` status `failure` is `RESURRECTION_FAILED` and the reason cites that run. No attempt keeps the row verdict and adds "Revival has not yet been attempted." Stages `refactor` and `upgrade` are `metadata.upgrade`. They do not change the verdict. A fork attempt does not override `NO_GO`. `decide()` with no attempts argument keeps the older row-only text so a caller that has not loaded attempts is unchanged.

A live model writes the explanation. If that explanation disagrees with the policy or does not quote the licence and `days_since_push`, it is rejected. The verdict text then comes from the policy. Sample mode's stand-in quotes the rows, so its explanation is accepted.

In live mode, after the deterministic check, the validator sends the archaeologist claims and the market-judge explanation to Nightshift (`meterless/auto`, or `MODEL_NAME` such as `nvidia/nemotron-3-super-120b-a12b`) and asks for `{"decision":"accept"|"reject",...}`. The judge is advisory. A parsed reject adds a rejection and can force one retry. A parsed accept does not erase a missing or unknown evidence id; the trace records `guard_override`. When the retries are exhausted, the evidence-backed claims stay and the deterministic policy verdict is still produced. The verdict text says the judge was advisory. If the judge times out, returns 401, or raises any other error, that failure is logged as `LLM_JUDGE` with status `timeout`, `unauthorized`, or `error`, `raw_decision` is `unavailable`, and the policy verdict is still produced. The same failure is copied into `metadata.llm.judge` and into the verdict text. The judge does not end the run with 422, 502, or 401. Sample mode does not make this call. A successful call logs `requested_model`, `served_model`, and `latency_ms`.

## Architecture

```
POST /run or run.py
  -> InvestigationOrchestrator
       -> Zetaris adapter (REST, or the sample cache of the same SQL)
       -> evidence graph (pre-existing)
       -> H-MEM store + retrieval
       -> Markovian chunk loop
       -> role prompts
            sample: deterministic stand-in
            live:   Meterless Runtime (primary) or NVIDIA direct (secondary)
       -> validator (deterministic guard, plus a live LLM judge on archaeologist and market-judge claims)
       -> logs/trace-<run_id>.jsonl and stdout
```

Zetaris is load-bearing. The agents do not call GitHub. If the adapter is missing, the key is missing in live mode, or the view returns no row, the run stops with a structured error. Sample mode changes the adapter's data source to `data/sample`, and when `data/real` is present it also answers those committed rows. It does not change the SQL or skip the adapter. The LLM in sample mode is still the stand-in. `data_origin` on a real row stays `github_rest_api:<date>`.

H-MEM (Meterless engine, implemented from the open-source spec, not imported as a package):

- `MemoryStoreService` stores evidence claims in the working tier and promotes the verdict to long-term.
- `MemoryRetrievalService` ranks with the documented hybrid formula and the judge step cites what it recalls.
- `TrustLedgerService` appends create, read, promote, feedback, and conflict actions. It has no delete of ledger lines.
- A later run of the same `repo_id` cites the earlier verdict as `CLM-PRIOR-*` with source `hmem`. Verdict lookup selects `kind=verdict` before the top-k cut, so evidence memories from repeat runs cannot crowd it out. Repeated evidence for the same claim is refreshed instead of stored again. Prior memory does not override the current Zetaris rows.
- The embedder is a deterministic hash, the same idea as the H-MEM reference mock. It is not a neural embedding model.

Markovian (second engine, used for the investigation loop): `ChunkManager` keeps a carryover of at most 1200 characters and stops after 8 chunks. Step timeout defaults to 120 seconds. The whole run defaults to 600 seconds.

## Setup on Windows

Zetaris's own commands run in WSL, as the participant guide says. Nightshift runs on Windows. Give Docker 8 GB and 4 CPUs before the Zetaris install. Python 3.12 is what the Docker image and CI use.

Sample mode needs no accounts. From PowerShell in this repo:

```powershell
py -3 -m venv .venv
Set-ExecutionPolicy -Scope Process Bypass -Force
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
py -3 -m pytest -q
py -3 run.py
```

`Activate.ps1` may be blocked by the execution policy. `Set-ExecutionPolicy -Scope Process Bypass -Force` applies only to that PowerShell window. The copied `.env.example` leaves `ZETARIS_API_KEY`, `METERLESS_RUNTIME_TOKEN`, and `NVIDIA_API_KEY` blank. Placeholder text such as `your-zetaris-api-key` is treated as unset, so `pytest` stays offline.

In a second terminal, with the venv inactive and the server left running:

```powershell
curl.exe -X POST http://127.0.0.1:8000/run -H "Content-Type: application/json" --data-binary "@input_examples/example_1.json"
```

`SAMPLE_MODE=true` is the default. An empty `.env` is enough. `mode: "live"` is rejected while sample mode is on, instead of silently switching.

### Zetaris

Install Zetaris from the Run locally page and confirm `./verify.sh` in WSL. Create an API key in the UI yourself. The app reads `ZETARIS_API_KEY` from the process environment and from `.env`. A Windows user environment variable is enough: leave the `.env` line blank and a non-empty process value wins. Placeholder text is ignored.

```
ZETARIS_REST_URL=http://localhost:8888/api/v1.0
ZETARIS_ORG_ID=1
ZETARIS_API_KEY=
```

A confirmed local `POST http://localhost:8888/api/v1.0/sql-editor/sqls/run` returns HTTP 200 JSON shaped `{"headers": ["column", ...], "data": [["226", "True", ...]]}`. Column names in that body are lowercase. The adapter matches them case-insensitively and keeps every cell as a string, then converts `"226"` and `"True"` when it builds the profile.

Then register the semantic layer. Run **one statement at a time** from `scripts/zetaris/` in the SQL Editor at `http://localhost:3000`. The files are:

1. `01_github_rest_repo.sql` creates the `GITHUB_REST` Lightning database.
2. `02_github_repo_table.sql` registers one public GitHub repository object. Replace `OWNER/REPO`. GitHub's API terms apply. Do not commit a token.
3. `03_curated_files.sql` is the header for the curated CSVs. Zetaris cannot read a file that only exists on the laptop. Copy `data/live/curated_signals.example.csv` and `data/live/revival_signals.example.csv` to storage Zetaris can reach (S3 or platform storage), fill the row whose `repo_id` is `owner/name`, and run the filestore statements with your path. Do not put storage keys in git.
4. `04_phoenix_views.sql` creates the `phoenix` SchemaStore container and the views the agent queries: `phoenix.repo_evidence`, `phoenix.revival_signals`, `phoenix.revival_attempts`, and `phoenix.revival_sources`.

`days_since_push`, tests, CI, and `unique_capability` are curated columns. They are not single fields on GitHub's repository object. This is a SchemaStore semantic layer. It is not a claim that a Zetaris Unified Semantic Layer definition was deployed; that DDL was not verified against a live instance.

The live repository id you POST is `owner/name`, matching GitHub `full_name` and the curated `repo_id`.

### Nightshift, NVIDIA, and Nemotron

Install Nightshift for Windows from meterless.ai. In Nightshift's Providers tab, add NVIDIA and paste the NVIDIA API key there. Do not put that key in Cursor's model settings.

On the NIGHT lanes, add a Nemotron model. The lane id observed in Nightshift model discovery is `nvidia/nemotron-3-super-120b-a12b`. `nemotron-3.5-super-vl-120b` was reported flaky.

From the Nightshift Console, copy the Runtime URL and the runtime token into `.env`:

```
SAMPLE_MODE=false
LLM_PROVIDER=meterless
METERLESS_BASE_URL=http://127.0.0.1:8088/v1
METERLESS_RUNTIME_TOKEN=the-console-token
METERLESS_MODEL=meterless/auto
MODEL_NAME=
```

The default requested model is `meterless/auto`. Set `MODEL_NAME` to override it. For the Nemotron lane:

```
MODEL_NAME=nvidia/nemotron-3-super-120b-a12b
```

Checked on the user's Windows machine at 22 Oct 2026 15:00 BST. Nightshift's public API was `http://127.0.0.1:8088/v1`. `GET /v1/models` with the runtime token returned HTTP 200 and listed `meterless/auto`, `meterless/fast`, `meterless/deep`, `meterless/judge`, `nvidia/nemotron-3-super-120b-a12b`, `nvidia/nemotron-3-ultra-550b-a55b`, and other ids. Chat completions requesting `nvidia/nemotron-3-super-120b-a12b` and `meterless/auto` both returned HTTP 200 in 8–12 seconds. In both cases the response `model` field was `nvidia/nemotron-3-ultra-550b-a55b`: Nightshift reroutes. The message content began with the model's reasoning prose (for example `The user asks: ... We must reply with exactly`) before the answer, sometimes inside a `<think>` block. This app sends `max_tokens` 8192, records `requested_model` and `served_model` on each `MODEL_OUTPUT` trace line and in `metadata.llm`, and parses the last JSON object after that preamble.

Check the connection (this does not print the token):

```powershell
py -3 scripts/test_meterless_connection.py
py -3 scripts/test_meterless_connection.py --auto-detect
py -3 smoke_test.py
```

`smoke_test.py` is the guide's three checks: Zetaris web, Zetaris `SELECT 1` over REST, and a chat completion on the configured route. It accepts either `MODEL_BASE_URL` / `MODEL_API_KEY` / `NEMOTRON_MODEL` or the `METERLESS_*` names. It exits non-zero if any check fails.

Then:

Replace `OWNER/REPO` in `examples_live/example_live.json`, then:

```powershell
py -3 run.py
curl.exe -X POST http://127.0.0.1:8000/run -H "Content-Type: application/json" --data-binary "@examples_live/example_live.json"
```

`--data-binary @file` is the form that works in Windows PowerShell 5.1 and pwsh 7. `curl.exe` is real curl, not `Invoke-WebRequest`.

### Secondary NVIDIA-direct fallback

`LLM_PROVIDER=nvidia` sends chat completions to `https://integrate.api.nvidia.com/v1` with `NVIDIA_API_KEY` and `NVIDIA_MODEL`. This is a dev fallback for machines that are not running Nightshift. It is not the primary route, and it is not how the NVIDIA key is meant to be used during the hackathon. Leave `LLM_PROVIDER=meterless` for the demo.

The product runtime is Meterless Nightshift. The 8 October skeleton's xAI Grok plan is withdrawn.

## Usage

`run.py` and `main.py` both bind `0.0.0.0` and `$PORT` (default 8000).

`POST /run` takes JSON:

```json
{"repository": "sample/phoenix-ledger", "mode": "sample", "max_repair_iterations": 0}
```

`mode` is `sample` or `live`. Errors are `{"error": {"type": "...", "message": "..."}}` with an HTTP status. Unknown sample ids return 404 `RepositoryNotInZetaris`. Invalid ids return 422.

The JSON response includes `decision`, `state`, `log_file`, and `metadata.claims`. Every non-memory claim has `evidence[].query_id`, `sql`, and `source`. `metadata.llm.sample_standin` is true in sample mode. `metadata.hmem.recalled` lists the memories retrieved for the judge. `metadata.hmem.prior_verdicts` is empty on the first run of a repo and filled on the next.

Docker, dependencies installed at build time:

```powershell
docker build -t phoenixrepo .
docker run --rm -p 8000:8000 -e SAMPLE_MODE=true -e PORT=8000 phoenixrepo
```

## Sample mode

`SAMPLE_MODE=true`, or `"mode": "sample"`, runs the same orchestrator, validator, H-MEM, and Markovian loop. The Zetaris adapter answers the same SELECT statements from `data/sample/*.csv`. Those rows are synthetic and the column `data_origin` is `synthetic_labelled`. They are not a scrape of real GitHub repositories and they are not hidden answers. If `data/real/*.csv` is present, the same adapter also serves those rows. Their `data_origin` is `github_rest_api:<date>`. Serving them in sample mode does not call GitHub or Nightshift.

The LLM is `sample-standin` / `deterministic-rule-standin`. The response label says so. It reads the rows in the prompt. It does not load `output_examples/`.

The three committed inputs and the outputs next to them:

| Input | Repository | Output decision |
| --- | --- | --- |
| `input_examples/example_1.json` | `sample/phoenix-ledger` | `RESURRECTED` |
| `input_examples/example_2.json` | `sample/generic-qr` | `NO_GO` |
| `input_examples/example_3.json` | `sample/field-notes` | `RESURRECTION_FAILED` |

Each output file is the JSON body of one real sample run, including that run's ids. A new run gets new ids. The decisions and claim sentences stay the same. Sample mode does not read these files back.

## Sponsor usage

- **Zetaris.** The only evidence source. Live calls use `POST {ZETARIS_REST_URL}/sql-editor/sqls/run` with a bearer API key, `X-Org-ID`, a fresh `X-Request-ID`, and a body of `queryId`, one `sql` statement, `source: SqlEditor`, and `limit`. Credentials are not logged.
- **Meterless H-MEM.** Persistent memory of evidence claims and verdicts, with retrieval traces and an append-only ledger. `tests/engines/test_hmem_conformance.py` holds project tests modelled on the published H-MEM spec: the store, the ranking formula, promotion, decay of non-long-term memory, and supersede-without-delete. The official TypeScript conformance suite at github.com/Meterless/Meterless was not run.
- **Meterless Markovian.** The multi-step investigation is a chunk loop with a bounded carryover.
- **Meterless Runtime.** Primary model provider, implemented from the Power prompt "Add Meterless as a provider" on meterless.ai/engines.html (fetched 22 Oct 2026, Runtime contract as published there). Discovery is `GET /v1/models`. Chat is `POST /v1/chat/completions` with `meterless-app: phoenixrepo` and `meterless-scope: user`. The default model is `meterless/auto`, overridable with `MODEL_NAME` (`nvidia/nemotron-3-super-120b-a12b` is the observed Nemotron lane). Nightshift may serve a different id than the one requested; each trace records both. The placeholder token `rt-local` is rejected. `/healthz` is not proof.
- **NVIDIA.** Optional direct fallback via `LLM_PROVIDER=nvidia`. The award path is the Nightshift lane above. A manual check on 22 Oct 2026 15:00 BST saw Nightshift serve `nvidia/nemotron-3-ultra-550b-a55b` when `meterless/auto` or `nvidia/nemotron-3-super-120b-a12b` was requested. The automated tests here still use a local mock.
- **Cursor.** Used as a coding agent. A Cursor cloud agent (Grok 4.7) implemented this build-window core, including the real-data snapshot and the live LLM judge. Cursor credit is not the product's model route. As of the 22 Oct 2026 organiser update, Cursor is optional and is declared here as a tool, not as a sponsor dependency of the running product. CI does not call Nightshift.

`metadata.json` is the same summary in one file.

## Data sources and licences

| Data | What it is | Licence / terms |
| --- | --- | --- |
| `data/sample/repo_evidence.csv`, `data/sample/revival_signals.csv` | Original synthetic rows written for this project. `data_origin=synthetic_labelled`. No PII. Not measurements of real repositories. | Project content. No upstream dataset. |
| `data/sample/revival_attempts.csv`, `data/sample/revival_sources.csv` | Header only. Sample repos have no attempt and no cited GitHub source. | Empty. |
| `data/real/repo_evidence.csv`, `data/real/revival_signals.csv`, `data/real/provenance.json` | Snapshot of public GitHub REST facts for five repositories, plus rule-based columns. `provenance.json` records `collected_at` (`2026-10-22T15:14:30.161592+00:00`), the endpoints, and the rule for each curated column. API fields that were null or missing are empty. No token is in the file. | GitHub API terms. Public repository metadata. The snapshot is committed so a judge can read it offline. |
| `data/real/revival_attempts.csv`, `data/real/revival_sources.csv`, `data/real/revival_attempts_provenance.json` | Fork attempt rows and the documents the hygen PRD quotes. Collected by `scripts/collect_revival_attempt.py` at the `collected_at` in that provenance file. | GitHub API terms. Public metadata and issue text. |
| `data/live/*.example.csv` | Header-only templates. | Empty. |
| GitHub REST registration in `scripts/zetaris/02_github_repo_table.sql` | Live query of `https://api.github.com/repos/OWNER/REPO` when you run it inside Zetaris. | GitHub API terms. Public repository metadata. Do not commit tokens or private-repo responses. |
| Curated CSV you fill | Operator-supplied columns (days since push, tests, CI, uniqueness). | Whatever you are allowed to store. Do not put personal data in it. |

No third-party dataset is vendored. The sample cache is not presented as GitHub data.

## Libraries

Python, FastAPI, Uvicorn, Pydantic, pydantic-settings, python-dotenv, httpx. Tests use pytest. No agent framework. HTTP clients are httpx. The Docker image installs `requirements.txt` during `docker build` and does not install packages on start.

## Pre-existing components

Declared: everything committed before 22 October 2026 00:00 UTC. In this repository that is the 8 October 2026 skeleton, through commit `62a1540` (`chore: complete pre-opening foundation audit`).

That skeleton includes the FastAPI process, Docker image, evidence graph and pack, integrity hash, state machine (including the three terminal names), audit trail, secret redaction, and `FoundationOrchestrator`. The foundation orchestrator still stops at `EVIDENCE_COMPLETE` with `decision: null`. It is not the path `POST /run` uses now. `POST /run` uses `InvestigationOrchestrator`, which is build-window work.

Build-window work includes the Zetaris REST client, the sample SQL cache, the Meterless Runtime provider, the NVIDIA fallback, H-MEM, the Markovian loop, the seven roles, the validator, traces in the hackathon field list, sample verdicts, `smoke_test.py`, the real-repository snapshot, the live LLM judge, the revival PRD, and this README. A Cursor cloud agent (Grok 4.7) wrote that build-window code, including this phase. It did not call Nightshift from CI.

## Plan, act, verify

The planner writes `docs/revival/<owner>__<name>.prd.md` and a JSON sibling for any repository that is not `NO_GO`. The committed hygen plan is `docs/revival/jondot__hygen.prd.md`, produced by `scripts/write_revival_prd.py` from the snapshot. `drafted_by` is `deterministic` and `llm_drafted` is false. A live model may draft the same shape; the validator drops any line that does not cite a loaded evidence id, and the deterministic builder replaces a draft that still fails after one retry.

The plan has three stages, each small enough to finish before 27 Oct 2026 23:45 UTC:

1. **REVIVE.** Install, build, test, and make eligible build/test CI green on the fork default branch. This is the only gate for `RESURRECTED`.
2. **REFACTOR / OPTIMISE.** One or two cleanups named by the evidence. For hygen that is the `actions/checkout@v2` and `actions/setup-node@v2` pins in `.github/workflows/ci.yml`.
3. **UPGRADE.** One visible change from a real issue. For hygen that is [jondot/hygen#450](https://github.com/jondot/hygen/issues/450), because the issue body shows `hygen --version` printing `Error: please specify a generator.`

`scripts/collect_revival_attempt.py` records one `revival_attempts` row per stage (`stage`, `status`, `ci_run_url`, `commit_sha`) plus the source rows the PRD quotes. The fork was identical to upstream when that script ran, so every stage is `not_attempted` and `commit_sha` is empty. The observed compare base SHA is stored in `head_sha` and is not an attempt commit. A later collection can mark stage revive success or failure only when the compare is ahead of upstream and the fork default branch has an eligible build/test run. Refactor and upgrade are not guessed from commit messages.

This loop was not closed here. The plan is committed. No coding agent has pushed to `https://github.com/Fu93/hygen`, and this environment did not run that repository's install, tests, or Actions.

## Real snapshot and local Zetaris

`python scripts/curate_real_repos.py` calls the public GitHub REST API (optional `GITHUB_TOKEN`, never written) and rewrites `data/real`. The committed files are the snapshot from the `collected_at` timestamp in `data/real/provenance.json`. Re-running the script replaces them with a new timestamp. Curated columns are only the rules in that file: tests come from tree paths, CI from the latest eligible build or test workflow conclusion (security scans, `action_required` runs, and fork pull requests do not count; the workflow name, event, and run URL are copied into the signal when the API sent them), and `successor_url` is a README URL next to "in favor of", "replaced by", "successor", or "moved to". A homepage is not a successor. `similar_repo_count` is the `total_count` of a close name or description search in the same language. It is not a topic-size count. `unique_capability` is the description copied verbatim. An empty Actions conclusion stays empty and becomes the signal `ci_unknown`.

The offline evidence policy on this snapshot is what `tests/data/test_curation.py` asserts. Each verdict below is the first policy reason, not a hand label:

- `golang/dep` is `NO_GO` because the README places `https://golang.org/ref/mod` next to "in favor of". The homepage is not that URL.
- `jxnblk/rgx` is `NO_GO` because the repository payload has no SPDX licence id.
- `discreteoptimization/setcover` is `RESURRECTION_FAILED` because the tree has no test path. Its Actions conclusion is empty, so CI is unknown and is not the reason.
- `jondot/hygen` is `RESURRECTION_FAILED` because its build/test workflow `CI` failed. The failed job step recorded by the Actions jobs API is `Use Node.js 14.x`, a toolchain setup step, so CI has decayed. That is not a test failure. The run is a schedule on `master` from `jondot/hygen`. The fork `Fu93/hygen` is identical to upstream (`ahead_by` 0) and has no Actions runs, so the attempt rows are `not_attempted`. An investigation verdict adds "Revival has not yet been attempted." The deterministic PRD is `docs/revival/jondot__hygen.prd.md`. No LLM drafted it.
- `42wim/matterbridge` is `RESURRECTED` with CI unknown. It is Apache-2.0, not archived, last pushed more than 180 days before collection, has test filenames, and the README records no successor URL. Its build/test workflow is `development.yml`. The latest runs of that workflow are fork pull requests awaiting approval (`action_required`) or fork-PR failures, so the default branch build state is unknown. A scheduled CodeQL success is a security scan and is not counted as CI success. The verdict text says CI is unknown. It does not say a successful CI conclusion supports revival. No revival attempt was collected for it, so an investigation verdict also says revival has not yet been attempted.

`ariya/phantomjs` is not in this snapshot. An earlier readme API fetch did not contain "Puppeteer", "Playwright", or a successor phrase, so this dataset does not say it was replaced by those tools.

Loading those CSVs into the user's local Zetaris (Docker on Windows or WSL2, REST at `http://localhost:8888/api/v1.0`) is **not verified** from this environment:

1. From the Zetaris platform directory, inside WSL, set `PHOENIX_REAL_DATA` to the absolute path of `data/real`. Pass the file explicitly: `docker compose -f docker-compose.yml -f /path/to/phoenixrepo/scripts/zetaris/phoenix-real-data.compose.yml`. It is not named `docker-compose.override.yml`, so Compose will not merge it automatically. The user's install reports the service name `driver` and rejects a mount under `/opt` with `Not allowed to access system file path`. The compose file mounts the snapshot at `/home/zetaris/customer-data/shared/phoenix_real`, which is the default `-ContainerPath`. This agent did not execute the mount.
2. `powershell -ExecutionPolicy Bypass -File scripts\zetaris\load_real.ps1` sends one Lightning statement per REST call. `ZETARIS_API_KEY` is read from the environment and is not printed. A HTTP 200 whose body contains an error object is a failure (`200 error-body`). If the error text says the database, table, schemastore container, or view already exists, the script continues. The word "duplicate" alone is not treated as existence. A view that already exists prints `view-exists` and a warning that the previous view was left in place. `-Alternative Filestore` uses `CREATE LIGHTNING FILESTORE TABLE ... FORMAT CSV OPTIONS (path ...)`. `-Alternative SparkCsv` uses `CREATE TABLE ... USING com.databricks.spark.csv`. The SQL manual documents both. The getting-started FILESTORE example that has been seen working is a public S3 path, not this bind mount. If `phoenix.repo_evidence` already exists from `04_phoenix_views.sql`, the warning leaves that older view in place. A DROP statement was not verified, so the script does not send one. After the statements it selects `repo_id, data_origin` and prints `verified rows=N` only when the row count matches `data/real/repo_evidence.csv` and every `data_origin` starts with `github_rest_api`. Otherwise it prints `verify-failed`.

`scripts/demo_live.ps1` checks Docker, `GET {METERLESS_BASE_URL}/models` with the same bearer token and `meterless-app` header the product uses, `GET /health` and requires `sample_mode` false, and `SELECT 1` on Zetaris. It then `POST /run` for three real repositories, one for each verdict. It writes `logs/demo-live.md` through `scripts/demo_summary.py`. It was not run here. The API process has to be up in live mode first. `scripts/render_trace.py` turns a trace JSONL into markdown or HTML for a demo video. That renderer was executed by the offline tests, not against a live Nightshift trace.

## Limitations

- CI and the tests in this repo never call a live Zetaris or a live Nightshift. Zetaris and Meterless are checked with a local HTTP server that speaks the documented contract, and with sample mode. The LLM-judge tests use a mock `complete()`. You still need a Windows run of `scripts/demo_live.ps1` before calling the live judge or the real-CSV mount verified.
- What was run live: on 22 Oct 2026 at `2026-10-22T15:14:30Z` this environment ran `scripts/curate_real_repos.py` against `https://api.github.com` with no token and wrote the five-repository snapshot in `data/real`. On 22 Oct 2026 at `2026-10-22T15:53:26Z` it ran `scripts/collect_revival_attempt.py` for fork `Fu93/hygen` against upstream `jondot/hygen`, including open issue 450, the CI workflow, and `package.json`. On 22 Oct 2026 15:00 BST a Windows machine saw Nightshift `GET /v1/models` return 200 and chat for `meterless/auto` and `nvidia/nemotron-3-super-120b-a12b` return HTTP 200 served as `nvidia/nemotron-3-ultra-550b-a55b`. The same machine's Zetaris SQL Editor body was `{"headers": [...], "data": [[string cells]]}`.
- What was not run live from this agent: the compose bind mount, `load_real.ps1`, a Nightshift LLM-judge call inside `POST /run`, `demo_live.ps1`, and any commit or CI run on `Fu93/hygen`. The PRD was not executed. Nightshift did not draft it.
- The verdict is a fixed evidence policy. The live LLM judge is advisory: a reject can retry once, and a timeout, 401, or other judge error is logged and does not replace the policy verdict. A missing evidence id still beats an LLM accept. The judge cannot receive `RESURRECTED` when the rows do not support it. Stage revive is the only attempt stage that can change that verdict. Refactor and upgrade are reported beside it.
- `RESURRECTED` is not, by itself, a rebuilt repository. It becomes one only after a recorded stage-revive attempt has eligible build/test CI success on the fork default branch. That attempt has not been made for the repos in this snapshot.
- The sample stand-in's first archaeologist and builder attempts are deliberately unsupported so the critique loop can be demonstrated offline.
- H-MEM embeddings are a hash mock. Ranking tests cover the published formula. They do not claim neural retrieval quality.
- One GitHub Lightning REST table is one repository endpoint, following the Zetaris recipe style. A catalog of many repos means more tables or a file you register yourself.
- Curated columns can be wrong if you fill them wrong. The agent trusts the views.
- H-MEM's JSON store is a local file (`data/hmem/runtime-store.json`). It is gitignored. A host with an ephemeral disk will forget it on restart.
- Nightshift does not run on this Linux environment, so the Nemotron lane was not exercised here.
- Timeouts are 30 seconds for Zetaris, 180 seconds for a Meterless chat (the Power prompt's model-load budget), 120 seconds per Markovian chunk, and 600 seconds for a run.

## Tests

```powershell
py -3 -m pytest -q
py -3 scripts/compliance_check.py
```

CI runs both on Python 3.12.
