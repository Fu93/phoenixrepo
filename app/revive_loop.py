"""Revival loop: draft a PRD, patch in two stages, and accept only on real checks.

Stage A makes the repository's own test command pass. Stage B applies minor and patch upgrades
from the ecosystem's outdated list (and any PRD upgrade that names a real version) by editing
the manifest, then asks the model only to repair a broken upgrade, write docs, or refactor.
A patch that breaks a check that was already passing is reverted.
The reviewer can add issues and send the builder back once. It cannot turn a failed check into a pass.

Every step is written to the hash-chained trace. Nothing here pushes or opens a pull request.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import shlex
import subprocess
import time
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
MAX_FILES_PER_ITEM = 5
NPM_FAST_FLAGS = "--prefer-offline --no-audit --no-fund"
SUMMARY_CHECKS = ("tests", "lint", "types", "build", "audit")
TEST_PATH_RE = re.compile(r"(^|/)(tests?|__tests__|spec|specs)(/|$)|(^|/)(test_[^/]*|[^/]*\.(test|spec)\.[a-z]+|[^/]*_test\.(py|go))$", re.IGNORECASE)
ENV_BINARY_RE = re.compile(r"Cannot find module '@(rollup|esbuild|swc|parcel)/|@(rollup|esbuild)/[a-z0-9-]+-(win32|linux|darwin)", re.IGNORECASE)
PROTECTED_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock"}
MANIFEST_NAMES = {
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "npm-shrinkwrap.json",
    "requirements.txt",
    "pyproject.toml",
    "go.mod",
    "setup.py",
}
DOC_SUFFIXES = {".md", ".rst", ".txt"}
DOC_CONTENT_MAX_BYTES = 200 * 1024
ENV_FAILURE_RE = re.compile(
    r"@(?:parcel/watcher|rollup/|swc/|esbuild/|parcel/)|"
    r"not recognized as an internal or external command|"
    r"npm ERR! network|"
    r"npm ERR! code (?:ECONNRESET|ECONNREFUSED|ETIMEDOUT|ENOTFOUND|EAI_AGAIN)|"
    r"\b(?:ECONNRESET|ECONNREFUSED|ETIMEDOUT|ENOTFOUND)\b|"
    r"getaddrinfo|"
    r"spawn[^\n]{0,120}ENOENT",
    re.IGNORECASE,
)

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
    env_repairs: list[dict[str, Any]] = field(default_factory=list)

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
            "env_repairs": self.env_repairs,
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


def _shell_arg(value: str) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline([value])
    return shlex.quote(value)


def is_environment_failure(output: str) -> bool:
    """Missing platform packages, a missing tool, or a network error. A missing source file is not one."""
    text = output or ""
    if missing_platform_module(text) or ENV_BINARY_RE.search(text):
        return True
    return bool(ENV_FAILURE_RE.search(text))


def ensure_env(
    repo: Path,
    runner: Runner,
    test_cmd: str,
    *,
    trace: Any = None,
    timeout: int = 300,
    install_timeout: int = 900,
    repairs: list[dict[str, Any]] | None = None,
) -> CmdResult:
    """Run the test command. If a platform package is missing, `npm install --no-save` it and re-run.

    One REVIVE_ENV_REPAIR trace event is recorded for each package installed this way.
    """
    result = runner(test_cmd, repo, timeout)
    for _ in range(3):
        if result.ok or not (repo / "package.json").is_file():
            return result
        missing = missing_platform_module(result.output)
        if not missing:
            return result
        cmd = f"npm install --no-save {NPM_FAST_FLAGS} {_shell_arg(missing)}"
        installed = runner(cmd, repo, install_timeout)
        result = runner(test_cmd, repo, timeout)
        record = {"package": missing, "install_cmd": cmd, "install_exit": installed.code, "test_exit": result.code, "seconds": round(float(installed.seconds or 0), 3)}
        if repairs is not None:
            repairs.append(record)
        if trace is not None:
            trace.event(
                agent_name="builder",
                action="REVIVE_ENV_REPAIR",
                input_summary=f"npm install --no-save {missing}",
                output_summary={"package": missing, "install_exit": installed.code, "test_exit": result.code, "seconds": record["seconds"], "timeout": install_timeout, "tail": tail(result.output, 300)},
                status="ok" if result.ok else "failed",
            )
    return result


def version_probe_command(repo: Path, package: str, version: str) -> str:
    """Ask the ecosystem whether this exact version exists. Quoted for the host shell."""
    if (repo / "package.json").is_file():
        return f"npm view {_shell_arg(f'{package}@{version}')} version"
    return f"python -m pip index versions {_shell_arg(package)}"


def _semver(text: str) -> tuple[int, int, int] | None:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def _locked_version(repo: Path, package: str) -> str | None:
    pkg_path = repo / "package.json"
    if pkg_path.is_file():
        try:
            data = json.loads(pkg_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if isinstance(data, dict):
            for key in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
                deps = data.get(key) or {}
                if isinstance(deps, dict) and package in deps:
                    return str(deps[package])
    req = repo / "requirements.txt"
    if req.is_file():
        prefix = package.lower() + "=="
        for line in req.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped.lower().startswith(prefix):
                return stripped.split("==", 1)[1].strip()
    return None


def _is_major_upgrade(repo: Path, package: str, version: str) -> bool:
    current = _locked_version(repo, package)
    if not current:
        return False
    cur = _semver(current)
    tgt = _semver(version)
    if not cur or not tgt:
        return False
    return tgt[0] > cur[0]


def _order_work_items(repo: Path, items: list[tuple[str, dict[str, Any]]]) -> list[tuple[str, dict[str, Any]]]:
    """Minor and patch upgrades before majors. Refactors stay after upgrades."""
    upgrades = [pair for pair in items if pair[0] == "upgrade"]
    rest = [pair for pair in items if pair[0] != "upgrade"]

    def rank(pair: tuple[str, dict[str, Any]]) -> int:
        item = pair[1]
        package = str(item.get("package") or "")
        version = str(item.get("version") or item.get("to_version") or "")
        return 1 if _is_major_upgrade(repo, package, version) else 0

    upgrades.sort(key=rank)
    return upgrades + rest


def _upgrade_skip_reason(repo: Path, runner: Runner, item: dict[str, Any], timeout: int) -> str | None:
    """None when the item may be attempted. Version misses are not retried."""
    package = str(item.get("package") or "").strip()
    version = str(item.get("version") or item.get("to_version") or "").strip()
    if not package or not version:
        return "target version not found"
    if _is_major_upgrade(repo, package, version) and len(str(item.get("reason") or "").strip()) < 12:
        return "major upgrade needs a stated reason"
    cmd = version_probe_command(repo, package, version)
    try:
        probed = runner(cmd, repo, timeout)
    except Exception:  # noqa: BLE001
        return "target version not found"
    output = str(getattr(probed, "output", "") or "")
    code = int(getattr(probed, "code", 1))
    if code != 0 or version not in output:
        return "target version not found"
    return None


def regression_ids(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> list[str]:
    """Real regressions only. An environment failure is not a code regression."""
    after_by = {str(row.get("id")): row for row in after}
    kept: list[str] = []
    for check_id in qg.regressions(before, after):
        detail = str((after_by.get(check_id) or {}).get("detail_tail") or "")
        if is_environment_failure(detail):
            continue
        kept.append(check_id)
    return kept


def _is_whole_file_doc(rel: str) -> bool:
    norm = rel.replace("\\", "/").lstrip("./")
    if not norm or ".." in norm.split("/"):
        return False
    suffix = Path(norm).suffix.lower()
    if suffix not in DOC_SUFFIXES:
        return False
    return "/" not in norm or norm.startswith("docs/")


def _manifest_touched(paths: list[str] | dict[str, Any]) -> bool:
    names = set()
    for rel in paths:
        names.add(str(rel).replace("\\", "/").rsplit("/", 1)[-1])
    return bool(names & MANIFEST_NAMES)


def manifest_content_changed(repo: Path, snap: dict[str, bytes | None]) -> bool:
    """True when restoring would change package.json or a lockfile. Unchanged manifests reuse node_modules."""
    for rel, data in snap.items():
        name = str(rel).replace("\\", "/").rsplit("/", 1)[-1]
        if name not in MANIFEST_NAMES:
            continue
        target = _safe_path(repo, rel)
        current = target.read_bytes() if target and target.is_file() else None
        if current != data:
            return True
    return False


def _json_blob(text: str) -> Any:
    raw = text or ""
    start_obj = raw.find("{")
    start_arr = raw.find("[")
    starts = [i for i in (start_obj, start_arr) if i >= 0]
    if not starts:
        return None
    start = min(starts)
    end = max(raw.rfind("}"), raw.rfind("]"))
    if end <= start:
        return None
    try:
        return json.loads(raw[start : end + 1])
    except ValueError:
        return None


def _minor_or_patch(current: str, *candidates: str | None) -> str | None:
    """The first candidate that stays on the same major and is newer. Majors are not automatic."""
    cur = _semver(current or "")
    if not cur:
        return None
    for raw in candidates:
        if not raw:
            continue
        tgt = _semver(str(raw))
        if tgt and tgt[0] == cur[0] and tgt > cur:
            return str(raw).strip()
    return None


def deterministic_upgrade_items(repo: Path, runner: Runner, timeout: int) -> list[dict[str, Any]]:
    """Minor and patch upgrades from the ecosystem's own outdated list. No model call."""
    items: list[dict[str, Any]] = []
    if (repo / "package.json").is_file():
        try:
            result = runner("npm outdated --json", repo, timeout)
        except Exception:  # noqa: BLE001
            return items
        data = _json_blob(str(getattr(result, "output", "") or ""))
        if not isinstance(data, dict):
            return items
        for name, info in data.items():
            if not isinstance(info, dict):
                continue
            current = str(info.get("current") or "")
            target = _minor_or_patch(current, str(info.get("wanted") or "") or None, str(info.get("latest") or "") or None)
            if not target:
                continue
            items.append(
                {
                    "id": f"upgrade-{name}",
                    "package": str(name),
                    "version": target,
                    "from_version": current,
                    "reason": "minor or patch upgrade reported by npm outdated",
                    "source": "deterministic",
                }
            )
        return items
    if any((repo / name).is_file() for name in ("requirements.txt", "pyproject.toml", "setup.py")):
        try:
            result = runner("python -m pip list --outdated --format json", repo, timeout)
        except Exception:  # noqa: BLE001
            return items
        data = _json_blob(str(getattr(result, "output", "") or ""))
        if not isinstance(data, list):
            return items
        for info in data:
            if not isinstance(info, dict):
                continue
            name = str(info.get("name") or "")
            current = str(info.get("version") or "")
            target = _minor_or_patch(current, str(info.get("latest_version") or "") or None)
            if not name or not target:
                continue
            items.append(
                {
                    "id": f"upgrade-{name}",
                    "package": name,
                    "version": target,
                    "from_version": current,
                    "reason": "minor or patch upgrade reported by pip list --outdated",
                    "source": "deterministic",
                }
            )
    return items


