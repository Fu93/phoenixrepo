# Rescue plan: ri-char/pwatch

Gate 1: **CANDIDATE**. Drafted by: deterministic, from the same evidence.
Trace: `logs/trace-rescue-ri-char__pwatch-20261027T204845Z.jsonl`, run hash `f1735a7a927dc08e376be7fc637a185cddaf0c0a909467f30ecafb88d277ce29`.

Every item below cites evidence that gate 1 recorded, and its quote was found verbatim in that evidence. Items that failed the check are listed at the end and are not part of the plan.

## Positioning

- **User:** unknown
- **Job:** A cli tool to install a hardware breakpoint/watchpoint on a process in linux.
- **Gap to own:** the reasons the must-beat alternatives did not block
- Evidence: `candidate:ri-char/pwatch`

## Can gate 2 verify it?

**needs_test_command.** A manifest is present but no test path is visible in the root: gate 2 would stop at NEEDS_REVIEW until a test command exists.

## Stage 1 — REVIVE

### R1. Add a runnable test command for the stated job *(added by code)*

- **Why:** Without one, gate 2's execution loop cannot verify any revival and stops at NEEDS_REVIEW.
- **Acceptance:** One command runs the tests and exits 0 on a clean checkout.
- **Evidence:** `repo:tree` — “Cargo.toml”

### R2. Make the stated job work again on current dependencies

- **Why:** This is the job gate 1 found worth rescuing.
- **Acceptance:** The README's main workflow runs end to end on a clean machine.
- **Evidence:** `candidate:ri-char/pwatch` — “A cli tool to install a hardware breakpoint/watchpoint on a process in linux.”

## Stage 2 — OPTIMISE

### O1. Refactor around the existing working core

- **Why:** The scorer's feasibility assessment of the codebase.
- **Acceptance:** The core paths named in the assessment keep working after the refactor.
- **Evidence:** `assessment:feasibility` — “The repository contains a functional CLI with documented usage, working builds for x86_64 and arm64, support for rooted Android, and clear argument parsing.”

## Stage 3 — UPGRADE

### U1. Keep or widen the gap against web:https://github.com/RinHizakura/kmemsnoop

- **Why:** Recorded by gate 1 as an alternative that did not block.
- **Acceptance:** A side-by-side check shows the revival still does what web:https://github.com/RinHizakura/kmemsnoop does not.
- **Evidence:** `must_beat:web:https://github.com/RinHizakura/kmemsnoop` — “did not block: not mature or official: stars=0, candidate_stars=265, official=False; candidate is free and self-hostable; the alternative has no free tier or pe”

### U2. Keep or widen the gap against microdoc/process_vm_io

- **Why:** Recorded by gate 1 as an alternative that did not block.
- **Acceptance:** A side-by-side check shows the revival still does what microdoc/process_vm_io does not.
- **Evidence:** `must_beat:microdoc/process_vm_io` — “did not block: not mature or official: stars=5, candidate_stars=265, official=False I/O access to virtual memory contents of processes”

### U3. Keep or widen the gap against danylo-volchenko/watchpoint-km

- **Why:** Recorded by gate 1 as an alternative that did not block.
- **Acceptance:** A side-by-side check shows the revival still does what danylo-volchenko/watchpoint-km does not.
- **Evidence:** `must_beat:danylo-volchenko/watchpoint-km` — “did not block: last update 2025-07-06 is 477 days before 2026-10-26; not mature or official: stars=3, candidate_stars=265, official=False; candidate is free and”

### U4. Keep or widen the gap against web:https://edwardtay.github.io/awesome-reverse-engineering/c/memory/

- **Why:** Recorded by gate 1 as an alternative that did not block.
- **Acceptance:** A side-by-side check shows the revival still does what web:https://edwardtay.github.io/awesome-reverse-engineering/c/memory/ does not.
- **Evidence:** `must_beat:web:https://edwardtay.github.io/awesome-reverse-engineering/c/memory/` — “did not block: overlap is partial, not the same job on the same platform; no push or release date; not mature or official: stars=0, candidate_stars=265, officia”

## Must beat

