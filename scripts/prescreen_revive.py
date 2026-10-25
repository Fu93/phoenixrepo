#!/usr/bin/env python3
"""Cheap gate-2 pre-screen. No model calls.

From a scores file, take the NEEDS_REVIEW and NEEDS_PRIOR_ART repos, clone each one
shallowly, and report language, whether a test command exists, the install command,
a time-limited baseline, how many direct dependencies are outdated, and a revivability
score. One repo failing does not stop the rest. Nothing is installed.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.revive_loop import CmdResult, detect_commands, shell_runner  # noqa: E402

REVIEW_VERDICTS = frozenset({"NEEDS_REVIEW", "NEEDS_PRIOR_ART"})
DEFAULT_INPUTS = (
    ROOT / "logs" / "curated30d_scores.json",
    ROOT / "data" / "discovery" / "raw" / "snapshot.json",
)


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


def revivability_score(row: dict[str, Any]) -> int:
    """Higher means a cheaper revival to try first. A clone or test-detection failure scores 0."""
    if row.get("error") or not row.get("has_tests"):
        return 0
    score = 3
    code = row.get("baseline_code")
    if code == 0:
        score += 2
    elif code not in (None, 124):
        score += 1
    outdated = row.get("outdated")
    if isinstance(outdated, int) and 1 <= outdated <= 20:
        score += 1
    if row.get("install"):
        score += 1
    return score


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


def _outdated_command(repo: Path) -> str | None:
    if (repo / "package.json").is_file():
        return "npm outdated --json"
    if any((repo / name).is_file() for name in ("requirements.txt", "pyproject.toml", "setup.py")):
        return "python -m pip list --outdated --format json"
    return None


def screen_repo(
    row: dict[str, Any],
    dest: Path,
    *,
    cloner: Callable[[str, Path, float], None],
    runner: Callable[[str, Path, int], CmdResult],
    per_repo_timeout: float = 180,
    now: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """One repo. Clone, look, run the test command within the remaining budget, count outdated deps."""
    repo_id = _repo_id(row)
    fallback_language = str(row.get("language") or row.get("primary_language") or "")
    record: dict[str, Any] = {
        "repo": repo_id,
        "language": fallback_language or "unknown",
        "has_tests": False,
        "install": "",
        "baseline_code": None,
        "baseline_seconds": None,
        "outdated": None,
        "score": 0,
        "error": "",
        "verdict": str(row.get("verdict") or ""),
    }
    started = now()

    def remaining() -> float:
        return float(per_repo_timeout) - (now() - started)

    if remaining() <= 0:
        record["error"] = "TIMEOUT"
        return record
    try:
        cloner(repo_id, dest, max(1.0, remaining()))
    except Exception as exc:  # noqa: BLE001 — one clone failure must not stop the table
        record["error"] = f"{type(exc).__name__}: {exc}"[:240]
        record["score"] = 0
        return record

    record["language"] = detect_language(dest, fallback_language)
    install, test = detect_commands(dest)
    record["install"] = install or ""
    record["has_tests"] = bool(test)
    if test and remaining() > 0:
        test_budget = remaining()
        if _outdated_command(dest) and remaining() > 25:
            test_budget = max(1.0, remaining() - 20)
        try:
            result = runner(test, dest, max(1, int(test_budget)))
            record["baseline_code"] = int(result.code)
            record["baseline_seconds"] = round(float(getattr(result, "seconds", 0) or 0), 3)
        except Exception as exc:  # noqa: BLE001
            record["error"] = f"baseline {type(exc).__name__}: {exc}"[:240]
    elif test:
        record["error"] = "TIMEOUT"
        record["baseline_code"] = 124

    if remaining() > 0:
        command = _outdated_command(dest)
        if command:
            try:
                listed = runner(command, dest, max(1, min(20, int(remaining()))))
                record["outdated"] = count_outdated(str(getattr(listed, "output", "") or ""))
            except Exception:  # noqa: BLE001 — an unavailable count is not a crash
                record["outdated"] = None
    record["score"] = revivability_score(record)
    return record


def screen_repos(
    repos: list[dict[str, Any]],
    workdir: Path,
    *,
    cloner: Callable[[str, Path, float], None] = git_clone,
    runner: Callable[[str, Path, int], CmdResult] = shell_runner,
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
            rows.append(
                {
                    "repo": repo_id,
                    "language": str(row.get("language") or "unknown"),
                    "has_tests": False,
                    "install": "",
                    "baseline_code": None,
                    "baseline_seconds": None,
                    "outdated": None,
                    "score": 0,
                    "error": f"{type(exc).__name__}: {exc}"[:240],
                    "verdict": str(row.get("verdict") or ""),
                }
            )
    return rows


def render_markdown(rows: list[dict[str, Any]]) -> str:
    ranked = sorted(rows, key=lambda row: (-int(row.get("score") or 0), str(row.get("repo") or "")))
    lines = [
        "| Rank | Repository | Language | Tests | Install | Baseline | Seconds | Outdated | Score | Error |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
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
                    _cell(row.get("install") or "—"),
                    _cell(row.get("baseline_code") if row.get("baseline_code") is not None else "—"),
                    _cell(row.get("baseline_seconds") if row.get("baseline_seconds") is not None else "—"),
                    _cell(row.get("outdated") if row.get("outdated") is not None else "—"),
                    str(int(row.get("score") or 0)),
                    _cell(row.get("error") or ""),
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
        runner=runner or shell_runner,
        per_repo_timeout=args.per_repo_timeout,
    )
    text = render_markdown(rows)
    sys.stdout.write(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