def bump_declared_version(repo: Path, package: str, old: str, new: str) -> tuple[list[str], list[str]]:
    """Move one declared dependency from old to new. One package, no other edits."""
    if not package or not old or not new or old == new:
        return [], [f"{package or '(package)'}: could not edit the manifest"]
    changed: list[str] = []
    pkg_path = repo / "package.json"
    if pkg_path.is_file():
        text = pkg_path.read_text(encoding="utf-8")
        pattern = re.compile(rf'("{re.escape(package)}"\s*:\s*")([^"]*)(")')
        pieces: list[str] = []
        cursor = 0
        replaced = False
        for match in pattern.finditer(text):
            spec = match.group(2)
            if old not in spec:
                continue
            pieces.append(text[cursor : match.start()])
            pieces.append(match.group(1) + spec.replace(old, new, 1) + match.group(3))
            cursor = match.end()
            replaced = True
        if replaced:
            pieces.append(text[cursor:])
            pkg_path.write_text("".join(pieces), encoding="utf-8", newline="\n")
            changed.append("package.json")
            return changed, []
    req = repo / "requirements.txt"
    if req.is_file():
        lines = req.read_text(encoding="utf-8").splitlines()
        out: list[str] = []
        replaced = False
        prefix = package.lower() + "=="
        for line in lines:
            stripped = line.strip()
            if stripped.lower().startswith(prefix) and old in stripped:
                out.append(package + "==" + new)
                replaced = True
            else:
                out.append(line)
        if replaced:
            req.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
            changed.append("requirements.txt")
            return changed, []
    return [], [f"{package}: could not edit the manifest"]


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
            install = f"npm ci {NPM_FAST_FLAGS}" if (repo / "package-lock.json").exists() else f"npm install {NPM_FAST_FLAGS}"
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


