# Rescue plan: DayanK/LinkedinSyncContacts

Gate 1: **CANDIDATE**. Drafted by: Nemotron (served `nvidia/nemotron-3-ultra-550b-a55b`), verified by code.
Trace: `logs/trace-rescue-dayank__linkedinsynccontacts-20261027T205555Z.jsonl`, run hash `fe66955896446643742716558eadd22b7534691c994c4669508f81a13bbb71aa`.

Every item below cites evidence that gate 1 recorded, and its quote was found verbatim in that evidence. Items that failed the check are listed at the end and are not part of the plan.

## Positioning

- **User:** Professionals who need to extract, back up, and access LinkedIn connections across devices without manual effort
- **Job:** Extract, sync, and manage LinkedIn connections with cloud backup and multi-device access
- **Gap to own:** No maintained free and self-hostable alternative covers contact extraction from LinkedIn, cloud sync and backup, search and filter contacts, CSV export, and user registration and login together
- Evidence: `candidate:DayanK/LinkedinSyncContacts`, `assessment:problem_severity`, `assessment:differentiation`, `must_beat:web:https://leaddelta.com/product/auto-sync`, `must_beat:web:https://www.n8nflow.net/workflows/8041-weekly-linkedin-connections-sync-and-analysis-with-apify-and-google-sheets`

## Can gate 2 verify it?

**needs_test_command.** No manifest and no test path in the root: gate 2 would stop at NEEDS_REVIEW until a build and test command exist.

## Stage 1 — REVIVE

### R1. Add a runnable test command for the stated job *(added by code)*

- **Why:** Without one, gate 2's execution loop cannot verify any revival and stops at NEEDS_REVIEW.
- **Acceptance:** One command runs the tests and exits 0 on a clean checkout.
- **Evidence:** `repo:tree` — “.gitignore”

### R2. Verify backend starts and serves the API on the documented port

- **Why:** README quick start claims backend runs on http://localhost:300 but the port appears truncated; the job requires a working backend
- **Acceptance:** cd backend && npm install && npm run dev starts a server that responds to a health endpoint
- **Evidence:** `candidate:DayanK/LinkedinSyncContacts` — “Running on http://localhost:300”

### R3. Confirm browser extension loads and can authenticate against the local backend

- **Why:** The stated job includes a browser extension with auto-sync; feasibility calls the project prototype status with no production hardening evidence
- **Acceptance:** Extension installs in Chrome/Firefox, completes OAuth flow with local backend, and shows a successful sync
- **Evidence:** `candidate:DayanK/LinkedinSyncContacts`, `assessment:feasibility` — “Browser Extension - Chrome/Firefox with auto-sync”

## Stage 2 — OPTIMISE

### O1. Add automated tests for backend API, frontend components, and extension background scripts

- **Why:** Feasibility scores 1 citing no test suite; a test suite is needed before any upgrade work
- **Acceptance:** Test coverage reported for backend routes, React components, and extension message passing; all tests pass in CI
- **Evidence:** `assessment:feasibility`, `candidate:DayanK/LinkedinSyncContacts` — “provides no evidence of a test suite, CI pipeline, or production hardening; indicates prototype status”

### O2. Pin and audit dependencies across backend, frontend, and extension

- **Why:** Feasibility notes prototype status; dependency drift is a risk for a multi-component system
- **Acceptance:** npm audit reports no high or critical vulnerabilities; lockfiles committed; dependabot or equivalent configured
- **Evidence:** `assessment:feasibility`, `repo:tree` — “provides no evidence of a test suite, CI pipeline, or production hardening; indicates prototype status”

### O3. Document the OAuth setup for LinkedIn and email/password auth in a single SETUP.md

- **Why:** README references LinkedIn OAuth ready and email/password auth but SETUP.md exists separately; operators need one authoritative guide
- **Acceptance:** SETUP.md contains step-by-step LinkedIn developer app creation, callback URLs, JWT secret generation, and first-user registration
- **Evidence:** `candidate:DayanK/LinkedinSyncContacts`, `repo:tree` — “Authentication - Email/Password + LinkedIn OAuth ready”

## Stage 3 — UPGRADE

### U1. Implement contact extraction from LinkedIn via the browser extension using the documented auto-sync

- **Why:** Differentiation lists 'Contact extraction from LinkedIn' as a missing capability not covered by any maintained free OSS alternative; the candidate claims auto-sync in the extension
- **Acceptance:** Extension extracts at least name, headline, company, and profile URL for 1st-degree connections and stores them in the local backend
- **Evidence:** `assessment:differentiation`, `candidate:DayanK/LinkedinSyncContacts` — “Missing capabilities: 'Contact extraction from LinkedIn', 'Cloud sync and backup', 'Search and filter contacts', 'CSV export', 'User registration and login' (5 not covered by any maintained free OSS alternative)”

### U2. Harden cloud sync and backup with conflict resolution and incremental sync

- **Why:** Differentiation lists 'Cloud sync and backup' as a missing capability; must_beat LeadDelta and n8n workflow offer sync but are not free or self-hostable
- **Acceptance:** Repeated syncs from multiple devices merge without data loss; backend retains last-synced timestamp per contact
- **Evidence:** `assessment:differentiation`, `must_beat:web:https://leaddelta.com/product/auto-sync`, `must_beat:web:https://www.n8nflow.net/workflows/8041-weekly-linkedin-connections-sync-and-analysis-with-apify-and-google-sheets`, `candidate:DayanK/LinkedinSyncContacts` — “Cloud Sync - All contacts backed up to database”

