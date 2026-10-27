"""Regression cases for verifier false positives. All runners are local fakes."""
import json

import pytest

from app.quality_gate import parse_npm_audit, parse_pip_audit
from app.revive_loop import CmdResult
from app.revive_verify import _diff_stat, _manifest_has, verify_revival


def _kept(command="accept", **extra):
    return {"id": "repair", "status": "KEPT", "acceptance_command": command, **extra}


def test_failed_final_tests_cannot_be_excused_by_an_independent_acceptance():
    def runner(cmd, cwd, timeout):
        return CmdResult(cmd, 0, "ok") if cmd == "accept" else CmdResult(cmd, 1, "1 failed")
    result = verify_revival({"src.py": b"bad"}, {"src.py": b"still bad"}, runner, test_cmd="tests", items=[_kept()])
    assert not result["resurrection_allowed"]
    assert "final test command" in result["blocked_reason"]


def test_nonzero_failure_counts_cannot_be_masked_by_exit_zero():
    def runner(cmd, cwd, timeout):
        return CmdResult(cmd, 0, "1 failed")
    result = verify_revival({"src.py": b"old"}, {"src.py": b"new"}, runner, test_cmd="tests", baseline_code=1, items=[_kept()])
    assert not result["resurrection_allowed"]
    assert next(c for c in result["checks"] if c["id"] == "test_counts")["status"] == "FAIL"


def test_verifier_runner_exception_is_unverified_not_a_broken_baseline():
    def runner(cmd, cwd, timeout):
        if (cwd / "src.py").read_bytes() == b"old":
            raise OSError("cannot start interpreter")
        return CmdResult(cmd, 0, "1 passed")
    result = verify_revival({"src.py": b"old"}, {"src.py": b"new"}, runner, test_cmd="tests", items=[_kept()])
    assert not result["resurrection_allowed"]
    assert result["baseline_broken_or_outdated"] is False
    assert next(c for c in result["checks"] if c["id"] == "baseline_tests")["status"] == "UNVERIFIED"


def test_all_kept_items_must_retain_their_passing_acceptance():
    def runner(cmd, cwd, timeout):
        if cmd == "second":
            return CmdResult(cmd, 1, "still broken")
        return CmdResult(cmd, 1 if (cwd / "src.py").read_bytes() == b"old" else 0, "1 passed")
    result = verify_revival({"src.py": b"old"}, {"src.py": b"new"}, runner, test_cmd="tests",
                            items=[_kept("tests"), _kept("second", id="second")])
    assert not result["resurrection_allowed"]
    assert "kept item" in result["blocked_reason"]


def test_equal_length_rewrite_still_exceeds_the_diff_budget():
    original = {"src.py": b"old\n" * 400}
    patched = {"src.py": b"new\n" * 400}
    assert _diff_stat(original, patched)[1] == 800
    result = verify_revival(original, patched, lambda c, p, t: CmdResult(c, 0, "1 passed"), test_cmd="tests", items=[_kept()])
    assert next(c for c in result["checks"] if c["id"] == "diff_scope")["status"] == "FAIL"


@pytest.mark.parametrize("path", ["../outside.txt", "absolute", "..\\outside.txt", ".git/config"])
def test_invalid_snapshot_path_never_reaches_a_runner(path, tmp_path):
    if path == "absolute":
        path = (tmp_path / "outside.txt").as_posix()
    calls = []
    def runner(c, p, t):
        calls.append(c)
        return CmdResult(c, 0, "1 passed")
    result = verify_revival({path: b"old"}, {path: b"new"}, runner, test_cmd="tests", items=[_kept()])
    assert not result["resurrection_allowed"]
    assert calls == []
    assert not (tmp_path / "outside.txt").exists()


