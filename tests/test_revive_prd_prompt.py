"""Small PRD, proposal, and repair calls. Model replies and outdated lists are scripted."""
from __future__ import annotations

import json
from pathlib import Path

from app import intent_tests as intent
from app import revive_loop as rl
from app import revive_prd as prd

SENTENCE = "Greet returns a hello for the given name."


class _Trace:
    def __init__(self):
        self.events = []

    def event(self, **kw):
        self.events.append(kw)


class _Client:
    def __init__(self, objs=None, raises=False):
        self.objs = list(objs or [])
        self.prompts = []
        self.steps = []
        self.raises = raises

    def complete_json(self, prompt, system_prompt="", repo_id="", step="", attempts=3, validate=None):
        self.steps.append(step)
        self.prompts.append((step, prompt))
        if self.raises:
            raise RuntimeError("nightshift down")
        obj = self.objs.pop(0) if self.objs else None
        if obj is not None and validate is not None and not validate(obj):
            obj = None
        return obj, "", "m", "m"


class _Http502(Exception):
    def __init__(self):
        super().__init__("HTTP 502 from nightshift")
        self.status_code = 502


def _repo(path: Path, readme: str = "Adds numbers.\n", source: str = "FULL_SOURCE_MARKER\n") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "README.md").write_text(readme, encoding="utf-8")
    (path / "src.js").write_text(source, encoding="utf-8")
    (path / "package.json").write_text(
        json.dumps({"name": "demo", "dependencies": {"left-pad": "1.2.0"}, "scripts": {"test": "node t.js"}}),
        encoding="utf-8",
    )
    return path


def _baseline(code: int = 1, output: str = "AssertionError expected 2") -> rl.CmdResult:
    return rl.CmdResult("npm test --silent", code, output)


def _outdated_runner(cmd, cwd, timeout):
    if "outdated" in cmd:
        return rl.CmdResult(cmd, 1, json.dumps({"left-pad": {"current": "1.2.0", "wanted": "1.3.0", "latest": "1.3.0"}}))
    return rl.CmdResult(cmd, 0, "")


def _diagnosis() -> dict:
    return {
        "original_intent": 'The README says "Adds numbers."',
        "diagnosis": "Baseline npm test exited 1. Evidence: AssertionError expected 2.",
        "problem": "The test command fails.",
        "target_user": "Maintainers of this repository.",
    }


def _work() -> dict:
    return {
        "scope": ["src.js", "package.json"],
        "non_goals": ["Do not edit tests."],
        "risks": ["The patch might miss the assertion."],
        "work_items": [
            {"id": "fix-baseline", "kind": "repair", "title": "Make the test exit 0", "files": ["src.js"]},
            {
                "id": "upgrade-left-pad",
                "kind": "upgrade",
                "title": "Upgrade left-pad to 1.3.0",
                "package": "left-pad",
                "files": ["package.json"],
            },
        ],
        "upgrade_items": [{"id": "upgrade-left-pad", "package": "left-pad", "version": "1.3.0", "reason": "patch reported by npm outdated"}],
        "refactor_items": [],
        "docs_items": [],
    }


def _acceptance() -> dict:
    command = {"acceptance_command": "npm test --silent", "expected": "exit code 0"}
    return {
        "acceptance_criteria": [
            {"id": "ac-tests", "statement": "The repository test command exits 0.", "check_id": "tests"},
            {"id": "ac-diff", "statement": "The diff touches at most 8 files and does not edit tests.", "check_id": "diff_scope"},
            {"id": "ac-audit", "statement": "High and critical audit findings stay at 0.", "check_id": "audit"},
        ],
        "items": [
            {"id": "fix-baseline", **command},
            {"id": "upgrade-left-pad", **command},
        ],
    }


def _full_prd() -> dict:
    drafted = {
        "problem": "The repository test command fails on the current source.",
        "target_user": "Maintainers of this repository.",
        "scope": ["src.js"],
        "non_goals": ["Do not edit tests."],
        "risks": ["A patch could touch the wrong file."],
        "original_intent": 'The README says "Adds numbers."',
        "diagnosis": "The baseline test command exits 1 with an AssertionError.",
        "work_items": [
            {
                "id": "fix-baseline",
                "kind": "repair",
                "title": "Make the repository test command exit 0",
                "acceptance_command": "npm test --silent",
                "expected": "exit code 0",
                "files": ["src.js"],
            }
        ],
        "upgrade_items": [],
        "refactor_items": [],
        "docs_items": [],
        "acceptance_criteria": [
            {"id": "ac-tests", "statement": "The test command exits 0.", "check_id": "tests"},
            {"id": "ac-diff", "statement": "The diff touches at most 8 files and does not edit tests.", "check_id": "diff_scope"},
            {"id": "ac-audit", "statement": "High and critical audit findings stay at 0.", "check_id": "audit"},
        ],
    }
    return drafted


