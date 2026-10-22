# PhoenixRepo

PhoenixRepo does not assume an abandoned repository should be saved. It asks whether the evidence says it deserves another life, and it will not issue a verdict it cannot point at a row.

Track 02 of the Open Agent Hackathon 2026, "The Agent That Can Explain Why". Solo entry under the team name PhoenixRepo.

This README describes the build-window core. `PRE_EVENT_PLAN.md` and `OPENING_READY.md` are the 8 October 2026 skeleton notes. They are historical. Where they disagree with this file, this file is the current behaviour.

## Problem

Abandoned repositories are cheap to restart and expensive to regret. A revival wastes time when the idea is a commodity clone, the licence will not travel, or nobody can show that the code still has a test signal. PhoenixRepo runs a small team of agents over one SQL surface and stops at one of three evidence verdicts:

| Verdict | Meaning in this core |
| --- | --- |
| `NO_GO` | The Zetaris rows fail the value rules (licence, abandonment, successor, or distinct capability). |
| `RESURRECTION_FAILED` | The rows say it is worth reviving, and the recorded tests or CI do not pass inside the repair budget. |
| `RESURRECTED` | The cited rows satisfy the revival rules: permissive licence, abandoned, a distinct capability, a licence file, tests, and a successful CI status. |

`RESURRECTED` does **not** mean this program cloned, compiled, tested, or republished the repository. The pre-event plan used that word for a future prover. This core reuses the state-machine token and means "the evidence policy accepted a revival contract". The response field `metadata.scope` says the same thing.

## Agents

The orchestrator, the evidence graph, and the state machine are infrastructure from the 8 October skeleton. They are not agents. Six roles share that graph.

| Role | What it does | Can block or force a retry |
| --- | --- | --- |
| Archaeologist | Asks Zetaris for `phoenix.repo_evidence` and `phoenix.revival_signals` and drafts claims. | No. The validator can reject its claims. |
| Market judge | Writes the explanation of the evidence-policy verdict. | No. An explanation that disagrees with the rows is rejected and retried once. The rows keep the verdict. |
| Architect | Drafts a revival contract after a value pass. Skipped on `NO_GO`. | No. |
| Builder | Proposes the next step. It is not allowed to mark verification as `PASS`. | No. A `PASS` or a step with no evidence id is rejected. |
| Validator | Checks evidence ids, quoted row values, and the builder rule. | Yes. It blocks unsupported claims and forces one retry. It is the only role that may accept a revival. |
| Diagnostician | On a failed validation, and only while repair budget remains, asks Zetaris for `phoenix.revival_signals` again. | It cannot declare success. A second failure becomes `RESURRECTION_FAILED`. |

Collaboration that is actually in the run:

- Shared state: one evidence graph and one H-MEM store.
- Critique loop: the validator rejects the archaeologist's first unsupported demand claim, and rejects a builder `PASS`.
- Retry: one bounded retry for those rejections, plus at most `max_repair_iterations` signal re-queries.
- Role authority: the builder cannot write `PASS`. `NO_GO` does not call the architect, builder, or diagnostician.
- Non-linear flow: a failed validation returns to Zetaris through the diagnostician instead of continuing forward.
- Markovian carryover: each role sees a bounded summary of the previous step, not the raw transcript.

The sample stand-in is written to emit one unsupported archaeologist claim and one builder `PASS` on the first attempt, so the critique loop is visible without a live model. That is a property of the stand-in, not a recorded trace being replayed. A live model that does not make those mistakes is not forced to fail. The validator still rejects them if they appear.

The terminal state itself is an evidence policy in `app/agents/policy.py`, not a free-form opinion:

- permissive SPDX licence (`MIT`, `Apache-2.0`, `BSD-3-Clause`, `BSD-2-Clause`, `ISC`, `MPL-2.0`)
- no successor URL
- `days_since_push` at least 180
- a non-empty `unique_capability` and `similar_repo_count` under 40
- for `RESURRECTED`: `has_tests`, `has_license_file`, and `ci_status = success`, with no `tests_absent` or `ci_failure` signal

A live model writes the explanation. If that explanation disagrees with the policy or does not quote the licence and `days_since_push`, it is rejected. The verdict text then comes from the policy. Sample mode's stand-in quotes the rows, so its explanation is accepted.

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
       -> validator
       -> logs/trace-<run_id>.jsonl and stdout
```

Zetaris is load-bearing. The agents do not call GitHub. If the adapter is missing, the key is missing in live mode, or the view returns no row, the run stops with a structured error. Sample mode changes the adapter's data source to `data/sample`. It does not change the SQL or skip the adapter.

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
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
py -3 -m pytest -q
py -3 run.py
```

`Activate.ps1` may be blocked by the execution policy. `Set-ExecutionPolicy -Scope Process Bypass` applies only to that PowerShell window. The copied `.env.example` leaves `ZETARIS_API_KEY`, `METERLESS_RUNTIME_TOKEN`, and `NVIDIA_API_KEY` blank. Placeholder text such as `your-zetaris-api-key` is treated as unset, so `pytest` stays offline.

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
4. `04_phoenix_views.sql` creates the `phoenix` SchemaStore container and the two views the agent queries: `phoenix.repo_evidence` and `phoenix.revival_signals`.

`days_since_push`, tests, CI, and `unique_capability` are curated columns. They are not single fields on GitHub's repository object. This is a SchemaStore semantic layer. It is not a claim that a Zetaris Unified Semantic Layer definition was deployed; that DDL was not verified against a live instance.

The live repository id you POST is `owner/name`, matching GitHub `full_name` and the curated `repo_id`.

