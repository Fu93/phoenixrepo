# Gate 2 verification contract

The model proposes a repair. Deterministic checks decide whether the repair can
be called `RESURRECTED`. A plan, a successful model response, or a single passing
acceptance command is insufficient.

The independent verifier requires:

- The final repository test command actually runs and exits zero. Reported test
  failures or errors also block success, even if the command exits zero.
- Every kept item's acceptance check still passes on the final snapshot. An
  interpreter, materialization, or runner error is `UNVERIFIED`, including when
  an item happens to expect exit code 1.
- A configured build finishes successfully. With no build command, the build
  remains `UNVERIFIED`; the verifier does not invent one or claim it passed.
- Tests are not removed, skipped to hide failures, or weakened. Changes to npm's
  test/pretest scripts, pytest configuration, or tox.ini require human review.
  A dependency-only pyproject.toml edit can pass this check.
- The patch stays within 8 files and 300 added/deleted lines. Replacing 400 lines
  with 400 different lines counts as 800, rather than one.
- The baseline test really failed, or a named dependency declaration increased
  to the claimed version. An infrastructure failure is not a broken baseline:
  runner exceptions, timeout exit 124, and executable-unavailable exits 127/9009
  are `UNVERIFIED`.

Dependency checks read package.json dependency sections, pinned requirements
files, and pyproject.toml project/Poetry dependencies. They match the package and
version together. Text notes and unrelated packages do not qualify. Only numeric
release increases are independently confirmed; unsupported formats or release
schemes need review. This proves a declaration changed, **not** that the package
was installed, is the latest release, or is secure.

Snapshot paths are validated before materialization. Absolute paths, parent
traversal, version-control paths, and aliases cannot be used to write outside the
temporary verification tree or overwrite another snapshot entry.

The quality gate marks malformed/error audit output, incomplete pip-audit scans,
and execution errors `unavailable`. An audit command that exits 1 with only
moderate findings still follows the existing high/critical policy; it is not a
claim that no vulnerabilities exist.

## Offline verification

```text
python -m pytest -q tests/test_gate2_verifier_hardening.py tests/test_revive_prd_verify.py tests/test_revive_quality.py tests/test_revive_backhalf.py tests/test_revive_prd_prompt.py tests/test_audit_fixes.py tests/test_intent_synthesis.py
python -m ruff check .
python scripts/compliance_check.py
python scripts/verify_all_replays.py
```

The new regression cases use fake runners and no model calls. Existing fixture
integration tests use a synthetic local project, scripted model responses, real
pytest, and mocked dependency installation/audit. These tests are evidence for
the verifier's behavior, not a successful Nemotron repair of a real candidate.

## Execution limit

The production host runner still executes repository commands with `shell=True`.
Temporary copies and a scrubbed environment do **not** provide a security sandbox.
Do not execute an untrusted candidate on the host. A separately reviewed container
or VM runner remains necessary before offering arbitrary-repository execution.
The test-weakening checks are heuristics, not a proof that arbitrary test code
retains its meaning. Gate 2 still has no demonstrated real-candidate repair run.