def test_prd_prompt_sends_an_excerpt_tree_tail_and_outdated_list(tmp_path):
    readme = "Adds numbers.\n" + ("padding line\n" * 2000) + "ENDMARKER\n"
    repo = _repo(tmp_path / "repo", readme=readme)
    client = _Client([_full_prd()])
    drafted = prd.draft_prd(client, repo, "o/r", "", _baseline(), runner=_outdated_runner, timeout=10)
    assert drafted["problem"] == "The repository test command fails on the current source."
    prompt = client.prompts[0][1]
    assert "Adds numbers." in prompt
    assert "ENDMARKER" not in prompt
    assert "src.js" in prompt
    assert "FULL_SOURCE_MARKER" not in prompt
    assert "=== " not in prompt
    assert "AssertionError expected 2" in prompt
    assert "left-pad 1.2.0 -> 1.3.0" in prompt
    assert len(prompt.encode("utf-8")) <= prd.PROMPT_BUDGET_BYTES
    assert len(readme.encode("utf-8")) > prd.README_EXCERPT_BYTES


def test_prd_pieces_are_separate_calls_and_reused_from_disk(tmp_path):
    repo = _repo(tmp_path / "repo")
    run = tmp_path / "run"
    client = _Client([_diagnosis(), _work(), _acceptance()])
    drafted = prd.draft_prd(client, repo, "o/r", "", _baseline(), runner=_outdated_runner, run_dir=run, timeout=10)
    assert client.steps == ["revive_prd_1", "revive_prd_work_1", "revive_prd_acceptance_1"]
    assert prd.validate_prd(drafted, repo) == []
    assert drafted.get("drafted_by") is None
    assert (run / "prd-diagnosis.json").is_file()
    assert (run / "prd-work.json").is_file()
    assert (run / "prd-acceptance.json").is_file()

    again = _Client(raises=True)
    reused = prd.draft_prd(again, repo, "o/r", "", _baseline(), runner=_outdated_runner, run_dir=run, timeout=10)
    assert again.steps == []
    assert reused["problem"] == drafted["problem"]
    assert prd.validate_prd(reused, repo) == []


def test_a_failed_piece_is_not_repeated_when_it_is_already_on_disk(tmp_path):
    repo = _repo(tmp_path / "repo")
    run = tmp_path / "run"

    class _WorkDown(_Client):
        def complete_json(self, prompt, system_prompt="", repo_id="", step="", attempts=3, validate=None):
            self.steps.append(step)
            self.prompts.append((step, prompt))
            if "work" in step:
                raise RuntimeError("nightshift down")
            obj = _diagnosis()
            if validate is not None and not validate(obj):
                return None, "", "", ""
            return obj, "", "", ""

    first = _WorkDown()
    partial = prd.draft_prd(first, repo, "o/r", "", _baseline(), runner=_outdated_runner, run_dir=run, timeout=10)
    assert first.steps == ["revive_prd_1", "revive_prd_work_1", "revive_prd_work_2"]
    assert (run / "prd-diagnosis.json").is_file()
    assert not (run / "prd-work.json").exists()
    assert partial["drafted_by"] == "mixed"
    assert "work" in partial["deterministic_pieces"]
    assert 'The README says "Adds numbers."' in partial["original_intent"]

    second = _Client([_work(), _acceptance()])
    finished = prd.draft_prd(second, repo, "o/r", "", _baseline(), runner=_outdated_runner, run_dir=run, timeout=10)
    assert second.steps == ["revive_prd_work_1", "revive_prd_acceptance_1"]
    assert "revive_prd_1" not in second.steps
    assert prd.validate_prd(finished, repo) == []
    assert finished.get("drafted_by") is None


def test_http_502_retries_only_the_piece_that_failed(tmp_path):
    repo = _repo(tmp_path / "repo")
    run = tmp_path / "run"
    calls = []

    class _Gateway:
        def complete_json(self, prompt="", system_prompt="", repo_id="", step="", attempts=1, validate=None):
            calls.append(step)
            if "work" in step:
                raise _Http502()
            obj = _diagnosis()
            if validate is not None and not validate(obj):
                return None, "", "", ""
            return obj, "", "", ""

    trace = _Trace()
    failures: list[dict] = []
    wrapped = rl.InfraRetry(_Gateway(), trace, failures, backoff=(0.0, 0.0, 0.0), sleep=lambda _delay: None)
    drafted = prd.draft_prd(wrapped, repo, "o/r", "", _baseline(), runner=_outdated_runner, run_dir=run, timeout=10)
    assert calls == ["revive_prd_1", "revive_prd_work_1", "revive_prd_work_1", "revive_prd_work_1"]
    assert (run / "prd-diagnosis.json").is_file()
    assert not (run / "prd-work.json").exists()
    assert drafted["drafted_by"] == "mixed"
    assert drafted["label"] == "deterministic PRD"
    assert "work" in drafted["deterministic_pieces"]
    assert "acceptance" in drafted["deterministic_pieces"]
    assert failures and failures[0]["step"] == "revive_prd_work_1"


