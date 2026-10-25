"""Revival loop: draft a PRD, let the model patch the code, run the repository's own tests, send failures back.

This is the part of the second gate that actually builds. The loop is deliberately small:

1. Run the repository's real install and test commands (baseline). The output is evidence.
2. A model drafts an upgrade PRD from the repository's own files and that baseline output.
3. A model proposes exact-text edits. The builder may not touch tests or the test command.
4. The validator applies nothing on trust: it re-runs the real test command. Only an exit code of
   0 from that command accepts the revival. A failure goes back to the builder with the real output.

Every step is written to the hash-chained trace. Nothing here pushes or opens a pull request.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

MAX_FILE_BYTES = 12_000
MAX_CONTEXT_FILES = 6
MAX_OUTPUT_CHARS = 6_000
TEST_PATH_RE = re.compile(r"(^|/)(tests?|__tests__|spec|specs)(/|$)|(^|/)(test_[^/]*|[^/]*\.(test|spec)\.[a-z]+|[^/]*_test\.(py|go))$", re.IGNORECASE)
PROTECTED_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock"}

PRD_SYSTEM = (
    "You are the architect of a repository revival. You only use facts from the files and command output you are given. "
    "Reply with one strict JSON object and nothing else."
)
BUILDER_SYSTEM = (
    "You are the builder in a repository revival. Fix the failure with the smallest correct change to NON-TEST source, "
    "configuration or dependency files. You may not edit tests, test configuration or the test command, and you may not "
    "delete features to make tests pass. Reply with one strict JSON object and nothing else."
)


@dataclass
class CmdResult:
    cmd: str
    code: int
    output: str
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.code == 0


@dataclass
class ReviveResult:
    state: str
    reason: str
    baseline: CmdResult | None = None
    final: CmdResult | None = None
    iterations: list[dict[str, Any]] = field(default_factory=list)
    prd: dict[str, Any] | None = None
    files_changed: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "reason": self.reason,
            "baseline_code": self.baseline.code if self.baseline else None,
            "final_code": self.final.code if self.final else None,
            "iterations": self.iterations,
            "files_changed": self.files_changed,
            "prd": self.prd,
        }


Runner = Callable[[str, Path, int], CmdResult]


def shell_runner(cmd: str, cwd: Path, timeout: int) -> CmdResult:
    """Run one command non-interactively (CI=1, no stdin) and kill the whole process tree on timeout."""
    import os
    import time

    env = dict(os.environ, CI="1", NO_COLOR="1", FORCE_COLOR="0")
    started = time.monotonic()
    proc = subprocess.Popen(cmd, cwd=str(cwd), shell=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", env=env)
    try:
        out, _ = proc.communicate(timeout=timeout)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        else:
            proc.kill()
        try:
            out, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            out = ""
        out = f"TIMEOUT after {timeout}s (the command never finished on its own)\n" + (out or "")
        code = 124
    return CmdResult(cmd, code, out or "", time.monotonic() - started)


def detect_commands(repo: Path) -> tuple[str | None, str | None]:
    """Return (install_cmd, test_cmd) from the repository's own manifest, or (None, None)."""
    pkg = repo / "package.json"
    if pkg.exists():
        try:
            scripts = (json.loads(pkg.read_text(encoding="utf-8")).get("scripts") or {})
        except (OSError, ValueError):
            scripts = {}
        test = scripts.get("test")
        if test and "no test specified" not in test:
            install = "npm ci --no-audit --no-fund" if (repo / "package-lock.json").exists() else "npm install --no-audit --no-fund"
            return install, "npm test --silent"
        return None, None
    if (repo / "pyproject.toml").exists() or (repo / "requirements.txt").exists() or (repo / "setup.py").exists():
        install = "python -m pip install -q -e ." if ((repo / "pyproject.toml").exists() or (repo / "setup.py").exists()) else "python -m pip install -q -r requirements.txt"
        return install + " && python -m pip install -q pytest", "python -m pytest -x -q"
    if (repo / "go.mod").exists():
        return "go mod download", "go test ./..."
    return None, None


def is_protected(path: str) -> bool:
    norm = path.replace("\\", "/").lstrip("./")
    name = norm.rsplit("/", 1)[-1]
    return bool(TEST_PATH_RE.search(norm)) or name in PROTECTED_NAMES or norm.startswith(".git/")


def _safe_path(repo: Path, rel: str) -> Path | None:
    rel = rel.replace("\\", "/").strip()
    if not rel or rel.startswith("/") or ".." in rel.split("/") or rel.startswith(".git/") or ":" in rel:
        return None
    target = (repo / rel).resolve()
    try:
        target.relative_to(repo.resolve())
    except ValueError:
        return None
    return target