### U3. Add search, filter, and CSV export to the web dashboard

- **Why:** Differentiation lists 'Search and filter contacts' and 'CSV export' as missing capabilities; candidate README claims both are built but feasibility says prototype
- **Acceptance:** Dashboard shows real-time filtered results as user types; CSV export downloads all visible columns for current filter
- **Evidence:** `assessment:differentiation`, `candidate:DayanK/LinkedinSyncContacts` — “Missing capabilities: 'Contact extraction from LinkedIn', 'Cloud sync and backup', 'Search and filter contacts', 'CSV export', 'User registration and login' (5 not covered by any maintained free OSS alternative)”

### U4. Complete user registration, login, and JWT session management for multi-device access

- **Why:** Differentiation lists 'User registration and login' as a missing capability; candidate claims both email/password and LinkedIn OAuth ready
- **Acceptance:** New user can register, verify email, log in on two devices, and see identical contact lists after sync
- **Evidence:** `assessment:differentiation`, `candidate:DayanK/LinkedinSyncContacts` — “Missing capabilities: 'Contact extraction from LinkedIn', 'Cloud sync and backup', 'Search and filter contacts', 'CSV export', 'User registration and login' (5 not covered by any maintained free OSS alternative)”

## Must beat

- **LeadDelta (web:https://leaddelta.com/product/auto-sync)**: Remain free and self-hostable while delivering auto-sync, enrichment, and export that LeadDelta gates behind a paid tier (`must_beat:web:https://leaddelta.com/product/auto-sync`)
- **n8n + Apify workflow (web:https://www.n8nflow.net/workflows/8041-weekly-linkedin-connections-sync-and-analysis-with-apify-and-google-sheets)**: Provide a self-hostable, no-code-required alternative that does not depend on Apify credits or Google Sheets (`must_beat:web:https://www.n8nflow.net/workflows/8041-weekly-linkedin-connections-sync-and-analysis-with-apify-and-google-sheets`)
- **polonia/contact-manager**: Ship a maintained release with a test suite and CI; the alternative has not been updated in 649 days and has only 2 stars (`must_beat:polonia/contact-manager`)

## Risks

- LinkedIn may block or rate-limit the extraction method used by the browser extension, making the core job unreliable
- No external demand signal was found (demand_evidence score 0), so adoption may not justify ongoing maintenance
- No structural moat identified (moat score 0); the functionality is replicable and similar tools already exist
- Productization path is undefined (score 1): no pricing, target persona definition, or distribution channel

## Evidence

| id | kind | source | content hash |
| --- | --- | --- | --- |
| `candidate:DayanK/LinkedinSyncContacts` | candidate | https://github.com/DayanK/LinkedinSyncContacts | — |
| `prior:web:https://www.linkedin-makeover.com/2012/01/09/backup-your-linkedin-account` | prior | https://www.linkedin-makeover.com/2012/01/09/backup-your-linkedin-account | — |
| `prior:web:https://github.com/calebnewtonusc/linkedin-clay-sync` | prior | https://github.com/calebnewtonusc/linkedin-clay-sync | — |
| `prior:web:https://connectsafely.ai/articles/export-linkedin-contacts-connections-guide-2026` | prior | https://connectsafely.ai/articles/export-linkedin-contacts-connections-guide-2026 | — |
| `prior:web:https://mailsoftly.com/blog/export-linkedin-contacts` | prior | https://mailsoftly.com/blog/export-linkedin-contacts | — |
| `prior:web:https://contactful.app/` | prior | https://contactful.app/ | — |
| `prior:web:https://leaddelta.com/product/auto-sync` | prior | https://leaddelta.com/product/auto-sync | — |
| `prior:web:https://www.n8nflow.net/workflows/8041-weekly-linkedin-connections-sync-and-analysis-with-apify-and-google-sheets` | prior | https://www.n8nflow.net/workflows/8041-weekly-linkedin-connections-sync-and-analysis-with-apify-and-google-sheets | — |
| `prior:mwpenn94/stewardly-command-center` | prior | https://github.com/mwpenn94/stewardly-command-center | — |
| `prior:bhealy/linkedin` | prior | https://github.com/bhealy/linkedin | — |
| `prior:polonia/contact-manager` | prior | https://github.com/polonia/contact-manager | — |
| `must_beat:web:https://leaddelta.com/product/auto-sync` | must_beat | — | — |
| `must_beat:web:https://www.n8nflow.net/workflows/8041-weekly-linkedin-connections-sync-and-analysis-with-apify-and-google-sheets` | must_beat | — | — |
| `must_beat:polonia/contact-manager` | must_beat | — | — |
| `assessment:problem_severity` | assessment | — | — |
| `assessment:demand_evidence` | assessment | — | — |
| `assessment:differentiation` | assessment | — | — |
| `assessment:feasibility` | assessment | — | — |
| `assessment:productization_path` | assessment | — | — |
| `assessment:moat` | assessment | — | — |
| `repo:tree` | repo | https://github.com/DayanK/LinkedinSyncContacts | — |

## Dropped by the check

- REVIVE: Add a test command and CI pipeline so the round-trip job is verifiable — quote not found verbatim in the cited evidence