def _patch_errors(obj: Any) -> list[str]:
    if not isinstance(obj, dict) or not isinstance(obj.get("edits"), list) or not obj["edits"]:
        return ["edits must be a non-empty list"]
    paths = [str(edit.get("path") or "") for edit in obj["edits"] if isinstance(edit, dict) and edit.get("path")]
    if len(set(paths)) > MAX_FILES_PER_ITEM:
        return [f"edits touch more than {MAX_FILES_PER_ITEM} files"]
    return []


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
    """Ask for one patch. A shape or file-cap rejection is quoted back once; there is no third call."""
    errors: list[str] = []
    step = f"revive_patch_{iteration}"
    for attempt in (1, 2):
        quoted = ""
        if errors:
            quoted = "Validation errors from the previous attempt:\n" + "\n".join(f"- {error}" for error in errors) + "\n\n"
        prompt = (
            f"Repository: {repo_id}. Iteration {iteration}.\n"
            f"PRD: {json.dumps(prd, ensure_ascii=False) if prd else '(none)'}\n\n"
            f"Earlier attempts that did not make the checks pass:\n{chr(10).join(history) or '(none)'}\n\n"
            f"{note}\n\n"
            f"{quoted}"
            f"Test command: {last.cmd}\nExit code: {last.code}\nOutput (tail):\n{tail(last.output)}\n\n"
            f"Files:\n{_files_block(files)}\n\n"
            'Reply with {"rationale": str, "edits": [{"path": str, "find": str, "replace": str}]}. '
            '"find" must be an exact substring that occurs exactly once in that file. To create a new file use {"path": str, "content": str}. '
            f"At most {MAX_FILES_PER_ITEM} files. Do not touch tests."
        )
        captured: list[str] = []

        def valid(obj: dict[str, Any]) -> bool:
            captured.clear()
            captured.extend(_patch_errors(obj))
            return not captured

        obj: Any = None
        try:
            obj, *_ = client.complete_json(prompt, BUILDER_SYSTEM, repo_id=repo_id, step=step, attempts=1, validate=valid)
        except Exception:  # noqa: BLE001 — a failed call must not become a pass
            captured = ["model returned no usable patch"]
            obj = None
        if obj is not None and not captured:
            return obj
        errors = captured or ["model returned no usable patch"]
    return None


