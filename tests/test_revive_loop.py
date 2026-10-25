from __future__ import annotations

import json
from pathlib import Path

from app import revive_loop as rl


class _Trace:
    def __init__(self):
        self.events = []

    def event(self, **kw):
        self.events.append(kw)


class _Client:
    """complete_json stand-in that replays scripted objects."""

    def __init__(self, objs):
        self.objs = list(objs)
        self.prompts = []

    def complete_json(self, prompt, system_prompt="", repo_id="", step="", attempts=3, validate=None):
        self.prompts.append((step, prompt))
        obj = self.objs.pop(0) if self.objs else None
        if obj is not None and validate is not None and not validate(obj):
            obj = None
        return obj, "", "m", "m"


def _make_repo(tmp_path: Path) -> Path:
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "node t.js"}}), encoding="utf-8")
    (tmp_path / "src.js").write_text("module.exports = () => 1;\n", encoding="utf-8")
    (tmp_path / "t.js").write_text("x", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "a.test.js").write_text("// test\n", encoding="utf-8")
    return tmp_path


def _valid_prd(scope=("src.js",), upgrades=None, refactors=None, checks=("tests", "diff_scope", "audit")):
    criteria = []
    statements = {
        "tests": "The test command exits 0.",
        "diff_scope": "The diff touches at most 8 files and does not edit tests.",
        "audit": "High and critical audit findings stay at 0.",
        "lint": "The linter exits 0.",
        "types": "The type checker exits 0.",
        "build": "The build command exits 0.",
        "smoke": "The smoke command exits 0.",
    }
    for check_id in checks:
        criteria.append({"id": f"ac-{check_id}", "statement": statements[check_id], "check_id": check_id})
    while len(criteria) < 3:
        criteria.append({"id": f"ac-extra-{len(criteria)}", "statement": "The diff touches at most 8 files and does not edit tests.", "check_id": "diff_scope"})
    return {
        "problem": "src.js returns the wrong value for the repository test command.",
        "target_user": "Maintainers running the repository test command.",
        "scope": list(scope),
        "non_goals": ["Do not edit tests."],
        "upgrade_items": list(upgrades or []),
        "refactor_items": list(refactors or []),
        "docs_items": [],
        "acceptance_criteria": criteria,
        "risks": ["The assertion may point at the wrong file."],
    }


def _runner_factory(repo: Path):
    calls = []

    def runner(cmd, cwd, timeout):
        calls.append(cmd)
        if cmd.startswith("npm ci") or cmd.startswith("npm install"):
            return rl.CmdResult(cmd, 0, "installed")
        text = (cwd / "src.js").read_text(encoding="utf-8")
        if "=> 2" in text:
            return rl.CmdResult(cmd, 0, "all tests pass")
        return rl.CmdResult(cmd, 1, "AssertionError in src.js: expected 2")

    return runner, calls


def test_detect_commands_reads_the_manifest(tmp_path):
    repo = _make_repo(tmp_path)
    install, test = rl.detect_commands(repo)
    assert test == "npm test --silent"
    assert install.startswith("npm install")
    assert "--prefer-offline" in install and "--no-audit" in install and "--no-fund" in install


