"""engtrial-pwgen: pytest exit 5 is not a product failure, and dependabot.yml is not a pytest fix.

The model and the installer are scripted. The protection check and the PRD validation are real.
"""
from __future__ import annotations

import json
from pathlib import Path

from app import revive_loop as rl
from app import revive_prd as prd

SENTENCE = "Generate a password of the requested length."
DEPENDABOT = ".github/dependabot.yml"


class _Trace:
    def __init__(self):
        self.events = []

    def event(self, **kw):
        self.events.append(kw)


class _Client:
    def __init__(self, objs):
        self.objs = list(objs)
        self.steps = []
        self.prompts = []

    def complete_json(self, prompt, system_prompt="", repo_id="", step="", attempts=3, validate=None):
        self.steps.append(step)
        self.prompts.append(prompt)
        obj = self.objs.pop(0) if self.objs else None
        if obj is not None and validate is not None and not validate(obj):
            obj = None
        return obj, "", "m", "m"


def _pwgen(tmp_path: Path) -> Path:
    repo = tmp_path / "pwgen"
    repo.mkdir()
    (repo / "README.md").write_text(SENTENCE + "\n", encoding="utf-8")
    (repo / "requirements.txt").write_text("\n", encoding="utf-8")
    (repo / "pwgen.py").write_text("def generate(length):\n    return 'x'\n", encoding="utf-8")
    bot = repo / ".github"
    bot.mkdir()
    (bot / "dependabot.yml").write_text("version: 2\nupdates: []\n", encoding="utf-8")
    return repo


def _no_tests_baseline() -> rl.CmdResult:
    return rl.CmdResult("python -m pytest -x -q", 5, "no tests ran")


def _intent_source() -> str:
    return (
        "def test_generate_respects_the_length():\n"
        f"    # README: {SENTENCE}\n"
        "    from pwgen import generate\n"
        "    assert len(generate(8)) == 8\n"
    )


def _proposal() -> dict:
    return {
        "entry_module": "pwgen",
        "entry_point": "generate",
        "pass_reason": "",
        "tests": [
            {
                "name": "test_generate_respects_the_length",
                "readme_sentence": SENTENCE,
                "source": _intent_source(),
            }
        ],
    }


def _dummy_test_edit() -> dict:
    return {
        "rationale": "add a dummy test so pytest exits 0",
        "edits": [{"path": "test_dummy.py", "content": "def test_dummy():\n    assert True\n"}],
    }


def _concrete(files, *, work_title="Make the baseline command exit 0", work_id="fix-baseline"):
    return {
        "problem": "The repository test command fails on the current source.",
        "target_user": "Maintainers of this repository.",
        "scope": ["pwgen.py"],
        "non_goals": ["Do not edit tests or lockfiles."],
        "risks": ["A patch could touch the wrong file."],
        "original_intent": f'The README says "{SENTENCE}"',
        "diagnosis": "The baseline test command exits 5. Evidence: no tests ran.",
        "work_items": [
            {
                "id": work_id,
                "kind": "repair",
                "title": work_title,
                "acceptance_command": "python -m pytest -x -q",
                "expected": "exit code 0",
                "files": list(files),
            }
        ],
        "upgrade_items": [],
        "refactor_items": [],
        "docs_items": [],
        "acceptance_criteria": [
            {"id": "ac-tests", "statement": "The repository test command exits 0.", "check_id": "tests"},
            {"id": "ac-diff", "statement": "The diff touches at most 8 files and does not edit tests.", "check_id": "diff_scope"},
            {"id": "ac-audit", "statement": "High and critical audit findings stay at 0.", "check_id": "audit"},
        ],
    }


def test_validation_rejects_a_pytest_goal_on_ci_config_and_a_protected_test(tmp_path):
    repo = _pwgen(tmp_path)
    baseline = _no_tests_baseline()
    dependabot = _concrete([DEPENDABOT], work_title="make pytest exit 0")
    errors = prd.validate_prd(dependabot, repo, baseline=baseline)
    assert any("unrelated" in error and DEPENDABOT in error for error in errors)
    assert any("unachievable" in error for error in errors)

    dummy = _concrete(["test_dummy.py"], work_title="make pytest exit 0")
    dummy_errors = prd.validate_prd(dummy, repo, baseline=baseline)
    assert any("not allowed to edit" in error and "test_dummy.py" in error for error in dummy_errors)
    assert any("unachievable" in error for error in dummy_errors)

    product = _concrete(["pwgen.py"], work_title="make pytest exit 0")
    assert any("unachievable" in error for error in prd.validate_prd(product, repo, baseline=baseline))
    failing = rl.CmdResult("python -m pytest -x -q", 1, "AssertionError expected 2")
    assert prd.validate_prd(product, repo, baseline=failing) == []


def test_fallback_prd_does_not_invent_a_pytest_fix_for_exit_5(tmp_path):
    repo = _pwgen(tmp_path)
    baseline = _no_tests_baseline()
    for drafted in (
        prd.minimal_prd(repo, "jocxfin/pwgen", baseline, "model draft failed"),
        prd.deterministic_prd(repo, "jocxfin/pwgen", baseline, reason="llm call failed"),
    ):
        assert prd.validate_prd(drafted, repo, baseline=baseline) == []
        item = drafted["work_items"][0]
        assert item["id"] == "no-tests-collected"
        assert item["files"] == []
        assert item["expected"] == "exit code 5"
        blob = json.dumps(drafted["work_items"])
        assert "dependabot" not in blob
        assert "exit code 0" not in blob
        assert "make pytest exit 0" not in blob.lower()
        assert DEPENDABOT not in drafted["work_items"][0].get("files", [])


def test_pwgen_exit_5_synthesizes_intent_tests_and_blocks_a_dummy_test(tmp_path):
    repo = _pwgen(tmp_path)
    original = (repo / DEPENDABOT).read_bytes()
    client = _Client([_proposal(), _dummy_test_edit(), _dummy_test_edit()])

    def runner(cmd, cwd, timeout):
        if "pip install" in cmd or "outdated" in cmd:
            return rl.CmdResult(cmd, 0, "{}" if "outdated" in cmd else "installed")
        if "intent_checks.py" in cmd:
            return rl.CmdResult(cmd, 1, "AssertionError")
        if "-m pytest" in cmd:
            return rl.CmdResult(cmd, 5, "no tests ran")
        if " -c " in cmd and "import " in cmd:
            return rl.shell_runner(cmd, cwd, timeout)
        return rl.CmdResult(cmd, 0, "")

    res = rl.revive(repo, client, _Trace(), "jocxfin/pwgen", runner=runner, test_timeout=30, heartbeat_s=0)
    assert not any(step.startswith("revive_prd") for step in client.steps)
    assert "revive_intent_1" in client.steps
    assert "revive_patch_1" not in client.steps
    assert res.state == "PARTIAL"
    assert res.state != "RESURRECTED"
    assert rl.is_no_tests(res.final.cmd, res.final.output, res.final.code)
    assert res.final.code == 5
    assert (repo / "phoenix_synthesized" / "intent_checks.py").is_file()
    assert not (repo / "test_dummy.py").exists()
    assert (repo / DEPENDABOT).read_bytes() == original
    rejected = [text for item in res.iterations for text in (item.get("rejected") or [])]
    assert any("test_dummy.py" in text and "protected" in text for text in rejected)
    assert not any("dependabot" in (prompt or "") and "fix-baseline" in (prompt or "") for prompt in client.prompts)
