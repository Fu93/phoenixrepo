"""Revival loop: draft a PRD, patch in two stages, and accept only on real checks.

Stage A makes the repository's own test command pass. Stage B applies the PRD's upgrade and
refactor items one at a time. A patch that breaks a check that was already passing is reverted.
The reviewer can add issues and send the builder back once. It cannot turn a failed check into a pass.

Every step is written to the hash-chained trace. Nothing here pushes or opens a pull request.
"""
from __future__ import annotations

import difflib
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from app import quality_gate as qg
from app.revive_prd import draft_prd

MAX_FILE_BYTES = 12_000
MAX_CONTEXT_FILES = 6
MAX_OUTPUT_CHARS = 6_000
MAX_ITEM_ATTEMPTS = 3
MAX_ITEMS_PER_KIND = 4
TEST_PATH_RE = re.compile(r"(^|/)(tests?|__tests__|spec|specs)(/|$)|(^|/)(test_[^/]*|[^/]*\.(test|spec)\.[a-z]+|[^/]*_test\.(py|go))$", re.IGNORECASE)
ENV_BINARY_RE = re.compile(r"Cannot find module '@(rollup|esbuild|swc|parcel)/|@(rollup|esbuild)/[a-z0-9-]+-(win32|linux|darwin)", re.IGNORECASE)
PROTECTED_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock"}

BUILDER_SYSTEM = (
    "You are the builder in a repository revival. Fix the failure with the smallest correct change to NON-TEST source, "
    "configuration or dependency files. You may not edit tests, test configuration or the test command, and you may not "
    "delete features to make tests pass. Reply with one strict JSON object and nothing else."
)
REVIEWER_SYSTEM = (
    "You are an independent reviewer of a repository revival. You may only report issues. "
    "You cannot declare a failing test, lint, typecheck, build, audit, smoke check, or diff check to be a pass. "
    "Reply with one strict JSON object and nothing else."
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
    quality: dict[str, Any] = field(default_factory=dict)
    prd_items_done: list[dict[str, Any]] = field(default_factory=list)
    prd_items_not_done: list[dict[str, Any]] = field(default_factory=list)
    review: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "reason": self.reason,
            "baseline_code": self.baseline.code if self.baseline else None,
            "final_code": self.final.code if self.final else None,
            "iterations": self.iterations,
            "files_changed": self.files_changed,
            "prd": self.prd,
            "quality": self.quality,
            "prd_items_done": self.prd_items_done,
            "prd_items_not_done": self.prd_items_not_done,
            "review": self.review,
        }


Runner = Callable[[str, Path, int], CmdResult]


MISSING_MODULE_RE = re.compile(r"Cannot find module '(@(?:rollup|esbuild|swc|parcel)/[A-Za-z0-9._-]+)'")


def missing_platform_module(output: str) -> str | None:
    """The scoped platform package a failed JS run could not find (e.g. @rollup/rollup-win32-x64-msvc)."""
    match = MISSING_MODULE_RE.search(output or "")
    return match.group(1) if match else None


def reinstall_command(install: str) -> str:
    """After a manifest edit the lockfile no longer matches, so `npm ci` would fail by design."""
    return install.replace("npm ci", "npm install", 1) if install.startswith("npm ci") else install