def test_model_call_trace_records_prompt_bytes_and_elapsed(tmp_path):
    repo = _repo(tmp_path / "repo", source="module.exports = () => 1;\n")
    (repo / "t.js").write_text("x\n", encoding="utf-8")
    patch = {"edits": [{"path": "src.js", "find": "=> 1", "replace": "=> 2"}]}
    client = _Client([_full_prd(), patch, {"approve": True, "issues": []}])

    def runner(cmd, cwd, timeout):
        if "outdated" in cmd:
            return rl.CmdResult(cmd, 0, "{}")
        if cmd.startswith("npm install") or cmd.startswith("npm ci"):
            return rl.CmdResult(cmd, 0, "installed")
        if "npm audit" in cmd:
            return rl.CmdResult(cmd, 0, json.dumps({"metadata": {"vulnerabilities": {"high": 0, "critical": 0}}}))
        text = (cwd / "src.js").read_text(encoding="utf-8")
        if "=> 2" in text:
            return rl.CmdResult(cmd, 0, "all tests pass")
        return rl.CmdResult(cmd, 1, "AssertionError expected 2")

    trace = _Trace()
    rl.revive(repo, client, trace, "o/r", runner=runner, heartbeat_s=0)
    calls = [event for event in trace.events if event["action"] == "REVIVE_MODEL_CALL"]
    assert calls
    prd_call = next(event for event in calls if event["output_summary"]["step"] == "revive_prd_1")
    assert prd_call["output_summary"]["prompt_bytes"] > 0
    assert prd_call["output_summary"]["prompt_bytes"] <= prd.PROMPT_BUDGET_BYTES
    assert prd_call["output_summary"]["elapsed_s"] >= 0
    assert "Adds numbers" not in json.dumps(prd_call["output_summary"])
    assert prd_call["status"] == "ok"


def test_deterministic_prd_needs_no_model_and_names_the_verified_upgrade(tmp_path):
    repo = _repo(tmp_path / "repo")
    client = _Client(raises=True)
    drafted = prd.draft_prd(client, repo, "o/r", "", _baseline(0, "all tests pass"), runner=_outdated_runner, timeout=10)
    assert client.steps == ["revive_prd_1", "revive_prd_2"]
    assert drafted["drafted_by"] == "deterministic"
    assert drafted["label"] == "deterministic PRD"
    assert "llm call failed" in drafted["fallback_reason"]
    upgrade = next(item for item in drafted["upgrade_items"] if item["package"] == "left-pad")
    assert upgrade["version"] == "1.3.0"
    assert upgrade["source"] == "deterministic"
    assert upgrade["acceptance_command"] == "npm test --silent"
    assert "exit code 0" in upgrade["expected"]
    assert prd.validate_prd(drafted, repo) == []
    text = prd.render_prd_markdown(drafted)
    assert "deterministic PRD" in text
    assert "outdated-dependency list" in text
    assert "without a model call" in text


def test_green_repo_applies_the_deterministic_upgrade_while_the_model_is_down(tmp_path):
    repo = _repo(tmp_path / "repo", source="module.exports = () => 2;\n")
    (repo / "t.js").write_text("x\n", encoding="utf-8")

    def runner(cmd, cwd, timeout):
        if "outdated" in cmd:
            return rl.CmdResult(cmd, 1, json.dumps({"left-pad": {"current": "1.2.0", "wanted": "1.3.0", "latest": "1.3.0"}}))
        if cmd.startswith("npm install") or cmd.startswith("npm ci"):
            return rl.CmdResult(cmd, 0, "installed")
        if "npm audit" in cmd:
            return rl.CmdResult(cmd, 0, json.dumps({"metadata": {"vulnerabilities": {"high": 0, "critical": 0}}}))
        text = (cwd / "src.js").read_text(encoding="utf-8")
        if "=> 2" in text:
            return rl.CmdResult(cmd, 0, "all tests pass")
        return rl.CmdResult(cmd, 1, "AssertionError expected 2")

    res = rl.revive(repo, _Client(raises=True), _Trace(), "o/r", runner=runner, heartbeat_s=0)
    assert "1.3.0" in (repo / "package.json").read_text(encoding="utf-8")
    assert res.prd["label"] == "deterministic PRD"
    assert res.prd["drafted_by"] == "deterministic"
    assert res.state == "RESURRECTED"
    assert res.verification["resurrection_allowed"] is True
    assert any(item.get("package") == "left-pad" and item.get("status") == "KEPT" for item in res.prd_items_done)


