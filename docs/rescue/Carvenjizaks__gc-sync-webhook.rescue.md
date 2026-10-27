# Rescue plan: Carvenjizaks/gc-sync-webhook

Gate 1: **CANDIDATE**. Drafted by: Nemotron (served `nvidia/nemotron-3-ultra-550b-a55b`), verified by code.
Trace: `logs/trace-rescue-carvenjizaks__gc-sync-webhook-20261027T204557Z.jsonl`, run hash `332d1cfe373e11ce964da21ce9b7eae829b61e9f1c7bd378229b2c5e49859564`.

Every item below cites evidence that gate 1 recorded, and its quote was found verbatim in that evidence. Items that failed the check are listed at the end and are not part of the plan.

## Positioning

- **User:** Global Control users with website forms
- **Job:** auto-sync contact submissions to Global Control with proper tags
- **Gap to own:** maintained free OSS alternatives lack Global Control–specific tag mapping and self-hostable webhook deployment
- Evidence: `candidate:Carvenjizaks/gc-sync-webhook`, `must_beat:web:https://www.staticforms.dev/blog/send-form-submissions-to-n8n-webhook`, `assessment:differentiation`

## Can gate 2 verify it?

**needs_test_command.** A manifest is present but no test path is visible in the root: gate 2 would stop at NEEDS_REVIEW until a test command exists.

## Stage 1 — REVIVE

### R1. Add test command and verify both sync endpoints

- **Why:** Gate 2 requires a test command; the repo has a manifest but no visible test path, and feasibility notes curl-based testing instructions for two endpoints
- **Acceptance:** Running `npm test` (or equivalent) executes without error and validates POST /api/sync and POST /api/batch-sync against a mock Global Control API
- **Evidence:** `repo:tree`, `assessment:feasibility` — “two documented endpoints (/api/sync, /api/batch-sync), explicit environment variable configuration (GLOBALCONTROL_API_KEY, GLOBALCONTROL_API_URL), curl-based testing instructions”

### R2. Confirm serverless deployments work on Vercel and Netlify

- **Why:** The candidate advertises deployment to Vercel, Netlify, or any serverless platform; vercel.json exists in the repo root
- **Acceptance:** A clean deploy to Vercel and Netlify succeeds and the /api/sync endpoint returns 200 for a valid payload
- **Evidence:** `candidate:Carvenjizaks/gc-sync-webhook`, `repo:tree`, `assessment:feasibility` — “Deploy to Vercel, Netlify, or any serverless platform”

### R3. Validate tag mapping for the three documented tags

- **Why:** The pain point is manually mapping submissions to specific tags (NL-FF newsletter, MGM, TT4Men); the candidate lists these mappings
- **Acceptance:** A test payload with each tag value results in the correct Global Control tag being applied (verified via mock or sandbox)
- **Evidence:** `candidate:Carvenjizaks/gc-sync-webhook`, `assessment:problem_severity` — “manually mapping contact submissions to specific tags (NL-FF newsletter, MGM, TT4Men)”

## Stage 2 — OPTIMISE

### O1. Add automated test suite with CI

- **Why:** Only curl-based testing instructions exist; repo shows a scripts folder and package.json but no test script
- **Acceptance:** GitHub Actions (or similar) runs unit and integration tests on every push; coverage >80% for api/ routes
- **Evidence:** `repo:tree`, `assessment:feasibility` — “curl-based testing instructions”

### O2. Pin and audit dependencies; add dependabot

- **Why:** node_modules and package-lock.json are present; no evidence of automated dependency updates or security auditing
- **Acceptance:** Dependabot alerts resolved; `npm audit` reports zero high/critical vulnerabilities; lockfile updated
- **Evidence:** `repo:tree` — “repository root: .env.local, .gitignore, README.md, api, node_modules, package-lock.json, package.json, scripts, vercel.json”

### O3. Complete API documentation with request/response schemas

