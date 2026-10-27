# Rescue plan: sealdice/qq-group-files-sync

Gate 1: **CANDIDATE**. Drafted by: Nemotron (served `nvidia/nemotron-3-ultra-550b-a55b`), verified by code.
Trace: `logs/trace-rescue-sealdice__qq-group-files-sync-20261027T211012Z.jsonl`, run hash `cb82f3da7ad0cdfc151692134e8672853761d71371afa37736030b26c51c1b3c`.

Every item below cites evidence that gate 1 recorded, and its quote was found verbatim in that evidence. Items that failed the check are listed at the end and are not part of the plan.

## Positioning

- **User:** Teachers, training institution managers, and community operators who need to sync files across multiple QQ groups
- **Job:** sync QQ group files with incremental sync, command-line mode, interactive QQ-group sync, and auto-generated display page
- **Gap to own:** alternatives lack free/permissive licence, are unmaintained, and do not provide the five capabilities (group file download, incremental sync, interactive QQ sync, command-line sync, auto-generated display page) that the candidate offers
- Evidence: `candidate:sealdice/qq-group-files-sync`, `assessment:differentiation`, `must_beat:web:https://qq.kailum.cn/sync-between-groups/`, `must_beat:web:https://qq.kailum.cn/features/`, `must_beat:lyjjl/qq-group-files-sync-python`, `must_beat:kaishu666/qq-group-file-sync`

## Can gate 2 verify it?

**needs_test_command.** A manifest is present but no test path is visible in the root: gate 2 would stop at NEEDS_REVIEW until a test command exists.

## Stage 1 — REVIVE

### R1. Add test command and CI configuration

- **Why:** Gate 2 stops at NEEDS_REVIEW until a test command is visible in the root
- **Acceptance:** running the test command from the repository root passes
- **Evidence:** `assessment:feasibility`, `repo:tree` — “The repository description contains no mention of a test suite or CI pipeline”

## Stage 2 — OPTIMISE

### O1. Implement multi-threaded download for small files

- **Why:** The TODO list notes missing multi-threaded download for small files, indicating a prototype with missing performance optimizations
- **Acceptance:** small file downloads complete faster with concurrent workers and no data corruption
- **Evidence:** `assessment:feasibility` — “the TODO list notes missing multi-threaded download for small files”

## Stage 3 — UPGRADE

### U1. Ensure all five capabilities are functional and documented

- **Why:** to close the differentiation gap against maintained free OSS alternatives
- **Acceptance:** user can run `go run . -a` for command-line sync, use `.同步当前` in interactive mode, download group files, perform incremental sync, and generate the display page
- **Evidence:** `candidate:sealdice/qq-group-files-sync`, `assessment:differentiation` — “Missing capabilities: 'Group file download', 'Incremental file synchronization', 'Interactive QQ group sync', 'Auto-generated display page', 'Command line sync mode' (5 not covered by any maintained free OSS alternative with external user demand pull).”

## Must beat

- **web:https://qq.kailum.cn/sync-between-groups/ (no licence, unmaintained)**: candidate is free, self-hostable, provides group file download, incremental sync, interactive QQ sync, command-line and UI modes, and auto-generated display page (`must_beat:web:https://qq.kailum.cn/sync-between-groups/`, `candidate:sealdice/qq-group-files-sync`)
- **web:https://qq.kailum.cn/features/ (no licence, unmaintained)**: candidate supplies the full feature set including incremental sync and interactive QQ control, unlike the alternative (`must_beat:web:https://qq.kailum.cn/features/`, `candidate:sealdice/qq-group-files-sync`)
- **lyjjl/qq-group-files-sync-python (unmaintained)**: candidate is Go-based, has UI, command-line and interactive QQ sync, and is free and self-hostable (`must_beat:lyjjl/qq-group-files-sync-python`, `candidate:sealdice/qq-group-files-sync`)
- **kaishu666/qq-group-file-sync (no licence, unmaintained)**: candidate offers free self-hostable sync with UI, incremental sync, command-line and interactive QQ control (`must_beat:kaishu666/qq-group-file-sync`, `candidate:sealdice/qq-group-files-sync`)

## Risks

- no structural moat (moat score 0); functionality easily replicated
- feasibility score 1 indicates prototype-level code with missing tests and CI
- productization_path score 1: no clear monetization or distribution channel beyond open-source sharing
- commercial alternative (QQ群文件同步助手) may capture market mindshare despite candidate being free

## Evidence

| id | kind | source | content hash |
| --- | --- | --- | --- |
| `candidate:sealdice/qq-group-files-sync` | candidate | https://github.com/sealdice/qq-group-files-sync | — |
| `prior:web:https://qq.kailum.cn/sync-between-groups/` | prior | https://qq.kailum.cn/sync-between-groups/ | — |
| `prior:web:https://qq.kailum.cn/features/` | prior | https://qq.kailum.cn/features/ | — |
| `prior:lyjjl/qq-group-files-sync-python` | prior | https://github.com/lyjjl/qq-group-files-sync-python | — |
| `prior:kaishu666/qq-group-file-sync` | prior | https://github.com/kaishu666/qq-group-file-sync | — |
| `must_beat:web:https://qq.kailum.cn/sync-between-groups/` | must_beat | — | — |
| `must_beat:web:https://qq.kailum.cn/features/` | must_beat | — | — |
| `must_beat:lyjjl/qq-group-files-sync-python` | must_beat | — | — |
| `must_beat:kaishu666/qq-group-file-sync` | must_beat | — | — |
| `assessment:problem_severity` | assessment | — | — |
| `assessment:demand_evidence` | assessment | — | — |
| `assessment:differentiation` | assessment | — | — |
| `assessment:feasibility` | assessment | — | — |
| `assessment:productization_path` | assessment | — | — |
| `assessment:moat` | assessment | — | — |
| `repo:tree` | repo | https://github.com/sealdice/qq-group-files-sync | — |