def test_dependency_version_must_belong_to_the_named_package():
    tree = {"package.json": json.dumps({"dependencies": {"wanted": "1.0.0", "other": "2.0.0"}}).encode(),
            "notes.txt": b"wanted 2.0.0"}
    assert not _manifest_has(tree, "wanted", "2.0.0")
    assert _manifest_has({"package.json": b'{"dependencies":{"wanted":"^2.0.0"}}'}, "wanted", "2.0.0")
    assert _manifest_has({"requirements.txt": b"requests==2.31.0\n"}, "requests", "2.31.0")
    assert not _manifest_has({"requirements.txt": b"requests==12.31.0\n"}, "requests", "2.31.0")


def test_unchanged_or_unverified_upgrade_cannot_manufacture_an_outdated_baseline():
    tree = {"package.json": b'{"dependencies":{"wanted":"1.0.0","other":"2.0.0"}}'}
    item = _kept("tests", kind="upgrade", package="wanted", version="2.0.0")
    result = verify_revival(tree, tree, lambda c, p, t: CmdResult(c, 0, "1 passed"), test_cmd="tests", items=[item])
    assert not result["resurrection_allowed"]
    assert not result["baseline_broken_or_outdated"]


@pytest.mark.parametrize("payload", [{"metadata": {}}, {"error": {"code": "EAUDITNOLOCK"}, "metadata": {}}])
def test_npm_audit_error_payload_cannot_count_as_clean(payload):
    assert parse_npm_audit(json.dumps(payload), 1)["status"] == "unavailable"


def test_empty_pip_audit_with_failed_exit_is_not_clean():
    assert parse_pip_audit('{"dependencies":[]}', 1)["status"] == "unavailable"


def test_configured_build_must_finish_successfully():
    def runner(c, p, t):
        if c == "build":
            return CmdResult(c, 1, "cannot build")
        return CmdResult(c, 1 if (p / "src.py").read_bytes() == b"old" else 0, "1 passed")
    result = verify_revival({"src.py": b"old"}, {"src.py": b"new"}, runner,
                            test_cmd="tests", build_cmd="build", items=[_kept("tests")])
    assert not result["resurrection_allowed"]
    assert "build" in result["blocked_reason"]


def test_a_small_genuine_fix_with_all_acceptances_can_pass():
    def runner(c, p, t):
        old = (p / "src.py").read_bytes() == b"old"
        return CmdResult(c, int(old), "1 failed" if old else "1 passed")
    result = verify_revival({"src.py": b"old"}, {"src.py": b"new"}, runner,
                            test_cmd="tests", build_cmd="build", items=[_kept(), _kept("second", id="second")])
    assert result["resurrection_allowed"]
    assert result["kept_acceptance_passed"]


def test_missing_final_test_command_is_not_a_verified_revival():
    result = verify_revival({"src.py": b"old"}, {"src.py": b"new"},
                            lambda c, p, t: CmdResult(c, 0, "ok"), baseline_code=1, items=[_kept()])
    assert not result["resurrection_allowed"]


def test_expected_exit_one_does_not_accept_a_verifier_exception():
    def runner(c, p, t):
        if c == "accept":
            raise OSError("cannot launch acceptance")
        old = (p / "src.py").read_bytes() == b"old"
        return CmdResult(c, int(old), "1 failed" if old else "1 passed")
    result = verify_revival({"src.py": b"old"}, {"src.py": b"new"}, runner,
                            test_cmd="tests", items=[_kept(expected="exit code 1")])
    assert not result["resurrection_allowed"]
    assert next(c for c in result["checks"] if c["id"] == "acceptance:repair")["status"] == "UNVERIFIED"


def test_aliased_snapshot_paths_never_reach_the_runner():
    calls = []
    tree = {"src.py": b"old", "./src.py": b"new"}
    result = verify_revival(tree, tree, lambda c, p, t: calls.append(c), test_cmd="tests", items=[_kept()])
    assert not result["resurrection_allowed"]
    assert calls == []


