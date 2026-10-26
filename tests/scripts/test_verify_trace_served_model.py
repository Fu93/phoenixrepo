"""scripts/verify_trace.py rejects a non-Nemotron served model and accepts a clean trace."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts.verify_trace import verify

ROOT = Path(__file__).resolve().parents[2]
CLEAN = ROOT / "tests" / "fixtures" / "traces" / "nemotron_clean.jsonl"
FOREIGN = ROOT / "tests" / "fixtures" / "traces" / "non_nemotron_served.jsonl"


def _cli(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "scripts/verify_trace.py", str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_verify_trace_cli_fails_on_a_non_nemotron_served_model_and_passes_on_a_clean_one():
    assert CLEAN.is_file() and FOREIGN.is_file()
    # The hash chain of the foreign fixture is intact. The command adds the family check.
    ok, detail = verify(FOREIGN)
    assert ok is True, detail
    foreign = _cli(FOREIGN)
    assert foreign.returncode == 1
    assert "gemini-3.1-flash-lite" in foreign.stdout
    assert "nvidia/nemotron-*" in foreign.stdout
    clean = _cli(CLEAN)
    assert clean.returncode == 0, clean.stdout + clean.stderr
    for bundled in (ROOT / "data" / "replays").glob("*/trace.jsonl"):
        result = _cli(bundled)
        assert result.returncode == 0, f"{bundled}: {result.stdout}{result.stderr}"
