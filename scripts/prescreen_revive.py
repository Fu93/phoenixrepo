#!/usr/bin/env python3
"""Cheap gate-2 pre-screen. No model calls.

From a scores file, take the NEEDS_REVIEW and NEEDS_PRIOR_ART repos and clone each
one shallowly. A repo has tests only when a collector reports a count above zero.
The install runs inside that repo's directory, in a venv or node_modules, before the
baseline. One repo failing does not stop the rest. A network or install failure is
INSTALL_FAILED, not a red test run.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.revive_loop import CmdResult, _shell_arg, detect_commands, is_no_tests, shell_runner  # noqa: E402
from app.revive_verify import parse_test_counts  # noqa: E402

REVIEW_VERDICTS = frozenset({"NEEDS_REVIEW", "NEEDS_PRIOR_ART"})
DEFAULT_INPUTS = (
    ROOT / "logs" / "curated30d_scores.json",
    ROOT / "data" / "discovery" / "raw" / "snapshot.json",
)
BASELINE_STATES = ("GREEN", "RED", "NO_TESTS", "INSTALL_FAILED", "TIMEOUT")
_COLLECTED_RE = re.compile(r"(\d+)\s+tests?\s+collected", re.IGNORECASE)
_GO_TEST_RE = re.compile(r"^(?:Test|Example)\w*")
_FAILURE_RE = re.compile(r"^(?:FAILED\b|--- FAIL:|FAIL\t|not ok\b|ERROR collecting\b)|AssertionError|ModuleNotFoundError|ImportError", re.IGNORECASE)


def load_review_repos(path: Path) -> list[dict[str, Any]]:
    """Rows whose verdict is NEEDS_REVIEW or NEEDS_PRIOR_ART.

    A snapshot that has repositories but no verdicts is not a screen list: cloning
    every discovery repo would not be a pre-screen of the gate-1 review pile.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = _rows(data)
    if not any(str(row.get("verdict") or "").strip() for row in rows):
        return []
    selected = []
    for row in rows:
        if str(row.get("verdict") or "") not in REVIEW_VERDICTS:
            continue
        repo_id = _repo_id(row)
        if repo_id:
            selected.append(row)
    return selected


def _rows(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item if isinstance(item, dict) else {"repo_id": str(item)} for item in data]
    if not isinstance(data, dict):
        return []
    rows: list[dict[str, Any]] = []
    for key in ("ineligible", "candidates"):
        for item in data.get(key) or []:
            if isinstance(item, dict):
                rows.append(item)
    repos = data.get("repos")
    if isinstance(repos, dict):
        for repo_id, info in repos.items():
            row = dict(info) if isinstance(info, dict) else {}
            row.setdefault("repo_id", repo_id)
            rows.append(row)
    elif isinstance(repos, list):
        rows.extend(item for item in repos if isinstance(item, dict))
    return rows


def _repo_id(row: dict[str, Any]) -> str:
    return str(row.get("repo_id") or row.get("full_name") or row.get("repo") or "").strip()


def detect_language(repo: Path, fallback: str = "") -> str:
    if (repo / "package.json").is_file():
        return "javascript"
    if (repo / "go.mod").is_file():
        return "go"
    if any((repo / name).is_file() for name in ("requirements.txt", "pyproject.toml", "setup.py")):
        return "python"
    return (fallback or "unknown").strip() or "unknown"


def venv_python(repo: Path) -> Path:
    """Interpreter inside this clone. Never the machine-wide one."""
    folder = "Scripts" if os.name == "nt" else "bin"
    name = "python.exe" if os.name == "nt" else "python"
    return repo / ".venv" / folder / name