- **web:https://github.com/RinHizakura/kmemsnoop**: keep the capability its recorded reason says it lacks (`must_beat:web:https://github.com/RinHizakura/kmemsnoop`)
- **microdoc/process_vm_io**: keep the capability its recorded reason says it lacks (`must_beat:microdoc/process_vm_io`)
- **danylo-volchenko/watchpoint-km**: keep the capability its recorded reason says it lacks (`must_beat:danylo-volchenko/watchpoint-km`)
- **web:https://edwardtay.github.io/awesome-reverse-engineering/c/memory/**: keep the capability its recorded reason says it lacks (`must_beat:web:https://edwardtay.github.io/awesome-reverse-engineering/c/memory/`)
- **web:https://stackoverflow.com/questions/1592457/how-to-debug-a-program-without-a-debugger**: keep the capability its recorded reason says it lacks (`must_beat:web:https://stackoverflow.com/questions/1592457/how-to-debug-a-program-without-a-debugger`)
- **web:https://github.com/torvalds/linux/blob/master/arch/x86/kernel/hw_breakpoint.c**: keep the capability its recorded reason says it lacks (`must_beat:web:https://github.com/torvalds/linux/blob/master/arch/x86/kernel/hw_breakpoint.c`)
- **web:https://sourceware.org/gdb/current/onlinedocs/gdb.html/Set-Watchpoints.html**: keep the capability its recorded reason says it lacks (`must_beat:web:https://sourceware.org/gdb/current/onlinedocs/gdb.html/Set-Watchpoints.html`)

## Risks

- None stated by the evidence.

## Evidence

| id | kind | source | content hash |
| --- | --- | --- | --- |
| `candidate:ri-char/pwatch` | candidate | https://github.com/ri-char/pwatch | — |
| `prior:web:https://github.com/RinHizakura/kmemsnoop` | prior | https://github.com/RinHizakura/kmemsnoop | — |
| `prior:web:https://edwardtay.github.io/awesome-reverse-engineering/c/memory/` | prior | https://edwardtay.github.io/awesome-reverse-engineering/c/memory/ | — |
| `prior:web:https://stackoverflow.com/questions/1592457/how-to-debug-a-program-without-a-debugger` | prior | https://stackoverflow.com/questions/1592457/how-to-debug-a-program-without-a-debugger | — |
| `prior:web:https://github.com/torvalds/linux/blob/master/arch/x86/kernel/hw_breakpoint.c` | prior | https://github.com/torvalds/linux/blob/master/arch/x86/kernel/hw_breakpoint.c | — |
| `prior:web:https://sourceware.org/gdb/current/onlinedocs/gdb.html/Set-Watchpoints.html` | prior | https://sourceware.org/gdb/current/onlinedocs/gdb.html/Set-Watchpoints.html | — |
| `prior:microdoc/process_vm_io` | prior | https://github.com/microdoc/process_vm_io | — |
| `prior:block/stoic` | prior | https://github.com/block/stoic | — |
| `prior:danylo-volchenko/watchpoint-km` | prior | https://github.com/danylo-volchenko/watchpoint-km | — |
| `must_beat:web:https://github.com/RinHizakura/kmemsnoop` | must_beat | — | — |
| `must_beat:microdoc/process_vm_io` | must_beat | — | — |
| `must_beat:danylo-volchenko/watchpoint-km` | must_beat | — | — |
| `must_beat:web:https://edwardtay.github.io/awesome-reverse-engineering/c/memory/` | must_beat | — | — |
| `must_beat:web:https://stackoverflow.com/questions/1592457/how-to-debug-a-program-without-a-debugger` | must_beat | — | — |
| `must_beat:web:https://github.com/torvalds/linux/blob/master/arch/x86/kernel/hw_breakpoint.c` | must_beat | — | — |
| `must_beat:web:https://sourceware.org/gdb/current/onlinedocs/gdb.html/Set-Watchpoints.html` | must_beat | — | — |
| `assessment:problem_severity` | assessment | — | — |
| `assessment:demand_evidence` | assessment | — | — |
| `assessment:differentiation` | assessment | — | — |
| `assessment:feasibility` | assessment | — | — |
| `assessment:productization_path` | assessment | — | — |
| `assessment:moat` | assessment | — | — |
| `repo:tree` | repo | https://github.com/ri-char/pwatch | — |