def test_repair_prompt_keeps_file_ends_and_drops_the_middle(tmp_path):
    body = "HEAD\n" + ("x" * 8000) + "\nMIDDLEMARKER\n" + ("y" * 8000) + "\nTAIL\n"
    files = {
        "calc.py": body,
        "b.py": "b" * 3000,
        "c.py": "c" * 3000,
        "d.py": "d" * 3000,
        "e.py": "EFILEMARKER" + ("e" * 3000),
    }
    brief = {
        "diagnosis": "baseline failed " + ("z" * 8000) + "DIAGMARKER",
        "original_intent": "Adds numbers.",
        "work_items": [{"id": "fix-baseline"}],
        "upgrade_items": [],
    }
    client = _Client([{"edits": [{"path": "calc.py", "find": "HEAD", "replace": "HEAD2"}]}])
    last = rl.CmdResult("npm test --silent", 1, "AssertionError expected 2")
    patch = rl.propose_patch(client, "o/r", brief, 1, last, files, ["old " + ("h" * 5000)])
    assert patch is not None and patch["edits"][0]["replace"] == "HEAD2"
    prompt = client.prompts[0][1]
    assert "MIDDLEMARKER" not in prompt
    assert "EFILEMARKER" not in prompt
    assert "DIAGMARKER" not in prompt
    assert "HEAD" in prompt and "TAIL" in prompt
    assert len(prompt.encode("utf-8")) <= rl.REPAIR_PROMPT_BYTES
    assert len(client.prompts) == 1


def test_repair_plan_then_edits_is_one_extra_small_call():
    files = {"calc.py": "return a\n", "huge.py": "HUGEMARKER" + ("z" * 20000)}
    client = _Client(
        [
            {"rationale": "touch calc", "files": ["calc.py"]},
            {"rationale": "fix calc", "edits": [{"path": "calc.py", "find": "return a", "replace": "return b"}]},
        ]
    )
    last = rl.CmdResult("npm test --silent", 1, "AssertionError expected 2")
    patch = rl.propose_patch(client, "o/r", {"diagnosis": "baseline failed", "work_items": [{"id": "fix"}]}, 4, last, files, [])
    assert patch["edits"][0]["path"] == "calc.py"
    assert client.steps == ["revive_patch_4", "revive_patch_4_edits"]
    edits_prompt = client.prompts[1][1]
    assert "calc.py" in edits_prompt
    assert "HUGEMARKER" not in edits_prompt
    assert len(edits_prompt.encode("utf-8")) <= rl.REPAIR_PROMPT_BYTES


def test_intent_proposal_uses_a_readme_excerpt_and_can_split(tmp_path):
    repo = tmp_path / "greet"
    repo.mkdir()
    readme = SENTENCE + "\n" + ("padding\n" * 2000) + "TAILMARKER\n"
    (repo / "README.md").write_text(readme, encoding="utf-8")
    (repo / "greet.py").write_text("def hello(name):\n    return 'BODYMARKER'\n", encoding="utf-8")
    source = (
        "def test_hello_uses_the_name():\n"
        f"    # README: {SENTENCE}\n"
        "    from greet import hello\n"
        "    assert hello('Ada') == 'Hello, Ada'\n"
    )
    full = {
        "entry_module": "greet",
        "entry_point": "hello",
        "pass_reason": "",
        "tests": [{"name": "test_hello_uses_the_name", "readme_sentence": SENTENCE, "source": source}],
    }
    sized = _Client([None])
    intent._ask_proposal(sized, repo, "o/greet", readme, [], 1)
    prompt = sized.prompts[0][1]
    assert SENTENCE in prompt
    assert "TAILMARKER" not in prompt
    assert "BODYMARKER" not in prompt
    assert len(prompt.encode("utf-8")) <= prd.PROMPT_BUDGET_BYTES

    split = _Client([{"entry_module": "greet", "entry_point": "hello"}, full])
    found, errors = intent._ask_proposal(split, repo, "o/greet", readme, [], 1)
    assert errors == []
    assert found["tests"][0]["name"] == "test_hello_uses_the_name"
    assert split.steps == ["revive_intent_1", "revive_intent_1_tests"]
    assert "BODYMARKER" not in split.prompts[1][1]
    assert "TAILMARKER" not in split.prompts[1][1]
