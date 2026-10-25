"""Gate 2 quality: PRD validation, real checks, revert, and acceptance. No network and no live model."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from app import quality_gate as qg
from app import revive_loop as rl
from app import revive_prd as prd
from scripts.revive import build_parser


class _Trace:
    def __init__(self):
        self.events = []

    def event(self, **kw):
        self.events.append(kw)


class _Client:
    def __init__(self, objs, raises=False):
        self.objs = list(objs)
        self.prompts = []
        self.raises = raises

    def complete_json(self, prompt, system_prompt="", repo_id="", step="", attempts=3, validate=None):
        if self.raises:
            raise RuntimeError("nightshift down")
        self.prompts.append((step, prompt))
        obj = self.objs.pop(0) if self.objs else None
        if obj is not None and validate is not None and not validate(obj):
            obj = None
        return obj, "", "m", "m"


def _prd(scope, *, upgrades=None, refactors=None, checks=("tests", "diff_scope", "audit")):
    statements = {
        "tests": "The test command exits 0.",
        "diff_scope": "The diff touches at most 8 files and does not edit tests.",
        "audit": "High and critical audit findings stay at 0.",
        "lint": "The linter exits 0.",
        "smoke": "The smoke command exits 0.",
    }
    criteria = [{"id": f"ac-{check}", "statement": statements[check], "check_id": check} for check in checks]
    return {
        "problem": "The repository test command fails on the current source.",
        "target_user": "Maintainers of this repository.",
        "scope": list(scope),
        "non_goals": ["Do not edit tests."],
        "upgrade_items": list(upgrades or []),
        "refactor_items": list(refactors or []),
        "docs_items": [{"id": "d1", "title": "Note the test command"}],
        "acceptance_criteria": criteria,
        "risks": ["A patch could touch the wrong file."],
    }


def _node_repo(tmp_path: Path, source: str = "module.exports = () => 1;\n", *, lint: bool = False) -> Path:
    scripts = {"test": "node t.js"}
    pkg: dict = {"scripts": scripts}
    if lint:
        scripts["lint"] = "eslint src.js"
        pkg["eslintConfig"] = {}
    (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
    (tmp_path / "src.js").write_text(source, encoding="utf-8")
    (tmp_path / "t.js").write_text("x", encoding="utf-8")
    return tmp_path


def _result(cmd: str, code: int, output: str) -> rl.CmdResult:
    return rl.CmdResult(cmd, code, output)


def test_prd_validation_rejects_thin_drafts_and_missing_files(tmp_path):
    (tmp_path / "src.js").write_text("x\n", encoding="utf-8")
    thin = {"problem": "short"}
    errors = prd.validate_prd(thin, tmp_path)
    assert "missing target_user" in errors
    assert any("acceptance_criteria" in error for error in errors)

    draft = _prd(["src.js"])
    draft["acceptance_criteria"] = draft["acceptance_criteria"][:2]
    assert any("at least 3" in error for error in prd.validate_prd(draft, tmp_path))

    draft = _prd(["src.js"])
    draft["acceptance_criteria"][0]["statement"] = "make it better"
    assert any("not measurable" in error for error in prd.validate_prd(draft, tmp_path))

    draft = _prd(["src.js"])
    draft["acceptance_criteria"][0]["check_id"] = "vibes"
    assert any("not a known quality check" in error for error in prd.validate_prd(draft, tmp_path))

    draft = _prd(["missing.js"])
    assert any("scope path does not exist" in error for error in prd.validate_prd(draft, tmp_path))

    draft = _prd(["src.js"], refactors=[{"id": "r1", "title": "split", "files": ["gone.js"]}])
    assert any("does not exist: gone.js" in error for error in prd.validate_prd(draft, tmp_path))

    draft = _prd(["src.js"], upgrades=[{"id": "u1", "package": "left-pad"}])
    assert any("package and version" in error for error in prd.validate_prd(draft, tmp_path))

    good = _prd(
        ["src.js"],
        upgrades=[{"id": "u1", "package": "left-pad", "version": "1.3.0", "reason": "pin a maintained release"}],
        refactors=[{"id": "r1", "title": "Name the export", "files": ["src.js"], "reason": "the file is one expression"}],
    )
    assert prd.validate_prd(good, tmp_path) == []

    reason_only = _prd(["src.js"], upgrades=[{"id": "u2", "reason": "Leave the Node 14 runtime because CI no longer provides it."}])
    assert prd.validate_prd(reason_only, tmp_path) == []


def test_prd_fallback_is_recorded_and_a_second_attempt_can_succeed(tmp_path):
    (tmp_path / "README.md").write_text("Adds numbers.\n", encoding="utf-8")
    (tmp_path / "src.js").write_text("x\n", encoding="utf-8")
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "node t.js"}}), encoding="utf-8")
    baseline = _result("npm test --silent", 1, "AssertionError expected 2")
    client = _Client([{"problem": "too thin"}, {"problem": "still thin"}])
    drafted = prd.draft_prd(client, tmp_path, "o/r", "demo", baseline)
    assert drafted["degraded"] is True
    assert "acceptance_criteria" in drafted["degraded_reason"] or "missing" in drafted["degraded_reason"]
    assert prd.validate_prd({k: v for k, v in drafted.items() if k not in {"degraded", "degraded_reason"}}, tmp_path) == []
    assert len(client.prompts) == 2
    assert "PRD attempt 2" in client.prompts[1][1]
    assert "Adds numbers." in client.prompts[0][1]
    assert "src.js" in client.prompts[0][1]
    assert "AssertionError expected 2" in client.prompts[0][1]
    markdown = prd.render_prd_markdown(drafted)
    assert "DEGRADED PRD" in markdown

    recovered = _prd(["src.js"])
    client = _Client([{"problem": "nope"}, recovered])
    drafted = prd.draft_prd(client, tmp_path, "o/r", "", baseline)
    assert drafted.get("degraded") is not True
    assert drafted["problem"] == recovered["problem"]


def test_failed_llm_call_degrades_the_prd_and_cannot_pass(tmp_path):
    repo = _node_repo(tmp_path)
    res = rl.revive(repo, _Client([], raises=True), _Trace(), "o/r", runner=_passing_after_fix(repo))
    assert res.state == "RESURRECTION_FAILED"
    assert res.prd["degraded"] is True
    assert "llm call failed" in res.prd["degraded_reason"]


def _passing_after_fix(repo: Path):
    def runner(cmd, cwd, timeout):
        if cmd.startswith("npm install") or cmd.startswith("npm ci"):
            return _result(cmd, 0, "installed")
        if "audit" in cmd:
            return _result(cmd, 0, json.dumps({"metadata": {"vulnerabilities": {"high": 0, "critical": 0, "moderate": 0}}}))
        text = (cwd / "src.js").read_text(encoding="utf-8")
        if "=> 2" in text and "BAD" not in text:
            return _result(cmd, 0, "all tests pass")
        return _result(cmd, 1, "AssertionError in src.js: expected 2")

    return runner


def test_audit_json_parsing_and_skipped_never_pass():
    failed = qg.parse_npm_audit(json.dumps({"metadata": {"vulnerabilities": {"high": 2, "critical": 1, "moderate": 4}}}), 1)
    assert failed["status"] == "fail"
    assert failed["high"] == 2 and failed["critical"] == 1

    clean = qg.parse_npm_audit(json.dumps({"metadata": {"vulnerabilities": {"high": 0, "critical": 0, "moderate": 3}}}), 1)
    assert clean["status"] == "pass"
    assert clean["high"] == 0

    from_map = qg.parse_npm_audit(json.dumps({"vulnerabilities": {"a": {"severity": "high"}, "b": {"severity": "low"}}}), 1)
    assert from_map["status"] == "fail" and from_map["high"] == 1

    assert qg.parse_npm_audit("all tests pass", 0)["status"] == "unavailable"
    assert qg.parse_npm_audit("{}", 0)["status"] == "unavailable"
    assert not qg.counts_as_pass(qg.parse_npm_audit("nope", 0))

    pip_clean = qg.parse_pip_audit(json.dumps({"dependencies": [{"name": "a", "version": "1", "vulns": []}]}), 0)
    assert pip_clean["status"] == "pass"
    pip_hit = qg.parse_pip_audit(json.dumps([{"name": "a", "vulns": [{"id": "PYSEC-1"}]}]), 1)
    assert pip_hit["status"] == "fail" and pip_hit["high"] == 1
    rated = qg.parse_pip_audit(json.dumps([{"name": "a", "vulns": [{"id": "PYSEC-1", "severity": "moderate"}]}]), 1)
    assert rated["status"] == "pass"

    for status in ("skipped", "unavailable", "fail", ""):
        assert not qg.counts_as_pass({"id": "lint", "status": status})
    assert qg.passing_ids([{"id": "tests", "status": "pass"}, {"id": "lint", "status": "skipped"}, {"id": "audit", "status": "unavailable"}]) == ["tests"]


def test_each_configured_check_is_parsed_and_unconfigured_checks_are_skipped(tmp_path):
    repo = _node_repo(tmp_path, "module.exports = () => 2;\n", lint=True)
    (repo / "tsconfig.json").write_text("{}", encoding="utf-8")
    pkg = json.loads((repo / "package.json").read_text(encoding="utf-8"))
    pkg["scripts"]["build"] = "echo build"
    pkg["scripts"]["start"] = "node server.js"
    (repo / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
    calls = []

    def runner(cmd, cwd, timeout):
        calls.append(cmd)
        if "audit" in cmd:
            return _result(cmd, 0, json.dumps({"metadata": {"vulnerabilities": {"high": 1, "critical": 0}}}))
        if cmd == "npm start":
            return _result(cmd, 124, "TIMEOUT after 10s\nstill listening")
        if "eslint" in cmd or "lint" in cmd:
            return _result(cmd, 0, "lint clean")
        if "tsc" in cmd:
            return _result(cmd, 0, "")
        if "build" in cmd:
            return _result(cmd, 1, "build failed")
        return _result(cmd, 0, "all tests pass")

    checks = qg.run_quality_gate(repo, runner, "npm test --silent", test_result=_result("npm test --silent", 0, "ok"), prd=_prd(["src.js"]))
    by_id = {row["id"]: row for row in checks}
    assert set(by_id) == set(qg.CHECK_IDS)
    assert by_id["tests"]["status"] == "pass"
    assert by_id["lint"]["status"] == "pass"
    assert by_id["types"]["status"] == "pass"
    assert by_id["build"]["status"] == "fail"
    assert by_id["audit"]["status"] == "fail" and by_id["audit"]["high"] == 1
    assert by_id["smoke"]["status"] == "pass"
    assert by_id["diff_scope"]["status"] == "pass"
    assert "npm run lint --silent" in calls
    assert "npx --no-install tsc --noEmit" in calls
    assert "npm audit --omit=dev --json" in calls
    assert not qg.counts_as_pass(by_id["build"])

    bare = tmp_path / "bare"
    bare.mkdir()
    bare_checks = qg.run_quality_gate(bare, runner, None)
    bare_by_id = {row["id"]: row for row in bare_checks}
    for check_id in ("lint", "types", "build", "audit", "smoke"):
        assert bare_by_id[check_id]["status"] == "skipped"
        assert not qg.counts_as_pass(bare_by_id[check_id])
    assert bare_by_id["tests"]["status"] == "unavailable"


def test_python_and_go_commands_match_the_manifest(tmp_path):
    py = tmp_path / "py"
    py.mkdir()
    (py / "pyproject.toml").write_text("[tool.ruff]\nline-length = 80\n[tool.mypy]\n[build-system]\nrequires = ['setuptools']\n", encoding="utf-8")
    (py / "requirements.txt").write_text("requests==2.31.0\n", encoding="utf-8")
    calls = []

    def runner(cmd, cwd, timeout):
        calls.append(cmd)
        if "pip-audit" in cmd:
            return _result(cmd, 0, json.dumps({"dependencies": []}))
        if "build" in cmd and "No module" not in cmd:
            return _result(cmd, 1, "No module named build")
        return _result(cmd, 0, "ok")

    checks = {row["id"]: row for row in qg.run_quality_gate(py, runner, "python -m pytest -q", tools_present=lambda name: "/usr/bin/pip-audit" if name == "pip-audit" else None)}
    assert any(cmd == "python -m ruff check ." for cmd in calls)
    assert any(cmd == "python -m mypy ." for cmd in calls)
    assert any("pip-audit -f json" == cmd for cmd in calls)
    assert checks["build"]["status"] == "unavailable"
    assert checks["audit"]["status"] == "pass"

    missing = {row["id"]: row for row in qg.run_quality_gate(py, runner, "python -m pytest -q", tools_present=lambda name: None)}
    assert missing["audit"]["status"] == "unavailable"
    assert "not installed" in missing["audit"]["detail_tail"]
    assert not qg.counts_as_pass(missing["audit"])

    go = tmp_path / "go"
    go.mkdir()
    (go / "go.mod").write_text("module example.com/demo\n\ngo 1.22\n", encoding="utf-8")
    (go / "main.go").write_text("package main\nfunc main() {}\n", encoding="utf-8")
    go_calls = []

    def go_runner(cmd, cwd, timeout):
        go_calls.append(cmd)
        if "vet" in cmd:
            return _result(cmd, 1, "vet: unreachable code")
        if cmd.endswith("--help"):
            return _result(cmd, 0, "usage")
        return _result(cmd, 0, "ok")

    go_checks = {row["id"]: row for row in qg.run_quality_gate(go, go_runner, "go test ./...")}
    assert "go vet ./..." in go_calls
    assert "go build ./..." in go_calls
    assert go_checks["audit"]["status"] == "fail"
    assert go_checks["smoke"]["status"] == "pass"


def test_diff_scope_rejects_tests_and_oversized_diffs(tmp_path):
    (tmp_path / "src.js").write_text("x\n", encoding="utf-8")
    prd_obj = _prd(["src.js"])
    protected = qg.diff_scope_check(["tests/a.test.js"], 2, prd_obj, rl.is_protected)
    assert protected["status"] == "fail"
    outside = qg.diff_scope_check(["other.js"], 2, prd_obj, rl.is_protected)
    assert outside["status"] == "fail"
    huge = qg.diff_scope_check(["src.js"], 50, prd_obj, rl.is_protected, max_files=8, max_lines=10)
    assert huge["status"] == "fail" and "oversized" in huge["detail_tail"]
    ok = qg.diff_scope_check(["src.js"], 4, prd_obj, rl.is_protected)
    assert ok["status"] == "pass"
    assert qg.regressions([{"id": "lint", "status": "pass"}], [{"id": "lint", "status": "skipped"}]) == ["lint"]
    assert qg.regressions([{"id": "audit", "status": "fail", "high": 1, "critical": 0}], [{"id": "audit", "status": "fail", "high": 2, "critical": 0}]) == ["audit"]
    assert qg.verified_criteria(prd_obj, [{"id": "tests", "status": "pass"}, {"id": "lint", "status": "skipped"}]) == ["ac-tests"]


def test_regression_reverts_the_patch_and_item_retries_then_not_done(tmp_path):
    repo = _node_repo(tmp_path, "module.exports = () => 2;\n", lint=True)
    original = (repo / "src.js").read_text(encoding="utf-8")

    def runner(cmd, cwd, timeout):
        if cmd.startswith("npm install") or "audit" in cmd:
            if "audit" in cmd:
                return _result(cmd, 0, json.dumps({"metadata": {"vulnerabilities": {"high": 0, "critical": 0}}}))
            return _result(cmd, 0, "installed")
        text = (cwd / "src.js").read_text(encoding="utf-8")
        if "lint" in cmd:
            if "BAD" in text:
                return _result(cmd, 1, "lint: unexpected BAD token")
            return _result(cmd, 0, "lint clean")
        if "=> 2" in text and "BAD" not in text:
            return _result(cmd, 0, "all tests pass")
        return _result(cmd, 1, "AssertionError tests failed")

    refactor = {"id": "r1", "title": "Tighten the export", "files": ["src.js"], "reason": "name the constant"}
    bad = {"edits": [{"path": "src.js", "find": "=> 2", "replace": "=> 2 /* BAD */"}]}
    client = _Client([_prd(["src.js"], refactors=[refactor]), bad, bad, bad])
    trace = _Trace()
    res = rl.revive(repo, client, trace, "o/r", runner=runner)
    assert res.state == "NOTHING_TO_FIX"
    assert (repo / "src.js").read_text(encoding="utf-8") == original
    assert res.prd_items_done == []
    assert res.prd_items_not_done[0]["id"] == "r1"
    assert "BAD" in res.prd_items_not_done[0]["reason"] or "lint" in res.prd_items_not_done[0]["reason"]
    statuses = [event["output_summary"].get("status") for event in trace.events if event["action"] == "REVIVE_ITEM"]
    assert statuses.count("reverted") == 3
    assert "not_done" in statuses
    assert "Item attempt 2" in client.prompts[-1][1] or any("Item attempt 2" in prompt for _, prompt in client.prompts)


def test_applied_upgrade_on_a_green_baseline_can_resurrect(tmp_path):
    repo = _node_repo(tmp_path, "module.exports = () => 2;\n")
    upgrade = {"id": "u1", "package": "left-pad", "version": "1.3.0", "reason": "pin a maintained release"}
    patch = {"edits": [{"path": "src.js", "find": "=> 2;", "replace": "=> 2; // pinned"}]}
    client = _Client([_prd(["src.js"], upgrades=[upgrade]), patch, {"approve": True, "issues": []}])
    res = rl.revive(repo, client, _Trace(), "o/r", runner=_passing_after_fix(repo))
    assert res.state == "RESURRECTED"
    assert res.prd_items_done[0]["id"] == "u1"
    assert "pinned" in (repo / "src.js").read_text(encoding="utf-8")
    assert "tests" not in res.quality["regressed"]


def test_final_acceptance_requires_a_real_verified_criterion(tmp_path):
    repo = _node_repo(tmp_path)
    patch = {"edits": [{"path": "src.js", "find": "=> 1", "replace": "=> 2"}]}
    client = _Client([_prd(["src.js"], checks=("lint", "lint", "lint")), patch, {"approve": True, "issues": []}])
    res = rl.revive(repo, client, _Trace(), "o/r", runner=_passing_after_fix(repo))
    assert res.final is not None and res.final.code == 0
    assert res.state == "NEEDS_REVIEW"
    assert res.quality["verified_criteria"] == []

    fixed = tmp_path / "fixed"
    fixed.mkdir()
    repo = _node_repo(fixed)
    client = _Client([_prd(["src.js"]), patch, {"approve": True, "issues": []}])
    res = rl.revive(repo, client, _Trace(), "o/r", runner=_passing_after_fix(repo))
    assert res.state == "RESURRECTED"
    assert "ac-tests" in res.quality["verified_criteria"]


def test_reviewer_cannot_override_a_failed_check_and_a_rejection_is_sent_back_once(tmp_path):
    repo = _node_repo(tmp_path)
    bad = {"edits": [{"path": "src.js", "find": "=> 1", "replace": "=> 3"}]}
    client = _Client([_prd(["src.js"]), bad, {"approve": True, "issues": [{"detail": "looks fine to me"}]}])
    trace = _Trace()
    res = rl.revive(repo, client, trace, "o/r", max_iterations=1, runner=_passing_after_fix(repo))
    assert res.state == "RESURRECTION_FAILED"
    tests = next(row for row in res.quality["after"] if row["id"] == "tests")
    assert tests["status"] == "fail"
    assert res.review["approve"] is True
    assert any(event["action"] == "REVIVE_REVIEW" for event in trace.events)
    assert not any(event["action"] == "REVIVE_PATCH" and isinstance(event["input_summary"], dict) and event["input_summary"].get("iteration") == "review-1" for event in trace.events)

    reviewed = tmp_path / "review"
    reviewed.mkdir()
    repo = _node_repo(reviewed)
    fix = {"edits": [{"path": "src.js", "find": "=> 1", "replace": "=> 2"}]}
    follow = {"edits": [{"path": "src.js", "find": "=> 2;", "replace": "=> 2; // reviewed"}]}
    review = {"approve": False, "issues": [{"id": "n1", "detail": "Rename the exported helper to addNumbers"}]}
    client = _Client([_prd(["src.js"]), fix, review, follow])
    trace = _Trace()
    res = rl.revive(repo, client, trace, "o/r", runner=_passing_after_fix(repo))
    assert res.state == "RESURRECTED"
    assert any("Rename the exported helper to addNumbers" in prompt and "Iteration review-1" in prompt for _, prompt in client.prompts)
    assert "reviewed" in (repo / "src.js").read_text(encoding="utf-8")
    assert sum(1 for event in trace.events if event["action"] == "REVIVE_REVIEW") == 1


def test_gate1_block_is_unchanged(tmp_path):
    repo = _node_repo(tmp_path)
    calls = []

    def runner(cmd, cwd, timeout):
        calls.append(cmd)
        return _result(cmd, 0, "nope")

    trace = _Trace()
    res = rl.revive(repo, _Client([]), trace, "o/r", gate_verdict="NO_GO", runner=runner)
    assert res.state == "NOT_ATTEMPTED_GATE1"
    assert res.files_changed == []
    assert calls == []
    assert [event["action"] for event in trace.events] == ["REVIVE_BLOCKED_BY_GATE1"]


def test_snapshot_wins_over_git_checkout(tmp_path):
    repo = tmp_path / "gitrepo"
    repo.mkdir()
    (repo / "src.js").write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "add", "src.js"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "v1"], cwd=repo, check=True)
    (repo / "src.js").write_text("v2 dirty\n", encoding="utf-8")
    snap = {"src.js": b"v2 dirty\n"}
    (repo / "src.js").write_text("v3 patched\n", encoding="utf-8")
    rl.restore_snapshot(repo, snap)
    assert (repo / "src.js").read_bytes() == b"v2 dirty\n"


def test_reports_show_skipped_checks_honestly(tmp_path):
    result = rl.ReviveResult(
        "NEEDS_REVIEW",
        "tests pass but no criterion was verified",
        prd=_prd(["src.js"]),
        quality={
            "before": [{"id": "lint", "status": "skipped", "detail_tail": "no linter"}],
            "after": [{"id": "lint", "status": "skipped", "detail_tail": "no linter"}, {"id": "tests", "status": "pass", "detail_tail": "exit 0"}],
            "files_changed": [],
            "lines_changed": 0,
            "regressed": [],
            "verified_criteria": [],
        },
    )
    prd.write_reports(tmp_path, result, "o/r", "run-1")
    html = (tmp_path / "evidence.html").read_text(encoding="utf-8")
    assert "skipped" in html
    assert "data-status=\"skipped\"" in html
    assert "<script src=" not in html
    assert "http://" not in html and "https://" not in html
    assert "DEGRADED PRD" not in (tmp_path / "PRD.md").read_text(encoding="utf-8")
    assert json.loads((tmp_path / "quality.json").read_text(encoding="utf-8"))["after"][0]["status"] == "skipped"


def test_cli_flags_stay_compatible():
    args = build_parser().parse_args(["https://github.com/o/r", "--workdir", "w", "--iterations", "3", "--run-id", "abc", "--gate-verdict", "CANDIDATE"])
    assert args.iterations == 3
    assert args.run_id == "abc"
    assert args.gate_verdict == "CANDIDATE"
    assert args.url.endswith("o/r")


def test_decide_state_ignores_any_reviewer_opinion():
    state, _ = rl.decide_revival_state(
        baseline_tests_passed=False,
        tests_passed=False,
        regressed=[],
        verified_criteria=["ac-tests"],
        had_work_items=False,
        items_applied=0,
    )
    assert state == "RESURRECTION_FAILED"
    state, reason = rl.decide_revival_state(
        baseline_tests_passed=True,
        tests_passed=True,
        regressed=[],
        verified_criteria=["ac-tests"],
        had_work_items=True,
        items_applied=0,
        not_done_reason="lint regressed",
    )
    assert state == "NOTHING_TO_FIX"
    assert "not applied" in reason or "no upgrade" in reason


def test_end_to_end_real_runner_fixes_a_python_module(tmp_path):
    repo = tmp_path / "calc"
    repo.mkdir()
    (repo / "README.md").write_text("# calc\nAdds numbers.\n", encoding="utf-8")
    (repo / "requirements.txt").write_text("# no third-party deps\n", encoding="utf-8")
    (repo / "pytest.ini").write_text("[pytest]\ntestpaths = .\n", encoding="utf-8")
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (repo / "test_calc.py").write_text("from calc import add\n\ndef test_add():\n    assert add(1, 1) == 2\n", encoding="utf-8")
    command = f"{shlex_quote(sys.executable)} -m pytest -q -p no:cacheprovider --noconftest --rootdir {shlex_quote(str(repo))} -c {shlex_quote(str(repo / 'pytest.ini'))}"

    def detect(_repo):
        return None, command

    client = _Client(
        [
            _prd(["calc.py"]),
            {"rationale": "addition, not subtraction", "edits": [{"path": "calc.py", "find": "return a - b", "replace": "return a + b"}]},
            {"approve": True, "issues": []},
        ]
    )
    trace = _Trace()
    original_detect = rl.detect_commands
    rl.detect_commands = detect
    try:
        res = rl.revive(repo, client, trace, "o/calc", description="add numbers", runner=rl.shell_runner, test_timeout=60)
    finally:
        rl.detect_commands = original_detect
    assert "Adds numbers." in client.prompts[0][1]
    assert "PRD attempt 1" in client.prompts[0][1]
    assert res.state == "RESURRECTED"
    assert "return a + b" in (repo / "calc.py").read_text(encoding="utf-8")
    audit = next(row for row in res.quality["after"] if row["id"] == "audit")
    lint = next(row for row in res.quality["after"] if row["id"] == "lint")
    assert lint["status"] == "skipped"
    assert not qg.counts_as_pass(lint)
    if shutil.which("pip-audit") is None:
        assert audit["status"] == "unavailable"
        assert not qg.counts_as_pass(audit)
    actions = {event["action"] for event in trace.events}
    assert {"REVIVE_PRD", "REVIVE_QUALITY", "REVIVE_REVIEW", "REVIVE_RESULT"} <= actions
    proc = subprocess.run(command, cwd=repo, shell=True, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    prd.write_reports(tmp_path / "reports", res, "o/calc", "e2e")
    html = (tmp_path / "reports" / "evidence.html").read_text(encoding="utf-8")
    assert "skipped" in html
    if shutil.which("pip-audit") is None:
        assert "unavailable" in html
    assert "<script src=" not in html


def shlex_quote(value: str) -> str:
    import os
    import shlex
    import subprocess

    if os.name == 'nt':
        return subprocess.list2cmdline([value])
    return shlex.quote(value)
