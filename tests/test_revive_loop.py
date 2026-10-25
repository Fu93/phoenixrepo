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


def test_no_test_command_is_needs_review(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": 'echo "Error: no test specified" && exit 1'}}), encoding="utf-8")
    res = rl.revive(tmp_path, _Client([]), _Trace(), "o/r")
    assert res.state == "NEEDS_REVIEW"


def test_revive_succeeds_only_when_the_real_test_command_passes(tmp_path):
    repo = _make_repo(tmp_path)
    runner, _ = _runner_factory(repo)
    client = _Client(
        [
            {"goal": "g", "tasks": [{"id": "1", "title": "t", "acceptance": "a"}]},
            {"rationale": "r", "edits": [{"path": "src.js", "find": "=> 1", "replace": "=> 2"}]},
        ]
    )
    trace = _Trace()
    res = rl.revive(repo, client, trace, "o/r", gate_verdict="CANDIDATE", runner=runner)
    assert res.state == "RESURRECTED"
    assert res.files_changed == ["src.js"]
    assert [e["action"] for e in trace.events if e["action"].startswith("REVIVE_VALIDATE")] == ["REVIVE_VALIDATE"]


def test_gate_one_refusal_downgrades_a_passing_revival(tmp_path):
    repo = _make_repo(tmp_path)
    runner, _ = _runner_factory(repo)
    client = _Client([{"goal": "g", "tasks": [{"id": "1"}]}, {"edits": [{"path": "src.js", "find": "=> 1", "replace": "=> 2"}]}])
    res = rl.revive(repo, client, _Trace(), "o/r", gate_verdict="ALREADY_EXISTS", runner=runner)
    assert res.state == "REVIVED_NOT_RECOMMENDED"


def test_failure_goes_back_to_the_builder_with_real_output(tmp_path):
    repo = _make_repo(tmp_path)
    runner, _ = _runner_factory(repo)
    client = _Client(
        [
            {"goal": "g", "tasks": [{"id": "1"}]},
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
    client = _Client([{"goal": "g", "tasks": [{"id": "1"}]}])
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