def apply_edits(repo: Path, edits: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """Apply edits. Returns (changed_paths, rejections). A rejected edit changes nothing."""
    changed: list[str] = []
    rejected: list[str] = []
    unique: list[str] = []
    for edit in edits:
        if isinstance(edit, dict) and edit.get("path"):
            rel = str(edit["path"])
            if rel not in unique:
                unique.append(rel)
    if len(unique) > MAX_FILES_PER_ITEM:
        return [], [f"edits touch more than {MAX_FILES_PER_ITEM} files"]
    for edit in edits:
        if not isinstance(edit, dict):
            rejected.append("edit is not an object")
            continue
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
                # Whole-file replacement is only for short docs. Source, tests, and lockfiles stay find/replace.
                if _is_whole_file_doc(rel) and target.stat().st_size < DOC_CONTENT_MAX_BYTES:
                    target.write_text(str(edit["content"]), encoding="utf-8", newline="\n")
                    changed.append(rel)
                    continue
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
    """Final state from deterministic checks. The reviewer is not an input.

    A green baseline with nothing kept is NOTHING_TO_FIX even if a later environment-broken
    check is red. RESURRECTION_FAILED is only a still-failing own test command.
    """
    if baseline_tests_passed and items_applied == 0:
        if not had_work_items:
            return "NOTHING_TO_FIX", "the repository's own tests already pass; there is no failing build to revive"
        extra = f" ({not_done_reason})" if not_done_reason else ""
        return "NOTHING_TO_FIX", "the repository's own tests already pass; no upgrade or refactor item was applied" + extra
    if not tests_passed:
        return "RESURRECTION_FAILED", "the repository's own test command still fails"
    if regressed:
        names = ", ".join(regressed)
        if baseline_tests_passed:
            return "NOTHING_TO_FIX", f"the repository's own tests already pass; quality checks regressed ({names}) so the patches were not accepted"
        return "RESURRECTION_FAILED", f"quality checks regressed versus the baseline: {names}"
    if not verified_criteria:
        if baseline_tests_passed:
            return "NOTHING_TO_FIX", "no acceptance criterion was verified by a passing quality check"
        return "NEEDS_REVIEW", "tests pass but no PRD acceptance criterion was verified by a passing quality check"
    if baseline_tests_passed:
        return "RESURRECTED", "upgrade or refactor items were applied and no quality check regressed; verified locally, not yet on fork CI"
    shown = ", ".join(verified_criteria)
    return "RESURRECTED", f"the repository's own test command passes and no quality check regressed; acceptance criteria verified: {shown}; verified locally, not yet on fork CI"


def adopt_deterministic_upgrades(prd: dict[str, Any], auto_upgrades: list[dict[str, Any]]) -> None:
    """Record ecosystem upgrades on the PRD so the diff-scope check can see package.json."""
    if not isinstance(prd, dict) or not auto_upgrades:
        return
    current = [item for item in (prd.get("upgrade_items") or []) if isinstance(item, dict)]
    seen = {str(item.get("package") or "") for item in current}
    for item in auto_upgrades:
        package = str(item.get("package") or "")
        if not package or package in seen:
            continue
        current.append(
            {
                "id": item.get("id"),
                "package": package,
                "version": item.get("version"),
                "reason": item.get("reason"),
                "source": "deterministic",
            }
        )
        seen.add(package)
    prd["upgrade_items"] = current


def format_run_summary(payload: dict[str, Any]) -> str:
    """One screen of plain text: state, the five checks, and what each item did."""
    quality = payload.get("quality") or {}
    before = {str(row.get("id")): row for row in (quality.get("before") or []) if isinstance(row, dict)}
    after = {str(row.get("id")): row for row in (quality.get("after") or []) if isinstance(row, dict)}
    lines = [
        f"{payload.get('repo') or 'repository'}: {payload.get('state') or ''}",
        _short(payload.get("reason") or ""),
        "Checks:",
    ]
    for check in SUMMARY_CHECKS:
        left = (before.get(check) or {}).get("status") or "n/a"
        right = (after.get(check) or {}).get("status") or "n/a"
        lines.append(f"  {check}: {left} -> {right}")
    done = [item for item in (payload.get("prd_items_done") or []) if isinstance(item, dict)]
    not_done = [item for item in (payload.get("prd_items_not_done") or []) if isinstance(item, dict)]
    reverted = [item for item in not_done if item.get("failed_check") or item.get("status") == "reverted"]
    reverted_ids = {id(item) for item in reverted}
    remaining = [item for item in not_done if id(item) not in reverted_ids]
    if done:
        lines.append("Applied:")
        for item in done:
            files = item.get("files") if isinstance(item.get("files"), list) else []
            lines.append(f"  {item.get('id')}: {len(files)} file(s), {int(item.get('lines') or 0)} lines")
    else:
        lines.append("Applied: none")
    if reverted:
        lines.append("Reverted:")
        for item in reverted:
            lines.append(f"  {item.get('id')}: {item.get('failed_check') or 'check'} failed ({_short(item.get('reason'))})")
    else:
        lines.append("Reverted: none")
    if remaining:
        lines.append("Not done:")
        for item in remaining:
            lines.append(f"  {item.get('id')}: {_short(item.get('reason'))}")
    else:
        lines.append("Not done: none")
    return "\n".join(lines)


def _short(text: Any, limit: int = 160) -> str:
    raw = " ".join(str(text or "").split())
    if len(raw) <= limit:
        return raw
    return raw[: limit - 3] + "..."


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


_CHECK_ID_RE = re.compile(r"\b(?:tests|lint|types|build|audit|smoke|diff_scope)\b")
_FILE_REF_RE = re.compile(
    r"(?:^|[\s`'\"(=])([A-Za-z0-9_@./\\-]+\.(?:js|jsx|ts|tsx|mjs|cjs|py|go|md|rst|txt|json|toml|yml|yaml|css|html))\b",
    re.IGNORECASE,
)


def _issue_is_concrete(detail: str) -> bool:
    """An issue counts only when it names a file or a quality-check id."""
    return bool(_CHECK_ID_RE.search(detail) or _FILE_REF_RE.search(detail))


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
    for kind, key in (("upgrade", "upgrade_items"), ("refactor", "refactor_items"), ("docs", "docs_items")):
        items = [item for item in (prd.get(key) or []) if isinstance(item, dict)]
        if kind == "docs":
            items = [item for item in items if isinstance(item.get("files"), list) and any(str(path).strip() for path in item["files"])]
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
    time_budget_s: float = 2400,
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

    started = time.monotonic()
    env_repairs: list[dict[str, Any]] = []

    def _ensure() -> CmdResult:
        return ensure_env(repo, _run, test, trace=trace, timeout=test_timeout, install_timeout=install_timeout, repairs=env_repairs)

    def _install(cmd: str) -> CmdResult:
        inst = _run(cmd, repo, install_timeout)
        trace.event(
            agent_name="builder",
            action="REVIVE_INSTALL",
            input_summary=cmd,
            output_summary={"exit": inst.code, "seconds": round(float(inst.seconds or 0), 3), "timeout": install_timeout, "tail": tail(inst.output, 300)},
            status="ok" if inst.ok else "failed",
        )
        return inst

    def _restore(snap: dict[str, bytes | None]) -> None:
        """Roll files back. Reinstall only when package.json or a lockfile actually changes."""
        changed_manifest = manifest_content_changed(repo, snap)
        had_manifest = _manifest_touched(snap)
        restore_snapshot(repo, snap)
        if install and changed_manifest:
            _install(reinstall_command(install))
        elif had_manifest:
            trace.event(
                agent_name="builder",
                action="REVIVE_INSTALL",
                input_summary="reuse node_modules",
                output_summary={"skipped": True, "reason": "package.json and lockfile unchanged", "seconds": 0, "timeout": install_timeout},
                status="skipped",
            )
        _ensure()

    originals: dict[str, bytes | None] = {}

    def _apply_tracked(edits: list[dict[str, Any]]) -> tuple[list[str], list[str], dict[str, bytes | None]]:
        edit_list = [edit for edit in edits if isinstance(edit, dict)]
        paths = [str(edit.get("path") or "") for edit in edit_list if edit.get("path")]
        if len(set(paths)) > MAX_FILES_PER_ITEM:
            return [], [f"edits touch more than {MAX_FILES_PER_ITEM} files"], {}
        snap: dict[str, bytes | None] = {}
        for edit in edit_list:
            rel = str(edit.get("path") or "")
            target = _safe_path(repo, rel)
            if target is None:
                continue
            data = target.read_bytes() if target.is_file() else None
            snap[rel] = data
            originals.setdefault(rel, data)
        changed, rejected = apply_edits(repo, edit_list)
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
        _install(install)
    baseline = _ensure()
    trace.event(agent_name="validator", action="REVIVE_BASELINE", input_summary=test, output_summary={"exit": baseline.code, "tail": tail(baseline.output, 400)}, status="ok" if baseline.ok else "failed")

    prd = draft_prd(client, repo, repo_id, description, baseline)
    trace.event(
        agent_name="architect",
        action="REVIVE_PRD",
        input_summary={"repo": repo_id, "degraded": bool(prd.get("degraded"))},
        output_summary=prd,
        status="degraded" if prd.get("degraded") else "ok",
        summary_limit=20000,
    )
    result = ReviveResult("RESURRECTION_FAILED", "", baseline=baseline, prd=prd, env_repairs=env_repairs)
    before = _gate(baseline, prd)
    work_items, capped = _work_items(prd)
    for skipped in capped:
        result.prd_items_not_done.append(skipped)
        trace.event(agent_name="builder", action="REVIVE_ITEM", input_summary={"id": skipped["id"], "kind": skipped["kind"]}, output_summary=skipped, status="not_done")

    def _emit_quality(final_checks: list[dict[str, Any]], verified: list[str]) -> None:
        files, lines = _changes()
        result.files_changed = files
        result.quality = qg.quality_report(before, final_checks, files, lines, verified)
        result.quality["regressed"] = regression_ids(before, final_checks)
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

    auto_upgrades: list[dict[str, Any]] = []
    if baseline.ok:
        auto_upgrades = deterministic_upgrade_items(repo, _run, min(60, test_timeout))
    if baseline.ok and not work_items and not auto_upgrades:
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
            if install and _manifest_touched(changed):
                _install(reinstall_command(install))
            run = _ensure()
            accepted = run.ok
            if accepted:
                gate = _gate(run, prd)
                regs = regression_ids(accepted_checks, gate)
                if regs:
                    _restore(snap)
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

    if stage_a_ok and not baseline.ok:
        auto_upgrades = deterministic_upgrade_items(repo, _run, min(60, test_timeout))

    def _lines_for(snap: dict[str, bytes | None], paths: list[str]) -> int:
        total = 0
        for rel in paths:
            target = _safe_path(repo, rel)
            after = target.read_bytes() if target and target.is_file() else None
            total += _line_delta(snap.get(rel), after)
        return total

    def _remember(paths: list[str]) -> dict[str, bytes | None]:
        snap: dict[str, bytes | None] = {}
        for rel in paths:
            target = _safe_path(repo, rel)
            if target is None:
                continue
            data = target.read_bytes() if target.is_file() else None
            snap[rel] = data
            originals.setdefault(rel, data)
        return snap

    def _record_not_done(item_id: str, kind: str, reason: str, failed_check: str = "") -> None:
        record: dict[str, Any] = {"id": item_id, "kind": kind, "reason": reason, "status": "reverted" if failed_check else "not_done"}
        if failed_check:
            record["failed_check"] = failed_check
        result.prd_items_not_done.append(record)
        trace.event(agent_name="builder", action="REVIVE_ITEM", input_summary={"id": item_id, "kind": kind}, output_summary={"status": "not_done", "reason": reason, "failed_check": failed_check}, status="not_done")

    def _record_applied(item_id: str, kind: str, files: list[str], lines: int) -> None:
        result.prd_items_done.append({"id": item_id, "kind": kind, "files": files, "lines": lines, "status": "applied"})
        trace.event(agent_name="builder", action="REVIVE_ITEM", input_summary={"id": item_id, "kind": kind}, output_summary={"status": "applied", "changed": files, "lines": lines}, status="applied")

    def _apply_deterministic_upgrade(item: dict[str, Any]) -> None:
        nonlocal accepted_checks, last
        item_id = str(item.get("id") or "upgrade")
        package = str(item.get("package") or "").strip()
        version = str(item.get("version") or item.get("to_version") or "").strip()
        if item.get("source") != "deterministic":
            skip = _upgrade_skip_reason(repo, _run, item, test_timeout)
            if skip:
                _record_not_done(item_id, "upgrade", skip)
                return
        old = str(item.get("from_version") or _locked_version(repo, package) or "")
        manifests = [name for name in ("package.json", "requirements.txt", "pyproject.toml") if (repo / name).is_file()]
        snap = _remember(manifests)
        changed, rejected = bump_declared_version(repo, package, old, version)
        if not changed:
            _record_not_done(item_id, "upgrade", "; ".join(rejected) or "could not edit the manifest")
            return
        if install and _manifest_touched(changed):
            _install(reinstall_command(install))
        run = _ensure()
        gate = _gate(run, prd)
        regs = regression_ids(accepted_checks, gate)
        if run.ok and not regs:
            accepted_checks = gate
            last = run
            _record_applied(item_id, "upgrade", changed, _lines_for(snap, changed))
            return
        failed = regs[0] if regs else "tests"
        repair_note = (
            f"The deterministic upgrade of {package} from {old} to {version} failed check {failed}. "
            "Repair non-test source so the checks pass. Do not edit tests. "
            "A missing platform package, a missing tool, or a network error is not a code defect."
        )
        repaired = False
        repair_files: list[str] = []
        rsnap: dict[str, bytes | None] = {}
        last_reason = tail(_failure_text(regs, gate) if regs else run.output, 600) or f"{failed} failed"
        for attempt in range(1, MAX_ITEM_ATTEMPTS + 1):
            files = context_files(repo, run.output)
            patch = propose_patch(client, repo_id, prd, f"{item_id}-repair-{attempt}", run, files, history, note=repair_note + f"\nItem attempt {attempt} of {MAX_ITEM_ATTEMPTS}.")
            if not patch:
                last_reason = "the model returned no usable patch"
                trace.event(agent_name="builder", action="REVIVE_ITEM", input_summary={"id": item_id, "kind": "upgrade", "attempt": attempt}, output_summary={"status": "reverted", "reason": last_reason, "failed_check": failed}, retry_count=attempt, status="failed")
                break
            rchanged, rrejected, rsnap = _apply_tracked(patch.get("edits") or [])
            if not rchanged:
                last_reason = "; ".join(rrejected) or "every edit was rejected"
                trace.event(agent_name="builder", action="REVIVE_ITEM", input_summary={"id": item_id, "kind": "upgrade", "attempt": attempt}, output_summary={"status": "reverted", "reason": last_reason, "failed_check": failed}, retry_count=attempt, status="failed")
                continue
            if install and _manifest_touched(rchanged):
                _install(reinstall_command(install))
            run = _ensure()
            gate = _gate(run, prd)
            regs = regression_ids(accepted_checks, gate)
            if run.ok and not regs:
                repaired = True
                repair_files = rchanged
                accepted_checks = gate
                last = run
                break
            _restore(rsnap)
            failed = regs[0] if regs else "tests"
            last_reason = tail(_failure_text(regs, gate) if regs else run.output, 600) or f"{failed} failed"
            history.append(f"item {item_id} repair {attempt}: reverted. {last_reason}")
            trace.event(agent_name="builder", action="REVIVE_ITEM", input_summary={"id": item_id, "kind": "upgrade", "attempt": attempt}, output_summary={"status": "reverted", "reason": last_reason, "failed_check": failed, "changed": rchanged}, retry_count=attempt, status="reverted")
        if repaired:
            _record_applied(item_id, "upgrade", changed + [path for path in repair_files if path not in changed], _lines_for(snap, changed) + _lines_for(rsnap, repair_files))
            return
        _restore(snap)
        _record_not_done(item_id, "upgrade", last_reason, failed)

    if stage_a_ok and (work_items or auto_upgrades):
        adopt_deterministic_upgrades(prd, auto_upgrades)
        prd_upgrades = [item for kind, item in work_items if kind == "upgrade"]
        others = [(kind, item) for kind, item in work_items if kind != "upgrade"]
        seen_packages: set[str] = set()
        merged: list[tuple[str, dict[str, Any]]] = []
        for item in [*auto_upgrades, *prd_upgrades]:
            package_name = str(item.get("package") or "").strip()
            item_id = str(item.get("id") or "upgrade")
            if not package_name:
                _record_not_done(item_id, "upgrade", "target version not found")
                continue
            if package_name in seen_packages:
                _record_not_done(item_id, "upgrade", f"{package_name} is already covered by an earlier upgrade")
                continue
            seen_packages.add(package_name)
            merged.append(("upgrade", item))
        for _, extra in merged[MAX_ITEMS_PER_KIND:]:
            _record_not_done(str(extra.get("id") or "upgrade"), "upgrade", f"not attempted: capped at {MAX_ITEMS_PER_KIND} upgrade items")
        ordered = _order_work_items(repo, merged[:MAX_ITEMS_PER_KIND] + others)
        index = 0
        while index < len(ordered):
            if time.monotonic() - started >= float(time_budget_s):
                for kind, item in ordered[index:]:
                    record = {"id": str(item.get("id") or kind), "kind": kind, "reason": "time budget"}
                    result.prd_items_not_done.append(record)
                    trace.event(agent_name="builder", action="REVIVE_ITEM", input_summary={"id": record["id"], "kind": kind}, output_summary={"status": "not_done", "reason": record["reason"]}, status="not_done")
                break
            kind, item = ordered[index]
            index += 1
            item_id = str(item.get("id") or kind)
            if kind == "upgrade":
                _apply_deterministic_upgrade(item)
                continue
            done = False
            last_reason = "no patch was applied"
            kept_files: list[str] = []
            kept_lines = 0
            failed_check = ""
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
                if install and _manifest_touched(changed):
                    _install(reinstall_command(install))
                run = _ensure()
                gate = _gate(run, prd)
                regs = regression_ids(accepted_checks, gate)
                # ensure_env has already run. An environment failure that survives it can be reverted;
                # one that ensure_env fixed is a pass and is kept.
                if regs or not run.ok:
                    failed_check = regs[0] if regs else "tests"
                    _restore(snap)
                    detail = _failure_text(regs, gate) if regs else run.output
                    last_reason = tail(detail, 600) or "quality check failed"
                    last = CmdResult(test, 1, last_reason)
                    history.append(f"item {item_id} attempt {attempt}: reverted. {last_reason}")
                    trace.event(agent_name="builder", action="REVIVE_ITEM", input_summary={"id": item_id, "kind": kind, "attempt": attempt}, output_summary={"status": "reverted", "reason": last_reason, "changed": changed, "regressed": regs, "failed_check": failed_check}, retry_count=attempt, status="reverted")
                    continue
                accepted_checks = gate
                last = run
                done = True
                kept_files = changed
                kept_lines = _lines_for(snap, changed)
                trace.event(agent_name="builder", action="REVIVE_ITEM", input_summary={"id": item_id, "kind": kind, "attempt": attempt}, output_summary={"status": "applied", "changed": changed, "lines": kept_lines}, retry_count=attempt, status="applied")
                break
            if done:
                result.prd_items_done.append({"id": item_id, "kind": kind, "files": kept_files, "lines": kept_lines, "status": "applied"})
            else:
                record = {"id": item_id, "kind": kind, "reason": last_reason, "status": "reverted" if failed_check else "not_done"}
                if failed_check:
                    record["failed_check"] = failed_check
                result.prd_items_not_done.append(record)
                trace.event(agent_name="builder", action="REVIVE_ITEM", input_summary={"id": item_id, "kind": kind}, output_summary={"status": "not_done", "reason": last_reason, "failed_check": failed_check}, status="not_done")
    elif work_items and not stage_a_ok:
        for kind, item in work_items:
            record = {"id": str(item.get("id") or kind), "kind": kind, "reason": "baseline tests still fail, so the item was not attempted"}
            result.prd_items_not_done.append(record)
            trace.event(agent_name="builder", action="REVIVE_ITEM", input_summary={"id": record["id"], "kind": kind}, output_summary={"status": "not_done", "reason": record["reason"]}, status="not_done")

    review = _review(client, repo_id, prd, _unified(), before, accepted_checks)
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
                if install and _manifest_touched(changed):
                    _install(reinstall_command(install))
                run = _ensure()
                gate = _gate(run, prd)
                regs = regression_ids(accepted_checks, gate)
                if regs or not run.ok:
                    _restore(snap)
                    trace.event(agent_name="builder", action="REVIVE_ITEM", input_summary={"id": "review-1", "kind": "review"}, output_summary={"status": "reverted", "reason": _failure_text(regs, gate) if regs else tail(run.output, 400)}, status="reverted")
                else:
                    accepted_checks = gate
                    last = run

    final_run = _ensure()
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
        had_work_items=bool(work_items or auto_upgrades),
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


def _quality_table(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"id": row["id"], "status": row["status"], "detail_tail": row.get("detail_tail", ""), "high": row.get("high"), "critical": row.get("critical")} for row in checks]


