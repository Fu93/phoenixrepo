"""Regression tests for defects found in the October 2026 audit. Each test names the defect it pins."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

from app import revive_loop as rl
from app.adapters.zetaris import rows_from_payload
from app.agents.relevance import search_prior_art
from app.errors import ZetarisError
from app.llm.provider import BoundLLM
from app.revive_loop import CmdResult, acceptance_ok
from app.revive_verify import verify_revival, weakened_test_diff
from app.tools.base import Budget, ToolBox, ToolResult
from app.tools.builtin import GitHubTool
from app.trace_log import HackathonTrace


# --- revive loop path guards ---------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [".git/config", "./.git/config", ".git/hooks/pre-commit", "./.git/hooks/pre-commit", ".GIT/config", "sub/.git/config", "a/./.git/x"],
)
def test_git_paths_are_protected_and_unsafe(tmp_path: Path, path: str):
    # lstrip("./") stripped characters, so ".git/..." became "git/..." and was editable.
    assert rl.is_protected(path)
    assert rl._safe_path(tmp_path, path) is None


def test_root_conftest_is_protected():
    assert rl.is_protected("conftest.py")
    assert rl.is_protected("./src/conftest.py")


def test_ordinary_source_paths_stay_editable(tmp_path: Path):
    for path in ("src/app.py", "./src/app.py", "package.json", "gitlab.yml", "github/workflows.md"):
        assert not rl.is_protected(path), path
        assert rl._safe_path(tmp_path, path) is not None, path


def test_apply_edits_rejects_dot_slash_git(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    changed, rejected = rl.apply_edits(tmp_path, [{"path": "./.git/config", "find": "[core]", "replace": "[core]\n\thooksPath = x"}])
    assert changed == []
    assert rejected and "protected" in rejected[0]
    assert (tmp_path / ".git" / "config").read_text(encoding="utf-8") == "[core]\n"


# --- revive loop command runner ------------------------------------------------------------


def test_shell_runner_removes_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("METERLESS_RUNTIME_TOKEN", "secret-runtime")
    monkeypatch.setenv("GITHUB_TOKEN", "secret-gh")
    monkeypatch.setenv("SOME_SERVICE_API_KEY", "secret-other")
    monkeypatch.setenv("PHOENIX_HARMLESS", "kept")
    script = "import os, json; print(json.dumps({k: os.environ.get(k) for k in ['METERLESS_RUNTIME_TOKEN','GITHUB_TOKEN','SOME_SERVICE_API_KEY','PHOENIX_HARMLESS','CI']}))"
    result = rl.shell_runner(f'"{sys.executable}" -c "{script}"', tmp_path, 30)
    assert result.ok, result.output
    seen = json.loads(result.output.strip().splitlines()[-1])
    assert seen == {"METERLESS_RUNTIME_TOKEN": None, "GITHUB_TOKEN": None, "SOME_SERVICE_API_KEY": None, "PHOENIX_HARMLESS": "kept", "CI": "1"}


def test_child_env_keeps_explicit_extras():
    env = rl.child_env({"npm_config_cache": "/x"})
    assert env["npm_config_cache"] == "/x"
    assert env["CI"] == "1"


@pytest.mark.skipif(os.name == "nt", reason="process groups are POSIX; Windows uses taskkill /T")
def test_shell_runner_timeout_kills_the_whole_tree(tmp_path: Path):
    marker = tmp_path / "child-survived"
    # The shell starts a grandchild that would write the marker after the timeout.
    child = f'"{sys.executable}" -c "import time, pathlib; time.sleep(3); pathlib.Path(r\'{marker}\').write_text(\'x\')"'
    started = time.monotonic()
    result = rl.shell_runner(f"{child} & wait", tmp_path, 1)
    assert result.code == 124
    assert time.monotonic() - started < 8
    time.sleep(3.5)
    assert not marker.exists()


# --- acceptance expectations --------------------------------------------------------------


def test_acceptance_ok_honours_the_stated_exit_code():
    assert acceptance_ok(CmdResult("t", 0, ""), "exit code 0")
    assert acceptance_ok(CmdResult("t", 5, ""), "exit code 5")
    assert not acceptance_ok(CmdResult("t", 0, ""), "exit code 5")
    assert acceptance_ok(CmdResult("t", 0, ""), "exits 0 and prints the version")
    assert not acceptance_ok(CmdResult("t", 1, ""), "the CLI prints its version")


# --- verifier tamper checks ---------------------------------------------------------------


def _pkg(test: str) -> bytes:
    return json.dumps({"name": "x", "scripts": {"test": test}}).encode()


def test_changed_test_script_is_weakening():
    reasons = weakened_test_diff({"package.json": _pkg("jest")}, {"package.json": _pkg("exit 0")})
    assert reasons == ["test command changed in package.json: scripts.test"]


def test_dependency_only_package_change_is_not_weakening():
    before = json.dumps({"scripts": {"test": "jest"}, "dependencies": {"a": "1"}}).encode()
    after = json.dumps({"scripts": {"test": "jest"}, "dependencies": {"a": "2"}}).encode()
    assert weakened_test_diff({"package.json": before}, {"package.json": after}) == []


def test_failures_turned_into_skips_fail_the_count_check():
    original = {"src/a.py": b"x = 1\n"}
    patched = {"src/a.py": b"x = 2\n"}

    def runner(cmd: str, cwd: Path, timeout: int) -> CmdResult:
        if (cwd / "src" / "a.py").read_text() == "x = 1\n":
            return CmdResult(cmd, 1, "30 passed, 10 failed")
        return CmdResult(cmd, 0, "0 passed, 40 skipped")

    report = verify_revival(original, patched, runner, test_cmd="pytest", items=[{"id": "i", "status": "KEPT"}])
    counts = next(c for c in report["checks"] if c["id"] == "test_counts")
    assert counts["status"] == "FAIL"
    assert report["resurrection_allowed"] is False


# --- prior-art search coverage ------------------------------------------------------------


class _Tool:
    def __init__(self, name: str, result: ToolResult) -> None:
        self.name = name
        self._result = result

    def call(self, args):
        return self._result


def _ok(items):
    return ToolResult(ok=True, data={"items": items}, error="", source_url="u", fetched_at="t")


def test_rate_limited_github_is_a_source_error():
    limited = ToolResult(ok=False, data="", error="GitHub HTTP 403: RATE_LIMITED: {}", source_url="u", fetched_at="t")
    web_items = [{"url": f"https://example.com/{i}", "title": f"t{i}", "snippet": "s"} for i in range(3)]
    npm_items = [{"name": f"pkg{i}", "description": "d"} for i in range(3)]
    box = ToolBox(
        tools={"github": _Tool("github", limited), "npm": _Tool("npm", _ok(npm_items)), "web_search": _Tool("web_search", _ok(web_items))},
        budget=Budget(max_calls=50, max_seconds=60),
    )
    found = search_prior_art({"repo_id": "o/r"}, ["q1"], box)
    assert found.source_status["github"] == "SOURCE_ERROR"
    assert found.insufficient_coverage is True


def test_source_counts_match_returned_rows():
    web_items = [{"url": f"https://example.com/{i}", "title": f"t{i}", "snippet": "s"} for i in range(6)]
    gh_items = [{"full_name": f"o/alt{i}", "readme": "r"} for i in range(5)]
    npm_items = [{"name": f"pkg{i}", "description": "d"} for i in range(5)]
    box = ToolBox(
        tools={"github": _Tool("github", _ok(gh_items)), "npm": _Tool("npm", _ok(npm_items)), "web_search": _Tool("web_search", _ok(web_items))},
        budget=Budget(max_calls=50, max_seconds=60),
    )
    found = search_prior_art({"repo_id": "o/r"}, ["q1", "q2", "q3"], box)
    assert len(found) <= 15
    assert sum(found.source_counts.values()) == len(found)


# --- GitHub tool URL encoding ------------------------------------------------------------


def test_github_search_query_is_url_encoded():
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, json={"items": []})

    tool = GitHubTool(token="", transport=httpx.MockTransport(handler))
    assert tool.call({"action": "repositories", "query": "C# Q&A bot", "per_page": 5}).ok
    assert seen[0].params["q"] == "C# Q&A bot"
    assert seen[0].params["per_page"] == "5"


# --- LLM provenance -----------------------------------------------------------------------


class _SlowThenFast:
    """First call sleeps past the bounded timeout, then reports a served model late."""

    timeout_seconds = 5

    def __init__(self) -> None:
        self.last_requested_model = ""
        self.last_served_model = None
        self.calls = 0
        self.release = threading.Event()

    def complete(self, messages, *, model=None, timeout=None):
        self.calls += 1
        if self.calls == 1:
            self.release.wait(2)
            self.last_served_model = "late-model"
            return "late"
        self.last_served_model = None  # the second reply has no `model` field
        return "fast"


def test_reply_without_model_field_does_not_keep_previous_served_model():
    class Inner:
        last_requested_model = "m"
        last_served_model = "first"

        def complete(self, messages, *, model=None):
            return "x"

    inner = Inner()
    llm = BoundLLM(inner, "nvidia", "m", False)
    llm.complete([{"role": "user", "content": "a"}])
    assert llm.served_model == "first"
    inner.last_served_model = None
    llm.complete([{"role": "user", "content": "b"}])
    assert llm.served_model is None


def test_abandoned_attempt_cannot_overwrite_provenance():
    inner = _SlowThenFast()
    llm = BoundLLM(inner, "meterless", "lane-a", False, lanes=("lane-a",), sleeper=lambda _s: None)
    with pytest.raises(Exception):
        llm._once_bounded([{"role": "user", "content": "x"}], "lane-a", 0.2)
    assert llm._once_bounded([{"role": "user", "content": "x"}], "lane-a", 1.0) == "fast"
    inner.release.set()
    time.sleep(0.3)
    assert llm.served_model is None


# --- trace ------------------------------------------------------------------------------


def test_trace_appends_and_still_verifies(tmp_path: Path):
    (tmp_path / "trace-r1.jsonl").write_text("stale line from an earlier run\n", encoding="utf-8")
    trace = HackathonTrace("r1", tmp_path)
    for i in range(5):
        trace.event(agent_name="a", action="STEP", input_summary={"i": i})
    trace.seal()
    lines = (tmp_path / "trace-r1.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 6
    assert "stale" not in lines[0]
    proc = subprocess.run([sys.executable, "scripts/verify_trace.py", str(tmp_path / "trace-r1.jsonl")], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_trace_redacts_token_shapes(tmp_path: Path):
    token = "ghp_" + "A" * 36
    trace = HackathonTrace("r2", tmp_path)
    record = trace.event(agent_name="a", action="STEP", output_summary=f"test printed {token}")
    assert token not in record["output_summary"]
    assert token not in (tmp_path / "trace-r2.jsonl").read_text(encoding="utf-8")


# --- Zetaris -----------------------------------------------------------------------------


def test_zetaris_error_object_without_message_is_a_zetaris_error():
    with pytest.raises(ZetarisError, match="Zetaris error"):
        rows_from_payload({"error": {"code": 7}})


# --- discovery collector -----------------------------------------------------------------


def test_github_token_ignores_shell_error_text(monkeypatch: pytest.MonkeyPatch):
    from scripts import collect_discovery as cd

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 127, stdout="", stderr="/bin/sh: gh: not found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert cd.get_github_token() == ""


def test_trace_secrets_include_env_github_token(monkeypatch: pytest.MonkeyPatch):
    from config.settings import Settings, trace_secrets

    monkeypatch.setenv("GITHUB_TOKEN", "gh-secret-value")
    settings = Settings(_env_file=None, meterless_runtime_token="rt-secret")
    assert set(trace_secrets(settings)) >= {"gh-secret-value", "rt-secret"}
    # secret_values() itself stays limited to Settings, so a copied .env.example still reports none.
    assert "gh-secret-value" not in settings.secret_values()