def plan_commands(repo: Path) -> dict[str, Any]:
    """Install, collect, and baseline commands. Install stays inside the clone."""
    language = detect_language(repo)
    install, test = detect_commands(repo)
    if language == "javascript" and test:
        return {
            "language": language,
            "install": [install] if install else [],
            "collect_probe": "npm test --silent -- --listTests",
            "collect": "npm test --silent -- --listTests",
            "test": test,
            "outdated": "npm outdated --json",
            "audit": "npm audit --omit=dev --json",
        }
    if language == "python" and test:
        py = _shell_arg(str(venv_python(repo)))
        create = f"{_shell_arg(sys.executable)} -m venv {_shell_arg(str(repo / '.venv'))}"
        # --rootdir and an explicit test path stop pytest from walking up to a parent config.
        isolated = "--noconftest -p no:cacheprovider --rootdir . -o testpaths=."
        pip = [create]
        if (repo / "requirements.txt").is_file():
            pip.append(f"{py} -m pip install -q --disable-pip-version-check -r requirements.txt")
        elif (repo / "pyproject.toml").is_file() or (repo / "setup.py").is_file():
            pip.append(f"{py} -m pip install -q --disable-pip-version-check -e .")
        pip.append(f"{py} -m pip install -q --disable-pip-version-check pytest")
        return {
            "language": language,
            "install": pip,
            "collect_probe": f"{_shell_arg(sys.executable)} -m pytest --collect-only -q {isolated}",
            "collect": f"{py} -m pytest --collect-only -q {isolated}",
            "test": f"{py} -m pytest -q {isolated}",
            "outdated": f"{py} -m pip list --outdated --format json",
            "audit": "",
        }
    if language == "go" and test:
        return {
            "language": language,
            "install": [install] if install else [],
            "collect_probe": "go test ./... -list .",
            "collect": "go test ./... -list .",
            "test": test,
            "outdated": "",
            "audit": "",
        }
    return {"language": language, "install": [], "collect_probe": "", "collect": "", "test": "", "outdated": "", "audit": ""}


def count_outdated(output: str) -> int | None:
    """Length of an npm outdated object or a pip outdated list. Non-JSON is unavailable."""
    raw = (output or "").strip()
    if not raw:
        return None
    data: Any = None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        for index, char in enumerate(raw):
            if char in "{[":
                try:
                    data = json.loads(raw[index:])
                except json.JSONDecodeError:
                    return None
                break
    if isinstance(data, dict):
        return len(data)
    if isinstance(data, list):
        return len(data)
    return None


def count_vulnerable(output: str) -> int | None:
    """High plus critical findings from npm audit JSON. Unreadable output is unavailable."""
    from app.quality_gate import parse_npm_audit

    parsed = parse_npm_audit(output or "", 0)
    if parsed.get("status") == "unavailable":
        return None
    return int(parsed.get("high") or 0) + int(parsed.get("critical") or 0)


def parse_collected(cmd: str, output: str, code: int) -> int:
    """How many tests the collector actually reported. Exit 5 and an empty list are zero."""
    text = output or ""
    if code == 5 or is_no_tests(cmd, text, code):
        return 0
    match = _COLLECTED_RE.search(text)
    if match:
        return int(match.group(1))
    if " -list " in cmd or cmd.strip().endswith("-list ."):
        return sum(1 for line in text.splitlines() if _GO_TEST_RE.match(line.strip()))
    if "--listTests" in cmd:
        count = 0
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(">"):
                continue
            if stripped.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")) or "/" in stripped or "\\" in stripped:
                count += 1
        return count
    nodes = [line for line in text.splitlines() if "::" in line]
    return len(nodes)


def explicit_no_tests(cmd: str, output: str, code: int) -> bool:
    """A collector that finished and found nothing. A missing tool is not this."""
    if is_no_tests(cmd, output, code):
        return True
    if code == 0 and parse_collected(cmd, output, code) == 0 and (output or "").strip():
        return True
    if code == 0 and not (output or "").strip() and ("--collect-only" in cmd or "--listTests" in cmd or " -list " in cmd):
        return True
    return False


def first_failure_line(output: str) -> str:
    for line in (output or "").splitlines():
        stripped = line.strip()
        if stripped and _FAILURE_RE.search(stripped):
            return stripped[:180]
    for line in (output or "").splitlines():
        stripped = line.strip()
        if stripped and "fail" in stripped.lower():
            return stripped[:180]
    return ""


def _has_debt(row: dict[str, Any]) -> bool:
    outdated = row.get("outdated")
    vulnerable = row.get("vulnerable")
    if isinstance(outdated, int) and outdated >= 1:
        return True
    return isinstance(vulnerable, int) and vulnerable >= 1


