"""Role prompts are versioned files and the trace hashes that version."""

from __future__ import annotations

import json
import re

from app.prompts import ROLES, load_prompt, prompt_versions
from app.trace_log import HackathonTrace
from scripts.verify_trace import verify


def test_every_role_prompt_has_one_job_a_schema_and_real_few_shots():
    versions = prompt_versions()
    assert set(versions) == set(ROLES)
    for role in ROLES:
        prompt = load_prompt(role)
        assert prompt.sha256 == versions[role]
        text = prompt.text
        assert "Job:" in text
        assert "JSON schema" in text
        assert "Positive example" in text
        assert "Negative example" in text
        assert "unknown" in text
        positive = text.split("Positive example", 1)[1]
        assert re.search(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", positive)
    reviewer = load_prompt("reviewer").text
    assert "unsupported" in reviewer.lower()
    assert "alternative" in reviewer.lower()
    equivalence = load_prompt("equivalence").text
    assert "problem clarity" in equivalence
    assert "target user" in equivalence
    assert "feasibility" in equivalence
    assert "al1abb/invoify" in equivalence
    assert "SergioMendolia/ytkb" in equivalence


def test_prompt_version_is_inside_the_hash_chain(tmp_path):
    prompt = load_prompt("reviewer")
    trace = HackathonTrace("prompts", tmp_path)
    trace.event(
        agent_name="reviewer",
        action="LLM_JUDGE",
        llm={
            "requested_model": "meterless/auto",
            "served_model": "meterless/auto",
            "prompt_version": prompt.sha256,
        },
    )
    run_hash = trace.seal()
    ok, detail = verify(trace.path, expected_run_hash=run_hash)
    assert ok is True
    assert detail == run_hash
    lines = trace.path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    assert record["prompt_version"] == prompt.sha256
    record["prompt_version"] = "0" * 64
    lines[0] = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    trace.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok, detail = verify(trace.path, expected_run_hash=run_hash)
    assert ok is False