def test_no_test_command_is_needs_review(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": 'echo "Error: no test specified" && exit 1'}}), encoding="utf-8")
    res = rl.revive(tmp_path, _Client([]), _Trace(), "o/r")
    assert res.state == "NEEDS_REVIEW"


def test_revive_succeeds_only_when_the_real_test_command_passes(tmp_path):
    repo = _make_repo(tmp_path)
    runner, _ = _runner_factory(repo)
    client = _Client(
        [
            _valid_prd(),
            {"rationale": "r", "edits": [{"path": "src.js", "find": "=> 1", "replace": "=> 2"}]},
        ]
    )
    trace = _Trace()
    res = rl.revive(repo, client, trace, "o/r", gate_verdict="CANDIDATE", runner=runner)
    assert res.state == "RESURRECTED"
    assert res.files_changed == ["src.js"]
    assert [e["action"] for e in trace.events if e["action"].startswith("REVIVE_VALIDATE")] == ["REVIVE_VALIDATE"]


def test_gate_one_refusal_blocks_revival_entirely(tmp_path):
    repo = _make_repo(tmp_path)
    runner, calls = _runner_factory(repo)
    client = _Client([])
    trace = _Trace()
    for verdict in ("ALREADY_EXISTS", "NO_GO", "NEEDS_REVIEW", "UNJUDGED"):
        res = rl.revive(repo, client, trace, "o/r", gate_verdict=verdict, runner=runner)
        assert res.state == "NOT_ATTEMPTED_GATE1"
        assert res.files_changed == []
    assert not calls  # nothing was installed, tested or patched
    assert [e["action"] for e in trace.events] == ["REVIVE_BLOCKED_BY_GATE1"] * 4


def test_failure_goes_back_to_the_builder_with_real_output(tmp_path):
    repo = _make_repo(tmp_path)
    runner, _ = _runner_factory(repo)
    client = _Client(
        [
            _valid_prd(),
            {"edits": [{"path": "src.js", "find": "=> 1", "replace": "=> 3"}]},
            {"edits": [{"path": "src.js", "find": "=> 3", "replace": "=> 2"}]},
        ]
    )
    res = rl.revive(repo, client, _Trace(), "o/r", runner=runner)
    assert res.state == "RESURRECTED"
    assert [i["accepted"] for i in res.iterations] == [False, True]
    second_prompt = [p for s, p in client.prompts if s == "revive_patch_2"][0]
    assert "AssertionError in src.js" in second_prompt


def test_builder_cannot_edit_tests_or_escape_the_repo(tmp_path):
    repo = _make_repo(tmp_path)
    changed, rejected = rl.apply_edits(
        repo,
        [
            {"path": "tests/a.test.js", "find": "// test", "replace": "// skip"},
            {"path": "../evil.js", "content": "x"},
            {"path": "package-lock.json", "content": "{}"},
            {"path": "src.js", "find": "nope", "replace": "x"},
        ],
    )
    assert changed == []
    assert len(rejected) == 4
    assert (repo / "tests" / "a.test.js").read_text(encoding="utf-8") == "// test\n"


def test_failure_after_all_iterations_is_resurrection_failed(tmp_path):
    repo = _make_repo(tmp_path)
    runner, _ = _runner_factory(repo)
    client = _Client([_valid_prd()])
    res = rl.revive(repo, client, _Trace(), "o/r", max_iterations=2, runner=runner)
    assert res.state == "RESURRECTION_FAILED"
    assert res.final is not None and res.final.code == 1


def test_passing_baseline_is_not_called_a_revival(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "src.js").write_text("module.exports = () => 2;\n", encoding="utf-8")
    runner, _ = _runner_factory(repo)
    res = rl.revive(repo, _Client([]), _Trace(), "o/r", runner=runner)
    assert res.state == "NOTHING_TO_FIX"

def test_missing_platform_binary_triggers_one_clean_reinstall_before_blaming_code(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "node_modules").mkdir()
    calls = []

    def runner(cmd, cwd, timeout):
        calls.append(cmd)
        if cmd.startswith("npm install") and "--no-audit" in cmd and len([c for c in calls if c.startswith("npm install")]) >= 2:
            return rl.CmdResult(cmd, 0, "ok")
        if cmd.startswith("npm test") and any("npm install" in c for c in calls[1:]):
            return rl.CmdResult(cmd, 0, "pass")
        if cmd.startswith("npm test"):
            return rl.CmdResult(cmd, 1, "Error: Cannot find module '@rollup/rollup-win32-x64-msvc'")
        return rl.CmdResult(cmd, 0, "installed")

    trace = _Trace()
    res = rl.revive(repo, _Client([]), trace, "o/r", runner=runner)
    assert res.state == "NOTHING_TO_FIX"
    assert any(e["action"] == "REVIVE_ENV_REPAIR" for e in trace.events)


def test_decode_output_handles_utf8_empty_and_non_utf8_bytes():
    assert rl.decode_output(None) == ""
    assert rl.decode_output("錯誤 error".encode("utf-8")) == "錯誤 error"
    garbled = "錯誤".encode("cp950")
    out = rl.decode_output(garbled)
    assert isinstance(out, str) and out  # never raises; on Windows it is decoded with the console code page


def test_reinstall_after_a_manifest_edit_never_uses_npm_ci():
    assert rl.reinstall_command("npm ci --no-audit --no-fund") == "npm install --no-audit --no-fund"
    assert rl.reinstall_command("npm install --no-audit") == "npm install --no-audit"
    assert rl.reinstall_command("python -m pip install -q -e .") == "python -m pip install -q -e ."
    assert rl.reinstall_command(f"npm ci {rl.NPM_FAST_FLAGS}") == f"npm install {rl.NPM_FAST_FLAGS}"


def test_missing_platform_module_is_read_from_the_error():
    out = "Error: Cannot find module '@rollup/rollup-win32-x64-msvc'. npm has a bug"
    assert rl.missing_platform_module(out) == "@rollup/rollup-win32-x64-msvc"
    assert rl.missing_platform_module("Cannot find module 'left-pad'") is None
    assert rl.missing_platform_module("") is None


def test_ensure_env_installs_the_missing_platform_package_and_reruns(tmp_path):
    repo = _make_repo(tmp_path)
    calls = []

    def runner(cmd, cwd, timeout):
        calls.append(cmd)
        if cmd.startswith("npm install"):
            return rl.CmdResult(cmd, 0, "added")
        if any(call.startswith("npm install") for call in calls):
            return rl.CmdResult(cmd, 0, "pass")
        return rl.CmdResult(cmd, 1, "Error: Cannot find module '@rollup/rollup-win32-x64-msvc'")

    trace = _Trace()
    repairs = []
    result = rl.ensure_env(repo, runner, "npm test --silent", trace=trace, repairs=repairs)
    assert result.ok
    assert any("--no-save" in call and "@rollup/rollup-win32-x64-msvc" in call for call in calls)
    assert any("--prefer-offline" in call and "--no-audit" in call and "--no-fund" in call for call in calls)
    assert repairs[0]["package"] == "@rollup/rollup-win32-x64-msvc"
    assert [event["action"] for event in trace.events] == ["REVIVE_ENV_REPAIR"]
    assert rl.is_environment_failure("Error: Cannot find module '@rollup/rollup-win32-x64-msvc'")
    assert rl.is_environment_failure("npm ERR! network request failed ECONNRESET")
    assert rl.is_environment_failure("spawn npm ENOENT")
    assert rl.is_environment_failure("'npm' is not recognized as an internal or external command")
    assert not rl.is_environment_failure("Error: ENOENT: no such file or directory, open 'src.js'")
    assert not rl.is_environment_failure("AssertionError expected 2")
    before = [{"id": "tests", "status": "pass", "detail_tail": "ok"}]
    env_fail = [{"id": "tests", "status": "fail", "detail_tail": "Cannot find module '@rollup/rollup-win32-x64-msvc'"}]
    real_fail = [{"id": "tests", "status": "fail", "detail_tail": "AssertionError expected 2"}]
    assert rl.regression_ids(before, env_fail) == []
    assert rl.regression_ids(before, real_fail) == ["tests"]


def test_docs_may_be_replaced_wholesale_and_source_may_not(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "CONTRIBUTING.md").write_text("# contributing\n", encoding="utf-8")
    (repo / "docs").mkdir()
    (repo / "docs" / "guide.txt").write_text("old\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "notes.md").write_text("notes\n", encoding="utf-8")
    changed, rejected = rl.apply_edits(
        repo,
        [
            {"path": "CONTRIBUTING.md", "content": "# contributing\n\nRun npm test.\n"},
            {"path": "docs/guide.txt", "content": "new guide\n"},
        ],
    )
    rejected_changed, rejected = rl.apply_edits(
        repo,
        [
            {"path": "src/notes.md", "content": "replaced\n"},
            {"path": "src.js", "content": "nope\n"},
            {"path": "tests/a.test.js", "content": "skip\n"},
            {"path": "package-lock.json", "content": "{}\n"},
        ],
    )
    assert rejected_changed == []
    assert changed == ["CONTRIBUTING.md", "docs/guide.txt"]
    assert (repo / "CONTRIBUTING.md").read_text(encoding="utf-8").endswith("Run npm test.\n")
    assert (repo / "docs" / "guide.txt").read_text(encoding="utf-8") == "new guide\n"
    assert (repo / "src" / "notes.md").read_text(encoding="utf-8") == "notes\n"
    assert (repo / "src.js").read_text(encoding="utf-8").startswith("module.exports")
    assert any("src/notes.md" in item and "content may only create a new file" in item for item in rejected)
    assert any("src.js" in item and "content may only create a new file" in item for item in rejected)
    assert any("protected" in item for item in rejected)

    (repo / "README.md").write_text("a" * (200 * 1024), encoding="utf-8")
    changed, rejected = rl.apply_edits(repo, [{"path": "README.md", "content": "short\n"}])
    assert changed == []
    assert any("README.md" in item and "content may only create a new file" in item for item in rejected)
    assert (repo / "README.md").read_text(encoding="utf-8").startswith("a")


def test_version_probe_command_is_quoted_for_the_host_shell(tmp_path, monkeypatch):
    import subprocess

    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    assert rl.version_probe_command(tmp_path, "left-pad", "1.3.0") == "npm view left-pad@1.3.0 version"
    monkeypatch.setattr(rl.os, "name", "nt")
    windows = rl.version_probe_command(tmp_path, "@scope/pkg", "1.2.3")
    assert windows == "npm view " + subprocess.list2cmdline(["@scope/pkg@1.2.3"]) + " version"
    monkeypatch.setattr(rl.os, "name", "posix")
    py = tmp_path / "py"
    py.mkdir()
    (py / "requirements.txt").write_text("left-pad==1.2.0\n", encoding="utf-8")
    assert rl.version_probe_command(py, "left-pad", "1.3.0") == "python -m pip index versions left-pad"
