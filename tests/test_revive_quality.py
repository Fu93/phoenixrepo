"""Gate 2 quality: PRD validation, real checks, revert, and acceptance. No network and no live model."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from app import quality_gate as qg
from app import revive_loop as rl
from app import revive_prd as prd
from scripts.revive import build_parser, emit_result


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
    prd = {
        "problem": "The repository test command fails on the current source.",
        "target_user": "Maintainers of this repository.",
        "scope": list(scope),
        "non_goals": ["Do not edit tests."],
        "upgrade_items": list(upgrades or []),
        "refactor_items": list(refactors or []),
        "docs_items": [{"id": "d1", "title": "Note the test command", "acceptance_command": "npm test --silent", "expected": "exit code 0"}],
        "acceptance_criteria": criteria,
        "risks": ["A patch could touch the wrong file."],
        "original_intent": "(no README or description)",
        "diagnosis": "The baseline test command exits 1 with an AssertionError.",
        "work_items": [
            {
                "id": "fix-baseline",
                "kind": "repair",
                "title": "Make the repository test command exit 0",
                "acceptance_command": "npm test --silent",
                "expected": "exit code 0",
                "files": [scope[0]] if scope else [],
            }
        ],
    }
    for bucket in ("upgrade_items", "refactor_items"):
        stamped = []
        for item in prd[bucket]:
            item = dict(item)
            item.setdefault("acceptance_command", "npm test --silent")
            item.setdefault("expected", "exit code 0")
            stamped.append(item)
        prd[bucket] = stamped
    return prd


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


def _result(cmd: str, code: int, output: str, seconds: float = 0.0) -> rl.CmdResult:
    return rl.CmdResult(cmd, code, output, seconds)


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
    assert any("target version" in error for error in prd.validate_prd(draft, tmp_path))

    (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"left-pad": "1.2.0"}}), encoding="utf-8")
    good = _prd(
        ["src.js"],
        upgrades=[{"id": "u1", "package": "left-pad", "version": "1.3.0", "reason": "pin a maintained release"}],
        refactors=[{"id": "r1", "title": "Name the export", "files": ["src.js"], "reason": "the file is one expression"}],
    )
    assert prd.validate_prd(good, tmp_path) == []

    reason_only = _prd(["src.js"], upgrades=[{"id": "u2", "reason": "Leave the Node 14 runtime because CI no longer provides it."}])
    assert any("target version" in error for error in prd.validate_prd(reason_only, tmp_path))


def test_prd_fallback_is_recorded_and_a_second_attempt_can_succeed(tmp_path):
    (tmp_path / "README.md").write_text("Adds numbers.\n", encoding="utf-8")
    (tmp_path / "src.js").write_text("x\n", encoding="utf-8")
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "node t.js"}}), encoding="utf-8")
    baseline = _result("npm test --silent", 1, "AssertionError expected 2")
    client = _Client([{"problem": "too thin"}, {"problem": "still thin"}])
    drafted = prd.draft_prd(client, tmp_path, "o/r", "demo", baseline)
    assert drafted.get("degraded") is not True
    assert drafted["drafted_by"] == "deterministic"
    assert drafted["label"] == "deterministic PRD"
    assert "missing" in drafted["fallback_reason"]
    assert prd.validate_prd(drafted, tmp_path) == []
    assert len(client.prompts) == 2
    assert "PRD attempt 2" in client.prompts[1][1]
    assert "Adds numbers." in client.prompts[0][1]
    assert "src.js" in client.prompts[0][1]
    assert "AssertionError expected 2" in client.prompts[0][1]
    assert "=== " not in client.prompts[0][1]
    assert len(client.prompts[0][1].encode("utf-8")) <= prd.PROMPT_BUDGET_BYTES
    markdown = prd.render_prd_markdown(drafted)
    assert "deterministic PRD" in markdown
    assert "without a model call" in markdown
    assert "DEGRADED PRD" not in markdown

    recovered = _prd(["src.js"])
    recovered["original_intent"] = 'The README says "Adds numbers."'
    client = _Client([{"problem": "nope"}, recovered])
    drafted = prd.draft_prd(client, tmp_path, "o/r", "", baseline)
    assert drafted.get("degraded") is not True
    assert drafted["problem"] == recovered["problem"]


def test_failed_llm_call_uses_a_deterministic_prd_and_cannot_pass(tmp_path):
    repo = _node_repo(tmp_path)
    res = rl.revive(repo, _Client([], raises=True), _Trace(), "o/r", runner=_passing_after_fix(repo))
    assert res.state == "RESURRECTION_FAILED"
    assert res.prd.get("degraded") is not True
    assert res.prd["drafted_by"] == "deterministic"
    assert res.prd["label"] == "deterministic PRD"
    assert "llm call failed" in res.prd["fallback_reason"]


def _passing_after_fix(repo: Path):
    def runner(cmd, cwd, timeout):
        if "npm view" in cmd:
            match = re.search(r"@(\d+\.\d+\.\d+)", cmd)
            return _result(cmd, 0, match.group(1) if match else "")
        if "outdated" in cmd:
            return _result(cmd, 0, "{}")
        if cmd.startswith("npm install") or cmd.startswith("npm ci"):
            return _result(cmd, 0, "installed")
        if "npm audit" in cmd:
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
    assert set(qg.CHECK_IDS) <= set(by_id)
    assert by_id["deps_freshness"]["status"] == "info"
    assert not qg.counts_as_pass(by_id["deps_freshness"])
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
    assert qg.MAX_DIFF_FILES == 8 and qg.MAX_DIFF_LINES == 300
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
    pkg = json.loads((repo / "package.json").read_text(encoding="utf-8"))
    pkg["dependencies"] = {"left-pad": "1.2.0"}
    (repo / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
    upgrade = {"id": "u1", "package": "left-pad", "version": "1.3.0", "reason": "pin a maintained release"}
    client = _Client([_prd(["src.js", "package.json"], upgrades=[upgrade]), {"approve": True, "issues": []}])
    res = rl.revive(repo, client, _Trace(), "o/r", runner=_passing_after_fix(repo))
    assert res.state == "RESURRECTED"
    assert res.prd_items_done[0]["id"] == "u1"
    assert res.prd_items_done[0]["status"] == "KEPT"
    assert res.prd_items_done[0]["lines"] >= 1
    assert "1.3.0" in (repo / "package.json").read_text(encoding="utf-8")
    assert "tests" not in res.quality["regressed"]
    assert not any("Item attempt" in prompt for _, prompt in client.prompts)


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
    review = {
        "approve": False,
        "issues": [
            {"id": "n1", "detail": "Rename the exported helper in src.js to addNumbers"},
            {"id": "n2", "detail": "try harder"},
        ],
    }
    client = _Client([_prd(["src.js"]), fix, review, follow])
    trace = _Trace()
    res = rl.revive(repo, client, trace, "o/r", runner=_passing_after_fix(repo))
    assert res.state == "RESURRECTED"
    assert any("Rename the exported helper in src.js to addNumbers" in prompt and "Iteration review-1" in prompt for _, prompt in client.prompts)
    follow = [prompt for step, prompt in client.prompts if step == "revive_patch_review-1"]
    assert follow and "try harder" not in follow[0]
    review_prompt = [prompt for step, prompt in client.prompts if step == "revive_review"][0]
    assert "Quality before" in review_prompt and "Quality after" in review_prompt
    assert "Environment-caused test failures are not code defects" in review_prompt
    assert "a/src.js" in review_prompt
    assert res.review["issues"] == [{"id": "n1", "detail": "Rename the exported helper in src.js to addNumbers"}]
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
    assert args.time_budget == 2400
    short = build_parser().parse_args(["https://github.com/o/r", "--time-budget", "30"])
    assert short.time_budget == 30


def test_cli_prints_a_one_screen_summary(capsys):
    payload = {
        "repo": "o/r",
        "gate1": "CANDIDATE",
        "state": "NOTHING_TO_FIX",
        "reason": "no upgrade was applied",
        "files_changed": [],
        "run_id": "abc",
        "quality": {
            "before": [{"id": "tests", "status": "pass"}, {"id": "lint", "status": "skipped"}],
            "after": [{"id": "tests", "status": "pass"}, {"id": "lint", "status": "fail"}, {"id": "audit", "status": "pass"}],
        },
        "prd_items_done": [{"id": "u1", "kind": "upgrade", "files": ["package.json"], "lines": 2, "status": "applied"}],
        "prd_items_not_done": [
            {"id": "u2", "kind": "upgrade", "reason": "AssertionError expected green", "status": "reverted", "failed_check": "tests"},
            {"id": "u3", "kind": "upgrade", "reason": "target version not found", "status": "not_done"},
        ],
    }
    text = rl.format_run_summary(payload)
    assert text.startswith("o/r: NOTHING_TO_FIX")
    assert "tests: pass -> pass" in text
    assert "lint: skipped -> fail" in text
    assert "u1: 1 file(s), 2 lines" in text
    assert "u2: tests failed" in text
    assert "u3: target version not found" in text
    emit_result(payload, ("repo", "state", "reason"))
    out = capsys.readouterr().out
    assert "o/r: NOTHING_TO_FIX" in out
    assert "tests: pass -> pass" in out
    assert '"state": "NOTHING_TO_FIX"' in out


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
    state, reason = rl.decide_revival_state(
        baseline_tests_passed=True,
        tests_passed=False,
        regressed=["tests"],
        verified_criteria=[],
        had_work_items=True,
        items_applied=0,
        not_done_reason="u1: reverted; u2: target version not found",
    )
    assert state == "NOTHING_TO_FIX"
    assert "u1" in reason and "target version not found" in reason
    state, _ = rl.decide_revival_state(
        baseline_tests_passed=True,
        tests_passed=False,
        regressed=[],
        verified_criteria=[],
        had_work_items=True,
        items_applied=1,
    )
    assert state == "RESURRECTION_FAILED"


def test_end_to_end_real_runner_fixes_a_python_module(tmp_path):
    repo = tmp_path / "calc"
    repo.mkdir()
    (repo / "README.md").write_text("# calc\nAdds numbers.\n", encoding="utf-8")
    (repo / "requirements.txt").write_text("# no third-party deps\n", encoding="utf-8")
    (repo / "pytest.ini").write_text("[pytest]\ntestpaths = .\n", encoding="utf-8")
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (repo / "test_calc.py").write_text("from calc import add\n\ndef test_add():\n    assert add(1, 1) == 2\n", encoding="utf-8")
    command = f"{shlex_quote(sys.executable)} -m pytest -q -p no:cacheprovider --noconftest --rootdir . -c pytest.ini"

    def detect(_repo):
        return None, command

    drafted = _prd(["calc.py"])
    drafted["original_intent"] = 'The README says "Adds numbers."'
    drafted["work_items"][0]["acceptance_command"] = command
    drafted["work_items"][0]["files"] = ["calc.py"]
    client = _Client(
        [
            drafted,
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


def test_next_or_vite_build_missing_platform_binary_is_unavailable(tmp_path):
    repo = _node_repo(tmp_path, "module.exports = () => 2;\n")
    pkg = json.loads((repo / "package.json").read_text(encoding="utf-8"))
    pkg["scripts"]["build"] = "next build"
    (repo / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
    assert qg.detect_build_commands(repo) == ["npm run build --silent"]
    pkg["scripts"]["build"] = "vite build"
    (repo / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
    assert qg.detect_build_commands(repo) == ["npm run build --silent"]

    def runner(cmd, cwd, timeout):
        if "outdated" in cmd:
            return _result(cmd, 1, json.dumps({"left-pad": {"current": "1.2.0", "latest": "1.3.0"}}))
        if "npm audit" in cmd:
            return _result(cmd, 0, json.dumps({"metadata": {"vulnerabilities": {"high": 0, "critical": 0}}}))
        if "build" in cmd:
            return _result(cmd, 1, "Error: Cannot find module '@parcel/watcher-win32-x64'")
        return _result(cmd, 0, "ok")

    checks = {row["id"]: row for row in qg.run_quality_gate(repo, runner, "npm test --silent", test_result=_result("npm test", 0, "ok"))}
    assert checks["build"]["status"] == "unavailable"
    assert "@parcel/watcher" in checks["build"]["detail_tail"]
    assert not qg.counts_as_pass(checks["build"])
    fresh = checks["deps_freshness"]
    assert fresh["status"] == "info" and fresh["outdated"] == 1
    assert not qg.counts_as_pass(fresh)
    assert "deps_freshness" not in qg.regressions([{"id": "deps_freshness", "status": "pass"}], [fresh])

    def real_break(cmd, cwd, timeout):
        if "build" in cmd:
            return _result(cmd, 1, "Failed to compile.")
        return _result(cmd, 0, "{}")

    broken = {row["id"]: row for row in qg.run_quality_gate(repo, real_break, "npm test --silent", test_result=_result("npm test", 0, "ok"))}
    assert broken["build"]["status"] == "fail"


def test_evidence_lists_not_done_reasons_and_env_repairs(tmp_path):
    result = rl.ReviveResult(
        "NOTHING_TO_FIX",
        "no upgrade was applied",
        prd=_prd(["src.js"]),
        prd_items_done=[{"id": "u1", "kind": "upgrade", "files": ["package.json"], "lines": 2, "status": "applied"}],
        prd_items_not_done=[
            {"id": "u2", "kind": "upgrade", "reason": "target version not found"},
            {"id": "u3", "kind": "upgrade", "reason": "AssertionError expected green", "status": "reverted", "failed_check": "tests"},
        ],
        env_repairs=[{"package": "@rollup/rollup-win32-x64-msvc", "install_exit": 0, "test_exit": 0}],
        quality={
            "before": [{"id": "deps_freshness", "status": "info", "detail_tail": "4 outdated package(s)"}],
            "after": [
                {"id": "deps_freshness", "status": "info", "detail_tail": "1 outdated package(s)"},
                {"id": "tests", "status": "pass", "detail_tail": "exit 0"},
            ],
            "files_changed": [],
            "lines_changed": 0,
            "regressed": [],
            "verified_criteria": [],
        },
    )
    prd.write_reports(tmp_path, result, "o/r", "run-env")
    html = (tmp_path / "evidence.html").read_text(encoding="utf-8")
    assert "target version not found" in html
    assert "@rollup/rollup-win32-x64-msvc" in html
    assert "Environment repairs" in html
    assert 'data-status="info"' in html
    assert "#eff6ff" in html
    assert 'data-status="pass"' in html
    assert "#ecfdf3" in html
    body = html.split("<tbody>", 1)[1]
    assert body.index(">tests<") < body.index(">lint<") < body.index(">types<") < body.index(">build<") < body.index(">audit<")
    assert "1 file(s), 2 lines" in html
    assert "package.json" in html
    assert "Failing check" in html
    assert ">u3<" in html
    assert "NOTHING_TO_FIX" in html


def test_unresolved_or_unexplained_major_upgrades_are_dropped(tmp_path):
    repo = _node_repo(tmp_path, "module.exports = () => 2;\n")
    pkg = json.loads((repo / "package.json").read_text(encoding="utf-8"))
    pkg["dependencies"] = {"left-pad": "1.2.0"}
    (repo / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
    missing = {"id": "u-missing", "package": "left-pad", "version": "9.9.9", "reason": "pin a release that does not exist"}
    client = _Client([_prd(["src.js", "package.json"], upgrades=[missing]), {"approve": True, "issues": []}])

    def runner(cmd, cwd, timeout):
        if "npm view" in cmd:
            return _result(cmd, 1, "npm ERR! code E404")
        return _passing_after_fix(repo)(cmd, cwd, timeout)

    res = rl.revive(repo, client, _Trace(), "o/r", runner=runner)
    assert res.state == "NOTHING_TO_FIX"
    assert res.prd_items_not_done[0]["reason"] == "target version not found"
    assert not any("Item attempt" in prompt for _, prompt in client.prompts)

    major_repo = tmp_path / "major"
    major_repo.mkdir()
    repo = _node_repo(major_repo, "module.exports = () => 2;\n")
    pkg = json.loads((repo / "package.json").read_text(encoding="utf-8"))
    pkg["dependencies"] = {"left-pad": "1.2.0"}
    (repo / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
    major = {"id": "u-major", "package": "left-pad", "version": "2.0.0", "reason": "bump"}
    client = _Client([_prd(["src.js", "package.json"], upgrades=[major]), {"approve": True, "issues": []}])
    res = rl.revive(repo, client, _Trace(), "o/r", runner=_passing_after_fix(repo))
    assert res.prd_items_not_done[0]["reason"] == "major upgrade needs a stated reason"
    assert not any("Item attempt" in prompt for _, prompt in client.prompts)


def test_minor_upgrades_run_before_a_justified_major(tmp_path):
    repo = _node_repo(tmp_path, "module.exports = () => 2;\n")
    pkg = json.loads((repo / "package.json").read_text(encoding="utf-8"))
    pkg["dependencies"] = {"left-pad": "1.2.0", "debug": "4.3.4"}
    (repo / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
    major = {"id": "u-major", "package": "left-pad", "version": "2.0.0", "reason": "The public API must move to the 2.x line"}
    minor = {"id": "u-minor", "package": "debug", "version": "4.4.0", "reason": "pick up the patch"}
    client = _Client([_prd(["src.js", "package.json"], upgrades=[major, minor]), {"approve": True, "issues": []}])
    res = rl.revive(repo, client, _Trace(), "o/r", runner=_passing_after_fix(repo))
    body = (repo / "package.json").read_text(encoding="utf-8")
    assert [item["id"] for item in res.prd_items_done] == ["u-minor", "u-major"]
    assert all(item["status"] == "KEPT" for item in res.prd_items_done)
    assert '"debug": "4.4.0"' in body and '"left-pad": "2.0.0"' in body
    assert res.state == "RESURRECTED"
    assert not any("Item attempt" in prompt for _, prompt in client.prompts)


def test_time_budget_stops_new_items_and_still_checks(tmp_path):
    repo = _node_repo(tmp_path, "module.exports = () => 2;\n")
    pkg = json.loads((repo / "package.json").read_text(encoding="utf-8"))
    pkg["dependencies"] = {"left-pad": "1.2.0", "debug": "4.3.4"}
    (repo / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
    upgrades = [
        {"id": "u1", "package": "left-pad", "version": "1.3.0", "reason": "pin a maintained release"},
        {"id": "u2", "package": "debug", "version": "4.3.5", "reason": "pin the logger"},
    ]
    client = _Client([_prd(["src.js"], upgrades=upgrades), {"approve": True, "issues": []}])
    res = rl.revive(repo, client, _Trace(), "o/r", runner=_passing_after_fix(repo), time_budget_s=0)
    assert res.state == "NOTHING_TO_FIX"
    assert [item["reason"] for item in res.prd_items_not_done] == ["time budget", "time budget"]
    assert "time budget" in res.reason
    assert res.final is not None and res.final.code == 0
    assert not any("Item attempt" in prompt for _, prompt in client.prompts)


def test_environment_failure_after_a_green_baseline_is_not_resurrection_failed(tmp_path):
    repo = _node_repo(tmp_path, "module.exports = () => 2;\n")
    seen = {"tests": 0}

    def runner(cmd, cwd, timeout):
        if "npm view" in cmd:
            return _result(cmd, 1, "npm ERR! code E404")
        if cmd.startswith("npm install") or "outdated" in cmd:
            return _result(cmd, 0, "{}")
        if "npm audit" in cmd:
            return _result(cmd, 0, json.dumps({"metadata": {"vulnerabilities": {"high": 0, "critical": 0}}}))
        if cmd.startswith("npm test"):
            seen["tests"] += 1
            if seen["tests"] == 1:
                return _result(cmd, 0, "all tests pass")
            return _result(cmd, 1, "npm ERR! network request failed ECONNRESET")
        return _result(cmd, 0, "ok")

    pkg = json.loads((repo / "package.json").read_text(encoding="utf-8"))
    pkg["dependencies"] = {"left-pad": "1.2.0"}
    (repo / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
    upgrade = {"id": "u-missing", "package": "left-pad", "version": "9.9.9", "reason": "pin a release"}
    client = _Client([_prd(["src.js", "package.json"], upgrades=[upgrade]), {"approve": True, "issues": []}])
    res = rl.revive(repo, client, _Trace(), "o/r", runner=runner)
    assert res.state == "NOTHING_TO_FIX"
    assert "target version not found" in res.reason
    assert "tests" not in (res.quality.get("regressed") or [])
    assert res.baseline is not None and res.baseline.ok
    assert res.final is not None and not res.final.ok


def _platform_replay_runner(outdated_json: str, break_marker: str = ""):
    state = {"platform": False}
    calls: list[str] = []

    def runner(cmd, cwd, timeout):
        calls.append(cmd)
        if "npm view" in cmd:
            match = re.search(r"@(\d+\.\d+\.\d+)", cmd)
            return _result(cmd, 0, match.group(1) if match else "")
        if "outdated" in cmd:
            return _result(cmd, 1, outdated_json)
        if "npm audit" in cmd:
            return _result(cmd, 0, json.dumps({"metadata": {"vulnerabilities": {"high": 0, "critical": 0}}}))
        if cmd.startswith("npm install") and "--no-save" in cmd:
            state["platform"] = True
            return _result(cmd, 0, "added platform", 0.4)
        if cmd.startswith("npm install") or cmd.startswith("npm ci"):
            state["platform"] = False
            return _result(cmd, 0, "installed", 1.5)
        if not state["platform"]:
            return _result(cmd, 1, "Error: Cannot find module '@rollup/rollup-win32-x64-msvc'")
        text = (cwd / "package.json").read_text(encoding="utf-8")
        if break_marker and break_marker in text:
            return _result(cmd, 1, "AssertionError expected green")
        return _result(cmd, 0, "all tests pass")

    return runner, calls, state


def test_platform_replay_reverts_a_breaking_upgrade_without_calling_it_failed(tmp_path):
    """Baseline dies on a missing platform module, a reinstall drops it, and the upgrade is reverted."""
    repo = _node_repo(tmp_path, "module.exports = () => 2;\n")
    pkg = {"scripts": {"test": "node t.js"}, "dependencies": {"debug": "4.3.4"}}
    (repo / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
    outdated = json.dumps({"debug": {"current": "4.3.4", "wanted": "4.4.0", "latest": "4.4.0"}})
    runner, calls, state = _platform_replay_runner(outdated, break_marker='"debug": "4.4.0"')
    client = _Client([_prd(["src.js", "package.json"])])
    trace = _Trace()
    res = rl.revive(repo, client, trace, "o/r", runner=runner)
    assert res.state == "NOTHING_TO_FIX"
    assert res.state != "RESURRECTION_FAILED"
    assert res.prd_items_done == []
    assert res.prd_items_not_done[0]["id"] == "upgrade-debug"
    assert res.prd_items_not_done[0]["failed_check"] == "tests"
    assert res.prd_items_not_done[0]["status"] == "ROLLED_BACK"
    assert "4.3.4" in (repo / "package.json").read_text(encoding="utf-8")
    assert "4.4.0" not in (repo / "package.json").read_text(encoding="utf-8")
    assert state["platform"] is True
    assert res.final is not None and res.final.ok
    assert "tests" not in (res.quality.get("regressed") or [])
    before_tests = next(row for row in res.quality["before"] if row["id"] == "tests")
    assert before_tests["status"] == "pass"
    installs = [cmd for cmd in calls if cmd.startswith("npm install") or cmd.startswith("npm ci")]
    assert all("--prefer-offline" in cmd and "--no-audit" in cmd and "--no-fund" in cmd for cmd in installs)
    assert sum(1 for cmd in installs if "--no-save" not in cmd) >= 2
    install_events = [event for event in trace.events if event["action"] == "REVIVE_INSTALL"]
    assert install_events and all("seconds" in event["output_summary"] and "timeout" in event["output_summary"] for event in install_events)
    assert any(event["output_summary"]["seconds"] > 0 for event in install_events)
    assert sum(1 for event in trace.events if event["action"] == "REVIVE_ENV_REPAIR") >= 1
    repair_prompts = [prompt for step, prompt in client.prompts if "repair" in step]
    assert len(repair_prompts) == 2
    assert "Validation errors from the previous attempt" in repair_prompts[1]
    assert "model returned no usable patch" in repair_prompts[1]


def test_platform_replay_keeps_a_harmless_upgrade_after_env_repair(tmp_path):
    repo = _node_repo(tmp_path, "module.exports = () => 2;\n")
    pkg = {"scripts": {"test": "node t.js"}, "dependencies": {"left-pad": "1.2.0"}}
    (repo / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
    outdated = json.dumps({"left-pad": {"current": "1.2.0", "wanted": "1.3.0", "latest": "1.3.0"}})
    runner, calls, state = _platform_replay_runner(outdated)
    client = _Client([_prd(["src.js", "package.json"]), {"approve": True, "issues": []}])
    trace = _Trace()
    res = rl.revive(repo, client, trace, "o/r", runner=runner)
    assert res.state == "RESURRECTED"
    assert [item["id"] for item in res.prd_items_done] == ["upgrade-left-pad"]
    assert res.prd_items_done[0]["lines"] >= 1
    assert "1.3.0" in (repo / "package.json").read_text(encoding="utf-8")
    assert state["platform"] is True
    assert res.final is not None and res.final.ok
    assert "tests" not in (res.quality.get("regressed") or [])
    assert sum(1 for event in trace.events if event["action"] == "REVIVE_ENV_REPAIR") >= 1
    assert any("--prefer-offline" in cmd for cmd in calls if cmd.startswith("npm install"))
    assert any(event["action"] == "REVIVE_INSTALL" and event["output_summary"].get("seconds", 0) > 0 for event in trace.events)


def test_deterministic_upgrade_keeps_a_source_repair(tmp_path):
    repo = _node_repo(tmp_path, "module.exports = () => 2;\n")
    pkg = json.loads((repo / "package.json").read_text(encoding="utf-8"))
    pkg["dependencies"] = {"debug": "4.3.4"}
    (repo / "package.json").write_text(json.dumps(pkg), encoding="utf-8")

    def runner(cmd, cwd, timeout):
        if "outdated" in cmd:
            return _result(cmd, 1, json.dumps({"debug": {"current": "4.3.4", "wanted": "4.4.0", "latest": "4.4.0"}}))
        if cmd.startswith("npm install") or cmd.startswith("npm ci"):
            return _result(cmd, 0, "installed")
        if "npm audit" in cmd:
            return _result(cmd, 0, json.dumps({"metadata": {"vulnerabilities": {"high": 0, "critical": 0}}}))
        manifest = (cwd / "package.json").read_text(encoding="utf-8")
        source = (cwd / "src.js").read_text(encoding="utf-8")
        if '"debug": "4.4.0"' in manifest and "// repaired" not in source:
            return _result(cmd, 1, "AssertionError expected green")
        if "=> 2" in source and "BAD" not in source:
            return _result(cmd, 0, "all tests pass")
        return _result(cmd, 1, "AssertionError expected 2")

    repair = {"edits": [{"path": "src.js", "find": "=> 2;", "replace": "=> 2; // repaired"}]}
    client = _Client([_prd(["src.js", "package.json"]), repair, {"approve": True, "issues": []}])
    res = rl.revive(repo, client, _Trace(), "o/r", runner=runner)
    assert res.state == "RESURRECTED"
    assert res.prd_items_done[0]["id"] == "upgrade-debug"
    assert "src.js" in res.prd_items_done[0]["files"]
    assert '"debug": "4.4.0"' in (repo / "package.json").read_text(encoding="utf-8")
    assert "// repaired" in (repo / "src.js").read_text(encoding="utf-8")
    assert any("failed check tests" in prompt for _, prompt in client.prompts)


def test_unusable_patch_is_retried_once_with_the_validation_error(tmp_path):
    repo = _node_repo(tmp_path)
    client = _Client(
        [
            _prd(["src.js"]),
            {"edits": []},
            {"edits": [{"path": "src.js", "find": "=> 1", "replace": "=> 2"}]},
            {"approve": True, "issues": []},
        ]
    )
    res = rl.revive(repo, client, _Trace(), "o/r", runner=_passing_after_fix(repo))
    assert res.state == "RESURRECTED"
    assert "=> 2" in (repo / "src.js").read_text(encoding="utf-8")
    retries = [prompt for step, prompt in client.prompts if step == "revive_patch_1"]
    assert len(retries) == 2
    assert "Validation errors from the previous attempt" in retries[1]
    assert "non-empty" in retries[1]


def test_patch_touching_more_than_the_file_cap_is_rejected(tmp_path):
    edits = [{"path": f"f{i}.js", "content": "x\n"} for i in range(rl.MAX_FILES_PER_ITEM + 1)]
    changed, rejected = rl.apply_edits(tmp_path, edits)
    assert changed == []
    assert any("more than" in item for item in rejected)
    assert not (tmp_path / "f0.js").exists()
    assert rl.MAX_FILES_PER_ITEM == 5
    assert qg.MAX_DIFF_FILES == 8 and qg.MAX_DIFF_LINES == 300


def test_revert_of_source_reuses_node_modules(tmp_path):
    repo = _node_repo(tmp_path, "module.exports = () => 2;\n")
    calls: list[str] = []

    def runner(cmd, cwd, timeout):
        calls.append(cmd)
        if cmd.startswith("npm install") or cmd.startswith("npm ci"):
            return _result(cmd, 0, "installed")
        if "npm audit" in cmd:
            return _result(cmd, 0, json.dumps({"metadata": {"vulnerabilities": {"high": 0, "critical": 0}}}))
        text = (cwd / "src.js").read_text(encoding="utf-8")
        if "=> 2" in text and "BAD" not in text:
            return _result(cmd, 0, "all tests pass")
        return _result(cmd, 1, "AssertionError tests failed")

    refactor = {"id": "r1", "title": "Tighten the export", "files": ["src.js"], "reason": "name the constant"}
    bad = {"edits": [{"path": "src.js", "find": "=> 2", "replace": "=> 2 /* BAD */"}]}
    client = _Client([_prd(["src.js"], refactors=[refactor]), bad, bad, bad])
    trace = _Trace()
    res = rl.revive(repo, client, trace, "o/r", runner=runner)
    installs = [cmd for cmd in calls if cmd.startswith("npm install") or cmd.startswith("npm ci")]
    assert installs == [installs[0]]
    assert "--prefer-offline" in installs[0] and "--no-audit" in installs[0] and "--no-fund" in installs[0]
    assert res.state == "NOTHING_TO_FIX"
    assert (repo / "src.js").read_text(encoding="utf-8") == "module.exports = () => 2;\n"
    assert not any(event["action"] == "REVIVE_INSTALL" and event["output_summary"].get("skipped") for event in trace.events)
    assert res.prd_items_not_done[0]["failed_check"] == "tests"


def test_deterministic_outdated_lists_skip_majors(tmp_path):
    repo = _node_repo(tmp_path, "module.exports = () => 2;\n")
    pkg = json.loads((repo / "package.json").read_text(encoding="utf-8"))
    pkg["dependencies"] = {"left-pad": "1.2.0"}
    (repo / "package.json").write_text(json.dumps(pkg), encoding="utf-8")

    def runner(cmd, cwd, timeout):
        assert cmd == "npm outdated --json"
        return _result(
            cmd,
            1,
            json.dumps(
                {
                    "left-pad": {"current": "1.2.0", "wanted": "1.3.0", "latest": "2.0.0"},
                    "debug": {"current": "4.0.0", "wanted": "5.0.0", "latest": "5.0.0"},
                }
            ),
        )

    items = rl.deterministic_upgrade_items(repo, runner, 30)
    assert [item["package"] for item in items] == ["left-pad"]
    assert items[0]["version"] == "1.3.0" and items[0]["source"] == "deterministic"
    assert rl.deterministic_upgrade_items(repo, lambda cmd, cwd, timeout: _result(cmd, 1, "not json"), 10) == []

    py = tmp_path / "py"
    py.mkdir()
    (py / "requirements.txt").write_text("requests==2.31.0\n", encoding="utf-8")

    def py_runner(cmd, cwd, timeout):
        assert "pip list --outdated" in cmd
        return _result(cmd, 0, json.dumps([{"name": "requests", "version": "2.31.0", "latest_version": "2.32.3"}]))

    pip_items = rl.deterministic_upgrade_items(py, py_runner, 30)
    assert pip_items[0]["package"] == "requests"
    assert pip_items[0]["version"] == "2.32.3"
    assert "pip" in pip_items[0]["reason"]
    changed, rejected = rl.bump_declared_version(py, "requests", "2.31.0", "2.32.3")
    assert changed == ["requirements.txt"] and rejected == []
    assert (py / "requirements.txt").read_text(encoding="utf-8") == "requests==2.32.3\n"


def test_docs_item_is_written_by_the_model(tmp_path):
    repo = _node_repo(tmp_path, "module.exports = () => 2;\n")
    (repo / "CONTRIBUTING.md").write_text("# contributing\n", encoding="utf-8")
    draft = _prd(["src.js", "CONTRIBUTING.md"])
    draft["docs_items"] = [
        {
            "id": "d1",
            "title": "Note the test command",
            "files": ["CONTRIBUTING.md"],
            "acceptance_command": "npm test --silent",
            "expected": "exit code 0",
        }
    ]
    edit = {"edits": [{"path": "CONTRIBUTING.md", "content": "# contributing\n\nRun npm test.\n"}]}
    client = _Client([draft, edit, {"approve": True, "issues": []}])
    res = rl.revive(repo, client, _Trace(), "o/r", runner=_passing_after_fix(repo))
    # A docs edit on an already-green baseline is kept, but it is not a resurrection:
    # nothing was broken or outdated.
    assert res.state == "NEEDS_REVIEW"
    assert res.verification["resurrection_allowed"] is False
    assert "baseline was not broken or outdated" in res.reason
    assert res.prd_items_done[0]["id"] == "d1"
    assert (repo / "CONTRIBUTING.md").read_text(encoding="utf-8").endswith("Run npm test.\n")
    assert any("docs" in prompt and "Item attempt 1" in prompt for _, prompt in client.prompts)


def test_unchanged_manifest_bytes_are_not_a_reinstall(tmp_path):
    repo = _node_repo(tmp_path, "module.exports = () => 2;\n")
    original = (repo / "package.json").read_bytes()
    assert rl.manifest_content_changed(repo, {"package.json": original}) is False
    assert rl.manifest_content_changed(repo, {"src.js": b"other"}) is False
    (repo / "package.json").write_text(original.decode("utf-8") + "\n", encoding="utf-8")
    assert rl.manifest_content_changed(repo, {"package.json": original}) is True


def shlex_quote(value: str) -> str:
    import os
    import shlex
    import subprocess

    if os.name == 'nt':
        return subprocess.list2cmdline([value])
    return shlex.quote(value)
