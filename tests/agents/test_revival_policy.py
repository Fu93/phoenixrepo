import csv
from pathlib import Path

from app.agents.policy import apply_revival_attempts, decide, normalize_attempt, normalize_profile, normalize_signal
from app.sql_catalog import ATTEMPT_COLUMNS, PROFILE_COLUMNS, SIGNAL_COLUMNS


def _rows(name: str, columns: tuple[str, ...]) -> list[dict[str, str]]:
    with (Path("data/real") / name).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert tuple(reader.fieldnames or ()) == columns
        return [dict(row) for row in reader]


def _profile(repo_id: str) -> dict:
    return next(normalize_profile(row) for row in _rows("repo_evidence.csv", PROFILE_COLUMNS) if row["repo_id"] == repo_id)


def _signals(repo_id: str) -> list[dict]:
    return [
        normalize_signal(row)
        for row in _rows("revival_signals.csv", SIGNAL_COLUMNS)
        if row["repo_id"] == repo_id
    ]


def _attempts(repo_id: str) -> list[dict]:
    return [
        normalize_attempt(row)
        for row in _rows("revival_attempts.csv", ATTEMPT_COLUMNS)
        if row["repo_id"] == repo_id
    ]


def _success(repo_id: str = "jondot/hygen") -> dict:
    return {
        "repo_id": repo_id,
        "fork_full_name": "Fu93/hygen",
        "upstream_full_name": repo_id,
        "stage": "revive",
        "status": "success",
        "ci_status": "success",
        "ci_run_url": "https://github.com/Fu93/hygen/actions/runs/1",
        "ci_run_name": "CI",
        "ci_run_event": "push",
        "commit_sha": "abc123",
        "compare_url": "https://github.com/jondot/hygen/compare/master...Fu93:master",
        "default_branch": "master",
        "head_sha": "abc123",
        "upstream_base_sha": "26f76d8dede565b2bf38d0b9e9f3009ee2c8f9ae",
        "ci_head_repo": "Fu93/hygen",
        "workflow_path": ".github/workflows/ci.yml",
        "workflow_steps": "yarn install; yarn build; yarn test",
        "workflow_runs_build": "true",
        "compare_shas": "abc123",
        "data_origin": "github_rest_api:2026-10-22",
    }


def test_decide_without_attempts_is_candidate_when_the_row_passes():
    row = _profile("42wim/matterbridge")
    decision, reason = decide(row, _signals("42wim/matterbridge"))
    assert decision == "CANDIDATE"
    assert "CI is unknown" in reason
    assert "CANDIDATE" in reason
    assert "not yet been attempted" not in reason


def test_matterbridge_with_no_attempt_is_candidate():
    row = _profile("42wim/matterbridge")
    decision, reason, upgrade = apply_revival_attempts(row, _signals("42wim/matterbridge"), [])
    assert decision == "CANDIDATE"
    assert "CI is unknown" in reason
    assert "successful CI conclusion" not in reason
    assert upgrade["changes_verdict"] is False
    assert upgrade["stages"] == []


def test_hygen_snapshot_attempt_is_not_yet_attempted():
    row = _profile("jondot/hygen")
    attempts = _attempts("jondot/hygen")
    assert [item["stage"] for item in attempts] == ["revive", "refactor", "upgrade"]
    assert {item["status"] for item in attempts} == {"not_attempted"}
    assert all(item["commit_sha"] == "" and item["ci_run_url"] == "" for item in attempts)
    decision, reason, upgrade = apply_revival_attempts(row, _signals("jondot/hygen"), attempts)
    assert decision == "RESURRECTION_FAILED"
    assert "Use Node.js 14.x" in reason
    assert "not a test failure" in reason
    assert "Revival has not yet been attempted." in reason
    assert [item["stage"] for item in upgrade["stages"]] == ["refactor", "upgrade"]
    assert all(item["status"] == "not_attempted" for item in upgrade["stages"])


def test_stage_revive_success_overrides_an_upstream_ci_failure():
    row = _profile("jondot/hygen")
    decision, reason, upgrade = apply_revival_attempts(row, _signals("jondot/hygen"), [_success()])
    assert decision == "RESURRECTED"
    assert "https://github.com/Fu93/hygen/actions/runs/1" in reason
    assert "abc123" in reason
    assert upgrade["stages"] == []