def revivability_score(row: dict[str, Any]) -> int:
    """RED with real tests ranks first, then a green repo whose dependencies are behind.

    NO_TESTS and INSTALL_FAILED are zero: there is nothing trustworthy to revive.
    A timeout is also zero, because the baseline never finished.
    """
    status = str(row.get("baseline_status") or "")
    if row.get("error") and status not in {"RED", "GREEN"}:
        return 0
    collected = int(row.get("collected") or 0)
    if status == "RED" and collected > 0:
        return 10 + (2 if _has_debt(row) else 0)
    if status == "GREEN" and collected > 0 and _has_debt(row):
        return 7
    if status == "GREEN" and collected > 0:
        return 1
    return 0


def explain_row(row: dict[str, Any]) -> str:
    status = str(row.get("baseline_status") or "")
    detail = str(row.get("detail") or "").strip()
    if status == "RED":
        return f"RED: {row.get('failed')} failed of {row.get('collected')} collected; {row.get('first_failure') or detail or 'no failure line'}"
    if status == "GREEN" and _has_debt(row):
        return f"GREEN: {row.get('passed')} passed of {row.get('collected')} collected; outdated {row.get('outdated')}; vulnerable {row.get('vulnerable')}"
    if status == "GREEN":
        return f"GREEN: {row.get('passed')} passed of {row.get('collected')} collected; dependencies current"
    if status == "NO_TESTS":
        return "NO_TESTS: " + (detail or "collection found 0 tests")
    if status == "INSTALL_FAILED":
        return "INSTALL_FAILED: " + (detail or "install did not exit 0")
    if status == "TIMEOUT":
        return "TIMEOUT: " + (detail or "the per-repo budget ran out")
    if row.get("error"):
        return str(row["error"])
    return detail


def clone_command(repo_id: str, dest: Path) -> list[str]:
    return ["git", "clone", "--depth", "1", f"https://github.com/{repo_id}.git", str(dest)]


def git_clone(repo_id: str, dest: Path, timeout: float) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        clone_command(repo_id, dest),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(1, int(timeout)),
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "git clone failed").strip()
        raise RuntimeError(detail[:300])


def prescreen_shell(cmd: str, cwd: Path, timeout: int) -> CmdResult:
    """shell_runner with caches kept inside the clone, so the screen does not touch a shared store."""
    extra = {
        "GOMODCACHE": str(cwd / ".gocache"),
        "GOCACHE": str(cwd / ".gocache" / "build"),
        "npm_config_cache": str(cwd / ".npm-cache"),
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    }
    return shell_runner(cmd, cwd, timeout, environ=extra)


def _blank(row: dict[str, Any], repo_id: str, language: str = "") -> dict[str, Any]:
    return {
        "repo": repo_id,
        "language": language or str(row.get("language") or row.get("primary_language") or "unknown"),
        "has_tests": False,
        "install": "",
        "baseline_status": "",
        "baseline_code": None,
        "baseline_seconds": None,
        "collected": 0,
        "passed": None,
        "failed": None,
        "first_failure": "",
        "outdated": None,
        "vulnerable": None,
        "score": 0,
        "reason": "",
        "detail": "",
        "error": "",
        "verdict": str(row.get("verdict") or ""),
    }


def _finish(record: dict[str, Any]) -> dict[str, Any]:
    record["score"] = revivability_score(record)
    record["reason"] = explain_row(record)
    return record


def _run(runner: Callable[[str, Path, int], CmdResult], cmd: str, dest: Path, budget: float) -> CmdResult:
    if budget <= 0:
        return CmdResult(cmd, 124, "TIMEOUT per-repo budget exhausted")
    try:
        return runner(cmd, dest, max(1, int(budget)))
    except Exception as exc:  # noqa: BLE001 — one command must not abort the table
        return CmdResult(cmd, 1, f"{type(exc).__name__}: {exc}")


