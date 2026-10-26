"""Gate 2 back half: human approval, pre-screen, deadlines, and honest terminals.

No network and no live model. Runners and cloners are fakes.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from app import revive_loop as rl
from app import revive_prd as prd
from scripts import prescreen_revive as screen
from scripts.revive import (
    APPROVAL_USAGE,
    REUSED_GATE_REASON,
    approval_error,
    approval_from_flags,
    build_parser,
    load_batch,
    main,
)


class _Trace:
    def __init__(self):
        self.events = []

    def event(self, **kw):
        self.events.append(kw)


class _Client:
    def __init__(self, objs=None):
        self.objs = list(objs or [])
        self.calls = []

    def complete_json(self, prompt, system_prompt="", repo_id="", step="", attempts=3, validate=None):
        self.calls.append(step)
        obj = self.objs.pop(0) if self.objs else None
        if obj is not None and validate is not None and not validate(obj):
            obj = None
        return obj, "", "m", "m"


class _Http502(Exception):
    def __init__(self):
        super().__init__("HTTP 502 from nightshift")
        self.status_code = 502


class _Boom:
    def __init__(self):
        self.calls = []

    def complete_json(self, prompt="", system_prompt="", repo_id="", step="", attempts=1, validate=None):
        self.calls.append(step)
        raise _Http502()


def _approval(verdict="NEEDS_REVIEW", reason="prior art was thin"):
    return {
        "reviewer": "Ada Lovelace",
        "note": "The tests exist and the problem is still open.",
        "timestamp": "2026-10-25T12:00:00+00:00",
        "verdict": verdict,
        "reason": reason,
    }


def _make_repo(tmp_path: Path, source: str = "module.exports = () => 1;\n") -> Path:
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "node t.js"}}), encoding="utf-8")
    (tmp_path / "src.js").write_text(source, encoding="utf-8")
    (tmp_path / "t.js").write_text("x", encoding="utf-8")
    return tmp_path


def _prd():
    return {
        "problem": "src.js returns the wrong value for the repository test command.",
        "target_user": "Maintainers running the repository test command.",
        "scope": ["src.js"],
        "non_goals": ["Do not edit tests."],
        "upgrade_items": [],
        "refactor_items": [],
        "docs_items": [],
        "original_intent": "(no README or description)",
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
        "acceptance_criteria": [
            {"id": "ac-tests", "statement": "The test command exits 0.", "check_id": "tests"},
            {"id": "ac-diff", "statement": "The diff touches at most 8 files and does not edit tests.", "check_id": "diff_scope"},
            {"id": "ac-audit", "statement": "High and critical audit findings stay at 0.", "check_id": "audit"},
        ],
        "risks": ["The assertion may point at the wrong file."],
    }


def _node_runner(repo: Path):
    calls = []

    def runner(cmd, cwd, timeout):
        calls.append((cmd, timeout))
        if str(cmd).startswith("npm ci") or str(cmd).startswith("npm install"):
            return rl.CmdResult(cmd, 0, "installed", 0.01)
        if "outdated" in str(cmd) or str(cmd).startswith("npm audit") or str(cmd).startswith("npx"):
            return rl.CmdResult(cmd, 0, "{}", 0.01)
        text = (cwd / "src.js").read_text(encoding="utf-8")
        if "=> 2" in text:
            return rl.CmdResult(cmd, 0, "all tests pass", 0.01)
        return rl.CmdResult(cmd, 1, "AssertionError in src.js: expected 2", 0.01)

    return runner, calls


def _actions(trace: _Trace) -> list[str]:
    return [event["action"] for event in trace.events]


def test_needs_review_with_approval_is_labelled_and_not_a_fake_candidate(tmp_path):
    repo = _make_repo(tmp_path, "module.exports = () => 2;\n")
    runner, calls = _node_runner(repo)
    trace = _Trace()
    approval = _approval()
    result = rl.revive(
        repo,
        _Client([]),
        trace,
        "o/r",
        gate_verdict="NEEDS_REVIEW",
        gate_reason=approval["reason"],
        human_approval=approval,
        runner=runner,
        heartbeat_s=0,
    )
    assert result.state == "NOTHING_TO_FIX"
    assert result.terminal == "NOTHING_TO_FIX"
    assert result.state != "RESURRECTED"
    assert result.label == rl.APPROVAL_LABEL
    assert result.human_approval["reviewer"] == "Ada Lovelace"
    assert result.human_approval["note"] == approval["note"]
    assert result.human_approval["timestamp"] == approval["timestamp"]
    assert result.human_approval["verdict"] == "NEEDS_REVIEW"
    assert result.human_approval["reason"] == approval["reason"]
    approved = [event for event in trace.events if event["action"] == "REVIVE_HUMAN_APPROVAL"]
    assert approved and approved[0]["status"] == "ok"
    assert "REVIVE_BLOCKED_BY_GATE1" not in _actions(trace)
    assert calls
    prd.write_reports(tmp_path / "out", result, "o/r", "run-approved")
    html = (tmp_path / "out" / "evidence.html").read_text(encoding="utf-8")
    assert "Outcome" in html
    assert "NOTHING_TO_FIX" in html
    assert "HUMAN_APPROVED_AFTER_NEEDS_REVIEW" in html
    assert "Human approval" in html
    assert "Ada Lovelace" in html
    assert "The tests exist and the problem is still open." in html
    assert "NEEDS_REVIEW" in html
    assert "prior art was thin" in html
    assert "Changed:" in html
    assert "Tests:" in html
    assert "Not verified:" in html
    assert "fork CI was not run" in html
    assert "<script src=" not in html
    assert "http://" not in html and "https://" not in html


def test_approved_repair_stays_labelled(tmp_path):
    repo = _make_repo(tmp_path)
    runner, _ = _node_runner(repo)
    result = rl.revive(
        repo,
        _Client([_prd(), {"rationale": "fix", "edits": [{"path": "src.js", "find": "=> 1", "replace": "=> 2"}]}]),
        _Trace(),
        "o/r",
        gate_verdict="NEEDS_PRIOR_ART",
        gate_reason="search was inconclusive",
        human_approval=_approval("NEEDS_PRIOR_ART", "search was inconclusive"),
        runner=runner,
        heartbeat_s=0,
    )
    assert result.state == "RESURRECTED"
    assert result.terminal == "RESURRECTED"
    assert result.label == rl.APPROVAL_LABEL
    assert result.human_approval["verdict"] == "NEEDS_PRIOR_ART"
    assert result.files_changed == ["src.js"]


def test_already_exists_no_go_and_unjudged_never_revive(tmp_path):
    repo = _make_repo(tmp_path, "module.exports = () => 2;\n")
    runner, calls = _node_runner(repo)
    trace = _Trace()
    for verdict in ("ALREADY_EXISTS", "NO_GO", "UNJUDGED"):
        result = rl.revive(
            repo,
            _Client([_prd(), {"edits": [{"path": "src.js", "find": "=> 2", "replace": "=> 9"}]}]),
            trace,
            "o/r",
            gate_verdict=verdict,
            gate_reason="do not revive",
            human_approval=_approval(verdict, "do not revive"),
            runner=runner,
            heartbeat_s=0,
        )
        assert result.state == "NOT_ATTEMPTED_GATE1"
        assert result.terminal == "NOT_ATTEMPTED_GATE1"
        assert result.label == ""
        assert result.state != "RESURRECTED"
    assert not calls
    refused = [event for event in trace.events if event["action"] == "REVIVE_HUMAN_APPROVAL"]
    blocked = [event for event in trace.events if event["action"] == "REVIVE_BLOCKED_BY_GATE1"]
    assert len(refused) == 3 and {event["status"] for event in refused} == {"refused"}
    assert len(blocked) == 3
    assert [event["action"] for event in trace.events] == ["REVIVE_HUMAN_APPROVAL", "REVIVE_BLOCKED_BY_GATE1"] * 3
    prd.write_reports(tmp_path / "refused", result, "o/r", "run-refused")
    html = (tmp_path / "refused" / "evidence.html").read_text(encoding="utf-8")
    assert "NOT_ATTEMPTED_GATE1" in html
    assert "HUMAN_APPROVED_AFTER_NEEDS_REVIEW" not in html
    assert "Human approval" in html
    assert "UNJUDGED" in html


def test_incomplete_approval_does_not_open_the_gate(tmp_path):
    repo = _make_repo(tmp_path)
    runner, calls = _node_runner(repo)
    trace = _Trace()
    result = rl.revive(
        repo,
        _Client([]),
        trace,
        "o/r",
        gate_verdict="NEEDS_REVIEW",
        human_approval={"reviewer": "", "note": "looks fine"},
        runner=runner,
        heartbeat_s=0,
    )
    assert result.state == "NOT_ATTEMPTED_GATE1"
    assert result.label == ""
    assert not calls
    assert [event["action"] for event in trace.events] == ["REVIVE_HUMAN_APPROVAL", "REVIVE_BLOCKED_BY_GATE1"]
    assert trace.events[0]["status"] == "refused"


def test_candidate_approval_does_not_take_the_review_label(tmp_path):
    repo = _make_repo(tmp_path, "module.exports = () => 2;\n")
    runner, _ = _node_runner(repo)
    result = rl.revive(
        repo,
        _Client([]),
        _Trace(),
        "o/r",
        gate_verdict="CANDIDATE",
        human_approval=_approval("CANDIDATE", "already a candidate"),
        runner=runner,
        heartbeat_s=0,
    )
    assert result.state == "NOTHING_TO_FIX"
    assert result.label == ""
    assert result.human_approval is None


def test_deadline_zero_does_not_call_the_model(tmp_path):
    repo = _make_repo(tmp_path)
    client = _Boom()
    result = rl.revive(repo, client, _Trace(), "o/r", deadline_s=0, heartbeat_s=0, max_iterations=4)
    assert result.state == "TIMEOUT"
    assert result.terminal == "TIMEOUT"
    assert "deadline" in result.reason
    assert client.calls == []


def test_step_timeout_caps_every_command(tmp_path):
    repo = _make_repo(tmp_path, "module.exports = () => 2;\n")
    runner, calls = _node_runner(repo)
    rl.revive(repo, _Client([]), _Trace(), "o/r", runner=runner, step_timeout_s=7, heartbeat_s=0)
    assert calls
    assert all(timeout <= 7 for _cmd, timeout in calls)


def test_http_502_is_retried_then_recorded_as_infrastructure(tmp_path):
    repo = _make_repo(tmp_path)
    runner, _ = _node_runner(repo)
    client = _Boom()
    trace = _Trace()
    result = rl.revive(
        repo,
        client,
        trace,
        "o/r",
        runner=runner,
        max_iterations=1,
        infra_backoff=(0.0, 0.0, 0.0),
        heartbeat_s=0,
    )
    assert result.state == "INFRA_FAILED"
    assert result.terminal == "INFRA_FAILED"
    assert result.state != "RESURRECTED"
    assert "not a verdict" in result.reason
    assert client.calls.count("revive_prd_1") == 3
    infra = [event for event in trace.events if event["action"] == "REVIVE_INFRA"]
    assert any(event["status"] == "retry" for event in infra)
    assert any(event["status"] == "failed" for event in infra)
    assert result.infrastructure_failures


def test_infra_retry_backs_off_and_ignores_other_errors():
    sleeps: list[float] = []
    trace = _Trace()
    failures: list[dict] = []

    class _Down:
        def complete_json(self, *args, **kwargs):
            raise RuntimeError("nightshift down")

    wrapper = rl.InfraRetry(_Down(), trace, failures, backoff=(0, 1, 3), sleep=sleeps.append)
    with pytest.raises(RuntimeError):
        wrapper.complete_json("prompt", step="revive_patch_1")
    assert sleeps == []
    assert failures == []

    class _Gateway:
        def complete_json(self, *args, **kwargs):
            raise _Http502()

    failures.clear()
    wrapper = rl.InfraRetry(_Gateway(), trace, failures, backoff=(0, 1, 3), sleep=sleeps.append)
    with pytest.raises(_Http502):
        wrapper.complete_json("prompt", step="revive_patch_1")
    assert sleeps == [1, 3]
    assert failures[0]["attempts"] == 3
    assert [event["status"] for event in trace.events] == ["retry", "retry", "failed"]


def test_environment_failure_is_its_own_terminal(tmp_path):
    repo = _make_repo(tmp_path)

    def runner(cmd, cwd, timeout):
        if str(cmd).startswith("npm install") or str(cmd).startswith("npm ci"):
            return rl.CmdResult(cmd, 0, "installed", 0.01)
        return rl.CmdResult(cmd, 1, "Cannot find module '@rollup/rollup-win32-x64-msvc'", 0.01)

    result = rl.revive(repo, _Client([]), _Trace(), "o/r", runner=runner, max_iterations=1, heartbeat_s=0)
    assert result.state == "ENV_FAILED"
    assert result.terminal == "ENV_FAILED"
    assert result.prd_items_done == []


def test_partial_terminal_when_items_landed_and_tests_still_fail():
    result = rl.ReviveResult(
        "RESURRECTION_FAILED",
        "tests still fail",
        final=rl.CmdResult("npm test", 1, "AssertionError"),
        prd_items_done=[{"id": "docs-1", "kind": "docs"}],
    )
    rl.apply_terminal(result)
    assert result.state == "PARTIAL"
    assert result.terminal == "PARTIAL"
    assert "still fail" in result.reason


def test_terminal_mapping_leaves_an_honest_green_baseline_alone():
    result = rl.ReviveResult("NOTHING_TO_FIX", "already passing", final=rl.CmdResult("npm test", 0, "ok"))
    rl.apply_terminal(result)
    assert result.state == "NOTHING_TO_FIX"
    assert result.terminal == "NOTHING_TO_FIX"


def test_heartbeat_writes_until_stopped():
    trace = _Trace()
    watch = rl.HeartbeatWatch(trace, 0.05)
    watch.start()
    time.sleep(0.45)
    watch.stop()
    beats = [event for event in trace.events if event["action"] == "REVIVE_HEARTBEAT"]
    assert beats
    assert beats[0]["output_summary"]["beat"] >= 1
    held = len(beats)
    time.sleep(0.3)
    assert len([event for event in trace.events if event["action"] == "REVIVE_HEARTBEAT"]) == held


def test_prescreen_ranks_and_continues_after_a_failure(tmp_path):
    clone_timeouts = []
    runner_timeouts = []

    def cloner(repo_id, dest, timeout):
        clone_timeouts.append((repo_id, timeout))
        assert timeout <= 180
        if repo_id == "bad/repo":
            raise TimeoutError("clone timed out")
        dest.mkdir(parents=True, exist_ok=True)
        if repo_id == "empty/repo":
            (dest / "package.json").write_text("{}", encoding="utf-8")
            return
        (dest / "package.json").write_text(json.dumps({"scripts": {"test": "node t.js"}}), encoding="utf-8")

    def runner(cmd, cwd, timeout):
        runner_timeouts.append(timeout)
        assert 1 <= timeout <= 180
        if "outdated" in cmd:
            return rl.CmdResult(cmd, 1, json.dumps({"left-pad": {"current": "1.0.0"}, "ms": {"current": "2.0.0"}}), 0.2)
        return rl.CmdResult(cmd, 1, "1 failing", 0.4)

    scores = {
        "ineligible": [
            {"repo_id": "good/repo", "verdict": "NEEDS_REVIEW", "reason": "thin", "language": "javascript"},
            {"repo_id": "bad/repo", "verdict": "NEEDS_PRIOR_ART", "reason": "search failed", "language": "python"},
            {"repo_id": "empty/repo", "verdict": "NEEDS_REVIEW", "reason": "no tests found"},
        ],
        "candidates": [{"repo_id": "skip/me", "verdict": "CANDIDATE"}],
    }
    path = tmp_path / "scores.json"
    path.write_text(json.dumps(scores), encoding="utf-8")
    selected = screen.load_review_repos(path)
    assert [row["repo_id"] for row in selected] == ["good/repo", "bad/repo", "empty/repo"]
    rows = screen.screen_repos(selected, tmp_path / "clones", cloner=cloner, runner=runner, per_repo_timeout=180)
    assert [row["repo"] for row in rows] == ["good/repo", "bad/repo", "empty/repo"]
    by_repo = {row["repo"]: row for row in rows}
    assert by_repo["good/repo"]["language"] == "javascript"
    assert by_repo["good/repo"]["has_tests"] is True
    assert by_repo["good/repo"]["install"]
    assert by_repo["good/repo"]["baseline_code"] == 1
    assert by_repo["good/repo"]["outdated"] == 2
    assert by_repo["good/repo"]["score"] == 6
    assert by_repo["good/repo"]["error"] == ""
    assert by_repo["bad/repo"]["score"] == 0
    assert "TimeoutError" in by_repo["bad/repo"]["error"]
    assert by_repo["bad/repo"]["language"] == "python"
    assert by_repo["empty/repo"]["has_tests"] is False
    assert by_repo["empty/repo"]["score"] == 0
    text = screen.render_markdown(rows)
    assert text.index("good/repo") < text.index("bad/repo")
    assert text.index("bad/repo") < text.index("empty/repo")
    assert "| Rank |" in text
    assert clone_timeouts and runner_timeouts
    assert screen.clone_command("good/repo", tmp_path / "x")[:4] == ["git", "clone", "--depth", "1"]


def test_prescreen_ignores_a_snapshot_without_verdicts(tmp_path, capsys):
    snapshot = {"repos": {"o/r": {"full_name": "o/r", "description": "x"}}}
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    assert screen.load_review_repos(path) == []
    assert screen.main(["--input", str(path), "--workdir", str(tmp_path / "w")]) == 0
    assert "no NEEDS_REVIEW or NEEDS_PRIOR_ART" in capsys.readouterr().out
    missing = tmp_path / "missing.json"
    assert screen.main(["--input", str(missing)]) == 2


def test_prescreen_main_uses_the_injected_cloner(tmp_path, capsys):
    path = tmp_path / "scores.json"
    path.write_text(
        json.dumps({"ineligible": [{"repo_id": "ok/repo", "verdict": "NEEDS_REVIEW", "reason": "open"}]}),
        encoding="utf-8",
    )
    seen = []

    def cloner(repo_id, dest, timeout):
        seen.append(repo_id)
        raise RuntimeError("offline")

    code = screen.main(
        ["--input", str(path), "--workdir", str(tmp_path / "w"), "--output", str(tmp_path / "table.md")],
        cloner=cloner,
        runner=lambda cmd, cwd, timeout: rl.CmdResult(cmd, 1, "nope"),
    )
    assert code == 0
    assert seen == ["ok/repo"]
    text = (tmp_path / "table.md").read_text(encoding="utf-8")
    assert "ok/repo" in text
    assert "RuntimeError" in text
    assert "0" in text


def test_cli_flags_and_batch_file(tmp_path):
    args = build_parser().parse_args(
        [
            "https://github.com/o/r",
            "--human-approved",
            "--reviewer",
            "Ada",
            "--review-note",
            "still open",
            "--deadline",
            "90",
            "--gate-verdict",
            "NEEDS_REVIEW",
            "--gate-reason",
            "thin prior art",
        ]
    )
    assert args.human_approved is True
    assert args.reviewer == "Ada"
    assert args.review_note == "still open"
    assert args.deadline == 90
    assert args.time_budget == 2400
    assert args.step_timeout == 900
    assert approval_error(True, "", "why") == APPROVAL_USAGE
    assert approval_error(False, "", "") is None
    approval = approval_from_flags("NEEDS_REVIEW", "thin prior art", True, "Ada", "still open")
    assert approval is not None
    assert approval["verdict"] == "NEEDS_REVIEW"
    assert approval["reason"] == "thin prior art"
    assert approval["timestamp"]
    batch = tmp_path / "batch.json"
    batch.write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "url": "https://github.com/o/r",
                        "human_approved": True,
                        "reviewer": "Ada",
                        "review_note": "still open",
                        "gate_verdict": "NEEDS_PRIOR_ART",
                        "reason": "search failed",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    jobs = load_batch(batch)
    assert jobs[0]["url"] == "https://github.com/o/r"
    assert jobs[0]["review_note"] == "still open"


def test_cli_refuses_a_blocked_verdict_and_passes_approval(tmp_path, monkeypatch, capsys):
    import scripts.revive as revive_cli

    monkeypatch.setattr(revive_cli, "ROOT", tmp_path)
    cloned = []

    def fake_git(args, cwd):
        cloned.append(list(args))
        if args and args[0] == "clone":
            Path(args[-1]).mkdir(parents=True, exist_ok=True)
        return ""

    monkeypatch.setattr(revive_cli, "_git", fake_git)
    monkeypatch.setattr(revive_cli.judge_repo, "fetch_repo", lambda name: {"repo": {"description": ""}})
    monkeypatch.setattr(revive_cli, "RelevanceClient", lambda: type("C", (), {"served_models": set()})())

    def explode(*args, **kwargs):
        raise AssertionError("gate 1 was re-judged")

    monkeypatch.setattr(revive_cli.judge_repo, "judge", explode)
    batch = tmp_path / "batch.json"
    batch.write_text(
        json.dumps(
            [
                {
                    "url": "https://github.com/blocked/repo",
                    "gate_verdict": "ALREADY_EXISTS",
                    "reason": "a maintained project already does this",
                    "human_approved": True,
                    "reviewer": "Ada",
                    "review_note": "please try anyway",
                },
                {
                    "url": "https://github.com/open/repo",
                    "gate_verdict": "NEEDS_REVIEW",
                    "gate_reason": "prior art was thin",
                    "human_approved": True,
                    "reviewer": "Ada",
                    "review_note": "tests exist and the problem is still open",
                },
            ]
        ),
        encoding="utf-8",
    )
    code = main(["--batch", str(batch), "--workdir", str(tmp_path / "work"), "--run-id", "batchrun", "--deadline", "30"])
    assert code == 3
    assert any("open/repo" in " ".join(args) for args in cloned)
    assert all("blocked/repo" not in " ".join(args) for args in cloned)
    reports = list((tmp_path / "reports").glob("*/evidence.html"))
    bodies = [path.read_text(encoding="utf-8") for path in reports]
    assert any("NOT_ATTEMPTED_GATE1" in body and "a maintained project already does this" in body for body in bodies)
    assert any("HUMAN_APPROVED_AFTER_NEEDS_REVIEW" in body and "prior art was thin" in body for body in bodies)
    traces = " ".join(path.read_text(encoding="utf-8") for path in (tmp_path / "logs").glob("trace-*.jsonl"))
    assert "supplied" in traces
    assert "a maintained project already does this" in traces
    assert "prior art was thin" in traces
    out = capsys.readouterr().out
    assert "NOT_ATTEMPTED_GATE1" in out
    assert "NEEDS_REVIEW" in out


def test_cli_usage_errors_do_not_judge(monkeypatch):
    import scripts.revive as revive_cli

    monkeypatch.setattr(revive_cli.judge_repo, "judge", lambda *a, **k: (_ for _ in ()).throw(AssertionError("judged")))
    assert main([]) == 2
    assert main(["https://github.com/o/r", "--human-approved", "--gate-verdict", "NEEDS_REVIEW"]) == 2


def test_supplied_verdict_without_a_reason_is_marked_reused(tmp_path, monkeypatch):
    import scripts.revive as revive_cli

    monkeypatch.setattr(revive_cli, "ROOT", tmp_path)
    monkeypatch.setattr(revive_cli.judge_repo, "judge", lambda *a, **k: (_ for _ in ()).throw(AssertionError("judged")))
    code = main(["https://github.com/o/r", "--gate-verdict", "NO_GO", "--run-id", "reused", "--workdir", str(tmp_path / "work")])
    assert code == 3
    trace = (tmp_path / "logs" / "trace-reused.jsonl").read_text(encoding="utf-8")
    assert REUSED_GATE_REASON in trace
    assert "supplied" in trace