@pytest.mark.parametrize("tree", [
    {"pyproject.toml": b'[project]\ndependencies = ["Requests==2.31.0; python_version >= \'3.10\'"]'},
    {"pyproject.toml": b'[tool.poetry.dependencies]\nrequests = "^2.31.0"'},
    {"requirements-dev.txt": b"requests[security]==2.31.0 # pinned\n"},
])
def test_python_dependency_declarations_are_recognized(tree):
    assert _manifest_has(tree, "requests", "2.31.0")


@pytest.mark.parametrize("payload", [
    {"metadata": {"vulnerabilities": {"high": -1, "critical": 0}}},
    {"metadata": {"vulnerabilities": {"high": "not a count", "critical": 0}}},
    {"vulnerabilities": {"dep": {}}},
])
def test_malformed_npm_audit_is_unavailable(payload):
    assert parse_npm_audit(json.dumps(payload), 0)["status"] == "unavailable"


@pytest.mark.parametrize("dependency", [{}, {"vulns": "not a list"}, {"vulns": [], "skip_reason": "unsupported"}])
def test_incomplete_pip_audit_is_unavailable(dependency):
    assert parse_pip_audit(json.dumps({"dependencies": [dependency]}), 0)["status"] == "unavailable"


@pytest.mark.parametrize("rel,before,after", [
    ("pytest.ini", b"[pytest]\n", b"[pytest]\naddopts = -k passing_only\n"),
    ("pyproject.toml", b"[project]\nname = 'fixture'\n", b"[tool.pytest.ini_options]\naddopts = '-k passing_only'\n"),
])
def test_changing_pytest_configuration_cannot_disguise_a_repair(rel, before, after):
    def runner(c, p, t):
        old = (p / "src.py").read_bytes() == b"old"
        return CmdResult(c, int(old), "1 failed" if old else "1 passed")
    result = verify_revival({"src.py": b"old", rel: before}, {"src.py": b"new", rel: after},
                            runner, test_cmd="tests", items=[_kept()])
    assert not result["resurrection_allowed"]
    assert next(c for c in result["checks"] if c["id"] == "weakened_tests")["status"] == "FAIL"


def test_dependency_only_pyproject_change_preserves_test_configuration():
    from app.revive_verify import weakened_test_diff

    original = {"pyproject.toml": b'[project]\ndependencies = ["requests==2.25.0"]\n'}
    patched = {"pyproject.toml": b'[project]\ndependencies = ["requests==2.31.0"]\n'}
    assert weakened_test_diff(original, patched) == []


def test_downgrade_is_not_an_upgrade_or_an_outdated_baseline():
    result = verify_revival(
        {"requirements.txt": b"requests==2.31.0\n"}, {"requirements.txt": b"requests==2.25.0\n"},
        lambda c, p, t: CmdResult(c, 0, "1 passed"), test_cmd="tests",
        items=[_kept(kind="upgrade", package="requests", version="2.25.0")],
    )
    assert not result["resurrection_allowed"]
    assert not result["baseline_broken_or_outdated"]


@pytest.mark.parametrize("parser,payload", [
    (parse_npm_audit, '{"metadata":{"vulnerabilities":{"high":0,"critical":0,"moderate":1}}}'),
    (parse_pip_audit, '[{"name":"dep","vulns":[{"severity":"moderate"}]}]'),
])
def test_audit_execution_error_cannot_pass_even_with_parseable_partial_findings(parser, payload):
    assert parser(payload, 124)["status"] == "unavailable"


@pytest.mark.parametrize("code", [124, 127, 9009])
def test_timeout_or_missing_executable_cannot_manufacture_a_broken_baseline(code):
    def runner(c, p, t):
        old = (p / "src.py").read_bytes() == b"old"
        return CmdResult(c, code if old else 0, "unavailable" if old else "1 passed")
    result = verify_revival({"src.py": b"old"}, {"src.py": b"new"}, runner,
                            test_cmd="tests", items=[_kept()])
    assert not result["resurrection_allowed"]
    assert not result["baseline_broken_or_outdated"]
    assert next(c for c in result["checks"] if c["id"] == "baseline_tests")["status"] == "UNVERIFIED"