def _review(client: Any, repo_id: str, prd: dict[str, Any], diff: str, before_checks: list[dict[str, Any]], after_checks: list[dict[str, Any]]) -> dict[str, Any]:
    prompt = (
        f"Repository: {repo_id}. Review iteration 1.\n"
        "You may only add issues. Approving cannot override a failed deterministic check.\n"
        "Environment-caused test failures are not code defects. A missing platform package "
        "(@rollup/*, @swc/*, @esbuild/*, @parcel/watcher), ENOENT of a tool, or a network error "
        "is an environment failure, not a defect in the patch.\n"
        f"PRD: {json.dumps(prd, ensure_ascii=False)}\n\n"
        f"Diff:\n{diff}\n\n"
        f"Quality before: {json.dumps(_quality_table(before_checks), ensure_ascii=False)}\n"
        f"Quality after: {json.dumps(_quality_table(after_checks), ensure_ascii=False)}\n\n"
        "Each issue must name a concrete file or one of these check ids: "
        + ", ".join(qg.CHECK_IDS)
        + ". Issues that do not are dropped.\n"
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
    issues = [issue for issue in _concrete_issues(obj.get("issues")) if _issue_is_concrete(issue["detail"])]
    return {"approve": obj.get("approve"), "issues": issues, "degraded": False}
