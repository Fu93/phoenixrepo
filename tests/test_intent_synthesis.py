"""Intent-test synthesis when a repository's own suite collects nothing.

The model replies are scripted. Pip installs are scripted. Import and pytest runs are real.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

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
    def __init__(self, objs):
        self.objs = list(objs)
        self.steps = []

    def complete_json(self, prompt, system_prompt="", repo_id="", step="", attempts=3, validate=None):
        self.steps.append(step)
        obj = self.objs.pop(0) if self.objs else None
        if obj is not None and validate is not None and not validate(obj):
            obj = None
        return obj, "", "m", "m"


def _repo(tmp_path: Path, body: str) -> Path:
    repo = tmp_path / "greet"
    repo.mkdir()
    (repo / "requirements.txt").write_text("\n", encoding="utf-8")
    (repo / "README.md").write_text(SENTENCE + "\n", encoding="utf-8")
    (repo / "greet.py").write_text(body, encoding="utf-8")
    return repo


def _good_source() -> str:
    return (
        "def test_hello_uses_the_name():\n"
        f"    # README: {SENTENCE}\n"
        "    from greet import hello\n"
        "    assert hello('Ada') == 'Hello, Ada'\n"
    )


def _proposal(source: str, *, pass_reason: str = "") -> dict:
    return {
        "entry_module": "greet",
        "entry_point": "hello",
        "pass_reason": pass_reason,
        "tests": [{"name": "test_hello_uses_the_name", "readme_sentence": SENTENCE, "source": source}],
    }


def _patch(find: str, replace: str) -> dict:
    return {"rationale": "make hello match the README", "edits": [{"path": "greet.py", "find": find, "replace": replace}]}


def _runner(cmd, cwd, timeout):
    if "pip install" in cmd:
        return rl.CmdResult(cmd, 0, "installed")
    if "intent_checks.py" in cmd or (" -c " in cmd and "import " in cmd):
        return rl.shell_runner(cmd, cwd, timeout)
    if "-m pytest" in cmd or "pytest -x -q" in cmd:
        isolated = f"{sys.executable} -m pytest -q -p no:cacheprovider --noconftest --rootdir . -o testpaths=."
        return rl.shell_runner(isolated, cwd, timeout)
    return rl.CmdResult(cmd, 0, "")


def _revive(repo, client):
    return rl.revive(repo, client, _Trace(), "o/greet", runner=_runner, test_timeout=60, heartbeat_s=0)


def test_lint_rejects_tautology_missing_assert_mocks_and_missing_import():
    tautology = (
        "def test_hello_uses_the_name():\n"
        f"    # README: {SENTENCE}\n"
        "    from greet import hello\n"
        "    hello('Ada')\n"
        "    assert True\n"
    )
    silent = (
        "def test_hello_uses_the_name():\n"
        f"    # README: {SENTENCE}\n"
        "    from greet import hello\n"
        "    hello('Ada')\n"
    )
    mocked = (
        "def test_hello_uses_the_name():\n"
        f"    # README: {SENTENCE}\n"
        "    from greet import hello\n"
        "    from unittest.mock import MagicMock\n"
        "    hello = MagicMock(return_value='Hello, Ada')\n"
        "    assert hello('Ada') == 'Hello, Ada'\n"
    )
    detached = (
        "def test_hello_uses_the_name():\n"
        f"    # README: {SENTENCE}\n"
        "    value = 'Hello, Ada'\n"
        "    assert value == 'Hello, Ada'\n"
    )
    assert any("tautolog" in error for error in intent.lint_test_source(tautology, readme=SENTENCE, entry_module="greet", entry_point="hello", name="test_hello_uses_the_name"))
    assert any("no assertion" in error for error in intent.lint_test_source(silent, readme=SENTENCE, entry_module="greet", entry_point="hello", name="test_hello_uses_the_name"))
    assert any("mocks the thing under test" in error for error in intent.lint_test_source(mocked, readme=SENTENCE, entry_module="greet", entry_point="hello", name="test_hello_uses_the_name"))
    assert any("never imports" in error for error in intent.lint_test_source(detached, readme=SENTENCE, entry_module="greet", entry_point="hello", name="test_hello_uses_the_name"))
    assert intent.lint_test_source(_good_source(), readme=SENTENCE, entry_module="greet", entry_point="hello", name="test_hello_uses_the_name") == []


@pytest.mark.parametrize(
    ("source", "needle"),
    [
        (
            "def test_hello_uses_the_name():\n"
            f"    # README: {SENTENCE}\n"
            "    from greet import hello\n"
            "    hello('Ada')\n"
            "    assert True\n",
            "tautolog",
        ),
        (
            "def test_hello_uses_the_name():\n"
            f"    # README: {SENTENCE}\n"
            "    from greet import hello\n"
            "    hello('Ada')\n",
            "no assertion",
        ),
        (
            "def test_hello_uses_the_name():\n"
            f"    # README: {SENTENCE}\n"
            "    from unittest.mock import MagicMock\n"
            "    hello = MagicMock(return_value='Hello, Ada')\n"
            "    assert hello('Ada') == 'Hello, Ada'\n",
            "mocks the thing under test",
        ),
        (
            "def test_hello_uses_the_name():\n"
            f"    # README: {SENTENCE}\n"
            "    value = 'Hello, Ada'\n"
            "    assert value == 'Hello, Ada'\n",
            "never imports",
        ),
    ],
)
def test_verifier_rejects_a_green_tautology_or_empty_check(tmp_path, source, needle):
    repo = _repo(tmp_path, "def hello(name):\n    return f'Hello, {name}'\n")
    target = repo / intent.SYNTH_FILE
    target.parent.mkdir()
    target.write_text(intent.HEADER + "\n" + source, encoding="utf-8")
    snap = rl.snapshot_worktree(repo)
    report = intent.verify_intent_tests(
        target.read_text(encoding="utf-8"),
        readme=SENTENCE + "\n",
        entry_module="greet",
        entry_point="hello",
        modules=["greet"],
        before_snap=snap,
        after_snap=snap,
        commands=intent.check_commands(repo, "greet", None),
        runner=_runner,
        timeout=60,
    )
    assert report["resurrection_allowed"] is False
    assert report["not_repo_coverage"] is True
    assert report["origin"] == "synthesized"
    assert needle in report["blocked_reason"]
    assert any(row["id"] == "intent" and row["status"] == "pass" for row in report["before"])


def test_intent_synthesis_resurrects_when_a_real_defect_fails_then_passes(tmp_path):
    repo = _repo(tmp_path, "def hello(name):\n    return name\n")
    client = _Client([_proposal(_good_source()), _patch("return name", "return f'Hello, {name}'")])
    res = _revive(repo, client)
    assert res.state == "RESURRECTED"
    assert res.terminal == "RESURRECTED"
    assert rl.is_no_tests(res.final.cmd, res.final.output, res.final.code)
    assert res.synthesized["origin"] == "synthesized"
    assert res.synthesized["not_repo_coverage"] is True
    assert res.synthesized["path"] == intent.SYNTH_FILE
    before = {row["id"]: row["status"] for row in res.synthesized["before"]}
    after = {row["id"]: row["status"] for row in res.synthesized["after"]}
    assert before["intent"] == "fail"
    assert after["intent"] == "pass"
    assert after["import"] == "pass"
    assert "Hello, {name}" in (repo / "greet.py").read_text(encoding="utf-8")
    text = (repo / intent.SYNTH_FILE).read_text(encoding="utf-8")
    assert intent.MARKER in text
    assert SENTENCE in text
    assert "revive_prd" not in "".join(client.steps)
    own = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--noconftest", "--rootdir", str(repo), "-o", "testpaths=."],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert own.returncode == 5, own.stdout + own.stderr
    direct = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--noconftest", "--rootdir", str(repo), intent.SYNTH_FILE],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert direct.returncode == 0, direct.stdout + direct.stderr
    prd.write_reports(tmp_path / "out", res, "o/greet", "synth")
    html = (tmp_path / "out" / "evidence.html").read_text(encoding="utf-8")
    assert "Synthesized checks" in html
    assert "not the repository&#x27;s own tests or coverage" in html
    assert "synthesized" in html
    assert "Nothing could be verified automatically." not in html
    quality = html.split("<h2>Quality before and after</h2>", 1)[1].split("<h2>", 1)[0]
    assert ">tests<" not in quality
    assert "No quality checks were run." in quality


def test_tautological_proposal_is_not_resurrected(tmp_path):
    repo = _repo(tmp_path, "def hello(name):\n    return name\n")
    source = (
        "def test_hello_uses_the_name():\n"
        f"    # README: {SENTENCE}\n"
        "    from greet import hello\n"
        "    hello('Ada')\n"
        "    assert True\n"
    )
    client = _Client([_proposal(source), _proposal(source)])
    res = _revive(repo, client)
    assert res.state == "NO_TESTS_TO_VERIFY"
    assert res.terminal == "NO_TESTS_TO_VERIFY"
    assert res.state != "RESURRECTED"
    assert not (repo / intent.SYNTH_FILE).exists()
    assert any("tautolog" in item for item in res.synthesized["rejected"])
    assert res.synthesized["not_repo_coverage"] is True
    assert "revive_prd" not in "".join(client.steps)
    prd.write_reports(tmp_path / "out", res, "o/greet", "taut")
    html = (tmp_path / "out" / "evidence.html").read_text(encoding="utf-8")
    assert "tautolog" in html
    assert "not the repository&#x27;s own tests or coverage" in html
    assert "Nothing could be verified automatically." in html
    assert "NO_TESTS_TO_VERIFY" in html


def test_builder_cannot_edit_synthesized_tests_to_force_a_pass(tmp_path):
    repo = _repo(tmp_path, "def hello(name):\n    return name\n")
    weaken = {
        "rationale": "make the new test pass without fixing hello",
        "edits": [{"path": intent.SYNTH_FILE, "find": "assert hello('Ada') == 'Hello, Ada'", "replace": "assert True"}],
    }
    client = _Client([_proposal(_good_source()), weaken])
    res = _revive(repo, client)
    assert res.state == "PARTIAL"
    assert res.state != "RESURRECTED"
    text = (repo / intent.SYNTH_FILE).read_text(encoding="utf-8")
    assert "assert hello('Ada') == 'Hello, Ada'" in text
    assert "return name" in (repo / "greet.py").read_text(encoding="utf-8")
    changed, rejected = rl.apply_edits(repo, [{"path": intent.SYNTH_FILE, "content": "def test_x():\n    assert True\n"}])
    assert changed == []
    assert rejected
    assert "assert hello('Ada') == 'Hello, Ada'" in (repo / intent.SYNTH_FILE).read_text(encoding="utf-8")


def test_still_failing_intent_tests_are_partial(tmp_path):
    repo = _repo(tmp_path, "def hello(name):\n    return name\n")
    client = _Client([_proposal(_good_source())])
    res = _revive(repo, client)
    assert res.state == "PARTIAL"
    assert res.state != "RESURRECTED"
    assert res.synthesized["before"]
    assert any(row["id"] == "intent" and row["status"] == "fail" for row in res.synthesized["after"])


def test_passing_intent_tests_without_a_prior_failure_are_not_resurrected(tmp_path):
    repo = _repo(tmp_path, "def hello(name):\n    return f'Hello, {name}'\n")
    unexplained = _Client([_proposal(_good_source())])
    rejected = _revive(repo, unexplained)
    assert rejected.state == "NO_TESTS_TO_VERIFY"
    assert rejected.state != "RESURRECTED"
    reason = "The function already returns the greeting the README describes."
    explained = _Client([_proposal(_good_source(), pass_reason=reason)])
    partial = _revive(repo, explained)
    assert partial.state == "PARTIAL"
    assert partial.state != "RESURRECTED"
    assert partial.verification["resurrection_allowed"] is False
    assert reason == partial.synthesized["pass_reason"]


def test_garbage_intent_reply_does_not_become_a_docs_revival(tmp_path):
    repo = _repo(tmp_path, "def hello(name):\n    return name\n")

    class Client:
        def __init__(self):
            self.steps = []

        def complete_json(self, prompt, system_prompt="", repo_id="", step="", attempts=3, validate=None):
            self.steps.append(step)
            obj = {"approve": True, "issues": [], "docs_items": [{"id": "d1", "title": "Write a guide"}]}
            if validate is not None:
                validate(obj)
            return obj, "", "m", "m"

    def runner(cmd, cwd, timeout):
        if "pip install" in cmd:
            return rl.CmdResult(cmd, 0, "installed")
        if "pytest" in cmd:
            return rl.CmdResult(cmd, 5, "no tests ran in 0.01s")
        return rl.CmdResult(cmd, 0, "")

    client = Client()
    res = rl.revive(repo, client, _Trace(), "o/greet", runner=runner, test_timeout=30, heartbeat_s=0)
    assert res.state == "NO_TESTS_TO_VERIFY"
    assert res.state != "RESURRECTED"
    assert client.steps == ["revive_intent_1", "revive_intent_2"]
    assert not (repo / intent.SYNTH_FILE).exists()