def screen_repo(
    row: dict[str, Any],
    dest: Path,
    *,
    cloner: Callable[[str, Path, float], None],
    runner: Callable[[str, Path, int], CmdResult],
    per_repo_timeout: float = 180,
    now: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Clone, collect, install inside the clone, then baseline. Never rank a pre-install crash as RED."""
    repo_id = _repo_id(row)
    record = _blank(row, repo_id)
    started = now()

    def remaining() -> float:
        return float(per_repo_timeout) - (now() - started)

    if remaining() <= 0:
        record["baseline_status"] = "TIMEOUT"
        record["detail"] = "budget exhausted before clone"
        record["error"] = "TIMEOUT"
        return _finish(record)
    try:
        cloner(repo_id, dest, max(1.0, remaining()))
    except Exception as exc:  # noqa: BLE001 — one clone failure must not stop the table
        record["error"] = f"{type(exc).__name__}: {exc}"[:240]
        return _finish(record)

    record["language"] = detect_language(dest, record["language"])
    plan = plan_commands(dest)
    record["language"] = plan["language"] if plan["language"] != "unknown" else record["language"]
    record["install"] = " && ".join(plan["install"])
    if not plan["test"]:
        record["baseline_status"] = "NO_TESTS"
        record["detail"] = "no test command in the manifest"
        return _finish(record)

    if plan["collect_probe"] and remaining() > 0:
        probe = _run(runner, plan["collect_probe"], dest, min(30.0, remaining()))
        if probe.code == 124:
            record["baseline_status"] = "TIMEOUT"
            record["baseline_code"] = 124
            record["detail"] = "collection probe timed out"
            return _finish(record)
        if explicit_no_tests(probe.cmd, probe.output, probe.code):
            record["baseline_status"] = "NO_TESTS"
            record["collected"] = 0
            record["baseline_code"] = probe.code
            record["detail"] = first_failure_line(probe.output) or (probe.output or "collection found 0 tests").strip().splitlines()[0][:180]
            return _finish(record)

    for command in plan["install"]:
        if remaining() <= 0:
            record["baseline_status"] = "TIMEOUT"
            record["baseline_code"] = 124
            record["detail"] = "budget exhausted before install finished"
            return _finish(record)
        installed = _run(runner, command, dest, remaining())
        if installed.code == 124:
            record["baseline_status"] = "TIMEOUT"
            record["baseline_code"] = 124
            record["detail"] = "install timed out"
            return _finish(record)
        if not installed.ok:
            record["baseline_status"] = "INSTALL_FAILED"
            record["baseline_code"] = installed.code
            record["detail"] = first_failure_line(installed.output) or (installed.output or "install failed").strip().splitlines()[0][:180]
            record["error"] = record["detail"]
            return _finish(record)

    if remaining() <= 0:
        record["baseline_status"] = "TIMEOUT"
        record["baseline_code"] = 124
        record["detail"] = "budget exhausted before collection"
        return _finish(record)
    collected = _run(runner, plan["collect"], dest, min(45.0, remaining()))
    if collected.code == 124:
        record["baseline_status"] = "TIMEOUT"
        record["baseline_code"] = 124
        record["detail"] = "collection timed out"
        return _finish(record)
    count = parse_collected(collected.cmd, collected.output, collected.code)
    record["collected"] = count
    record["has_tests"] = count > 0
    if count <= 0:
        record["baseline_status"] = "NO_TESTS"
        record["baseline_code"] = collected.code
        record["detail"] = first_failure_line(collected.output) or (collected.output or "collection found 0 tests").strip().splitlines()[0][:180]
        return _finish(record)

    if remaining() <= 0:
        record["baseline_status"] = "TIMEOUT"
        record["baseline_code"] = 124
        record["detail"] = "budget exhausted before baseline"
        return _finish(record)
    baseline = _run(runner, plan["test"], dest, remaining())
    record["baseline_code"] = int(baseline.code)
    record["baseline_seconds"] = round(float(getattr(baseline, "seconds", 0) or 0), 3)
    counts = parse_test_counts(baseline.output or "")
    if counts:
        record["passed"] = counts["passed"]
        record["failed"] = counts["failed"] + counts["errors"]
    if baseline.code == 124:
        record["baseline_status"] = "TIMEOUT"
        record["detail"] = "baseline timed out"
        return _finish(record)
    if is_no_tests(baseline.cmd, baseline.output, baseline.code) or (baseline.ok and counts and counts["passed"] == 0 and counts["failed"] == 0):
        record["baseline_status"] = "NO_TESTS"
        record["has_tests"] = False
        record["collected"] = 0
        record["detail"] = first_failure_line(baseline.output) or "baseline collected 0 tests"
        return _finish(record)
    if baseline.ok:
        record["baseline_status"] = "GREEN"
        if record["passed"] is None:
            record["passed"] = count
        record["failed"] = record["failed"] or 0
    else:
        record["baseline_status"] = "RED"
        if record["failed"] is None:
            record["failed"] = 1
        record["first_failure"] = first_failure_line(baseline.output)
        record["detail"] = record["first_failure"]

    if plan["outdated"] and remaining() > 0:
        listed = _run(runner, plan["outdated"], dest, min(20.0, remaining()))
        if listed.code != 124:
            record["outdated"] = count_outdated(listed.output or "")
    if plan["audit"] and remaining() > 0:
        audited = _run(runner, plan["audit"], dest, min(20.0, remaining()))
        if audited.code != 124:
            record["vulnerable"] = count_vulnerable(audited.output or "")
    return _finish(record)


def screen_repos(
    repos: list[dict[str, Any]],
    workdir: Path,
    *,
    cloner: Callable[[str, Path, float], None] = git_clone,
    runner: Callable[[str, Path, int], CmdResult] = prescreen_shell,
    per_repo_timeout: float = 180,
) -> list[dict[str, Any]]:
    workdir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for row in repos:
        repo_id = _repo_id(row) or "unknown"
        dest = workdir / repo_id.replace("/", "__")
        try:
            rows.append(screen_repo(row, dest, cloner=cloner, runner=runner, per_repo_timeout=per_repo_timeout))
        except Exception as exc:  # noqa: BLE001
            blank = _blank(row, repo_id)
            blank["error"] = f"{type(exc).__name__}: {exc}"[:240]
            rows.append(_finish(blank))
    return rows


def render_markdown(rows: list[dict[str, Any]]) -> str:
    ranked = sorted(rows, key=lambda row: (-int(row.get("score") or 0), str(row.get("repo") or "")))
    lines = [
        "| Rank | Repository | Language | Tests | Status | Collected | Passed | Failed | Outdated | Vulnerable | Score | Reason |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for rank, row in enumerate(ranked, start=1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(rank),
                    _cell(row.get("repo")),
                    _cell(row.get("language")),
                    "yes" if row.get("has_tests") else "no",
                    _cell(row.get("baseline_status") or "—"),
                    _cell(row.get("collected") if row.get("collected") is not None else "—"),
                    _cell(row.get("passed") if row.get("passed") is not None else "—"),
                    _cell(row.get("failed") if row.get("failed") is not None else "—"),
                    _cell(row.get("outdated") if row.get("outdated") is not None else "—"),
                    _cell(row.get("vulnerable") if row.get("vulnerable") is not None else "—"),
                    str(int(row.get("score") or 0)),
                    _cell(row.get("reason") or ""),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def _cell(value: Any) -> str:
    return str("" if value is None else value).replace("|", "/").replace("\n", " ")


def main(argv: list[str] | None = None, *, cloner: Callable | None = None, runner: Callable | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=None, help="Scores JSON. Defaults to logs/curated30d_scores.json, then the discovery snapshot.")
    parser.add_argument("--workdir", type=Path, default=ROOT / "work" / "prescreen")
    parser.add_argument("--per-repo-timeout", type=float, default=180, dest="per_repo_timeout")
    parser.add_argument("--output", type=Path, default=None, help="Also write the markdown table to this path.")
    args = parser.parse_args(argv)
    if args.input is not None:
        path = args.input
        if not path.is_file():
            sys.stderr.write(f"scores file not found: {path}\n")
            return 2
    else:
        path = next((candidate for candidate in DEFAULT_INPUTS if candidate.is_file()), None)
        if path is None:
            sys.stderr.write("no scores file found (looked for logs/curated30d_scores.json and data/discovery/raw/snapshot.json)\n")
            return 2
    repos = load_review_repos(path)
    if not repos:
        print(f"no NEEDS_REVIEW or NEEDS_PRIOR_ART rows in {path}")
        return 0
    rows = screen_repos(
        repos,
        args.workdir,
        cloner=cloner or git_clone,
        runner=runner or prescreen_shell,
        per_repo_timeout=args.per_repo_timeout,
    )
    text = render_markdown(rows)
    sys.stdout.write(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    from app.console import ensure_utf8_stdio

    ensure_utf8_stdio()
    raise SystemExit(main())
