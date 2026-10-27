# Rescue plan: oleksiiluchnikov/obsidian-localtable

Gate 1: **CANDIDATE**. Drafted by: deterministic, from the same evidence.
Trace: `logs/trace-rescue-oleksiiluchnikov__obsidian-localtable-20261027T210137Z.jsonl`, run hash `903688d4fce47e9207a4c540352e0e988819e306640c8e7a8acb0c285c793834`.

Every item below cites evidence that gate 1 recorded, and its quote was found verbatim in that evidence. Items that failed the check are listed at the end and are not part of the plan.

## Positioning

- **User:** unknown
- **Job:** Browse linked records from notes and sync note links with Airtable integration.
- **Gap to own:** the reasons the must-beat alternatives did not block
- Evidence: `candidate:oleksiiluchnikov/obsidian-localtable`

## Can gate 2 verify it?

**needs_test_command.** A manifest is present but no test path is visible in the root: gate 2 would stop at NEEDS_REVIEW until a test command exists.

## Stage 1 — REVIVE

### R1. Add a runnable test command for the stated job *(added by code)*

- **Why:** Without one, gate 2's execution loop cannot verify any revival and stops at NEEDS_REVIEW.
- **Acceptance:** One command runs the tests and exits 0 on a clean checkout.
- **Evidence:** `repo:tree` — “package.json”

### R2. Make the stated job work again on current dependencies

- **Why:** This is the job gate 1 found worth rescuing.
- **Acceptance:** The README's main workflow runs end to end on a clean machine.
- **Evidence:** `candidate:oleksiiluchnikov/obsidian-localtable` — “Browse linked records from notes and sync note links with Airtable integration.”

## Stage 2 — OPTIMISE

### O1. Refactor around the existing working core

- **Why:** The scorer's feasibility assessment of the codebase.
- **Acceptance:** The core paths named in the assessment keep working after the refactor.
- **Evidence:** `assessment:feasibility` — “The repository provides a working plugin distributable via BRAT or manual install (main.js, manifest.json, styles.css) and documents setup clearly.”

## Stage 3 — UPGRADE

### U1. Keep or widen the gap against web:https://community.obsidian.md/plugins/auto-note-importer

- **Why:** Recorded by gate 1 as an alternative that did not block.
- **Acceptance:** A side-by-side check shows the revival still does what web:https://community.obsidian.md/plugins/auto-note-importer does not.
- **Evidence:** `must_beat:web:https://community.obsidian.md/plugins/auto-note-importer` — “did not block: no push or release date; not mature or official: stars=0, candidate_stars=0, official=False; candidate is free and self-hostable; the alternative”

### U2. Keep or widen the gap against uppinote20/obsidian-auto-note-importer

- **Why:** Recorded by gate 1 as an alternative that did not block.
- **Acceptance:** A side-by-side check shows the revival still does what uppinote20/obsidian-auto-note-importer does not.
- **Evidence:** `must_beat:uppinote20/obsidian-auto-note-importer` — “did not block: not mature or official: stars=18, candidate_stars=0, official=False Two-way sync between Obsidian and your databases — Airtable, SeaTable, Supaba”

## Must beat

- **web:https://community.obsidian.md/plugins/auto-note-importer**: keep the capability its recorded reason says it lacks (`must_beat:web:https://community.obsidian.md/plugins/auto-note-importer`)
- **uppinote20/obsidian-auto-note-importer**: keep the capability its recorded reason says it lacks (`must_beat:uppinote20/obsidian-auto-note-importer`)

## Risks

- None stated by the evidence.

## Evidence

| id | kind | source | content hash |
| --- | --- | --- | --- |
| `candidate:oleksiiluchnikov/obsidian-localtable` | candidate | https://github.com/oleksiiluchnikov/obsidian-localtable | — |
| `prior:web:https://obsidian.md/help/import/airtable` | prior | https://obsidian.md/help/import/airtable | — |
| `prior:web:https://community.obsidian.md/plugins/auto-note-importer` | prior | https://community.obsidian.md/plugins/auto-note-importer | — |
| `prior:uppinote20/obsidian-auto-note-importer` | prior | https://github.com/uppinote20/obsidian-auto-note-importer | — |
| `must_beat:web:https://community.obsidian.md/plugins/auto-note-importer` | must_beat | — | — |
| `must_beat:uppinote20/obsidian-auto-note-importer` | must_beat | — | — |
| `assessment:problem_severity` | assessment | — | — |
| `assessment:demand_evidence` | assessment | — | — |
| `assessment:differentiation` | assessment | — | — |
| `assessment:feasibility` | assessment | — | — |
| `assessment:productization_path` | assessment | — | — |
| `assessment:moat` | assessment | — | — |
| `repo:tree` | repo | https://github.com/oleksiiluchnikov/obsidian-localtable | — |
