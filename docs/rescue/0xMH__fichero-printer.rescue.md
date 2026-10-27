# Rescue plan: 0xMH/fichero-printer

Gate 1: **CANDIDATE**. Drafted by: Nemotron (served `nvidia/nemotron-3-ultra-550b-a55b`), verified by code.
Trace: `logs/trace-rescue-0xmh__fichero-printer-20261027T001117Z.jsonl`, run hash `51e828e538ed8f05dda15648fc673c3461ca310a87c0af77c53482e1f0a086c4`.

Every item below cites evidence that gate 1 recorded, and its quote was found verbatim in that evidence. Items that failed the check are listed at the end and are not part of the plan.

## Positioning

- **User:** printer owners who value privacy
- **Job:** print labels without granting excessive permissions
- **Gap to own:** no maintained free OSS alternative covers Web GUI, Python CLI, BLE connectivity, Classic Bluetooth SPP connectivity, and protocol documentation for the Fichero D11s
- Evidence: `candidate:0xMH/fichero-printer`, `assessment:problem_severity`, `assessment:differentiation`

## Can gate 2 verify it?

**verifiable.** A test path is visible in the root, so gate 2's execution loop can verify the REVIVE stage.

## Stage 1 — REVIVE

### R1. Verify Python CLI prints a label on a physical Fichero D11s

- **Why:** The candidate claims a working Python CLI but gate 2 must verify the round-trip works on hardware
- **Acceptance:** Running the CLI test command against a real printer produces a printed label
- **Evidence:** `candidate:0xMH/fichero-printer`, `assessment:feasibility`, `repo:tree` — “The repository provides a working Python CLI, a web GUI, and protocol documentation, indicating a functional baseline.”

### R2. Verify Web GUI loads and pairs via Web Bluetooth

- **Why:** The candidate advertises a Web GUI; it must be loadable and able to connect over BLE
- **Acceptance:** Opening the web app in a browser shows the interface and a BLE pairing prompt appears
- **Evidence:** `candidate:0xMH/fichero-printer`, `assessment:differentiation`, `repo:tree` — “Candidate quotes: for 'Web-based graphical interface': "Web GUI"”

### R3. Run existing test suite to confirm no regressions

- **Why:** The repository includes a tests directory; tests must pass before any changes
- **Acceptance:** All tests in the tests/ directory pass with the current dependencies
- **Evidence:** `repo:tree`, `assessment:feasibility` — “repository root: .github, .gitignore, README.md, docs, fichero, pyproject.toml, tests, uv.lock, web”

## Stage 2 — OPTIMISE

### O1. Pin all dependencies in pyproject.toml and verify uv.lock reproducibility

- **Why:** Clean dependencies are noted; pinning prevents future breakage
- **Acceptance:** uv sync --frozen succeeds and produces identical uv.lock on a clean machine
- **Evidence:** `repo:tree`, `assessment:feasibility` — “the code appears maintainable with clean dependencies.”

### O2. Add GitHub Actions workflow to run tests on every push

- **Why:** The .github directory exists but no CI is confirmed; automation prevents regressions
- **Acceptance:** A workflow in .github/workflows/ runs the test suite and reports success on the default branch
- **Evidence:** `repo:tree` — “repository root: .github, .gitignore, README.md, docs, fichero, pyproject.toml, tests, uv.lock, web”

## Stage 3 — UPGRADE

### U1. Add label template engine to Web GUI for non-technical users

- **Why:** Web GUI is a listed differentiator; templates extend its reach beyond developers
- **Acceptance:** Web GUI offers a template selector (address, QR, barcode) and prints a filled template without code
- **Evidence:** `candidate:0xMH/fichero-printer`, `assessment:differentiation`, `must_beat:web:https://hackaday.com/2026/03/09/reverse-engineering-the-bluetooth-fichero-thermal-label-printer-protocol` — “Candidate quotes: for 'Web-based graphical interface': "Web GUI"”

### U2. Release a versioned protocol specification document with changelog

- **Why:** Protocol documentation is a listed differentiator; versioning makes it a stable reference for other implementers
- **Acceptance:** docs/protocol-v1.0.md exists with a changelog section and is linked from README.md
- **Evidence:** `candidate:0xMH/fichero-printer`, `assessment:differentiation` — “Missing capabilities: 'Web-based graphical interface', 'Python command-line interface', 'Bluetooth Low Energy connectivity', 'Classic Bluetooth SPP connectivity', 'Protocol documentation for reverse-engineered printer' (5 not covered by any maintained free OSS alternative).”

## Must beat

- **ChiaraCannolee/thermal-pocket-printer-basic**: Support the Fichero D11s specifically (different printer) and provide Web GUI plus protocol documentation (`must_beat:web:https://github.com/ChiaraCannolee/thermal-pocket-printer-basic`, `assessment:differentiation`)
- **Hackaday article on Fichero reverse engineering**: Remain free, self-hostable, and under a permissive licence while the article is not an alternative with code (`must_beat:web:https://hackaday.com/2026/03/09/reverse-engineering-the-bluetooth-fichero-thermal-label-printer-protocol`)

## Risks

- Demand evidence is weak (score 1): only a single Hackaday mention and one comment
- No moat (score 0): the reverse-engineered protocol and CLI can be replicated over a weekend
- No monetization or distribution channel beyond open-source adoption (productization_path score 1)
- Single printer model dependency: if Action discontinues the Fichero D11s, the user base evaporates

## Evidence

| id | kind | source | content hash |
| --- | --- | --- | --- |
| `candidate:0xMH/fichero-printer` | candidate | https://github.com/0xMH/fichero-printer | — |
| `prior:web:https://github.com/ChiaraCannolee/thermal-pocket-printer-basic` | prior | https://github.com/ChiaraCannolee/thermal-pocket-printer-basic | — |
| `prior:web:https://hackaday.com/2026/03/09/reverse-engineering-the-bluetooth-fichero-thermal-label-printer-protocol` | prior | https://hackaday.com/2026/03/09/reverse-engineering-the-bluetooth-fichero-thermal-label-printer-protocol | — |
| `must_beat:web:https://hackaday.com/2026/03/09/reverse-engineering-the-bluetooth-fichero-thermal-label-printer-protocol` | must_beat | — | — |
| `must_beat:web:https://github.com/ChiaraCannolee/thermal-pocket-printer-basic` | must_beat | — | — |
| `assessment:problem_severity` | assessment | — | — |
| `assessment:demand_evidence` | assessment | — | — |
| `assessment:differentiation` | assessment | — | — |
| `assessment:feasibility` | assessment | — | — |
| `assessment:productization_path` | assessment | — | — |
| `assessment:moat` | assessment | — | — |
| `repo:tree` | repo | https://github.com/0xMH/fichero-printer | — |

## Dropped by the check

- OPTIMISE: Publish reverse-engineered protocol as standalone Markdown in docs/ — quote not found verbatim in the cited evidence
- UPGRADE: Implement automatic BLE/SPP fallback in the Python CLI — quote not found verbatim in the cited evidence
