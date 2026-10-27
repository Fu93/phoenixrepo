"""Offline tests for the Gate 2 Docker sandbox runner and the verification.json seal.

Nothing here needs Docker: the runner is driven in dry_run mode, which records every docker
invocation instead of executing it. The real-container checks are in test_gate2_docker_smoke.py.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

import pytest

from app import revive_loop as rl
from app.revive_loop import CmdResult, ReviveResult
from app.revive_prd import write_reports
from app.revive_sandbox import (
    UNSUPPORTED_GO_MESSAGE,
    CommandRunner,
    DockerSandboxRunner,
    HostRunner,
    VerificationSealConflict,
    check_verification_seal,
    detect_project_kind,
    get_runner,
    seal_verification,
)
from app.trace_log import HackathonTrace
from scripts.verify_trace import verify
from tests.test_revive_loop import _Client, _make_repo, _runner_factory, _valid_prd


def _mk(path: Path) -> Path:
    path.mkdir(parents=True)
    return path


def _flat(planned: list[list[str]]) -> list[str]:
    return [" ".join(args) for args in planned]


def _kinds(planned: list[list[str]]) -> list[str]:
    """docker subcommand sequence, e.g. run, exec, network, rm."""
    return [args[1] for args in planned]


def _repo_with_git(tmp_path: Path) -> Path:
    repo = tmp_path / "candidate_repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    (repo / ".env").write_text("SECRET_KEY=from_the_candidate_clone\n", encoding="utf-8")
    return repo


# ---- runner selection -----------------------------------------------------------------------


def test_get_runner_modes_and_callable_protocol(tmp_path: Path) -> None:
    assert isinstance(get_runner("host"), HostRunner)
    assert isinstance(get_runner("local"), HostRunner)
    docker_runner = get_runner("docker", dry_run=True)
    assert isinstance(docker_runner, DockerSandboxRunner)
    assert isinstance(get_runner("sandbox", dry_run=True), DockerSandboxRunner)
    with pytest.raises(ValueError, match="Unknown runner mode"):
        get_runner("invalid_mode")
    assert issubclass(HostRunner, CommandRunner) and issubclass(DockerSandboxRunner, CommandRunner)
    res = docker_runner("pytest", tmp_path, 30)
    assert isinstance(res, CmdResult) and res.ok and "[dry-run]" in res.output
    docker_runner.close()


def test_root_uid_is_refused() -> None:
    for bad in ("0:0", "0:1000", "root", "1000"):
        with pytest.raises(ValueError):
            DockerSandboxRunner(uid_gid=bad, dry_run=True)


# ---- container shape ------------------------------------------------------------------------


def test_container_is_persistent_locked_down_and_mounts_only_the_clone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_API_KEY", "dummy")
    monkeypatch.setenv("FAKE_HOST_VALUE", "dummy")
    repo = _repo_with_git(tmp_path)
    runner = DockerSandboxRunner(run_id="unit1", dry_run=True)
    args = runner.build_run_args(repo)

    assert args[:3] == ["docker", "run", "-d"], "one persistent container, not `docker run --rm` per command"
    assert "--rm" not in args
    assert args[args.index("--name") + 1] == "phoenix-g2-unit1"
    assert args[args.index("--user") + 1] == "1000:1000"
    assert args[args.index("--cap-drop") + 1] == "ALL"
    assert args[args.index("--security-opt") + 1] == "no-new-privileges"
    assert "--pids-limit" in args and "--read-only" in args
    assert "--memory=2g" in args and "--cpus=2" in args and "--memory-swap=2g" in args
    assert args[args.index("-w") + 1] == "/workspace"
    assert args[-4:] == ["python:3.12-slim", "sh", "-c", "while :; do wait; sleep 1; done"]

    # Mounts: the clone rw and its .git ro, nothing else from the host.
    mounts = [args[i + 1] for i, token in enumerate(args) if token == "--mount"]
    clone = repo.resolve().as_posix()
    assert mounts == [
        f"type=bind,source={clone},target=/workspace",
        f"type=bind,source={clone}/.git,target=/workspace/.git,readonly",
    ]
    assert "-v" not in args and "--volume" not in args and "--env-file" not in args
    joined = " ".join(args)
    assert "docker.sock" not in joined and "/var/run" not in joined
    assert ".env" not in joined
    assert repo.parent.resolve().as_posix() not in joined.replace(clone, "")

    # Writable state is on a tmpfs, so the root fs stays read-only and the clone stays clean.
    tmpfs = [args[i + 1] for i, token in enumerate(args) if token == "--tmpfs"]
    assert any(t.startswith("/sandbox:rw,exec,") and "uid=1000" in t for t in tmpfs)
    assert any(t.startswith("/tmp:") and "noexec" in t for t in tmpfs)

    env = dict(args[i + 1].partition("=")[::2] for i, token in enumerate(args) if token == "-e")
    assert env["HOME"] == "/sandbox/home"
    assert env["PIP_CACHE_DIR"].startswith("/sandbox/")
    assert env["VIRTUAL_ENV"] == "/sandbox/venv"
    assert env["PATH"].startswith("/sandbox/venv/bin:")
    assert "FAKE_API_KEY" not in env and "FAKE_HOST_VALUE" not in env and "FAKE_API_KEY" not in joined
    assert not (set(env) & {"USERNAME", "USERPROFILE", "SystemRoot", "GITHUB_TOKEN", "OPENAI_API_KEY"})


def test_git_mount_is_omitted_when_there_is_no_git_dir(tmp_path: Path) -> None:
    repo = tmp_path / "plain"
    repo.mkdir()
    args = DockerSandboxRunner(dry_run=True).build_run_args(repo)
    assert not any(".git" in token for token in args)


def test_exec_args_scrub_credentials_and_reserved_names(tmp_path: Path) -> None:
    runner = DockerSandboxRunner(run_id="unit2", dry_run=True)
    dirty = {
        "GITHUB_TOKEN": "x", "GH_TOKEN": "x", "OPENAI_API_KEY": "x", "ANTHROPIC_API_KEY": "x", "NVIDIA_API_KEY": "x",
        "MODEL_API_KEY": "x", "XAI_API_KEY": "x", "DATABASE_PASSWORD": "x", "AWS_SECRET_ACCESS_KEY": "x",
        "SERVICE_PRIVATE_KEY": "x", "USER_CREDENTIALS": "x", "PATH": "/evil", "HOME": "/evil",
        "SAFE_CUSTOM_FLAG": "enabled", "BUILD_TARGET": "production",
    }
    args = runner.build_exec_args("pytest -q", "/workspace", dirty)
    passed = dict(args[i + 1].partition("=")[::2] for i, token in enumerate(args) if token == "-e")
    assert passed == {"SAFE_CUSTOM_FLAG": "enabled", "BUILD_TARGET": "production"}
    assert args[-4:] == ["phoenix-g2-unit2", "sh", "-c", "pytest -q"]
    assert args[:4] == ["docker", "exec", "-w", "/workspace"]


# ---- image selection by project type ------------------------------------------------------------


@pytest.mark.parametrize(
    ("files", "kind"),
    [
        (["package.json"], "node"),
        (["package.json", "requirements.txt"], "node"),  # same precedence as detect_commands
        (["pyproject.toml"], "python"),
        (["requirements.txt"], "python"),
        (["setup.py"], "python"),
        (["go.mod"], "go"),
        (["go.mod", "requirements.txt"], "python"),
        ([], "unknown"),
    ],
)
def test_detect_project_kind(tmp_path: Path, files: list[str], kind: str) -> None:
    for name in files:
        (tmp_path / name).write_text("", encoding="utf-8")
    assert detect_project_kind(tmp_path) == kind


@pytest.mark.parametrize(
    ("files", "image"),
    [(["package.json"], "node:22-slim"), (["pyproject.toml"], "python:3.12-slim"), ([], "python:3.12-slim")],
)
def test_image_follows_the_manifest_and_an_explicit_image_wins(tmp_path: Path, files: list[str], image: str) -> None:
    for name in files:
        (tmp_path / name).write_text("", encoding="utf-8")
    runner = DockerSandboxRunner(dry_run=True)
    assert runner.build_run_args(tmp_path)[-4] == image
    assert runner.image_used == image
    explicit = DockerSandboxRunner(image="example/custom:1", dry_run=True)
    assert explicit.build_run_args(tmp_path)[-4] == "example/custom:1"


def test_node_container_has_no_venv_and_installs_run_on_the_network_phase(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    runner = DockerSandboxRunner(run_id="node1", dry_run=True)
    runner("npm install --no-audit --no-fund", tmp_path, 60, phase="install")
    runner("npm test --silent", tmp_path, 60, phase="test")
    flat = _flat(runner.planned)
    run_line = next(line for line in flat if line.startswith("docker run -d"))
    assert "node:22-slim" in run_line and "VIRTUAL_ENV" not in run_line and "/sandbox/venv" not in run_line
    assert "npm_config_cache=/sandbox/npm-cache" in run_line
    boot = next(line for line in flat if "mkdir -p /sandbox/home" in line)
    assert "venv" not in boot
    order = [line.split()[2] if line.startswith("docker network") else ("INSTALL" if "npm install" in line else "TEST" if "npm test" in line else "") for line in flat]
    assert [x for x in order if x] == ["INSTALL", "disconnect", "TEST"]


def test_staged_node_tree_is_linked_to_the_clones_node_modules(tmp_path: Path) -> None:
    clone = _mk(tmp_path / "clone")
    (clone / "package.json").write_text("{}", encoding="utf-8")
    other = _mk(tmp_path / "other")
    runner = DockerSandboxRunner(run_id="node2", dry_run=True)
    runner("npm test", clone, 5, phase="verify")
    runner("npm test", other, 5, phase="verify")
    assert any("ln -s /workspace/node_modules" in line for line in _flat(runner.planned))


def test_go_projects_are_refused_with_a_clear_message_and_start_no_container(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module x\n", encoding="utf-8")
    runner = DockerSandboxRunner(dry_run=True)
    res = runner("go test ./...", tmp_path, 30, phase="test")
    assert res.code == 125 and UNSUPPORTED_GO_MESSAGE in res.output and "--sandbox-mode host" in res.output
    assert runner.planned == []


# ---- lifecycle and network phases -------------------------------------------------------------


def test_one_container_many_execs_network_follows_the_explicit_phase(tmp_path: Path) -> None:
    repo = _repo_with_git(tmp_path)
    runner = DockerSandboxRunner(run_id="unit3", dry_run=True)
    runner("python -m pip install -q -r requirements.txt", repo, 60, phase="install")
    runner("python -m pip install -q pytest", repo, 60, phase="install")
    runner("python -m pytest -x -q", repo, 60, phase="test")
    runner("python -m pytest -x -q", repo, 60, phase="verify")
    runner("python -m pip install --no-save more", repo, 60, phase="install")
    runner("python -m pytest -x -q", repo, 60, phase="test")
    runner.close()

    flat = _flat(runner.planned)
    assert sum(1 for line in flat if line.startswith("docker run -d")) == 1
    steps = []
    for line in flat:
        if line.startswith("docker network"):
            steps.append(line.split()[2])
        elif line.startswith("docker exec") and " sh -c " in line and "pytest" in line and "install" not in line:
            steps.append("TEST")
        elif line.startswith("docker exec") and "pip install" in line:
            steps.append("INSTALL")
    # Network stays on for the installs, is cut once before the first test and not again before the
    # verify run, is re-attached for the next install, and is cut again afterwards.
    assert steps == ["INSTALL", "INSTALL", "disconnect", "TEST", "TEST", "connect", "INSTALL", "disconnect", "TEST"]
    assert flat[-1] == "docker rm -f phoenix-g2-unit3"


def test_network_is_never_inferred_from_the_command_text(tmp_path: Path) -> None:
    runner = DockerSandboxRunner(run_id="unit4", dry_run=True)
    # "install" in the text, but the caller says test: no network.
    runner("python -m pip install requests", tmp_path, 30, phase="test")
    assert _flat(runner.planned)[-2].startswith("docker network disconnect bridge phoenix-g2-unit4")
    # A neutral command in an install phase does get one.
    runner("echo hi", tmp_path, 30, phase="install")
    assert any(line.startswith("docker network connect bridge phoenix-g2-unit4") for line in _flat(runner.planned))
    assert runner.wants_network("install") is True


@pytest.mark.parametrize("phase", ["exec", "test", "verify", "build", "acceptance", "", "INSTALL", "bogus", "download"])
def test_unknown_or_non_install_phase_gets_no_network(tmp_path: Path, phase: str) -> None:
    runner = DockerSandboxRunner(dry_run=True)
    assert runner.wants_network(phase) is False
    runner("echo", tmp_path, 5, phase=phase)
    assert any(line.startswith("docker network disconnect") for line in _flat(runner.planned))
    assert not any(line.startswith("docker network connect") for line in _flat(runner.planned))


def test_runner_without_a_phase_argument_defaults_to_no_network(tmp_path: Path) -> None:
    runner = DockerSandboxRunner(dry_run=True)
    runner("echo", tmp_path, 5)  # legacy 3-argument call
    assert any(line.startswith("docker network disconnect") for line in _flat(runner.planned))


def test_close_removes_the_container_is_idempotent_and_blocks_reuse(tmp_path: Path) -> None:
    runner = DockerSandboxRunner(run_id="unit5", dry_run=True)
    runner("echo", tmp_path, 5, phase="test")
    runner.close()
    runner.close()
    assert sum(1 for line in _flat(runner.planned) if line == "docker rm -f phoenix-g2-unit5") == 1
    assert runner.close_ok is True
    res = runner("echo", tmp_path, 5, phase="test")
    assert res.code == 125 and "closed" in res.output


def test_context_manager_closes(tmp_path: Path) -> None:
    with DockerSandboxRunner(run_id="unit6", dry_run=True) as runner:
        runner("echo", tmp_path, 5, phase="test")
    assert "docker rm -f phoenix-g2-unit6" in _flat(runner.planned)


def test_cwd_outside_the_clone_is_staged_into_the_container_and_removed(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    clone.mkdir()
    other = tmp_path / "verify_tree"
    other.mkdir()
    (other / "a.py").write_text("x = 1\n", encoding="utf-8")
    runner = DockerSandboxRunner(run_id="unit7", dry_run=True)
    runner("pytest", clone, 5, phase="verify")  # binds the clone
    runner("pytest", other, 5, phase="verify")
    flat = _flat(runner.planned)
    assert any(" tar -x -C /sandbox/stage/" in line for line in flat)
    assert any("exec -w /sandbox/stage/" in line for line in flat)
    assert any(" rm -rf /sandbox/stage/" in line for line in flat)
    sub = clone / "pkg"
    sub.mkdir()
    runner("pytest", sub, 5, phase="test")
    assert _flat(runner.planned)[-1] == "docker exec -w /workspace/pkg phoenix-g2-unit7 sh -c pytest"


def test_timeout_teardown_kills_everything_but_pid1(tmp_path: Path) -> None:
    runner = DockerSandboxRunner(run_id="unit8", dry_run=True)
    runner("sleep 999", tmp_path, 5, phase="test")
    assert runner._kill_everything(None) is True
    assert any("kill -9 -1" in line for line in _flat(runner.planned))


# ---- revive() wiring ---------------------------------------------------------------------------


def test_default_revive_is_the_unchanged_host_path(monkeypatch: pytest.MonkeyPatch) -> None:
    assert inspect.signature(rl._revive_impl).parameters["runner"].default is rl.shell_runner
    seen: dict = {}

    def fake_impl(*args, **kwargs):
        seen.update(kwargs)
        return "result"

    monkeypatch.setattr(rl, "_revive_impl", fake_impl)
    assert rl.revive(Path("."), None, None, "o/r") == "result"
    assert "runner" not in seen, "host mode must leave main's default runner (shell_runner) in place"


def test_revive_docker_mode_always_closes_its_container(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.revive_sandbox as sandbox

    made: list[DockerSandboxRunner] = []

    def fake_get_runner(mode: str = "host", **kwargs):
        runner = DockerSandboxRunner(dry_run=True, **kwargs)
        made.append(runner)
        return runner

    def boom(*args, **kwargs):
        made[0]("echo", Path("."), 1, phase="test")
        raise RuntimeError("model step exploded")

    monkeypatch.setattr(sandbox, "get_runner", fake_get_runner)
    monkeypatch.setattr(rl, "_revive_impl", boom)

    class T:
        run_id = "run/42 weird"

    with pytest.raises(RuntimeError, match="exploded"):
        rl.revive(Path("."), None, T(), "o/r", sandbox_mode="docker")
    assert made[0].name == "phoenix-g2-run-42-weird"
    assert made[0]._closed and "docker rm -f phoenix-g2-run-42-weird" in _flat(made[0].planned)

    # A caller-supplied runner is the caller's to close.
    caller = DockerSandboxRunner(dry_run=True)
    monkeypatch.setattr(rl, "_revive_impl", lambda *a, **k: "ok")
    assert rl.revive(Path("."), None, T(), "o/r", sandbox_mode="docker", runner=caller) == "ok"
    assert not caller._closed
    caller.close()


def test_revive_passes_explicit_phases(tmp_path: Path) -> None:
    repo = _make_repo(_mk(tmp_path / "repo"))
    inner, _ = _runner_factory(repo)
    calls: list[tuple[str, str]] = []

    class PhaseRunner(CommandRunner):
        def run(self, cmd, cwd, timeout=300, environ=None, phase="exec"):
            calls.append((cmd, phase))
            return inner(cmd, cwd, timeout)

    client = _Client([_valid_prd(), {"rationale": "r", "edits": [{"path": "src.js", "find": "=> 1", "replace": "=> 2"}]}])
    res = rl.revive(repo, client, HackathonTrace("phase-run", tmp_path), "o/r", runner=PhaseRunner(), heartbeat_s=0)
    assert res.state == "RESURRECTED"
    by_phase = {phase: [c for c, p in calls if p == phase] for phase in {p for _, p in calls}}
    assert all(cmd.startswith("npm install") or cmd.startswith("npm ci") for cmd in by_phase["install"])
    assert "npm test --silent" in by_phase["test"] and "verify" in by_phase
    assert not any("install" in cmd for phase, cmds in by_phase.items() if phase != "install" for cmd in cmds)


def test_ensure_env_repair_install_uses_the_install_runner(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    seen: list[tuple[str, str]] = []
    n = {"tests": 0}

    def test_runner(cmd, cwd, timeout):
        seen.append(("test", cmd))
        n["tests"] += 1
        out = "Cannot find module '@rollup/rollup-linux-x64-gnu'" if n["tests"] == 1 else "ok"
        return CmdResult(cmd, 1 if n["tests"] == 1 else 0, out)

    def install_runner(cmd, cwd, timeout):
        seen.append(("install", cmd))
        return CmdResult(cmd, 0, "")

    res = rl.ensure_env(tmp_path, test_runner, "npm test", install_runner=install_runner)
    assert res.ok
    assert [kind for kind, _ in seen] == ["test", "install", "test"]


# ---- verification seal -----------------------------------------------------------------------


def _verification() -> dict:
    return {
        "checks": [
            {"id": "final_tests", "status": "PASS", "detail": "12 passed"},
            {"id": "diff_budget", "status": "PASS", "detail": "4 files"},
        ],
        "resurrection_allowed": True,
        "blocked_reason": "",
    }


def _seal_events(trace: HackathonTrace) -> list[dict]:
    lines = [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [rec for rec in lines if rec.get("action") == "GATE2_VERIFICATION_SEAL"]


def _sealed_sha(event: dict) -> str:
    summary = event["output_summary"]
    return (json.loads(summary) if isinstance(summary, str) else summary)["sha256"]


def test_seal_event_hash_equals_final_file_chain_verifies_and_tampering_is_detected(tmp_path: Path) -> None:
    out = tmp_path / "reports"
    trace = HackathonTrace("seal-1", tmp_path)
    trace.event(agent_name="builder", action="REVIVE_RESULT", input_summary="o/r", output_summary={"state": "RESURRECTED"})
    digest, summary = seal_verification(out, _verification(), trace=trace)
    trace.event(agent_name="builder", action="AFTER_SEAL", input_summary="x", output_summary={})
    run_hash = trace.seal()

    v_file = out / "verification.json"
    on_disk = hashlib.sha256(v_file.read_bytes()).hexdigest()
    events = _seal_events(trace)
    assert len(events) == 1
    assert _sealed_sha(events[0]) == on_disk == digest == summary["sha256"]
    assert summary["size_bytes"] == len(v_file.read_bytes()) and summary["checks_count"] == 2
    assert b"\r\n" not in v_file.read_bytes(), "same bytes on every OS"

    ok, detail = verify(trace.path, expected_run_hash=run_hash)
    assert ok, detail
    assert check_verification_seal(v_file, trace.path) == (True, "verification.json matches the sealed hash")

    # Edit verification.json after the run: the trace chain is still valid (the trace was not
    # touched) but the file no longer matches the sealed event, and that is detectable.
    tampered = _verification() | {"resurrection_allowed": False}
    v_file.write_text(json.dumps(tampered, indent=2), encoding="utf-8")
    assert hashlib.sha256(v_file.read_bytes()).hexdigest() != _sealed_sha(events[0])
    assert verify(trace.path, expected_run_hash=run_hash)[0]
    ok, detail = check_verification_seal(v_file, trace.path)
    assert not ok and "does not match" in detail


def test_seal_is_single_authoritative_second_different_content_is_refused(tmp_path: Path) -> None:
    out = tmp_path / "reports"
    trace = HackathonTrace("seal-2", tmp_path)
    first, _ = seal_verification(out, _verification(), trace=trace)
    again, _ = seal_verification(out, _verification(), trace=trace)  # identical: no new event
    assert first == again and len(_seal_events(trace)) == 1
    with pytest.raises(VerificationSealConflict):
        seal_verification(out, _verification() | {"resurrection_allowed": False}, trace=trace)
    assert hashlib.sha256((out / "verification.json").read_bytes()).hexdigest() == first, "file untouched by the refused seal"
    assert len(_seal_events(trace)) == 1


def test_seal_without_a_trace_only_writes_the_file(tmp_path: Path) -> None:
    digest, _ = seal_verification(tmp_path, None)
    assert json.loads((tmp_path / "verification.json").read_text(encoding="utf-8"))["blocked_reason"] == "verification was not run"
    assert digest == hashlib.sha256((tmp_path / "verification.json").read_bytes()).hexdigest()


def test_write_reports_is_the_one_seal_and_its_event_matches_the_file(tmp_path: Path) -> None:
    out = tmp_path / "out"
    trace = HackathonTrace("reports-1", tmp_path)
    result = ReviveResult(state="RESURRECTED", reason="all tests passing", verification=_verification())
    write_reports(out, result, "test/repo", "reports-1", trace=trace)
    write_reports(out, result, "test/repo", "reports-1", trace=trace)  # same content: still one event
    events = _seal_events(trace)
    assert len(events) == 1
    assert _sealed_sha(events[0]) == hashlib.sha256((out / "verification.json").read_bytes()).hexdigest()
    for name in ("quality.json", "PRD.md", "evidence.html"):
        assert (out / name).exists()
    ok, detail = verify(trace.path, expected_run_hash=trace.seal())
    assert ok, detail


def test_revive_itself_adds_no_seal_event_and_write_reports_seals_the_final_result(tmp_path: Path) -> None:
    """The CLI order: revive() -> write_reports() -> trace.seal(). Host verdicts are unchanged."""
    repo = _make_repo(_mk(tmp_path / "repo"))
    runner, _ = _runner_factory(repo)
    trace = HackathonTrace("flow-1", tmp_path)
    client = _Client([_valid_prd(), {"rationale": "r", "edits": [{"path": "src.js", "find": "=> 1", "replace": "=> 2"}]}])
    res = rl.revive(repo, client, trace, "o/r", runner=runner, run_dir=tmp_path / "run_dir", heartbeat_s=0)
    assert res.state == "RESURRECTED"
    assert _seal_events(trace) == [], "revive() must not seal; the seal happens once, after the final verification"
    assert not (tmp_path / "run_dir" / "verification.json").exists()

    out = tmp_path / "reports"
    write_reports(out, res, "o/r", "flow-1", trace=trace)
    run_hash = trace.seal()
    events = _seal_events(trace)
    assert len(events) == 1
    assert _sealed_sha(events[0]) == hashlib.sha256((out / "verification.json").read_bytes()).hexdigest()
    assert json.loads((out / "verification.json").read_text(encoding="utf-8")) == res.verification
    assert verify(trace.path, expected_run_hash=run_hash)[0]
    assert check_verification_seal(out / "verification.json", trace.path)[0]