"""PRD checks, exact rollback, and an independent verifier. The model is scripted. Pytest on the fixture is real."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from app import revive_loop as rl
from app import revive_prd as prd
from app.revive_verify import verify_revival, weakened_test_diff


class _Trace:
    def __init__(self):
        self.events = []

    def event(self, **kw):
        self.events.append(kw)


class _Client:
    def __init__(self, objs):
        self.objs = list(objs)
        self.prompts = []

    def complete_json(self, prompt, system_prompt="", repo_id="", step="", attempts=3, validate=None):
        self.prompts.append((step, prompt))
        obj = self.objs.pop(0) if self.objs else None
        if obj is not None and validate is not None and not validate(obj):
            obj = None
        return obj, "", "m", "m"


def _quote(value: str) -> str:
    import shlex

    if sys.platform == "win32":
        return subprocess.list2cmdline([value])
    return shlex.quote(value)


def _pytest_cmd() -> str:
    return f"{_quote(sys.executable)} -m pytest -q -p no:cacheprovider --noconftest --rootdir . -c pytest.ini"


def _concrete(scope, **extra):
    body = {
        "problem": "The repository test command fails and a declared dependency is behind.",
        "target_user": "Maintainers of this repository.",
        "scope": list(scope),
        "non_goals": ["Do not edit tests.", "Do not add features."],
        "upgrade_items": [],
        "refactor_items": [],
        "docs_items": [],
        "acceptance_criteria": [
            {"id": "ac-tests", "statement": "The test command exits 0.", "check_id": "tests"},
            {"id": "ac-diff", "statement": "The diff touches at most 8 files and does not edit tests.", "check_id": "diff_scope"},
            {"id": "ac-audit", "statement": "High and critical audit findings stay at 0.", "check_id": "audit"},
        ],
        "risks": ["A dependency bump can change runtime behaviour."],
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
    body.update(extra)
    return body


def test_prd_rejects_missing_acceptance_unknown_names_and_readme_only(tmp_path):
    (tmp_path / "src.js").write_text("module.exports = () => 1;\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("Adds numbers for the demo project.\n", encoding="utf-8")
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"left-pad": "1.2.0"}}), encoding="utf-8")

    missing_command = _concrete(["src.js"])
    missing_command["original_intent"] = 'The README says "Adds numbers for the demo project."'
    missing_command["work_items"][0]["acceptance_command"] = ""
    errors = prd.validate_prd(missing_command, tmp_path)
    assert any("no acceptance command" in error for error in errors)

    unknown_package = _concrete(
        ["src.js"],
        upgrade_items=[{"id": "u1", "package": "not-a-real-dep", "version": "9.9.9", "acceptance_command": "npm test --silent", "expected": "exit code 0"}],
    )
    unknown_package["original_intent"] = 'The README says "Adds numbers for the demo project."'
    errors = prd.validate_prd(unknown_package, tmp_path)
    assert any("not-a-real-dep" in error and "not in the repo" in error for error in errors)

    unknown_api = _concrete(
        ["src.js"],
        refactor_items=[
            {
                "id": "r1",
                "title": "Call the helper",
                "files": ["src.js"],
                "api": "functionThatDoesNotExist",
                "acceptance_command": "npm test --silent",
                "expected": "exit code 0",
            }
        ],
    )
    unknown_api["original_intent"] = 'The README says "Adds numbers for the demo project."'
    errors = prd.validate_prd(unknown_api, tmp_path)
    assert any("functionThatDoesNotExist" in error and "not in the repo" in error for error in errors)

    readme_only = _concrete(["src.js"])
    readme_only["original_intent"] = 'The README says "Adds numbers for the demo project."'
    readme_only["problem"] = "Adds numbers for the demo project."
    readme_only["diagnosis"] = "Adds numbers for the demo project."
    errors = prd.validate_prd(readme_only, tmp_path)
    assert any("restates the README only" in error for error in errors)

    good = _concrete(
        ["src.js"],
        upgrade_items=[{"id": "u1", "package": "left-pad", "version": "1.3.0", "acceptance_command": "npm test --silent", "expected": "exit code 0"}],
    )
    good["original_intent"] = 'The README says "Adds numbers for the demo project."'
    assert prd.validate_prd(good, tmp_path) == []


def test_minimal_prd_quotes_the_readme_and_cites_the_baseline(tmp_path):
    (tmp_path / "README.md").write_text("# calc\nAdds numbers.\n", encoding="utf-8")
    (tmp_path / "src.js").write_text("x\n", encoding="utf-8")
    baseline = rl.CmdResult("npm test --silent", 1, "AssertionError expected 2")
    drafted = prd.minimal_prd(tmp_path, "o/r", baseline, "model draft failed")
    assert prd.validate_prd({k: v for k, v in drafted.items() if k not in {"degraded", "degraded_reason"}}, tmp_path) == []
    assert "Adds numbers." in drafted["original_intent"]
    assert "exited 1" in drafted["diagnosis"]
    assert drafted["work_items"][0]["acceptance_command"] == "npm test --silent"


def test_reports_write_prd_md_and_a_verification_section(tmp_path):
    (tmp_path / "src.js").write_text("x\n", encoding="utf-8")
    result = rl.ReviveResult(
        "RESURRECTED",
        "checks passed locally",
        prd=_concrete(["src.js"]),
        verification={
            "resurrection_allowed": True,
            "blocked_reason": "",
            "checks": [
                {"id": "final_tests", "status": "PASS", "detail": "exit 0"},
                {"id": "weakened_tests", "status": "FAIL", "detail": "test file removed: t.js"},
                {"id": "build", "status": "UNVERIFIED", "detail": "no build command was detected"},
            ],
        },
    )
    prd.write_reports(tmp_path, result, "o/r", "run-prd")
    markdown = (tmp_path / "prd.md").read_text(encoding="utf-8")
    assert markdown == (tmp_path / "PRD.md").read_text(encoding="utf-8")
    assert "## Original intent" in markdown
    assert "## Diagnosis" in markdown
    assert "## Non-goals" in markdown
    assert "## Risks" in markdown
    assert "npm test --silent" in markdown
    html = (tmp_path / "evidence.html").read_text(encoding="utf-8")
    assert '<a href="prd.md">prd.md</a>' in html
    assert ">PASS<" in html and ">FAIL<" in html and ">UNVERIFIED<" in html
    assert "http://" not in html and "https://" not in html
    payload = json.loads((tmp_path / "verification.json").read_text(encoding="utf-8"))
    assert payload["resurrection_allowed"] is True
    assert {row["status"] for row in payload["checks"]} == {"PASS", "FAIL", "UNVERIFIED"}


def test_work_items_run_repairs_then_upgrades_then_docs_then_refactors():
    drafted = {
        "refactor_items": [{"id": "r1", "title": "optional cleanup", "files": ["src.js"]}],
        "docs_items": [{"id": "d1", "title": "Note the command", "files": ["README.md"]}],
        "upgrade_items": [{"id": "u1", "package": "left-pad", "version": "1.3.0", "reason": "security patch for a vulnerability"}],
    }
    selected, skipped = rl._work_items(drafted)
    assert skipped == []
    assert [kind for kind, _ in selected] == ["upgrade", "docs", "refactor"]
    assert rl._item_rank("repair", {"title": "fix the baseline"}) < rl._item_rank("upgrade", {})
    assert rl._item_rank("upgrade", {}) < rl._item_rank("docs", {})
    assert rl._item_rank("docs", {}) < rl._item_rank("refactor", {"title": "optional cleanup"})


def test_restore_worktree_puts_back_files_the_edit_list_did_not_name(tmp_path):
    (tmp_path / "src.js").write_text("one\n", encoding="utf-8")
    snap = rl.snapshot_worktree(tmp_path)
    (tmp_path / "src.js").write_text("two\n", encoding="utf-8")
    extra = tmp_path / "notes" / "scratch.txt"
    extra.parent.mkdir()
    extra.write_text("leftover\n", encoding="utf-8")
    rl.restore_worktree(tmp_path, snap)
    assert (tmp_path / "src.js").read_text(encoding="utf-8") == "one\n"
    assert not extra.exists()


def test_failed_item_rolls_the_whole_worktree_back(tmp_path, monkeypatch):
    repo = tmp_path / "node"
    repo.mkdir()
    (repo / "package.json").write_text(json.dumps({"scripts": {"test": "node t.js"}}), encoding="utf-8")
    (repo / "src.js").write_text("module.exports = () => 2;\n", encoding="utf-8")
    (repo / "t.js").write_text("x", encoding="utf-8")
    original = (repo / "src.js").read_text(encoding="utf-8")
    real_apply = rl.apply_edits

    def apply_and_drop_a_file(target, edits):
        changed, rejected = real_apply(target, edits)
        if changed:
            extra = target / "notes" / "scratch.txt"
            extra.parent.mkdir(parents=True, exist_ok=True)
            extra.write_text("leftover\n", encoding="utf-8")
        return changed, rejected

    monkeypatch.setattr(rl, "apply_edits", apply_and_drop_a_file)

    def runner(cmd, cwd, timeout):
        if "outdated" in cmd:
            return rl.CmdResult(cmd, 0, "{}")
        if "npm audit" in cmd or cmd.startswith("npm install"):
            if "npm audit" in cmd:
                return rl.CmdResult(cmd, 0, json.dumps({"metadata": {"vulnerabilities": {"high": 0, "critical": 0}}}))
            return rl.CmdResult(cmd, 0, "installed")
        text = (cwd / "src.js").read_text(encoding="utf-8")
        if "BAD" in text:
            return rl.CmdResult(cmd, 1, "lint: unexpected BAD token")
        if "=> 2" in text:
            return rl.CmdResult(cmd, 0, "all tests pass")
        return rl.CmdResult(cmd, 1, "AssertionError tests failed")

    drafted = _concrete(
        ["src.js"],
        refactor_items=[{"id": "r1", "title": "Tighten the export", "files": ["src.js"], "acceptance_command": "npm test --silent", "expected": "exit code 0"}],
    )
    bad = {"edits": [{"path": "src.js", "find": "=> 2", "replace": "=> 2 /* BAD */"}]}
    res = rl.revive(repo, _Client([drafted, bad, bad, bad]), _Trace(), "o/r", runner=runner)
    assert res.state == "NOTHING_TO_FIX"
    assert (repo / "src.js").read_text(encoding="utf-8") == original
    assert not (repo / "notes" / "scratch.txt").exists()
    rolled = res.prd_items_not_done[0]
    assert rolled["id"] == "r1"
    assert rolled["status"] == "ROLLED_BACK"
    assert rolled["diff_size"] == {"files": 0, "lines": 0}
    assert rolled["acceptance"]["ok"] is False


def test_blocked_gate_verdicts_never_become_resurrected(tmp_path):
    for verdict in ("ALREADY_EXISTS", "NO_GO", "UNJUDGED"):
        res = rl.revive(tmp_path / verdict, _Client([{"problem": "would have fixed it"}]), _Trace(), "o/r", gate_verdict=verdict)
        assert res.state == "NOT_ATTEMPTED_GATE1"
        assert res.state != "RESURRECTED"


def _widget(root: Path) -> tuple[Path, str]:
    repo = root / "widget"
    repo.mkdir()
    (repo / "README.md").write_text("# widget\nPrints a greeting for the command line.\n", encoding="utf-8")
    (repo / "requirements.txt").write_text("requests==2.25.0\n", encoding="utf-8")
    (repo / "pytest.ini").write_text("[pytest]\ntestpaths = .\n", encoding="utf-8")
    (repo / "widget.py").write_text("def greet():\n    return 'bye'\n", encoding="utf-8")
    (repo / "test_widget.py").write_text("from widget import greet\n\ndef test_greet():\n    assert greet() == 'hello'\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    return repo, _pytest_cmd()


def _widget_prd(command: str) -> dict:
    return {
        "problem": "greet() returns the wrong string and requests is pinned to an old release.",
        "original_intent": 'The README says "Prints a greeting for the command line."',
        "diagnosis": "Baseline pytest exits 1 with AssertionError: greet() == 'hello'. requirements.txt pins requests==2.25.0, which is outdated.",
        "target_user": "People who run the widget CLI.",
        "scope": ["widget.py", "requirements.txt", "README.md"],
        "non_goals": ["Do not delete or skip tests.", "Do not add a new feature."],
        "risks": ["The requests bump can change HTTP behaviour for later callers."],
        "work_items": [
            {
                "id": "fix-baseline",
                "kind": "repair",
                "title": "Make greet() return hello",
                "acceptance_command": command,
                "expected": "exit code 0",
                "files": ["widget.py"],
            },
            {
                "id": "upgrade-requests",
                "kind": "upgrade",
                "title": "Bump requests to 2.31.0",
                "package": "requests",
                "acceptance_command": command,
                "expected": "exit code 0",
                "files": ["requirements.txt"],
            },
        ],
        "upgrade_items": [
            {
                "id": "upgrade-requests",
                "package": "requests",
                "version": "2.31.0",
                "from_version": "2.25.0",
                "reason": "move off the outdated 2.25.0 pin",
                "acceptance_command": command,
                "expected": "exit code 0",
            }
        ],
        "refactor_items": [],
        "docs_items": [
            {
                "id": "docs-readme",
                "title": "Document greet()",
                "files": ["README.md"],
                "acceptance_command": command,
                "expected": "exit code 0",
            }
        ],
        "acceptance_criteria": [
            {"id": "ac-tests", "statement": "The test command exits 0.", "check_id": "tests"},
            {"id": "ac-diff", "statement": "The diff touches at most 8 files and does not edit tests.", "check_id": "diff_scope"},
            {"id": "ac-audit", "statement": "High and critical audit findings stay at 0.", "check_id": "audit"},
        ],
    }


def _offline_index(real):
    def runner(cmd, cwd, timeout):
        if "pip list --outdated" in cmd:
            return rl.CmdResult(cmd, 0, "[]")
        if "pip index versions" in cmd:
            return rl.CmdResult(cmd, 0, "2.31.0")
        if "pip-audit" in cmd:
            return rl.CmdResult(cmd, 0, "[]")
        if "pip install" in cmd:
            return rl.CmdResult(cmd, 0, "installed")
        return real(cmd, cwd, timeout)

    return runner


def test_fixture_prd_build_verify_and_report(tmp_path, monkeypatch):
    repo, command = _widget(tmp_path)
    drafted = _widget_prd(command)
    assert prd.validate_prd(drafted, repo) == []
    client = _Client(
        [
            drafted,
            {"rationale": "the greeting is hello", "edits": [{"path": "widget.py", "find": "return 'bye'", "replace": "return 'hello'"}]},
            {"rationale": "name the function", "edits": [{"path": "README.md", "find": "Prints a greeting for the command line.", "replace": "Prints a greeting for the command line.\n`greet()` returns hello."}]},
            {"approve": True, "issues": []},
        ]
    )

    def detect(_repo):
        return None, command

    monkeypatch.setattr(rl, "detect_commands", detect)
    res = rl.revive(repo, client, _Trace(), "o/widget", runner=_offline_index(rl.shell_runner), test_timeout=60, commit_each_item=True)
    assert res.state == "RESURRECTED"
    assert [item["id"] for item in res.prd_items_done] == ["fix-baseline", "upgrade-requests", "docs-readme"]
    assert {item["status"] for item in res.prd_items_done} == {"KEPT"}
    for item in res.prd_items_done:
        assert item["diff_size"]["files"] >= 1
        assert item["acceptance"]["ok"] is True
    assert "return 'hello'" in (repo / "widget.py").read_text(encoding="utf-8")
    assert "requests==2.31.0" in (repo / "requirements.txt").read_text(encoding="utf-8")
    assert res.verification["resurrection_allowed"] is True
    assert res.verification["blocked_reason"] == ""
    statuses = {row["id"]: row["status"] for row in res.verification["checks"]}
    assert statuses["baseline_tests"] == "FAIL"
    assert statuses["final_tests"] == "PASS"
    assert statuses["weakened_tests"] == "PASS"
    assert statuses["acceptance:fix-baseline"] == "PASS"
    assert statuses["upgrade:requests"] == "PASS"
    log = subprocess.run(["git", "log", "--format=%s"], cwd=repo, check=True, capture_output=True, text=True)
    assert "revive: fix-baseline" in log.stdout
    assert "revive: upgrade-requests" in log.stdout
    assert "revive: docs-readme" in log.stdout
    prd.write_reports(tmp_path / "reports", res, "o/widget", "fixture")
    report_dir = tmp_path / "reports"
    assert (report_dir / "prd.md").is_file()
    assert (report_dir / "verification.json").is_file()
    html = (report_dir / "evidence.html").read_text(encoding="utf-8")
    assert '<a href="prd.md">prd.md</a>' in html
    assert ">PASS<" in html and ">FAIL<" in html
    markdown = (report_dir / "prd.md").read_text(encoding="utf-8")
    assert "Prints a greeting for the command line." in markdown
    assert "AssertionError" in markdown
    assert "Do not delete or skip tests." in markdown
    saved = json.loads((report_dir / "verification.json").read_text(encoding="utf-8"))
    assert saved["resurrection_allowed"] is True


def test_builder_cannot_delete_a_test_to_go_green(tmp_path, monkeypatch):
    repo, command = _widget(tmp_path)
    drafted = _widget_prd(command)
    original_test = (repo / "test_widget.py").read_text(encoding="utf-8")
    client = _Client(
        [
            drafted,
            {"rationale": "drop the failing assertion", "edits": [{"path": "test_widget.py", "find": "assert greet() == 'hello'", "replace": "assert True"}]},
        ]
    )

    def detect(_repo):
        return None, command

    monkeypatch.setattr(rl, "detect_commands", detect)
    res = rl.revive(repo, client, _Trace(), "o/widget", runner=_offline_index(rl.shell_runner), test_timeout=60, max_iterations=1)
    assert res.state != "RESURRECTED"
    assert (repo / "test_widget.py").read_text(encoding="utf-8") == original_test
    assert "return 'bye'" in (repo / "widget.py").read_text(encoding="utf-8")
    assert res.verification["resurrection_allowed"] is False


def test_verifier_rejects_a_deleted_or_loosened_test_even_when_the_run_is_green(tmp_path):
    repo, command = _widget(tmp_path)
    original = rl.snapshot_worktree(repo)
    loosened = tmp_path / "loosened"
    loosened.mkdir()
    for rel, data in original.items():
        target = loosened / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    test_path = loosened / "test_widget.py"
    test_path.write_text(test_path.read_text(encoding="utf-8").replace("assert greet() == 'hello'", "assert True"), encoding="utf-8")
    proc = subprocess.run(command, cwd=loosened, shell=True, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    patched = rl.snapshot_worktree(loosened)
    reasons = weakened_test_diff(original, patched)
    assert any("loosened" in reason or "removed" in reason for reason in reasons)
    kept = [{"id": "fix-baseline", "status": "KEPT", "kind": "repair", "acceptance_command": command, "expected": "exit code 0"}]
    verdict = verify_revival(original, patched, rl.shell_runner, test_cmd=command, prd={"work_items": []}, items=kept, timeout=60)
    by_id = {row["id"]: row for row in verdict["checks"]}
    assert by_id["weakened_tests"]["status"] == "FAIL"
    assert by_id["final_tests"]["status"] == "PASS"
    assert verdict["resurrection_allowed"] is False
    assert "weakened" in verdict["blocked_reason"]

    deleted = dict(original)
    deleted.pop("test_widget.py")

    def dishonest(cmd, cwd, timeout):
        if (cwd / "test_widget.py").is_file():
            return rl.CmdResult(cmd, 1, "1 failed")
        return rl.CmdResult(cmd, 0, "1 passed")

    verdict = verify_revival(original, deleted, dishonest, test_cmd=command, prd={"work_items": []}, items=kept, timeout=30)
    by_id = {row["id"]: row for row in verdict["checks"]}
    assert by_id["weakened_tests"]["status"] == "FAIL"
    assert "test file removed" in by_id["weakened_tests"]["detail"]
    assert by_id["final_tests"]["status"] == "PASS"
    assert verdict["resurrection_allowed"] is False