def test_stage_revive_failure_cites_the_run():
    row = _profile("42wim/matterbridge")
    attempt = _success("42wim/matterbridge")
    attempt["status"] = "failure"
    attempt["ci_status"] = "failure"
    attempt["ci_run_url"] = "https://github.com/example/matterbridge/actions/runs/9"
    decision, reason, _upgrade = apply_revival_attempts(row, _signals("42wim/matterbridge"), [attempt])
    assert decision == "RESURRECTION_FAILED"
    assert "https://github.com/example/matterbridge/actions/runs/9" in reason


def test_refactor_success_does_not_change_the_verdict():
    row = _profile("jondot/hygen")
    attempt = _success()
    attempt["stage"] = "refactor"
    decision, reason, upgrade = apply_revival_attempts(row, _signals("jondot/hygen"), [attempt])
    assert decision == "RESURRECTION_FAILED"
    assert "Use Node.js 14.x" in reason
    assert upgrade["stages"][0]["stage"] == "refactor"
    assert upgrade["stages"][0]["status"] == "success"
    assert upgrade["changes_verdict"] is False


def test_fork_success_does_not_override_no_go():
    row = _profile("golang/dep")
    decision, reason, _upgrade = apply_revival_attempts(row, _signals("golang/dep"), [_success("golang/dep")])
    assert decision == "NO_GO"
    assert "successor" in reason
    assert "Stage revive succeeded" not in reason


def test_stage_revive_success_does_not_override_missing_tests():
    row = _profile("discreteoptimization/setcover")
    decision, reason, _upgrade = apply_revival_attempts(
        row, _signals("discreteoptimization/setcover"), [_success("discreteoptimization/setcover")]
    )
    assert decision == "RESURRECTION_FAILED"
    assert "has_tests is false" in reason
    assert "does not override" in reason


def test_upstream_base_commit_is_not_a_stage_one_success():
    row = _profile("jondot/hygen")
    attempt = _success()
    attempt["commit_sha"] = attempt["upstream_base_sha"]
    decision, reason, _upgrade = apply_revival_attempts(row, _signals("jondot/hygen"), [attempt])
    assert decision == "RESURRECTION_FAILED"
    assert "upstream base" in reason


def test_ci_run_on_the_upstream_repo_is_not_a_fork_success():
    row = _profile("jondot/hygen")
    attempt = _success()
    attempt["ci_head_repo"] = "jondot/hygen"
    decision, reason, _upgrade = apply_revival_attempts(row, _signals("jondot/hygen"), [attempt])
    assert decision == "RESURRECTION_FAILED"
    assert "not the fork" in reason


def test_a_commit_outside_the_compare_list_is_not_a_stage_one_success():
    row = _profile("jondot/hygen")
    attempt = _success()
    attempt["compare_shas"] = "someone-else"
    decision, reason, _upgrade = apply_revival_attempts(row, _signals("jondot/hygen"), [attempt])
    assert decision == "RESURRECTION_FAILED"
    assert "compare commit list" in reason


def test_a_workflow_that_does_not_run_tests_is_flagged():
    row = _profile("42wim/matterbridge")
    attempt = _success("42wim/matterbridge")
    attempt["workflow_steps"] = "echo hello"
    attempt["workflow_runs_build"] = "false"
    decision, reason, _upgrade = apply_revival_attempts(row, _signals("42wim/matterbridge"), [attempt])
    assert decision == "CANDIDATE"
    assert "not counted" in reason


def test_a_later_failure_beats_an_earlier_success():
    row = _profile("42wim/matterbridge")
    success = _success("42wim/matterbridge")
    failure = _success("42wim/matterbridge")
    failure["status"] = "failure"
    failure["ci_status"] = "failure"
    failure["commit_sha"] = "def456"
    failure["compare_shas"] = "abc123 def456"
    failure["ci_run_url"] = "https://github.com/Fu93/hygen/actions/runs/2"
    decision, reason, _upgrade = apply_revival_attempts(row, _signals("42wim/matterbridge"), [success, failure])
    assert decision == "RESURRECTION_FAILED"
    assert "actions/runs/2" in reason
    assert "Stage revive succeeded" not in reason
