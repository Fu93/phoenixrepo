"""Gate 2 container sandbox runner and verification-hash sealing.

Two runners share one interface (CommandRunner). The image follows the project type (python:3.12-slim
for Python, node:22-slim for Node; Go is refused, see detect_project_kind):

- HostRunner: runs on the host through shell_runner. This is the default and is unchanged.
- DockerSandboxRunner: runs every command of ONE revival run inside ONE persistent container
  (docker run -d ... then docker exec per command), removed again by close().

What the container setup does (see docs/GATE2_SANDBOX.md for what it does NOT guarantee):

- Only the candidate clone is mounted (rw) at /workspace, with its .git mounted read-only on top.
  No .env, no other host path, no docker.sock. The host environment is never inherited.
- Non-root user (default 1000:1000), --cap-drop ALL, no-new-privileges, pids/memory/cpu limits,
  read-only root filesystem.
- Writable state lives on a tmpfs at /sandbox (HOME, pip cache, TMPDIR and a venv at
  /sandbox/venv that is first on PATH). It persists for the container lifetime, so a pip install
  in one command is visible to the next one, and it never touches the clone.
- Network is explicit per phase. The container starts attached to a network; before each command
  the runner attaches it (phase in network_enabled_phases, default only "install") or runs
  `docker network disconnect` (every other phase, including unknown ones). Fail closed: if the
  network state cannot be set, the command is not run.
- A timed-out command kills every process in the container (except PID 1) and the container keeps
  running with its venv; if that fails the container is removed and re-created on the next command.

seal_verification() writes verification.json and records its SHA-256 in the trace chain once.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import re
import subprocess
import tarfile
import threading
import time
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from app.revive_loop import (
    _SECRET_ENV_RE,
    SECRET_ENV_NAMES,
    CmdResult,
    decode_output,
    shell_runner,
)

DEFAULT_IMAGE = "python:3.12-slim"
NODE_IMAGE = "node:22-slim"
IMAGES = {"python": DEFAULT_IMAGE, "node": NODE_IMAGE}
UNSUPPORTED_GO_MESSAGE = (
    "Go projects (go.mod) are not supported by the Docker sandbox: no Go image is configured and nothing "
    "was run. Use --sandbox-mode host for this repository."
)
CONTAINER_PREFIX = "phoenix-g2-"
SANDBOX_LABEL = "phoenix.g2.sandbox"
WORKSPACE = "/workspace"
SANDBOX_DIR = "/sandbox"
# PID 1 inside the container (a plain shell, so every image works): `wait` reaps orphans, and PID 1
# ignores SIGKILL sent from inside the container, so a timeout can `kill -9 -1` every other process
# without stopping the container (and losing the tmpfs state).
_REAPER = ["sh", "-c", "while :; do wait; sleep 1; done"]
_RESERVED_ENV = frozenset({"PATH", "HOME", "VIRTUAL_ENV", "TMPDIR", "PIP_CACHE_DIR"})


def _clean_mount_path(path: Path | str) -> str:
    """Forward-slash absolute path, accepted by Docker Desktop on Windows and by Linux docker."""
    return Path(path).resolve().as_posix()


def detect_project_kind(root: Path | str) -> str:
    """"node", "python", "go" or "unknown", from the manifest. Same precedence as detect_commands:
    package.json first, then Python manifests, then go.mod. A repo with several is treated as the first."""
    base = Path(root)
    if (base / "package.json").is_file():
        return "node"
    if any((base / name).is_file() for name in ("pyproject.toml", "requirements.txt", "setup.py", "setup.cfg")):
        return "python"
    if (base / "go.mod").is_file():
        return "go"
    return "unknown"


def _baseline_env(kind: str = "python") -> dict[str, str]:
    venv = kind != "node"
    return {
        "HOME": f"{SANDBOX_DIR}/home",
        "TMPDIR": f"{SANDBOX_DIR}/tmp",
        **({"VIRTUAL_ENV": f"{SANDBOX_DIR}/venv"} if venv else {}),
        "PATH": ("/sandbox/venv/bin:" if venv else "") + "/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin",
        "PIP_CACHE_DIR": f"{SANDBOX_DIR}/pip-cache",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INPUT": "1",
        "npm_config_cache": f"{SANDBOX_DIR}/npm-cache",
        "PYTHONDONTWRITEBYTECODE": "1",
        "CI": "1",
        "NO_COLOR": "1",
        "FORCE_COLOR": "0",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    }


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "")).strip("-.")
    return cleaned[:48] or uuid.uuid4().hex[:12]


class CommandRunner(ABC):
    """A runner executes one shell command in a directory and reports a CmdResult."""

    @abstractmethod
    def run(
        self,
        cmd: str,
        cwd: Path,
        timeout: float = 300,
        environ: dict[str, str] | None = None,
        phase: str = "exec",
    ) -> CmdResult:
        """Execute cmd in cwd. phase is explicit: "install" may use the network, nothing else does."""
        ...

    def __call__(self, cmd: str, cwd: Path, timeout: float = 300, *, environ: dict[str, str] | None = None, phase: str = "exec") -> CmdResult:
        return self.run(cmd, cwd, timeout=timeout, environ=environ, phase=phase)

    def close(self) -> None:  # noqa: B027 - optional hook
        """Release anything the runner holds. Idempotent."""

    def __enter__(self) -> CommandRunner:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class HostRunner(CommandRunner):
    """Host execution through shell_runner. The phase is accepted and ignored."""

    def run(self, cmd: str, cwd: Path, timeout: float = 300, environ: dict[str, str] | None = None, phase: str = "exec") -> CmdResult:
        del phase
        return shell_runner(cmd, cwd, int(timeout), environ=environ)


_LIVE: set[DockerSandboxRunner] = set()


@atexit.register
def _close_live_runners() -> None:  # pragma: no cover - interpreter shutdown safety net
    for runner in list(_LIVE):
        try:
            runner.close()
        except Exception:  # noqa: BLE001
            pass


class DockerSandboxRunner(CommandRunner):
    """One persistent, locked-down container per run. Use as a context manager or call close()."""

    def __init__(
        self,
        image: str | None = None,
        uid_gid: str = "1000:1000",
        memory_limit: str = "2g",
        cpus: str = "2",
        pids_limit: int = 512,
        network_enabled_phases: tuple[str, ...] = ("install",),
        network_name: str = "bridge",
        dry_run: bool = False,
        docker_bin: str = "docker",
        run_id: str | None = None,
        sandbox_size: str = "1g",
        tmp_size: str = "256m",
    ) -> None:
        if not re.fullmatch(r"\d+:\d+", uid_gid) or uid_gid.split(":")[0] == "0":
            raise ValueError(f"uid_gid must be a non-root numeric uid:gid, got {uid_gid!r}")
        self.image = image  # None: chosen from the project type on the first command
        self.kind = "unknown"
        self._resolved = False
        self.uid_gid = uid_gid
        self.memory_limit = memory_limit
        self.cpus = cpus
        self.pids_limit = int(pids_limit)
        self.network_enabled_phases = tuple(network_enabled_phases)
        self.network_name = network_name
        self.dry_run = dry_run
        self.docker_bin = docker_bin
        self.run_id = _safe_name(run_id) if run_id else uuid.uuid4().hex[:12]
        self.sandbox_size = sandbox_size
        self.tmp_size = tmp_size
        self.planned: list[list[str]] = []  # every docker invocation, filled in dry-run mode
        self._root: Path | None = None
        self._generation = 0
        self._started = False
        self._net_attached = True  # a fresh container starts attached to network_name
        self._closed = False
        self._lock = threading.RLock()
        self.close_ok: bool | None = None
        self._note = ""
        _LIVE.add(self)

    # ---- naming and command composition -------------------------------------------------

    @property
    def name(self) -> str:
        base = f"{CONTAINER_PREFIX}{self.run_id}"
        return base if self._generation == 0 else f"{base}-r{self._generation}"

    def wants_network(self, phase: str) -> bool:
        """Only a phase that is explicitly listed gets a network. Unknown phases get none."""
        return phase in self.network_enabled_phases

    def _resolve(self, root: Path) -> None:
        if not self._resolved:
            self.kind = detect_project_kind(root)
            self._resolved = True

    @property
    def image_used(self) -> str:
        """The image this runner starts: the explicit one, else by project type (Python for unknown)."""
        return self.image or IMAGES.get(self.kind, DEFAULT_IMAGE)

    def build_run_args(self, root: Path) -> list[str]:
        """`docker run -d` for the persistent container. Mounts: the clone (rw) and its .git (ro)."""
        resolved = Path(root).resolve()
        self._resolve(resolved)
        src = _clean_mount_path(resolved)
        if "," in src:
            raise ValueError("clone path contains a comma; docker --mount cannot express it")
        uid, gid = self.uid_gid.split(":")
        args = [
            self.docker_bin, "run", "-d",
            "--name", self.name,
            "--label", f"{SANDBOX_LABEL}=1",
            "--user", self.uid_gid,
            "--network", self.network_name,
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--pids-limit", str(self.pids_limit),
            f"--memory={self.memory_limit}",
            f"--memory-swap={self.memory_limit}",
            f"--cpus={self.cpus}",
            "--read-only",
            "--tmpfs", f"/tmp:rw,noexec,nosuid,nodev,size={self.tmp_size}",
            "--tmpfs", f"{SANDBOX_DIR}:rw,exec,nosuid,nodev,size={self.sandbox_size},uid={uid},gid={gid},mode=0700",
            "--mount", f"type=bind,source={src},target={WORKSPACE}",
        ]
        git_entry = resolved / ".git"
        if git_entry.exists():
            args += ["--mount", f"type=bind,source={_clean_mount_path(git_entry)},target={WORKSPACE}/.git,readonly"]
        args += ["-w", WORKSPACE]
        for key, value in _baseline_env(self.kind).items():
            args += ["-e", f"{key}={value}"]
        args += [self.image_used, *_REAPER]
        return args

    def build_exec_args(self, cmd: str, workdir: str, environ: dict[str, str] | None = None) -> list[str]:
        """`docker exec` for one command. Caller-supplied variables are filtered for credentials."""
        args = [self.docker_bin, "exec", "-w", workdir]
        for key, value in (environ or {}).items():
            key = str(key)
            if key in _RESERVED_ENV or key.upper() in SECRET_ENV_NAMES or _SECRET_ENV_RE.search(key):
                continue
            args += ["-e", f"{key}={value}"]
        args += [self.name, "sh", "-c", cmd]
        return args

    # ---- docker plumbing ----------------------------------------------------------------

    def _docker(self, args: list[str], timeout: float = 60) -> tuple[int, str]:
        if self.dry_run:
            self.planned.append([self.docker_bin, *args])
            return 0, ""
        try:
            done = subprocess.run([self.docker_bin, *args], stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            return 124, f"docker {args[0]} timed out"
        except OSError as exc:
            return 125, f"docker launch error: {exc}"
        return done.returncode, decode_output(done.stdout + done.stderr)

    def _running(self) -> bool:
        if self.dry_run:
            return True
        code, out = self._docker(["inspect", "-f", "{{.State.Running}}", self.name], 30)
        return code == 0 and out.strip() == "true"

    def _remove_container(self) -> bool:
        code, out = self._docker(["rm", "-f", self.name], 60)
        if self.dry_run:
            return True
        gone, _ = self._docker(["inspect", self.name], 30)
        return gone != 0 or (code == 0 and "No such container" in out)

    def _ensure_container(self, root: Path) -> str | None:
        """Start (or re-start) the container. Returns an error text or None."""
        if self._started and self._running():
            return None
        note = ""
        if self._started:  # it died or was removed: state on the tmpfs is gone with it
            self._remove_container()
            self._generation += 1
            note = "[sandbox] container was lost; a fresh one was started and the venv is empty again\n"
        self._started = False
        code, out = self._docker(self.build_run_args(root)[1:], 120)
        if code != 0:
            return f"docker run failed ({code}): {out.strip()[-400:]}"
        self._started = True
        self._net_attached = True
        boot = f"mkdir -p {SANDBOX_DIR}/home {SANDBOX_DIR}/tmp {SANDBOX_DIR}/pip-cache {SANDBOX_DIR}/npm-cache {SANDBOX_DIR}/stage"
        if self.kind != "node":
            boot += f" && python -m venv {SANDBOX_DIR}/venv"
        code, out = self._docker(["exec", "-w", WORKSPACE, self.name, "sh", "-c", boot], 180)
        if code != 0:
            return f"sandbox bootstrap failed ({code}): {out.strip()[-400:]}"
        self._note = note
        return None

    def _sync_network(self, phase: str) -> str | None:
        want = self.wants_network(phase)
        if want == self._net_attached:
            return None
        verb = "connect" if want else "disconnect"
        code, out = self._docker(["network", verb, self.network_name, self.name], 60)
        if code != 0:
            return f"docker network {verb} failed ({code}); command not run: {out.strip()[-300:]}"
        self._net_attached = want
        return None

    def _kill_everything(self, client: subprocess.Popen[bytes] | None) -> bool:
        """Timeout teardown: kill all processes in the container except PID 1, then the exec client."""
        code, _ = self._docker(["exec", self.name, "sh", "-c", "kill -9 -1"], 20)
        if client is not None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(client.pid)], capture_output=True, check=False)
            else:
                client.kill()
        # The shell killing itself exits 137; anything else (e.g. cannot fork: pids limit) means
        # the safe-restart path failed, so remove the container and let the next call re-create it.
        if code not in (0, 137) or not self._running():
            self._remove_container()
            self._started = False
            self._generation += 1
            return False
        return True

    def _stage(self, cwd: Path) -> tuple[str | None, str | None]:
        """Copy a directory outside the clone into the container's tmpfs. Returns (path, error)."""
        if not cwd.is_dir():
            return None, f"cwd does not exist: {cwd}"
        dest = f"{SANDBOX_DIR}/stage/{uuid.uuid4().hex[:8]}"
        if self.dry_run:
            self.planned.append([self.docker_bin, "exec", "-i", self.name, "tar", "-x", "-C", dest])
            return dest, self._link_node_modules(dest)
        proc = subprocess.Popen(
            [self.docker_bin, "exec", "-i", self.name, "sh", "-c", f"mkdir -p {dest} && tar -x -C {dest}"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        try:
            assert proc.stdin is not None
            with tarfile.open(fileobj=proc.stdin, mode="w|") as tar:
                for folder, dirs, files in os.walk(cwd):
                    dirs[:] = [d for d in dirs if d != ".git"]
                    for entry in [*dirs, *files]:
                        full = Path(folder) / entry
                        tar.add(full, arcname=full.relative_to(cwd).as_posix(), recursive=False)
            proc.stdin.close()
            out, _ = proc.communicate(timeout=300)
        except Exception as exc:  # noqa: BLE001
            proc.kill()
            return None, f"staging failed: {type(exc).__name__}: {exc}"
        if proc.returncode != 0:
            return None, f"staging failed ({proc.returncode}): {decode_output(out)[-300:]}"
        return dest, self._link_node_modules(dest)

    def _link_node_modules(self, dest: str) -> str | None:
        """A staged Node tree has no node_modules (snapshots skip it); point it at the clone's install."""
        if self.kind != "node":
            return None
        link = f"[ ! -d {WORKSPACE}/node_modules ] || [ -e {dest}/node_modules ] || ln -s {WORKSPACE}/node_modules {dest}/node_modules"
        code, out = self._docker(["exec", self.name, "sh", "-c", link], 30)
        return None if code == 0 else f"could not link node_modules into the staged tree: {out.strip()[-200:]}"

    # ---- the runner ---------------------------------------------------------------------

    def run(self, cmd: str, cwd: Path, timeout: float = 300, environ: dict[str, str] | None = None, phase: str = "exec") -> CmdResult:
        started = time.monotonic()

        def fail(text: str) -> CmdResult:
            return CmdResult(cmd, 125, text, time.monotonic() - started)

        with self._lock:
            if self._closed:
                return fail("sandbox runner is closed")
            try:
                host_cwd = Path(cwd).resolve()
                if self._root is None:
                    self._root = host_cwd
                self._resolve(self._root)
                if self.kind == "go":
                    return fail(UNSUPPORTED_GO_MESSAGE)
                self._note = ""
                err = self._ensure_container(self._root)
                if err:
                    return fail(err)
                err = self._sync_network(phase)
                if err:
                    return fail(err)
                staged = None
                try:
                    rel = host_cwd.relative_to(self._root)
                    workdir = WORKSPACE + (f"/{rel.as_posix()}" if rel.parts else "")
                except ValueError:
                    staged, err = self._stage(host_cwd)
                    if err or staged is None:
                        return fail(err or "staging failed")
                    workdir = staged
                try:
                    return self._exec(cmd, workdir, timeout, environ, phase, started)
                finally:
                    if staged:
                        self._docker(["exec", self.name, "rm", "-rf", staged], 60)
            except Exception as exc:  # noqa: BLE001
                return fail(f"sandbox error: {type(exc).__name__}: {exc}")

    def _exec(self, cmd: str, workdir: str, timeout: float, environ: dict[str, str] | None, phase: str, started: float) -> CmdResult:
        args = self.build_exec_args(cmd, workdir, environ)
        if self.dry_run:
            self.planned.append(args)
            return CmdResult(cmd, 0, f"[dry-run] phase={phase} network={'on' if self._net_attached else 'off'}: {' '.join(args)}", 0.0)
        try:
            proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        except OSError as exc:
            return CmdResult(cmd, 125, f"docker launch error: {exc}", time.monotonic() - started)
        try:
            raw, _ = proc.communicate(timeout=timeout)
            out, code = decode_output(raw), proc.returncode
        except subprocess.TimeoutExpired:
            restarted_ok = self._kill_everything(proc)
            try:
                raw, _ = proc.communicate(timeout=10)
                out = decode_output(raw)
            except subprocess.TimeoutExpired:
                out = ""
            tag = "" if restarted_ok else " (container could not be cleaned and was removed)"
            out = f"TIMEOUT after {timeout}s (the command never finished on its own){tag}\n" + (out or "")
            code = 124
        return CmdResult(cmd, code, (self._note or "") + (out or ""), time.monotonic() - started)

    def close(self) -> None:
        """Remove the container. Safe to call repeatedly, after errors and after timeouts."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self.close_ok = self._remove_container()
        _LIVE.discard(self)

    def __del__(self) -> None:  # pragma: no cover
        try:
            if not self._closed and self._started:
                self.close()
        except Exception:  # noqa: BLE001
            pass


def get_runner(mode: str = "host", image: str | None = None, **kwargs: Any) -> CommandRunner:
    """Factory: 'host' (default) or 'docker'."""
    mode_lower = (mode or "host").strip().lower()
    if mode_lower in ("host", "shell", "local"):
        return HostRunner()
    if mode_lower in ("docker", "container", "sandbox"):
        return DockerSandboxRunner(image=image, **kwargs)
    raise ValueError(f"Unknown runner mode: {mode!r}. Expected 'host' or 'docker'.")


# ---- verification.json seal ---------------------------------------------------------------

SEAL_ACTION = "GATE2_VERIFICATION_SEAL"
_DEFAULT_VERIFICATION = {"checks": [], "resurrection_allowed": False, "blocked_reason": "verification was not run"}


class VerificationSealConflict(RuntimeError):
    """A second seal with different content: the first seal event would no longer match the file."""


def seal_verification(directory: Path, verification: dict[str, Any] | None, trace: Any = None) -> tuple[str, dict[str, Any]]:
    """Write verification.json and record its SHA-256 as ONE GATE2_VERIFICATION_SEAL trace event.

    Call this once, after the final verification is determined (write_reports does). The hash is
    taken from the bytes on disk. Sealing the same content again is a no-op for the trace; sealing
    different content into the same trace raises VerificationSealConflict instead of leaving an
    event that disagrees with the file.
    """
    payload = verification or _DEFAULT_VERIFICATION
    data = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
    expected = hashlib.sha256(data).hexdigest()
    previous = getattr(trace, "_g2_verification_seal", None) if trace is not None else None
    if previous is not None and previous != expected:
        raise VerificationSealConflict("verification.json was already sealed with different content")
    path = Path(directory) / "verification.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)  # bytes, not text: no newline translation, so the hash is the same on every OS
    on_disk = path.read_bytes()
    digest = hashlib.sha256(on_disk).hexdigest()
    summary = {"sha256": digest, "size_bytes": len(on_disk), "checks_count": len(payload.get("checks") or [])}
    if trace is not None and hasattr(trace, "event") and previous is None:
        trace.event(agent_name="validator", action=SEAL_ACTION, input_summary={"path": "verification.json"}, output_summary=summary)
        try:
            trace._g2_verification_seal = digest
        except AttributeError:  # a trace object that refuses attributes
            pass
    return digest, summary


def check_verification_seal(verification_path: Path, trace_path: Path) -> tuple[bool, str]:
    """Compare verification.json on disk with the sha256 in the trace's GATE2_VERIFICATION_SEAL event.

    This detects an edit to verification.json after the run. It does not detect a rewritten trace:
    that is what scripts/verify_trace.py and the recorded run hash are for.
    """
    seals: list[dict[str, Any]] = []
    for line in Path(trace_path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if record.get("action") != SEAL_ACTION:
            continue
        summary = record.get("output_summary")
        if isinstance(summary, str):
            try:
                summary = json.loads(summary)
            except ValueError:
                summary = {}
        seals.append(summary if isinstance(summary, dict) else {})
    if len(seals) != 1:
        return False, f"expected exactly one {SEAL_ACTION} event, found {len(seals)}"
    actual = hashlib.sha256(Path(verification_path).read_bytes()).hexdigest()
    sealed = str(seals[0].get("sha256") or "")
    if actual != sealed:
        return False, f"verification.json sha256 {actual} does not match sealed {sealed}"
    return True, "verification.json matches the sealed hash"