- **Why:** The candidate README truncates the /api/sync body example; feasibility says endpoints are documented but the text is incomplete
- **Acceptance:** README.md contains full JSON schemas for both endpoints, including all fields, tag values, error codes, and example curl commands
- **Evidence:** `candidate:Carvenjizaks/gc-sync-webhook`, `assessment:feasibility` — “email": "require”

## Stage 3 — UPGRADE

### U1. Harden the five differentiating capabilities with contract tests and docs

- **Why:** Differentiation assessment lists five capabilities not covered by any maintained free OSS alternative; the candidate quotes them but they need verification and explicit documentation
- **Acceptance:** Each capability (auto-sync with tag mapping, serverless deploy, form webhook config, single sync, batch sync) has a contract test and a dedicated docs section
- **Evidence:** `assessment:differentiation` — “Missing capabilities: 'Auto-sync contact data with tag mapping', 'Deploy webhook to serverless platform', 'Configure website form with webhook URL', 'Sync single contact to Global Control', 'Sync multiple contacts at once' (5 not covered by any maintained free OSS alternative)”

### U2. Add webhook authentication and rate limiting to strengthen self-hostable advantage

- **Why:** Must-beat notes the alternative has no free tier or permissive licence while the candidate is free and self-hostable; adding auth and rate limiting makes the self-hosted offering production-grade without sacrificing the free/self-hosted edge
- **Acceptance:** Endpoints accept a shared secret header (e.g., X-GC-Sync-Token); requests exceeding configurable rate return 429; docs explain setup
- **Evidence:** `must_beat:web:https://www.staticforms.dev/blog/send-form-submissions-to-n8n-webhook`, `assessment:moat` — “candidate is free and self-hostable; the alternative has no free tier or permissive licence”

### U3. Publish a Docker image and npm package for zero-config deployment

- **Why:** Productization path notes the addressable audience is undefined and no go-to-market channel beyond self-serve; a published Docker image and npm package lower the barrier for Global Control users
- **Acceptance:** Docker image on GHCR/Docker Hub and npm package `gc-sync-webhook` installable; `docker run` and `npx gc-sync-webhook` start the server with env vars
- **Evidence:** `assessment:productization_path`, `assessment:differentiation` — “Clear developer-oriented deployment path exists, but the addressable audience (Global Control users with web forms) is undefined in size, no pricing or revenue model is articulated, and no go-to-market channel is identified beyond self-serve deployment”

## Must beat

- **web:https://www.staticforms.dev/blog/send-form-submissions-to-n8n-webhook (Static Forms → n8n)**: Keep the free, self-hostable, Global Control–specific tag-mapping webhook; the alternative lacks a free tier, permissive licence, and Global Control integration (`must_beat:web:https://www.staticforms.dev/blog/send-form-submissions-to-n8n-webhook`)

## Risks

- Demand signal is weak and ambiguous for Global Control–specific sync (assessment:demand_evidence)
- No structural moat; the integration is replicable in a weekend with basic HTTP/JSON knowledge (assessment:moat)
- Addressable audience size undefined; no pricing or revenue model articulated (assessment:productization_path)
- Upstream Global Control API changes could break sync without notice

## Evidence

| id | kind | source | content hash |
| --- | --- | --- | --- |
| `candidate:Carvenjizaks/gc-sync-webhook` | candidate | https://github.com/Carvenjizaks/gc-sync-webhook | — |
| `prior:web:https://www.staticforms.dev/blog/send-form-submissions-to-n8n-webhook` | prior | https://www.staticforms.dev/blog/send-form-submissions-to-n8n-webhook | — |
| `must_beat:web:https://www.staticforms.dev/blog/send-form-submissions-to-n8n-webhook` | must_beat | — | — |
| `assessment:problem_severity` | assessment | — | — |
| `assessment:demand_evidence` | assessment | — | — |
| `assessment:differentiation` | assessment | — | — |
| `assessment:feasibility` | assessment | — | — |
| `assessment:productization_path` | assessment | — | — |
| `assessment:moat` | assessment | — | — |
| `repo:tree` | repo | https://github.com/Carvenjizaks/gc-sync-webhook | — |