### Nightshift, NVIDIA, and Nemotron

Install Nightshift for Windows from meterless.ai. In Nightshift's Providers tab, add NVIDIA and paste the NVIDIA API key there. Do not put that key in Cursor's model settings.

On the NIGHT lanes, add a Nemotron model. Use a **Nemotron 3 Super** lane. `nemotron-3.5-super-vl-120b` was reported flaky. Confirm the exact id in your NVIDIA catalog and in Nightshift's model list. This repo does not guess a slug.

From the Nightshift Console, copy the Runtime URL and the runtime token into `.env`:

```
SAMPLE_MODE=false
LLM_PROVIDER=meterless
METERLESS_BASE_URL=http://127.0.0.1:8088/v1
METERLESS_RUNTIME_TOKEN=the-console-token
METERLESS_MODEL=meterless/auto
```

`meterless/auto` is a routing mode. If several lanes are eligible it may not land on Nemotron. For a Nemotron demo, pin a `meterless/*` id from discovery that is backed by the Nemotron lane.

Check the connection (this does not print the token):

```powershell
py -3 scripts/test_meterless_connection.py
py -3 scripts/test_meterless_connection.py --auto-detect
py -3 smoke_test.py
```

`smoke_test.py` is the guide's three checks: Zetaris web, Zetaris `SELECT 1` over REST, and a chat completion on the configured route. It accepts either `MODEL_BASE_URL` / `MODEL_API_KEY` / `NEMOTRON_MODEL` or the `METERLESS_*` names. It exits non-zero if any check fails.

Then:

Replace `OWNER/REPO` in `input_examples/example_live.json`, then:

```powershell
py -3 run.py
curl.exe -X POST http://127.0.0.1:8000/run -H "Content-Type: application/json" --data-binary "@input_examples/example_live.json"
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

`SAMPLE_MODE=true`, or `"mode": "sample"`, runs the same orchestrator, validator, H-MEM, and Markovian loop. The Zetaris adapter answers the same SELECT statements from `data/sample/*.csv`. Those rows are synthetic and the column `data_origin` is `synthetic_labelled`. They are not a scrape of real GitHub repositories and they are not hidden answers.

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
- **Meterless Runtime.** Primary model provider, implemented from the Power prompt "Add Meterless as a provider" on meterless.ai/engines.html (fetched 22 Oct 2026, Runtime contract as published there). Discovery is `GET /v1/models`. Chat is `POST /v1/chat/completions` with `meterless-app: phoenixrepo` and `meterless-scope: user`. The placeholder token `rt-local` is rejected. `/healthz` is not proof.
- **NVIDIA.** Optional direct fallback. The award path is a Nemotron lane reached through Nightshift, which this Linux build environment cannot run. That live path is unverified here.
- **Cursor.** Used as a coding agent. A Cursor cloud agent (Grok 4.7) implemented this build-window core. Cursor credit is not the product's model route. As of the 22 Oct 2026 organiser update, Cursor is optional and is declared here as a tool, not as a sponsor dependency of the running product.

`metadata.json` is the same summary in one file.

## Data sources and licences

| Data | What it is | Licence / terms |
| --- | --- | --- |
| `data/sample/repo_evidence.csv`, `data/sample/revival_signals.csv` | Original synthetic rows written for this project. `data_origin=synthetic_labelled`. No PII. Not measurements of real repositories. | Project content. No upstream dataset. |
| `data/live/*.example.csv` | Header-only templates. | Empty. |
| GitHub REST registration in `scripts/zetaris/02_github_repo_table.sql` | Live query of `https://api.github.com/repos/OWNER/REPO` when you run it inside Zetaris. | GitHub API terms. Public repository metadata. Do not commit tokens or private-repo responses. |
| Curated CSV you fill | Operator-supplied columns (days since push, tests, CI, uniqueness). | Whatever you are allowed to store. Do not put personal data in it. |

No third-party dataset is vendored. The sample cache is not presented as GitHub data.

## Libraries

Python, FastAPI, Uvicorn, Pydantic, pydantic-settings, python-dotenv, httpx. Tests use pytest. No agent framework. HTTP clients are httpx. The Docker image installs `requirements.txt` during `docker build` and does not install packages on start.

## Pre-existing components

Declared: everything committed before 22 October 2026 00:00 UTC. In this repository that is the 8 October 2026 skeleton, through commit `62a1540` (`chore: complete pre-opening foundation audit`).

That skeleton includes the FastAPI process, Docker image, evidence graph and pack, integrity hash, state machine (including the three terminal names), audit trail, secret redaction, and `FoundationOrchestrator`. The foundation orchestrator still stops at `EVIDENCE_COMPLETE` with `decision: null`. It is not the path `POST /run` uses now. `POST /run` uses `InvestigationOrchestrator`, which is build-window work.

Build-window work includes the Zetaris REST client, the sample SQL cache, the Meterless Runtime provider, the NVIDIA fallback, H-MEM, the Markovian loop, the six roles, the validator, traces in the hackathon field list, sample verdicts, `smoke_test.py`, and this README.

## Limitations

- CI and the tests in this repo never call a live Zetaris or a live Nightshift. Zetaris and Meterless are checked with a local HTTP server that speaks the documented contract, and with sample mode. You still need a Windows run of `smoke_test.py` and one live `POST /run` before calling the integration verified.
- The verdict is a fixed evidence policy. A model cannot overrule a missing licence or a red CI row. Its job on the live path is the explanation, which is rejected when it contradicts the rows.
- `RESURRECTED` is not a rebuilt repository.
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