def decode_output(raw: bytes | None) -> str:
    """Decode child output: UTF-8 first, then the Windows console code page (cmd.exe messages on a
    non-English locale are not UTF-8 and would otherwise reach the model as replacement characters)."""
    if not raw:
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    import os

    if os.name == "nt":
        try:
            import ctypes

            return raw.decode(f"cp{ctypes.windll.kernel32.GetOEMCP()}", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    return raw.decode("utf-8", errors="replace")


def shell_runner(cmd: str, cwd: Path, timeout: int) -> CmdResult:
    """Run one command non-interactively (CI=1, no stdin) and kill the whole process tree on timeout."""
    import os
    import time

    env = dict(os.environ, CI="1", NO_COLOR="1", FORCE_COLOR="0", PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    started = time.monotonic()
    proc = subprocess.Popen(cmd, cwd=str(cwd), shell=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    try:
        raw, _ = proc.communicate(timeout=timeout)
        out = decode_output(raw)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        else:
            proc.kill()
        try:
            raw, _ = proc.communicate(timeout=10)
            out = decode_output(raw)
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
    for match in re.finditer(r"([A-Za-z0-9_./\\-]+\.(?:js|ts|tsx|jsx|mjs|cjs|py|go|json|toml|cfg|yml|yaml))", output):
        rel = match.group(1).replace("\\", "/")
        if "node_modules" in rel or rel in wanted:
            continue
        for candidate in (rel, rel.split("/", 1)[-1]):
            path = _safe_path(repo, candidate)
            if path and path.is_file() and candidate not in wanted:
                wanted.append(candidate)
                break
    files: dict[str, str] = {}
    for rel in wanted[: MAX_CONTEXT_FILES + 2]:
        path = _safe_path(repo, rel)
        if path and path.is_file():
            files[rel] = path.read_text(encoding="utf-8", errors="replace")[:MAX_FILE_BYTES]
    return files


def _files_block(files: dict[str, str]) -> str:
    return "\n\n".join(f"=== {name} ===\n{body}" for name, body in files.items())


def propose_patch(
    client: Any,
    repo_id: str,
    prd: dict[str, Any] | None,
    iteration: int | str,
    last: CmdResult,
    files: dict[str, str],
    history: list[str],
    note: str = "",
) -> dict[str, Any] | None:
    prompt = (
        f"Repository: {repo_id}. Iteration {iteration}.\n"
        f"PRD: {json.dumps(prd, ensure_ascii=False) if prd else '(none)'}\n\n"
        f"Earlier attempts that did not make the checks pass:\n{chr(10).join(history) or '(none)'}\n\n"
        f"{note}\n\n"
        f"Test command: {last.cmd}\nExit code: {last.code}\nOutput (tail):\n{tail(last.output)}\n\n"
        f"Files:\n{_files_block(files)}\n\n"
        'Reply with {"rationale": str, "edits": [{"path": str, "find": str, "replace": str}]}. '
        '"find" must be an exact substring that occurs exactly once in that file. To create a new file use {"path": str, "content": str}. '
        "At most 5 edits. Do not touch tests."
    )

    def valid(obj: dict[str, Any]) -> bool:
        return isinstance(obj.get("edits"), list) and bool(obj["edits"])

    step = f"revive_patch_{iteration}"
    try:
        obj, *_ = client.complete_json(prompt, BUILDER_SYSTEM, repo_id=repo_id, step=step, attempts=2, validate=valid)
    except Exception:  # noqa: BLE001 — a failed call must not become a pass
        return None
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
    if any(rel.endswith(".py") for rel in changed):
        # A same-size edit in the same mtime second leaves a stale .pyc, so the next pytest
        # run executes the old bytecode and a real fix looks like a failure.
        _drop_python_bytecode(repo)
    return changed, rejected


def _drop_python_bytecode(repo: Path) -> None:
    import os
    import shutil

    skip = {".git", ".venv", "venv", "node_modules"}
    for cache in repo.rglob("__pycache__"):
        if any(part in skip for part in cache.parts):
            continue
        shutil.rmtree(cache, ignore_errors=True)
    for rel in file_py_paths(repo):
        path = _safe_path(repo, rel)
        if path and path.is_file():
            os.utime(path, None)


def file_py_paths(repo: Path) -> list[str]:
    found: list[str] = []
    skip = {".git", ".venv", "venv", "node_modules", "__pycache__"}
    for path in repo.rglob("*.py"):
        if any(part in skip for part in path.parts):
            continue
        found.append(path.relative_to(repo).as_posix())
    return found


def restore_snapshot(repo: Path, snap: dict[str, bytes | None]) -> None:
    """Put files back. Git checkout runs first when the clone has a history; the snapshot is authoritative."""
    if (repo / ".git").exists():
        existing = [rel for rel, data in snap.items() if data is not None and _safe_path(repo, rel) is not None]
        if existing:
            subprocess.run(["git", "checkout", "--", *existing], cwd=str(repo), capture_output=True)
    for rel, data in snap.items():
        target = _safe_path(repo, rel)
        if target is None:
            continue
        if data is None:
            if target.is_file():
                target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def decide_revival_state(
    *,
    baseline_tests_passed: bool,
    tests_passed: bool,
    regressed: list[str],
    verified_criteria: list[str],
    had_work_items: bool,
    items_applied: int,
    not_done_reason: str = "",
) -> tuple[str, str]:
    """Final state from deterministic checks. The reviewer is not an input."""
    if not tests_passed:
        return "RESURRECTION_FAILED", "the repository's own test command still fails"
    if regressed:
        names = ", ".join(regressed)
        if baseline_tests_passed:
            return "NOTHING_TO_FIX", f"the repository's own tests already pass; quality checks regressed ({names}) so the patches were not accepted"
        return "RESURRECTION_FAILED", f"quality checks regressed versus the baseline: {names}"
    if baseline_tests_passed and not had_work_items:
        return "NOTHING_TO_FIX", "the repository's own tests already pass; there is no failing build to revive"
    if baseline_tests_passed and items_applied == 0:
        extra = f" ({not_done_reason})" if not_done_reason else ""
        return "NOTHING_TO_FIX", "the repository's own tests already pass; no upgrade or refactor item was applied" + extra
    if not verified_criteria:
        if baseline_tests_passed:
            return "NOTHING_TO_FIX", "no acceptance criterion was verified by a passing quality check"
        return "NEEDS_REVIEW", "tests pass but no PRD acceptance criterion was verified by a passing quality check"
    if baseline_tests_passed:
        return "RESURRECTED", "upgrade or refactor items were applied and no quality check regressed; verified locally, not yet on fork CI"
    shown = ", ".join(verified_criteria)
    return "RESURRECTED", f"the repository's own test command passes and no quality check regressed; acceptance criteria verified: {shown}; verified locally, not yet on fork CI"


def _line_delta(before: bytes | None, after: bytes | None) -> int:
    old = (before or b"").decode("utf-8", "replace").splitlines()
    new = (after or b"").decode("utf-8", "replace").splitlines()
    delta = 0
    for line in difflib.unified_diff(old, new, lineterm=""):
        if line.startswith(("+++", "---", "@@")):
            continue
        if line.startswith(("+", "-")):
            delta += 1
    return delta


def _concrete_issues(raw: Any) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if not isinstance(raw, list):
        return issues
    for item in raw:
        if isinstance(item, str) and item.strip():
            issues.append({"id": "", "detail": item.strip()})
        elif isinstance(item, dict):
            detail = str(item.get("detail") or item.get("issue") or "").strip()
            if detail:
                issues.append({"id": str(item.get("id") or ""), "detail": detail})
    return issues


def _work_items(prd: dict[str, Any]) -> tuple[list[tuple[str, dict[str, Any]]], list[dict[str, Any]]]:
    selected: list[tuple[str, dict[str, Any]]] = []
    skipped: list[dict[str, Any]] = []
    for kind, key in (("upgrade", "upgrade_items"), ("refactor", "refactor_items")):
        items = [item for item in (prd.get(key) or []) if isinstance(item, dict)]
        for item in items[:MAX_ITEMS_PER_KIND]:
            selected.append((kind, item))
        for item in items[MAX_ITEMS_PER_KIND:]:
            skipped.append({"id": str(item.get("id") or ""), "kind": kind, "reason": f"not attempted: capped at {MAX_ITEMS_PER_KIND} {kind} items"})
    return selected, skipped


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
    if gate_verdict != "CANDIDATE":
        # Gate 1 is a hard precondition: an idea that is not worth rescuing is never built, so a
        # refused repository can never end up RESURRECTED (or "revived anyway").
        trace.event(agent_name="orchestrator", action="REVIVE_BLOCKED_BY_GATE1", input_summary=repo_id, output_summary={"gate1": gate_verdict, "note": "revival not attempted"}, status="blocked")
        return ReviveResult("NOT_ATTEMPTED_GATE1", f"gate 1 said {gate_verdict}, so no revival was attempted")
    install, test = detect_commands(repo)
    if not test:
        trace.event(agent_name="validator", action="REVIVE_UNSUPPORTED", input_summary=repo_id, output_summary="No runnable test command found in the manifest.", status="needs_review")
        result = ReviveResult("NEEDS_REVIEW", "no test command could be detected, so a revival cannot be verified")
        trace.event(agent_name="orchestrator", action="REVIVE_RESULT", input_summary=repo_id, output_summary={"state": result.state, "reason": result.reason}, status="needs_review")
        return result

    def _run(cmd: str, cwd: Path, timeout: int) -> CmdResult:
        try:
            return runner(cmd, cwd, timeout)
        except Exception as exc:  # noqa: BLE001
            return CmdResult(cmd, 1, f"runner error: {type(exc).__name__}")

    originals: dict[str, bytes | None] = {}

    def _apply_tracked(edits: list[dict[str, Any]]) -> tuple[list[str], list[str], dict[str, bytes | None]]:
        snap: dict[str, bytes | None] = {}
        for edit in edits[:5]:
            rel = str(edit.get("path") or "")
            target = _safe_path(repo, rel)
            if target is None:
                continue
            data = target.read_bytes() if target.is_file() else None
            snap[rel] = data
            originals.setdefault(rel, data)
        changed, rejected = apply_edits(repo, edits)
        return changed, rejected, snap

    def _changes() -> tuple[list[str], int]:
        files: list[str] = []
        lines = 0
        for rel, before in originals.items():
            target = _safe_path(repo, rel)
            if target is None:
                continue
            after = target.read_bytes() if target.is_file() else None
            if after == before:
                continue
            files.append(rel)
            lines += _line_delta(before, after)
        return files, lines

    def _unified() -> str:
        chunks: list[str] = []
        for rel, before in originals.items():
            target = _safe_path(repo, rel)
            after = target.read_bytes() if target and target.is_file() else None
            if after == before:
                continue
            old = (before or b"").decode("utf-8", "replace").splitlines(keepends=True)
            new = (after or b"").decode("utf-8", "replace").splitlines(keepends=True)
            chunks.extend(difflib.unified_diff(old, new, fromfile=f"a/{rel}", tofile=f"b/{rel}"))
        return ("".join(chunks)[:8000]) or "(no diff)"

    def _gate(test_result: CmdResult | None, prd_obj: dict[str, Any] | None) -> list[dict[str, Any]]:
        files, lines = _changes()
        return qg.run_quality_gate(
            repo,
            _run,
            test,
            test_timeout=test_timeout,
            changed_files=files,
            lines_changed=lines,
            prd=prd_obj,
            test_result=test_result,
        )

    if install:
        inst = _run(install, repo, install_timeout)
        trace.event(agent_name="builder", action="REVIVE_INSTALL", input_summary=install, output_summary={"exit": inst.code, "tail": tail(inst.output, 400)}, status="ok" if inst.ok else "failed")
    baseline = _run(test, repo, test_timeout)
    trace.event(agent_name="validator", action="REVIVE_BASELINE", input_summary=test, output_summary={"exit": baseline.code, "tail": tail(baseline.output, 400)}, status="ok" if baseline.ok else "failed")
    if not baseline.ok and ENV_BINARY_RE.search(baseline.output) and (repo / "package.json").exists():
        # A missing platform binary (npm optional-dependency bug) is an environment problem, not a code
        # defect: clean-install once before blaming the repository, and record that we did.
        import shutil

        shutil.rmtree(repo / "node_modules", ignore_errors=True)
        again = _run("npm install --no-audit --no-fund", repo, install_timeout)
        baseline = _run(test, repo, test_timeout)
        missing = missing_platform_module(baseline.output)
        if not baseline.ok and missing:
            # npm skipped the optional platform package again: install exactly that package without
            # touching package.json or the lockfile.
            again = _run(f"npm install --no-save --no-audit --no-fund {missing}", repo, install_timeout)
            baseline = _run(test, repo, test_timeout)
        trace.event(agent_name="builder", action="REVIVE_ENV_REPAIR", input_summary="clean reinstall of node_modules (platform binary was missing)", output_summary={"install_exit": again.code, "test_exit": baseline.code, "tail": tail(baseline.output, 300)}, status="ok" if baseline.ok else "failed")

    prd = draft_prd(client, repo, repo_id, description, baseline)
    trace.event(
        agent_name="architect",
        action="REVIVE_PRD",
        input_summary={"repo": repo_id, "degraded": bool(prd.get("degraded"))},
        output_summary=prd,
        status="degraded" if prd.get("degraded") else "ok",
        summary_limit=20000,
    )
    result = ReviveResult("RESURRECTION_FAILED", "", baseline=baseline, prd=prd)
    before = _gate(baseline, prd)
    work_items, capped = _work_items(prd)
    for skipped in capped:
        result.prd_items_not_done.append(skipped)
        trace.event(agent_name="builder", action="REVIVE_ITEM", input_summary={"id": skipped["id"], "kind": skipped["kind"]}, output_summary=skipped, status="not_done")

    def _emit_quality(final_checks: list[dict[str, Any]], verified: list[str]) -> None:
        files, lines = _changes()
        result.files_changed = files
        result.quality = qg.quality_report(before, final_checks, files, lines, verified)
        trace.event(agent_name="validator", action="REVIVE_QUALITY", input_summary=repo_id, output_summary=result.quality, status="ok" if not result.quality["regressed"] else "failed", summary_limit=12000)

    def _emit_result() -> None:
        trace.event(
            agent_name="orchestrator",
            action="REVIVE_RESULT",
            input_summary=repo_id,
            output_summary={
                "state": result.state,
                "reason": result.reason,
                "prd_items_done": [item.get("id") for item in result.prd_items_done],
                "prd_items_not_done": [item.get("id") for item in result.prd_items_not_done],
                "regressed": (result.quality or {}).get("regressed") or [],
                "verified_criteria": (result.quality or {}).get("verified_criteria") or [],
            },
            status="ok" if result.state in ("RESURRECTED", "NOTHING_TO_FIX") else "failed",
            summary_limit=4000,
        )

    if baseline.ok and not work_items:
        verified = qg.verified_criteria(prd, before)
        _emit_quality(before, verified)
        result.final = baseline
        result.state = "NOTHING_TO_FIX"
        result.reason = "the repository's own tests already pass; there is no failing build to revive"
        _emit_result()
        return result

    history: list[str] = []
    last = baseline
    accepted_checks = before
    stage_a_ok = baseline.ok
    if not baseline.ok:
        for n in range(1, max_iterations + 1):
            files = context_files(repo, last.output)
            patch = propose_patch(client, repo_id, prd, n, last, files, history)
            if not patch:
                trace.event(agent_name="builder", action="REVIVE_PATCH", input_summary=f"iteration {n}", output_summary="no usable patch from the model", retry_count=n, status="failed")
                result.iterations.append({"n": n, "patch": None, "exit": last.code})
                history.append(f"iteration {n}: the model returned no usable patch")
                continue
            changed, rejected, snap = _apply_tracked(patch["edits"])
            trace.event(agent_name="builder", action="REVIVE_PATCH", input_summary={"iteration": n, "rationale": str(patch.get("rationale", ""))[:300]}, output_summary={"changed": changed, "rejected": rejected}, retry_count=n, status="ok" if changed else "failed")
            if not changed:
                result.iterations.append({"n": n, "patch": "rejected", "rejected": rejected, "exit": last.code})
                history.append(f"iteration {n}: every edit was rejected: {'; '.join(rejected)}")
                continue
            if install and any(path in ("package.json", "requirements.txt", "pyproject.toml", "go.mod", "setup.py") for path in changed):
                _run(reinstall_command(install), repo, install_timeout)
            run = _run(test, repo, test_timeout)
            accepted = run.ok
            if accepted:
                gate = _gate(run, prd)
                regs = qg.regressions(accepted_checks, gate)
                if regs:
                    restore_snapshot(repo, snap)
                    detail = _failure_text(regs, gate)
                    accepted = False
                    run = CmdResult(test, 1, detail)
                    history.append(f"iteration {n}: reverted because quality checks regressed ({', '.join(regs)}). {tail(detail, 600)}")
            trace.event(agent_name="validator", action="REVIVE_VALIDATE", input_summary=test, output_summary={"exit": run.code, "accepted": accepted, "tail": tail(run.output, 400)}, retry_count=n, status="ok" if accepted else "rejected")
            result.iterations.append({"n": n, "changed": changed, "rejected": rejected, "exit": run.code, "accepted": accepted})
            last = run
            if accepted:
                accepted_checks = gate
                stage_a_ok = True
                break
            history.append(f"iteration {n}: changed {changed}, test exit {run.code}; output tail: {tail(run.output, 600)}")

    if stage_a_ok and work_items:
        for kind, item in work_items:
            item_id = str(item.get("id") or kind)
            done = False
            last_reason = "no patch was applied"
            kept_files: list[str] = []
            for attempt in range(1, MAX_ITEM_ATTEMPTS + 1):
                note = (
                    f"Apply this single {kind} item and nothing else. Item attempt {attempt} of {MAX_ITEM_ATTEMPTS}.\n"
                    f"Item: {json.dumps(item, ensure_ascii=False)}"
                )
                files = context_files(repo, last.output)
                patch = propose_patch(client, repo_id, prd, f"{item_id}-{attempt}", last, files, history, note=note)
                if not patch:
                    last_reason = "the model returned no usable patch"
                    trace.event(agent_name="builder", action="REVIVE_ITEM", input_summary={"id": item_id, "kind": kind, "attempt": attempt}, output_summary={"status": "reverted", "reason": last_reason}, retry_count=attempt, status="failed")
                    continue
                changed, rejected, snap = _apply_tracked(patch["edits"])
                if not changed:
                    last_reason = "; ".join(rejected) or "every edit was rejected"
                    trace.event(agent_name="builder", action="REVIVE_ITEM", input_summary={"id": item_id, "kind": kind, "attempt": attempt}, output_summary={"status": "reverted", "reason": last_reason, "rejected": rejected}, retry_count=attempt, status="failed")
                    continue
                if install and any(path in ("package.json", "requirements.txt", "pyproject.toml", "go.mod", "setup.py") for path in changed):
                    _run(reinstall_command(install), repo, install_timeout)
                run = _run(test, repo, test_timeout)
                gate = _gate(run, prd)
                regs = qg.regressions(accepted_checks, gate)
                if regs or not run.ok:
                    restore_snapshot(repo, snap)
                    detail = _failure_text(regs, gate) if regs else run.output
                    last_reason = tail(detail, 600) or "quality check failed"
                    last = CmdResult(test, 1, last_reason)
                    history.append(f"item {item_id} attempt {attempt}: reverted. {last_reason}")
                    trace.event(agent_name="builder", action="REVIVE_ITEM", input_summary={"id": item_id, "kind": kind, "attempt": attempt}, output_summary={"status": "reverted", "reason": last_reason, "changed": changed, "regressed": regs}, retry_count=attempt, status="reverted")
                    continue
                accepted_checks = gate
                last = run
                done = True
                kept_files = changed
                trace.event(agent_name="builder", action="REVIVE_ITEM", input_summary={"id": item_id, "kind": kind, "attempt": attempt}, output_summary={"status": "applied", "changed": changed}, retry_count=attempt, status="applied")
                break
            if done:
                result.prd_items_done.append({"id": item_id, "kind": kind, "files": kept_files})
            else:
                record = {"id": item_id, "kind": kind, "reason": last_reason}
                result.prd_items_not_done.append(record)
                trace.event(agent_name="builder", action="REVIVE_ITEM", input_summary={"id": item_id, "kind": kind}, output_summary={"status": "not_done", "reason": last_reason}, status="not_done")
    elif work_items and not stage_a_ok:
        for kind, item in work_items:
            record = {"id": str(item.get("id") or kind), "kind": kind, "reason": "baseline tests still fail, so the item was not attempted"}
            result.prd_items_not_done.append(record)
            trace.event(agent_name="builder", action="REVIVE_ITEM", input_summary={"id": record["id"], "kind": kind}, output_summary={"status": "not_done", "reason": record["reason"]}, status="not_done")

    review = _review(client, repo_id, prd, _unified(), accepted_checks)
    result.review = review
    trace.event(agent_name="reviewer", action="REVIVE_REVIEW", input_summary=repo_id, output_summary=review, status="degraded" if review.get("degraded") else ("ok" if review.get("approve") else "rejected"), summary_limit=4000)
    if review.get("approve") is False and review.get("issues"):
        note = "Reviewer iteration review-1. The reviewer rejected the revival with these issues:\n" + "\n".join(f"- {issue['detail']}" for issue in review["issues"])
        files = context_files(repo, last.output)
        patch = propose_patch(client, repo_id, prd, "review-1", last, files, history, note=note)
        if patch and patch.get("edits"):
            changed, rejected, snap = _apply_tracked(patch["edits"])
            trace.event(agent_name="builder", action="REVIVE_PATCH", input_summary={"iteration": "review-1"}, output_summary={"changed": changed, "rejected": rejected}, status="ok" if changed else "failed")
            if changed:
                if install and any(path in ("package.json", "requirements.txt", "pyproject.toml", "go.mod", "setup.py") for path in changed):
                    _run(reinstall_command(install), repo, install_timeout)
                run = _run(test, repo, test_timeout)
                gate = _gate(run, prd)
                regs = qg.regressions(accepted_checks, gate)
                if regs or not run.ok:
                    restore_snapshot(repo, snap)
                    trace.event(agent_name="builder", action="REVIVE_ITEM", input_summary={"id": "review-1", "kind": "review"}, output_summary={"status": "reverted", "reason": _failure_text(regs, gate) if regs else tail(run.output, 400)}, status="reverted")
                else:
                    accepted_checks = gate
                    last = run

    final_run = _run(test, repo, test_timeout)
    final_checks = _gate(final_run, prd)
    verified = qg.verified_criteria(prd, final_checks)
    _emit_quality(final_checks, verified)
    result.final = final_run
    not_done_reason = "; ".join(str(item.get("reason") or "") for item in result.prd_items_not_done if item.get("reason"))
    result.state, result.reason = decide_revival_state(
        baseline_tests_passed=baseline.ok,
        tests_passed=qg.counts_as_pass(next((row for row in final_checks if row["id"] == "tests"), None)),
        regressed=list(result.quality.get("regressed") or []),
        verified_criteria=verified,
        had_work_items=bool(work_items),
        items_applied=len(result.prd_items_done),
        not_done_reason=not_done_reason[:400],
    )
    _emit_result()
    return result


def _failure_text(regs: list[str], gate: list[dict[str, Any]]) -> str:
    by_id = {str(row["id"]): row for row in gate}
    parts = []
    for check_id in regs:
        row = by_id.get(check_id) or {}
        parts.append(f"{check_id} {row.get('status')}: {row.get('detail_tail') or ''}")
    return "\n".join(parts) or "quality check regressed"


def _review(client: Any, repo_id: str, prd: dict[str, Any], diff: str, checks: list[dict[str, Any]]) -> dict[str, Any]:
    table = [{"id": row["id"], "status": row["status"], "detail_tail": row.get("detail_tail", ""), "high": row.get("high"), "critical": row.get("critical")} for row in checks]
    prompt = (
        f"Repository: {repo_id}. Review iteration 1.\n"
        "You may only add issues. Approving cannot override a failed deterministic check.\n"
        f"PRD: {json.dumps(prd, ensure_ascii=False)}\n\n"
        f"Diff:\n{diff}\n\n"
        f"Quality table: {json.dumps(table, ensure_ascii=False)}\n\n"
        'Reply with {"approve": bool, "issues": [{"id": str, "detail": str}]}.'
    )

    def valid(obj: dict[str, Any]) -> bool:
        return isinstance(obj.get("approve"), bool) and isinstance(obj.get("issues"), list)

    try:
        obj, *_ = client.complete_json(prompt, REVIEWER_SYSTEM, repo_id=repo_id, step="revive_review", attempts=1, validate=valid)
    except Exception as exc:  # noqa: BLE001
        return {"approve": None, "issues": [], "degraded": True, "reason": f"llm call failed: {type(exc).__name__}"}
    if not isinstance(obj, dict):
        return {"approve": None, "issues": [], "degraded": True, "reason": "reviewer returned no JSON object"}
    return {"approve": obj.get("approve"), "issues": _concrete_issues(obj.get("issues")), "degraded": False}