def tail(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    return text if len(text) <= limit else "...[truncated]...\n" + text[-limit:]


def context_files(repo: Path, output: str) -> dict[str, str]:
    """Manifest files plus every existing file path that the failure output mentions."""
    wanted: list[str] = []
    for name in ("package.json", "pyproject.toml", "requirements.txt", "setup.py", "go.mod", "tsconfig.json", "README.md"):
        if (repo / name).exists():
            wanted.append(name)
    for m in re.finditer(r"([A-Za-z0-9_./\\-]+\.(?:js|ts|tsx|jsx|mjs|cjs|py|go|json|toml|cfg|yml|yaml))", output):
        rel = m.group(1).replace("\\", "/")
        if "node_modules" in rel or rel in wanted:
            continue
        for candidate in (rel, rel.split("/", 1)[-1]):
            p = _safe_path(repo, candidate)
            if p and p.is_file() and candidate not in wanted:
                wanted.append(candidate)
                break
    files: dict[str, str] = {}
    for rel in wanted[:MAX_CONTEXT_FILES + 2]:
        p = _safe_path(repo, rel)
        if p and p.is_file():
            files[rel] = p.read_text(encoding="utf-8", errors="replace")[:MAX_FILE_BYTES]
    return files


def _files_block(files: dict[str, str]) -> str:
    return "\n\n".join(f"=== {name} ===\n{body}" for name, body in files.items())


def draft_prd(client: Any, repo_id: str, description: str, baseline: CmdResult, files: dict[str, str]) -> dict[str, Any] | None:
    prompt = (
        f"Repository: {repo_id}\nDescription: {description}\n\n"
        f"Baseline command: {baseline.cmd}\nExit code: {baseline.code}\nOutput (tail):\n{tail(baseline.output)}\n\n"
        f"Files:\n{_files_block(files)}\n\n"
        "Write a short upgrade PRD for reviving this repository so that its own test command passes. "
        'Schema: {"goal": str, "failure_summary": str, "tasks": [{"id": str, "title": str, "acceptance": str}], '
        '"out_of_scope": [str]}. At most 4 tasks. Do not invent features.'
    )

    def valid(obj: dict[str, Any]) -> bool:
        return isinstance(obj.get("goal"), str) and isinstance(obj.get("tasks"), list) and bool(obj["tasks"])

    obj, *_ = client.complete_json(prompt, PRD_SYSTEM, repo_id=repo_id, step="revive_prd", attempts=2, validate=valid)
    return obj


def propose_patch(client: Any, repo_id: str, prd: dict[str, Any] | None, iteration: int, last: CmdResult, files: dict[str, str], history: list[str]) -> dict[str, Any] | None:
    prompt = (
        f"Repository: {repo_id}. Iteration {iteration}.\n"
        f"PRD: {json.dumps(prd, ensure_ascii=False) if prd else '(none)'}\n\n"
        f"Earlier attempts that did not make the test command pass:\n{chr(10).join(history) or '(none)'}\n\n"
        f"Test command: {last.cmd}\nExit code: {last.code}\nOutput (tail):\n{tail(last.output)}\n\n"
        f"Files:\n{_files_block(files)}\n\n"
        'Reply with {"rationale": str, "edits": [{"path": str, "find": str, "replace": str}]}. '
        '"find" must be an exact substring that occurs exactly once in that file. To create a new file use {"path": str, "content": str}. '
        "At most 5 edits. Do not touch tests."
    )

    def valid(obj: dict[str, Any]) -> bool:
        return isinstance(obj.get("edits"), list) and bool(obj["edits"])

    obj, *_ = client.complete_json(prompt, BUILDER_SYSTEM, repo_id=repo_id, step=f"revive_patch_{iteration}", attempts=2, validate=valid)
    return obj


def apply_edits(repo: Path, edits: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """Apply edits. Returns (changed_paths, rejections). A rejected edit changes nothing."""
    changed: list[str] = []
    rejected: list[str] = []
    for edit in edits[:5]:
        rel = str(edit.get("path") or "")
        if is_protected(rel):
            rejected.append(f"{rel}: protected (tests, lockfiles and .git cannot be edited by the builder)")
            continue
        target = _safe_path(repo, rel)
        if target is None:
            rejected.append(f"{rel}: path outside the repository")
            continue
        if "content" in edit and "find" not in edit:
            if target.exists():
                rejected.append(f"{rel}: content may only create a new file")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(edit["content"]), encoding="utf-8", newline="\n")
            changed.append(rel)
            continue
        find, replace = edit.get("find"), edit.get("replace")
        if not isinstance(find, str) or not isinstance(replace, str) or not find:
            rejected.append(f"{rel}: find/replace missing")
            continue
        if not target.is_file():
            rejected.append(f"{rel}: file does not exist")
            continue
        text = target.read_text(encoding="utf-8", errors="replace")
        if text.count(find) != 1:
            rejected.append(f"{rel}: find text occurs {text.count(find)} times, need exactly 1")
            continue
        target.write_text(text.replace(find, replace, 1), encoding="utf-8", newline="\n")
        changed.append(rel)
    return changed, rejected


def revive(
    repo: Path,
    client: Any,
    trace: Any,
    repo_id: str,
    description: str = "",
    gate_verdict: str = "CANDIDATE",
    max_iterations: int = 4,
    runner: Runner = shell_runner,
    install_timeout: int = 900,
    test_timeout: int = 300,
) -> ReviveResult:
    install, test = detect_commands(repo)
    if not test:
        trace.event(agent_name="validator", action="REVIVE_UNSUPPORTED", input_summary=repo_id, output_summary="No runnable test command found in the manifest.", status="needs_review")
        return ReviveResult("NEEDS_REVIEW", "no test command could be detected, so a revival cannot be verified")

    if install:
        inst = runner(install, repo, install_timeout)
        trace.event(agent_name="builder", action="REVIVE_INSTALL", input_summary=install, output_summary={"exit": inst.code, "tail": tail(inst.output, 400)}, status="ok" if inst.ok else "failed")
    baseline = runner(test, repo, test_timeout)
    trace.event(agent_name="validator", action="REVIVE_BASELINE", input_summary=test, output_summary={"exit": baseline.code, "tail": tail(baseline.output, 400)}, status="ok" if baseline.ok else "failed")
    result = ReviveResult("RESURRECTION_FAILED", "", baseline=baseline)
    if baseline.ok:
        result.final = baseline
        result.state = "NOTHING_TO_FIX"
        result.reason = "the repository's own tests already pass; there is no failing build to revive"
        return result

    files = context_files(repo, baseline.output)
    prd = draft_prd(client, repo_id, description, baseline, files)
    result.prd = prd
    trace.event(agent_name="architect", action="REVIVE_PRD", input_summary=repo_id, output_summary=prd if prd else "PRD generation failed", status="ok" if prd else "degraded")

    history: list[str] = []
    last = baseline
    changed_all: list[str] = []
    for n in range(1, max_iterations + 1):
        files = context_files(repo, last.output)
        patch = propose_patch(client, repo_id, prd, n, last, files, history)
        if not patch:
            trace.event(agent_name="builder", action="REVIVE_PATCH", input_summary=f"iteration {n}", output_summary="no usable patch from the model", retry_count=n, status="failed")
            result.iterations.append({"n": n, "patch": None, "exit": last.code})
            history.append(f"iteration {n}: the model returned no usable patch")
            continue
        changed, rejected = apply_edits(repo, patch["edits"])
        trace.event(agent_name="builder", action="REVIVE_PATCH", input_summary={"iteration": n, "rationale": str(patch.get("rationale", ""))[:300]}, output_summary={"changed": changed, "rejected": rejected}, retry_count=n, status="ok" if changed else "failed")
        if not changed:
            result.iterations.append({"n": n, "patch": "rejected", "rejected": rejected, "exit": last.code})
            history.append(f"iteration {n}: every edit was rejected: {'; '.join(rejected)}")
            continue
        changed_all.extend(c for c in changed if c not in changed_all)
        if install and any(c in ("package.json", "requirements.txt", "pyproject.toml", "go.mod", "setup.py") for c in changed):
            runner(install, repo, install_timeout)
        run = runner(test, repo, test_timeout)
        accepted = run.ok
        trace.event(agent_name="validator", action="REVIVE_VALIDATE", input_summary=test, output_summary={"exit": run.code, "accepted": accepted, "tail": tail(run.output, 400)}, retry_count=n, status="ok" if accepted else "rejected")
        result.iterations.append({"n": n, "changed": changed, "rejected": rejected, "exit": run.code, "accepted": accepted})
        last = run
        if accepted:
            result.final = run
            result.files_changed = changed_all
            if gate_verdict == "CANDIDATE":
                result.state = "RESURRECTED"
                result.reason = f"the repository's own test command passes after {n} model-written patch iteration(s); verified locally, not yet on fork CI"
            else:
                result.state = "REVIVED_NOT_RECOMMENDED"
                result.reason = f"technically revived, but gate 1 said {gate_verdict}, so it is not recommended"
            return result
        history.append(f"iteration {n}: changed {changed}, test exit {run.code}; output tail: {tail(run.output, 600)}")
    result.final = last
    result.files_changed = changed_all
    result.reason = f"the test command still fails after {max_iterations} iterations"
    return result
