# Rescue plan: samwho/spacer

Gate 1: **CANDIDATE**. Drafted by: Nemotron (served `nvidia/nemotron-3-ultra-550b-a55b`), verified by code.
Trace: `logs/trace-rescue-samwho__spacer-20261027T205900Z.jsonl`, run hash `fd453bad96a61f825cb592b9639695b2ed83685fb1fbf1e282721e1bd05a4c2f`.

Every item below cites evidence that gate 1 recorded, and its quote was found verbatim in that evidence. Items that failed the check are listed at the end and are not part of the plan.

## Positioning

- **User:** Engineers and sysadmins tailing terminal logs
- **Job:** visually distinguish consecutive command outputs in log streams
- **Gap to own:** no maintained free OSS alternative inserts spacers when command output stops
- Evidence: `candidate:samwho/spacer`, `assessment:problem_severity`, `assessment:differentiation`

## Can gate 2 verify it?

**needs_test_command.** A manifest is present but no test path is visible in the root: gate 2 would stop at NEEDS_REVIEW until a test command exists.

## Stage 1 — REVIVE

### R1. Add a runnable test command for gate 2 verification

- **Why:** Gate 1 flagged needs_test_command because no test path is visible in the repository root, causing gate 2 to stop at NEEDS_REVIEW
- **Acceptance:** cargo test runs successfully and exits zero in a clean checkout
- **Evidence:** `repo:tree`, `candidate:samwho/spacer` — “repository root: .github, .gitignore, Cargo.lock, Cargo.toml, LICENSE, README.md, images, src”

## Stage 2 — OPTIMISE

### O1. Update dependencies and ensure CI passes on current Rust toolchain

- **Why:** The repo has working CI and a Cargo.lock but dependencies may be outdated; keeping them current reduces security and compatibility risk
- **Acceptance:** cargo update && cargo test passes on the latest stable Rust
- **Evidence:** `assessment:feasibility`, `repo:tree` — “The repo has working CI (Build badge visible), is available via Homebrew and Cargo package managers, and functions as a usable CLI tool with documented usage patterns and installation scripts.”

### O2. Add integration tests for the --after flag and stdin piping

- **Why:** The core job is a round-trip through stdin with a configurable idle timeout; automated tests prevent regressions in that behavior
- **Acceptance:** cargo test includes at least one integration test that pipes data, waits past --after, and asserts a spacer is emitted
- **Evidence:** `candidate:samwho/spacer` — “By default, `spacer` outputs a spacer after 1 second with no output. You can change this with the `--after` flag.”

## Stage 3 — UPGRADE

### U1. Support customizable separator strings

- **Why:** cleanlines lets users define their own separator text; adding this closes a customization gap against that alternative
- **Acceptance:** spacer --separator '=== NEW REQUEST ===' uses the provided string as the spacer line
- **Evidence:** `must_beat:alejandrovr/cleanlines` — “Simple bash script to clean output of the terminal and write a customazible separator.”

## Must beat

- **web:https://pkgs.alpinelinux.org/package/v3.23/community/aarch64/spacer**: The candidate is the upstream source with active CI, Homebrew and Cargo distribution, and 1818 stars; the Alpine package is a downstream build with no independent release cadence (`must_beat:web:https://pkgs.alpinelinux.org/package/v3.23/community/aarch64/spacer`, `assessment:feasibility`)
- **alejandrovr/cleanlines**: cleanlines is a bash script last updated in 2016 with zero stars and no license; the candidate is a maintained Rust CLI with CI, packaging, and a permissive license (`must_beat:alejandrovr/cleanlines`, `assessment:feasibility`)
- **juniac/tail-divider**: tail-divider has zero stars, no license, and no packaging; the candidate adds color, file-follow, and custom separators (via UPGRADE) while retaining mature distribution channels (`must_beat:juniac/tail-divider`, `assessment:feasibility`)
- **AharonSegal/termseparator**: termseparator is a Python library with no CLI, no license, and no recent activity; the candidate is a standalone CLI with installation via Homebrew and Cargo (`assessment:feasibility`)

## Risks

- No external demand signals beyond the repo itself (assessment:demand_evidence score 0)
- No structural moat; the tool is easily replicated (assessment:moat score 0)
- No clear monetization path; value remains a free CLI utility (assessment:productization_path score 1)

## Evidence

| id | kind | source | content hash |
| --- | --- | --- | --- |
| `candidate:samwho/spacer` | candidate | https://github.com/samwho/spacer | — |
| `prior:web:https://pkgs.alpinelinux.org/package/v3.23/community/aarch64/spacer` | prior | https://pkgs.alpinelinux.org/package/v3.23/community/aarch64/spacer | — |
| `prior:alejandrovr/cleanlines` | prior | https://github.com/alejandrovr/cleanlines | — |
| `prior:juniac/tail-divider` | prior | https://github.com/juniac/tail-divider | — |
| `prior:AharonSegal/termseparator` | prior | https://github.com/AharonSegal/termseparator | — |
| `must_beat:web:https://pkgs.alpinelinux.org/package/v3.23/community/aarch64/spacer` | must_beat | — | — |
| `must_beat:alejandrovr/cleanlines` | must_beat | — | — |
| `must_beat:juniac/tail-divider` | must_beat | — | — |
| `assessment:problem_severity` | assessment | — | — |
| `assessment:demand_evidence` | assessment | — | — |
| `assessment:differentiation` | assessment | — | — |
| `assessment:feasibility` | assessment | — | — |
| `assessment:productization_path` | assessment | — | — |
| `assessment:moat` | assessment | — | — |
| `repo:tree` | repo | https://github.com/samwho/spacer | — |

## Dropped by the check

- UPGRADE: Add color support for spacer lines — quote not found verbatim in the cited evidence
- UPGRADE: Add file-following mode to read logs directly — quote not found verbatim in the cited evidence
