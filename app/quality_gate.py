"""Deterministic quality checks for a revival.

Every check returns one of pass, fail, skipped, or unavailable. skipped and unavailable are
never treated as a pass: a revival can only claim a check that actually ran and exited cleanly.
The runner is injectable so tests never need a real toolchain.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any, Callable

CHECK_IDS = ("tests", "lint", "types", "build", "audit", "smoke", "diff_scope")
MAX_DIFF_FILES = 8
MAX_DIFF_LINES = 300
DETAIL_LIMIT = 500
# A missing optional platform binary is an environment gap, not a failed build.
PLATFORM_BINARY_RE = re.compile(r"@(?:parcel/watcher|rollup/|swc/|esbuild/|parcel/)", re.IGNORECASE)

Runner = Callable[..., Any]
ToolLookup = Callable[[str], str | None]


def counts_as_pass(check: dict[str, Any] | None) -> bool:
    """Only an explicit pass counts. skipped and unavailable are not passes."""
    return bool(check) and check.get("status") == "pass"


def passing_ids(checks: list[dict[str, Any]]) -> list[str]:
    return [str(c["id"]) for c in checks if counts_as_pass(c)]


def tail(text: str, limit: int = DETAIL_LIMIT) -> str:
    text = text or ""
    if len(text) <= limit:
        return text.strip()
    return ("...[truncated]...\n" + text[-limit:]).strip()


def _check(check_id: str, status: str, detail: str, **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {"id": check_id, "status": status, "detail_tail": tail(detail)}
    row.update(extra)
    return row


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _json_value(text: str) -> Any:
    start_obj = text.find("{")
    start_arr = text.find("[")
    starts = [i for i in (start_obj, start_arr) if i >= 0]
    if not starts:
        return None
    start = min(starts)
    end_obj = text.rfind("}")
    end_arr = text.rfind("]")
    end = max(end_obj, end_arr)
    if end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except ValueError:
        return None


def _unavailable_output(output: str, code: int) -> bool:
    low = (output or "").lower()
    return code == 127 or "command not found" in low or "not recognized" in low or "no module named" in low or "not found" in low and "cannot find module" in low


def parse_npm_audit(output: str, code: int) -> dict[str, Any]:
    """Parse `npm audit --json`. Non-JSON is unavailable, never a pass."""
    if _unavailable_output(output, code) and _json_value(output or "") is None:
        return _check("audit", "unavailable", output or f"npm audit exit {code}")
    data = _json_value(output or "")
    if not isinstance(data, dict):
        return _check("audit", "unavailable", output or "npm audit did not return JSON")
    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    vulns = meta.get("vulnerabilities") if isinstance(meta.get("vulnerabilities"), dict) else None
    high = critical = moderate = 0
    if isinstance(vulns, dict) and ("high" in vulns or "critical" in vulns):
        high = int(vulns.get("high") or 0)
        critical = int(vulns.get("critical") or 0)
        moderate = int(vulns.get("moderate") or 0)
    elif isinstance(data.get("vulnerabilities"), dict):
        for item in data["vulnerabilities"].values():
            if not isinstance(item, dict):
                continue
            severity = str(item.get("severity") or "").lower()
            if severity == "high":
                high += 1
            elif severity == "critical":
                critical += 1
            elif severity == "moderate":
                moderate += 1
    elif "vulnerabilities" not in data and "metadata" not in data:
        return _check("audit", "unavailable", "npm audit JSON has no vulnerability metadata")
    status = "fail" if high + critical > 0 else "pass"
    detail = f"npm audit high={high} critical={critical} moderate={moderate} (exit {code})"
    return _check("audit", status, detail, high=high, critical=critical)


def parse_pip_audit(output: str, code: int) -> dict[str, Any]:
    """Parse `pip-audit -f json`. Findings without a severity are counted as high, not ignored."""
    if _unavailable_output(output, code) and _json_value(output or "") is None:
        return _check("audit", "unavailable", output or f"pip-audit exit {code}")
    data = _json_value(output or "")
    deps: list[Any] | None = None
    if isinstance(data, dict) and isinstance(data.get("dependencies"), list):
        deps = data["dependencies"]
    elif isinstance(data, list):
        deps = data
    if deps is None:
        return _check("audit", "unavailable", output or "pip-audit did not return JSON")
    high = critical = 0
    for dep in deps:
        if not isinstance(dep, dict):
            continue
        vulns = dep.get("vulns") or []
        if not isinstance(vulns, list):
            continue
        for vuln in vulns:
            if not isinstance(vuln, dict):
                high += 1
                continue
            # pip-audit often omits severity. An unrated finding is still a finding.
            severity = str(vuln.get("severity") or "").lower()
            if severity == "critical":
                critical += 1
            elif severity in {"moderate", "medium", "low", "info"}:
                continue
            else:
                high += 1
    status = "fail" if high + critical > 0 else "pass"
    detail = f"pip-audit high={high} critical={critical} (unrated findings count as high; exit {code})"
    return _check("audit", status, detail, high=high, critical=critical)


def _merge_status(statuses: list[str]) -> str:
    if not statuses:
        return "skipped"
    if any(s == "fail" for s in statuses):
        return "fail"
    if any(s == "unavailable" for s in statuses):
        return "unavailable"
    if all(s == "skipped" for s in statuses):
        return "skipped"
    if all(s == "pass" for s in statuses):
        return "pass"
    return "unavailable"


def _cfg_has(repo: Path, section: str) -> bool:
    needle = f"[{section}]"
    for name in ("setup.cfg", "tox.ini"):
        if needle in _read_text(repo / name):
            return True
    return False


def detect_lint_command(repo: Path) -> str | None:
    pkg = _read_json(repo / "package.json")
    scripts = pkg.get("scripts") if isinstance(pkg.get("scripts"), dict) else {}
    lint_script = scripts.get("lint") if isinstance(scripts, dict) else None
    eslint_names = (
        ".eslintrc",
        ".eslintrc.js",
        ".eslintrc.cjs",
        ".eslintrc.json",
        ".eslintrc.yml",
        ".eslintrc.yaml",
        "eslint.config.js",
        "eslint.config.mjs",
        "eslint.config.cjs",
    )
    has_eslint = any((repo / name).exists() for name in eslint_names) or "eslintConfig" in pkg
    if isinstance(lint_script, str) and lint_script.strip():
        low = lint_script.lower()
        if has_eslint or any(tool in low for tool in ("eslint", "ruff", "flake8")):
            return "npm run lint --silent"
    if has_eslint:
        return "npx --no-install eslint ."
    pyproject = _read_text(repo / "pyproject.toml")
    if (repo / "ruff.toml").is_file() or (repo / ".ruff.toml").is_file() or "[tool.ruff]" in pyproject:
        return "python -m ruff check ."
    if (repo / ".flake8").is_file() or "[tool.flake8]" in pyproject or _cfg_has(repo, "flake8"):
        return "python -m flake8 ."
    return None


def detect_type_commands(repo: Path) -> list[str]:
    cmds: list[str] = []
    if (repo / "tsconfig.json").is_file():
        cmds.append("npx --no-install tsc --noEmit")
    pyproject = _read_text(repo / "pyproject.toml")
    if (repo / "mypy.ini").is_file() or (repo / ".mypy.ini").is_file() or "[tool.mypy]" in pyproject or _cfg_has(repo, "mypy"):
        cmds.append("python -m mypy .")
    return cmds


def detect_build_commands(repo: Path) -> list[str]:
    """Run the repo's own build, including `next build` and `vite build` scripts."""
    cmds: list[str] = []
    pkg = _read_json(repo / "package.json")
    scripts = pkg.get("scripts") if isinstance(pkg.get("scripts"), dict) else {}
    build = scripts.get("build") if isinstance(scripts, dict) else None
    if isinstance(build, str) and build.strip():
        cmds.append("npm run build --silent")
    if "[build-system]" in _read_text(repo / "pyproject.toml"):
        cmds.append("python -m build")
    if (repo / "go.mod").is_file():
        cmds.append("go build ./...")
    return cmds


def _build_check(runner: Runner, repo: Path, cmd: str, timeout: int) -> dict[str, Any]:
    row = _run_exit_check("build", runner, repo, cmd, timeout)
    if row["status"] == "fail" and PLATFORM_BINARY_RE.search(str(row.get("detail_tail") or "")):
        row = dict(row)
        row["status"] = "unavailable"
    return row


def detect_smoke(repo: Path) -> tuple[str, str] | None:
    """Return (command, kind) where kind is 'server' or 'cli'. None means undetectable."""
    pkg = _read_json(repo / "package.json")
    scripts = pkg.get("scripts") if isinstance(pkg.get("scripts"), dict) else {}
    if isinstance(scripts, dict):
        start = scripts.get("start")
        if isinstance(start, str) and start.strip():
            return "npm start", "server"
        serve = scripts.get("serve")
        if isinstance(serve, str) and serve.strip():
            return "npm run serve", "server"
    if (repo / "go.mod").is_file() and (repo / "main.go").is_file():
        return "go run . --help", "cli"
    if (repo / "__main__.py").is_file():
        return "python __main__.py --help", "cli"
    for main in sorted(repo.glob("*/__main__.py")):
        mod = main.parent.name
        if mod.isidentifier() and mod not in {"tests", "test", "docs"}:
            return f"python -m {mod} --help", "cli"
    return None


def _run_exit_check(check_id: str, runner: Runner, repo: Path, cmd: str, timeout: int) -> dict[str, Any]:
    try:
        result = runner(cmd, repo, timeout)
    except Exception as exc:  # noqa: BLE001 — a crashed runner is not a pass
        return _check(check_id, "unavailable", f"{cmd} runner error: {type(exc).__name__}")
    output = str(getattr(result, "output", "") or "")
    code = int(getattr(result, "code", 1))
    if _unavailable_output(output, code):
        return _check(check_id, "unavailable", f"{cmd}\n{output}")
    if code == 0:
        return _check(check_id, "pass", f"{cmd}\n{output}" if output else f"{cmd} exit 0")
    return _check(check_id, "fail", f"{cmd}\n{output}" if output else f"{cmd} exit {code}")


def _merge_runs(check_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    status = _merge_status([str(row["status"]) for row in rows])
    detail = " | ".join(row.get("detail_tail") or row["status"] for row in rows)
    extra: dict[str, Any] = {}
    if any("high" in row for row in rows):
        extra["high"] = sum(int(row.get("high") or 0) for row in rows)
        extra["critical"] = sum(int(row.get("critical") or 0) for row in rows)
    return _check(check_id, status, detail, **extra)


def audit_check(repo: Path, runner: Runner, timeout: int, tools_present: ToolLookup | None = None) -> dict[str, Any]:
    present = tools_present or shutil.which
    rows: list[dict[str, Any]] = []
    applicable = False
    if (repo / "package.json").is_file():
        applicable = True
        try:
            result = runner("npm audit --omit=dev --json", repo, timeout)
            output = str(getattr(result, "output", "") or "")
            code = int(getattr(result, "code", 1))
        except Exception as exc:  # noqa: BLE001
            rows.append(_check("audit", "unavailable", f"npm audit runner error: {type(exc).__name__}"))
        else:
            rows.append(parse_npm_audit(output, code))
    if any((repo / name).is_file() for name in ("requirements.txt", "pyproject.toml", "setup.py")):
        applicable = True
        if not present("pip-audit"):
            rows.append(_check("audit", "unavailable", "pip-audit is not installed"))
        else:
            try:
                result = runner("pip-audit -f json", repo, timeout)
                output = str(getattr(result, "output", "") or "")
                code = int(getattr(result, "code", 1))
            except Exception as exc:  # noqa: BLE001
                rows.append(_check("audit", "unavailable", f"pip-audit runner error: {type(exc).__name__}"))
            else:
                rows.append(parse_pip_audit(output, code))
    if (repo / "go.mod").is_file():
        applicable = True
        rows.append(_run_exit_check("audit", runner, repo, "go vet ./...", timeout))
    if not applicable:
        return _check("audit", "skipped", "no dependency manifest to audit")
    if len(rows) == 1:
        row = dict(rows[0])
        row["id"] = "audit"
        return row
    return _merge_runs("audit", rows)


def allowed_paths(prd: dict[str, Any] | None) -> set[str]:
    allowed: set[str] = set()
    if not isinstance(prd, dict):
        return allowed
    for entry in prd.get("scope") or []:
        if isinstance(entry, str) and entry.strip():
            allowed.add(entry.replace("\\", "/").strip().lstrip("./"))
    for key in ("refactor_items", "upgrade_items", "docs_items"):
        for item in prd.get(key) or []:
            if not isinstance(item, dict):
                continue
            for field in ("files", "file", "path"):
                value = item.get(field)
                if isinstance(value, str) and value.strip():
                    allowed.add(value.replace("\\", "/").strip().lstrip("./"))
                elif isinstance(value, list):
                    for entry in value:
                        if isinstance(entry, str) and entry.strip():
                            allowed.add(entry.replace("\\", "/").strip().lstrip("./"))
    if prd.get("upgrade_items"):
        allowed.update({"package.json", "requirements.txt", "pyproject.toml", "go.mod", "setup.py", "setup.cfg"})
    return allowed


def _in_scope(path: str, allowed: set[str]) -> bool:
    norm = path.replace("\\", "/").lstrip("./")
    if norm in allowed:
        return True
    for item in allowed:
        if not item or " " in item:
            continue
        prefix = item.rstrip("/")
        if prefix and (norm == prefix or norm.startswith(prefix + "/")):
            return True
    return False


def diff_scope_check(
    changed_files: list[str],
    lines_changed: int,
    prd: dict[str, Any] | None,
    is_protected: Callable[[str], bool],
    max_files: int = MAX_DIFF_FILES,
    max_lines: int = MAX_DIFF_LINES,
) -> dict[str, Any]:
    changed = [path.replace("\\", "/") for path in changed_files]
    if not changed and lines_changed <= 0:
        return _check("diff_scope", "pass", "no files changed", files=0, lines=0)
    protected = [path for path in changed if is_protected(path)]
    if protected:
        return _check("diff_scope", "fail", "edits touch tests or other protected paths: " + ", ".join(protected), files=len(changed), lines=lines_changed)
    if len(changed) > max_files or lines_changed > max_lines:
        return _check(
            "diff_scope",
            "fail",
            f"oversized diff: {len(changed)} files, {lines_changed} lines (limits {max_files} files, {max_lines} lines)",
            files=len(changed),
            lines=lines_changed,
        )
    allowed = allowed_paths(prd)
    outside = [path for path in changed if not _in_scope(path, allowed)]
    if outside:
        return _check("diff_scope", "fail", "files outside the PRD scope: " + ", ".join(outside), files=len(changed), lines=lines_changed)
    return _check("diff_scope", "pass", f"{len(changed)} file(s), {lines_changed} changed lines, inside the PRD scope", files=len(changed), lines=lines_changed)


def tests_check(test_cmd: str | None, runner: Runner, repo: Path, timeout: int, test_result: Any | None = None) -> dict[str, Any]:
    if not test_cmd and test_result is None:
        return _check("tests", "unavailable", "no test command was detected")
    result = test_result
    if result is None:
        try:
            result = runner(test_cmd or "", repo, timeout)
        except Exception as exc:  # noqa: BLE001
            return _check("tests", "unavailable", f"test runner error: {type(exc).__name__}")
    output = str(getattr(result, "output", "") or "")
    code = int(getattr(result, "code", 1))
    cmd = str(getattr(result, "cmd", "") or test_cmd or "")
    if code == 0:
        return _check("tests", "pass", f"{cmd}\n{output}" if output else f"{cmd} exit 0")
    return _check("tests", "fail", f"{cmd}\n{output}" if output else f"{cmd} exit {code}")


def smoke_check(repo: Path, runner: Runner, timeout: int) -> dict[str, Any]:
    detected = detect_smoke(repo)
    if detected is None:
        return _check("smoke", "skipped", "no start command or --help/--version entry point detected")
    cmd, kind = detected
    try:
        result = runner(cmd, repo, timeout)
    except Exception as exc:  # noqa: BLE001
        return _check("smoke", "unavailable", f"{cmd} runner error: {type(exc).__name__}")
    output = str(getattr(result, "output", "") or "")
    code = int(getattr(result, "code", 1))
    if _unavailable_output(output, code):
        return _check("smoke", "unavailable", f"{cmd}\n{output}")
    if code == 0:
        return _check("smoke", "pass", f"{cmd} exit 0\n{output}")
    if code == 124 and kind == "server":
        return _check("smoke", "pass", f"{cmd} stayed alive until the {timeout}s timeout\n{output}")
    return _check("smoke", "fail", f"{cmd} exit {code}\n{output}")


def run_quality_gate(
    repo: Path,
    runner: Runner,
    test_cmd: str | None,
    *,
    test_timeout: int = 300,
    check_timeout: int = 180,
    smoke_timeout: int = 10,
    changed_files: list[str] | None = None,
    lines_changed: int = 0,
    prd: dict[str, Any] | None = None,
    test_result: Any | None = None,
    tools_present: ToolLookup | None = None,
    is_protected: Callable[[str], bool] | None = None,
    max_files: int = MAX_DIFF_FILES,
    max_lines: int = MAX_DIFF_LINES,
) -> list[dict[str, Any]]:
    """Run every quality check. The list always contains one row per check id."""
    if is_protected is None:
        from app.revive_loop import is_protected as default_protected

        is_protected = default_protected
    checks: list[dict[str, Any]] = [tests_check(test_cmd, runner, repo, test_timeout, test_result)]
    lint_cmd = detect_lint_command(repo)
    if lint_cmd:
        checks.append(_run_exit_check("lint", runner, repo, lint_cmd, check_timeout))
    else:
        checks.append(_check("lint", "skipped", "no eslint, ruff, or flake8 configuration found"))
    type_cmds = detect_type_commands(repo)
    if type_cmds:
        checks.append(_merge_runs("types", [_run_exit_check("types", runner, repo, cmd, check_timeout) for cmd in type_cmds]))
    else:
        checks.append(_check("types", "skipped", "no tsc or mypy configuration found"))
    build_cmds = detect_build_commands(repo)
    if build_cmds:
        checks.append(_merge_runs("build", [_build_check(runner, repo, cmd, check_timeout) for cmd in build_cmds]))
    else:
        checks.append(_check("build", "skipped", "no npm build script, Python build-system, or go.mod found"))
    checks.append(audit_check(repo, runner, check_timeout, tools_present))
    checks.append(smoke_check(repo, runner, smoke_timeout))
    checks.append(diff_scope_check(changed_files or [], lines_changed, prd, is_protected, max_files=max_files, max_lines=max_lines))
    checks.append(deps_freshness_check(repo, runner, check_timeout))
    return checks


def deps_freshness_check(repo: Path, runner: Runner, timeout: int) -> dict[str, Any]:
    """How many dependencies are behind. Informational only: never pass or fail."""
    if (repo / "package.json").is_file():
        cmd = "npm outdated --json"
    elif any((repo / name).is_file() for name in ("requirements.txt", "pyproject.toml", "setup.py")):
        cmd = "python -m pip list --outdated --format json"
    else:
        return _check("deps_freshness", "info", "no npm or pip manifest to compare", outdated=None)
    try:
        result = runner(cmd, repo, min(timeout, 30))
    except Exception as exc:  # noqa: BLE001
        return _check("deps_freshness", "info", f"outdated package list was unavailable ({type(exc).__name__})", outdated=None)
    output = str(getattr(result, "output", "") or "")
    code = int(getattr(result, "code", 1))
    if not output.strip() and code == 0:
        return _check("deps_freshness", "info", "0 outdated package(s)", outdated=0)
    data = _json_value(output)
    if isinstance(data, dict):
        count = len(data)
    elif isinstance(data, list):
        count = len(data)
    else:
        return _check("deps_freshness", "info", "outdated package list was unavailable", outdated=None)
    return _check("deps_freshness", "info", f"{count} outdated package(s)", outdated=count)


def regressions(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> list[str]:
    """Checks that were passing and no longer are, plus audit counts that increased."""
    previous = {str(row["id"]): row for row in before}
    current = {str(row["id"]): row for row in after}
    found: list[str] = []
    for check_id, row in previous.items():
        if not counts_as_pass(row):
            continue
        later = current.get(check_id)
        if later is not None and later.get("status") == "info":
            continue
        if later is None or not counts_as_pass(later):
            found.append(check_id)
    before_audit = previous.get("audit") or {}
    after_audit = current.get("audit") or {}
    if "high" in before_audit or "critical" in before_audit:
        before_n = int(before_audit.get("high") or 0) + int(before_audit.get("critical") or 0)
        after_n = int(after_audit.get("high") or 0) + int(after_audit.get("critical") or 0)
        if after_n > before_n and "audit" not in found:
            found.append("audit")
    return found


def verified_criteria(prd: dict[str, Any] | None, checks: list[dict[str, Any]]) -> list[str]:
    """Acceptance criteria whose mapped check actually passed. skipped does not verify one."""
    if not isinstance(prd, dict):
        return []
    by_id = {str(row["id"]): row for row in checks}
    verified: list[str] = []
    for criterion in prd.get("acceptance_criteria") or []:
        if not isinstance(criterion, dict):
            continue
        check = by_id.get(str(criterion.get("check_id") or ""))
        if counts_as_pass(check):
            verified.append(str(criterion.get("id") or criterion.get("check_id")))
    return verified


def quality_report(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    files_changed: list[str],
    lines_changed: int,
    verified: list[str],
) -> dict[str, Any]:
    return {
        "before": before,
        "after": after,
        "files_changed": list(files_changed),
        "lines_changed": lines_changed,
        "regressed": regressions(before, after),
        "verified_criteria": list(verified),
    }


def _cell(value: Any) -> str:
    import html

    return html.escape("" if value is None else str(value))


def render_evidence_html(payload: dict[str, Any]) -> str:
    """One static page. No external assets, scripts, or stylesheets."""
    quality = payload.get("quality") or {}
    before = {str(row["id"]): row for row in quality.get("before") or [] if isinstance(row, dict) and row.get("id")}
    after = [row for row in (quality.get("after") or []) if isinstance(row, dict)]
    rows = []
    ordered = _ordered_checks(after)
    for row in ordered:
        check_id = str(row.get("id") or "")
        prev = before.get(check_id) or {}
        rows.append(
            "<tr>"
            f"<td>{_cell(check_id)}</td>"
            f"<td data-status=\"{_cell(prev.get('status') or 'n/a')}\">{_cell(prev.get('status') or 'n/a')}</td>"
            f"<td data-status=\"{_cell(row.get('status'))}\">{_cell(row.get('status'))}</td>"
            f"<td>{_cell(_audit_counts(prev))}</td>"
            f"<td>{_cell(_audit_counts(row))}</td>"
            f"<td>{_cell(row.get('detail_tail') or '')}</td>"
            "</tr>"
        )
    if not rows:
        rows.append("<tr><td colspan=\"6\">No quality checks were run.</td></tr>")
    done = [item for item in (payload.get("prd_items_done") or []) if isinstance(item, dict)]
    not_done = [item for item in (payload.get("prd_items_not_done") or []) if isinstance(item, dict) or item]
    reverted = [item for item in not_done if isinstance(item, dict) and (item.get("failed_check") or item.get("status") in {"reverted", "ROLLED_BACK"})]
    reverted_ids = {id(item) for item in reverted}
    other = [item for item in not_done if id(item) not in reverted_ids]
    done_html = "".join(_applied_row(item) for item in done) or "<tr><td colspan=\"4\">None.</td></tr>"
    reverted_html = "".join(_reverted_row(item) for item in reverted) or "<tr><td colspan=\"3\">None.</td></tr>"
    not_html = "".join(f"<li>{_cell(_item_line(item))}</li>" for item in other) or "<li>None.</li>"
    repairs = payload.get("env_repairs") or []
    repair_html = "".join(f"<li>{_cell(_repair_line(item))}</li>" for item in repairs) or "<li>None.</li>"
    files = ", ".join(quality.get("files_changed") or payload.get("files_changed") or []) or "none"
    lines = quality.get("lines_changed", 0)
    review = payload.get("review") or {}
    issues = review.get("issues") or []
    issue_html = "".join(f"<li>{_cell(issue.get('detail') if isinstance(issue, dict) else issue)}</li>" for issue in issues) or "<li>None.</li>"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Revival evidence {_cell(payload.get("run_id") or "")}</title>
<style>
body {{ font-family: Georgia, "Times New Roman", serif; margin: 2rem; color: #1c1917; background: #faf7f2; }}
h1 {{ font-size: 1.6rem; margin-bottom: 0.2rem; }}
table {{ border-collapse: collapse; width: 100%; background: #fff; }}
th, td {{ border: 1px solid #d6d3d1; padding: 0.4rem 0.5rem; vertical-align: top; text-align: left; font-size: 0.92rem; }}
th {{ background: #e7e5e4; }}
td[data-status="pass"] {{ background: #ecfdf3; }}
td[data-status="fail"] {{ background: #fef3f2; }}
td[data-status="skipped"], td[data-status="unavailable"] {{ background: #f5f5f4; }}
td[data-status="info"] {{ background: #eff6ff; }}
.note {{ color: #44403c; }}
.outcome {{ background: #fff; border: 2px solid #1c1917; padding: 0.8rem 1rem; margin: 0 0 1.2rem; }}
</style>
</head>
<body>
{_outcome_banner(payload)}
{_approval_block(payload)}
<h1>{_cell(payload.get("repo") or "repository")} — {_cell(payload.get("state") or "")}</h1>
<p>{_cell(payload.get("reason") or "")}</p>
<p class="note">Run {_cell(payload.get("run_id") or "")}. Skipped and unavailable checks are not passes. Files changed: {_cell(files)}. Lines changed: {_cell(lines)}. <a href="prd.md">prd.md</a></p>
{_verification_section(payload)}
<h2>Quality before and after</h2>
<table>
<thead><tr><th>Check</th><th>Before</th><th>After</th><th>Audit before</th><th>Audit after</th><th>Detail</th></tr></thead>
<tbody>
{''.join(rows)}
</tbody>
</table>
{_synthesized_section(payload)}
<h2>PRD items applied</h2>
<table>
<thead><tr><th>Item</th><th>Kind</th><th>Files</th><th>Diff stat</th></tr></thead>
<tbody>
{done_html}
</tbody>
</table>
<h2>Reverted</h2>
<table>
<thead><tr><th>Item</th><th>Failing check</th><th>Reason</th></tr></thead>
<tbody>
{reverted_html}
</tbody>
</table>
<h2>PRD items not done</h2>
<ul>{not_html}</ul>
<h2>Environment repairs</h2>
<ul>{repair_html}</ul>
<h2>Reviewer</h2>
<p>approve={_cell(review.get("approve"))}</p>
<ul>{issue_html}</ul>
</body>
</html>
"""


def _outcome_banner(payload: dict[str, Any]) -> str:
    """One honest status block: what changed, tests before and after, and what was not verified."""
    quality = payload.get("quality") or {}
    before = {str(row.get("id")): row for row in quality.get("before") or [] if isinstance(row, dict)}
    after = {str(row.get("id")): row for row in quality.get("after") or [] if isinstance(row, dict)}
    status = payload.get("terminal") or payload.get("state") or ""
    files = quality.get("files_changed") or payload.get("files_changed") or []
    if not isinstance(files, list):
        files = []
    changed = ", ".join(str(path) for path in files) or "nothing"
    lines = quality.get("lines_changed", 0)
    left = (before.get("tests") or {}).get("status") or "n/a"
    right = (after.get("tests") or {}).get("status") or "n/a"
    unverified = payload.get("unverified") or []
    missing = "; ".join(str(item) for item in unverified) if isinstance(unverified, list) else str(unverified)
    if not missing:
        missing = "none recorded"
    label = str(payload.get("label") or "")
    label_html = f"<p>Label: {_cell(label)}</p>" if label else ""
    empty = "<p>Nothing could be verified automatically.</p>" if status == "NO_TESTS_TO_VERIFY" else ""
    synth = payload.get("synthesized") if isinstance(payload.get("synthesized"), dict) else {}
    synth_note = ""
    if synth:
        synth_note = "<p>Repository tests: none collected. Checks marked synthesized were created by the pipeline and are not the repository's own coverage.</p>"
    return (
        "<section class=\"outcome\">"
        "<h2>Outcome</h2>"
        f"<p><strong>{_cell(status)}</strong></p>"
        f"{label_html}"
        f"{empty}"
        f"{synth_note}"
        f"<p>Changed: {_cell(changed)} ({_cell(lines)} lines).</p>"
        f"<p>Tests: {_cell(left)} -&gt; {_cell(right)}.</p>"
        f"<p>Not verified: {_cell(missing)}</p>"
        "</section>"
    )


def _synthesized_section(payload: dict[str, Any]) -> str:
    """Pipeline-written checks. Empty when the run did not synthesize tests, so the quality table stays first."""
    synth = payload.get("synthesized")
    if not isinstance(synth, dict) or not synth:
        return ""
    note = "These checks were synthesized by the pipeline. They are not the repository's own tests or coverage."
    rejected = [item for item in (synth.get("rejected") or []) if item]
    rejected_html = "<ul>" + "".join(f"<li>{_cell(item)}</li>" for item in rejected) + "</ul>" if rejected else ""
    cited = []
    for item in synth.get("tests") or []:
        if isinstance(item, dict) and item.get("readme_sentence"):
            cited.append(f"{item.get('name') or 'test'}: {item.get('readme_sentence')}")
    cited_html = "<ul>" + "".join(f"<li>{_cell(item)}</li>" for item in cited) + "</ul>" if cited else ""
    before = {str(row.get("id")): row for row in (synth.get("before") or []) if isinstance(row, dict) and row.get("id")}
    after_rows = [row for row in (synth.get("after") or []) if isinstance(row, dict) and row.get("id")]
    after = {str(row.get("id")): row for row in after_rows}
    ids: list[str] = []
    for row in list(before.values()) + after_rows:
        check_id = str(row.get("id") or "")
        if check_id and check_id not in ids:
            ids.append(check_id)
    body = []
    for check_id in ids:
        left = before.get(check_id) or {}
        right = after.get(check_id) or {}
        body.append(
            "<tr>"
            f"<td>{_cell(check_id)}</td>"
            "<td>synthesized</td>"
            f"<td data-status=\"{_cell(left.get('status') or 'n/a')}\">{_cell(left.get('status') or 'n/a')}</td>"
            f"<td data-status=\"{_cell(right.get('status') or 'n/a')}\">{_cell(right.get('status') or 'n/a')}</td>"
            f"<td>{_cell(right.get('detail') or left.get('detail') or '')}</td>"
            "</tr>"
        )
    table = ""
    if body:
        table = (
            "<table><thead><tr><th>Check</th><th>Origin</th><th>Before</th><th>After</th><th>Detail</th></tr></thead><tbody>"
            + "".join(body)
            + "</tbody></table>"
        )
    return (
        "<h2>Synthesized checks</h2>"
        f"<p>{_cell(note)}</p>"
        f"<p>Path: {_cell(synth.get('path') or '')}. {_cell(synth.get('note') or '')}</p>"
        f"{cited_html}{rejected_html}{table}"
    )


def _verification_section(payload: dict[str, Any]) -> str:
    verification = payload.get("verification") or {}
    checks = verification.get("checks") if isinstance(verification, dict) else None
    if not isinstance(checks, list) or not checks:
        return "<h2>Verification</h2><p>UNVERIFIED: verification was not run.</p>"
    rows = []
    for row in checks:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "UNVERIFIED")
        rows.append(
            "<tr>"
            f"<td>{_cell(row.get('id') or '')}</td>"
            f"<td data-status=\"{_cell(status.lower())}\">{_cell(status)}</td>"
            f"<td>{_cell(row.get('detail') or '')}</td>"
            "</tr>"
        )
    allowed = verification.get("resurrection_allowed")
    return (
        "<h2>Verification</h2>"
        f"<p>Independent of the builder. resurrection_allowed={_cell(allowed)}. {_cell(verification.get('blocked_reason') or '')}</p>"
        "<table><thead><tr><th>Check</th><th>Status</th><th>Detail</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _approval_block(payload: dict[str, Any]) -> str:
    approval = payload.get("human_approval") or {}
    if not isinstance(approval, dict) or not approval:
        return ""
    rows = (
        ("reviewer", approval.get("reviewer")),
        ("note", approval.get("note")),
        ("timestamp", approval.get("timestamp")),
        ("verdict", approval.get("verdict")),
        ("reason", approval.get("reason")),
    )
    items = "".join(f"<li>{_cell(name)}: {_cell(value)}</li>" for name, value in rows)
    return f"<h2>Human approval</h2><ul>{items}</ul>"


def _ordered_checks(after: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """tests, lint, types, build, and audit first. Other rows keep their original order."""
    if not after:
        return []
    present = {str(row.get("id") or ""): row for row in after}
    summary = list(CHECK_IDS[:5])
    ordered = [present.get(check_id) or {"id": check_id, "status": "n/a", "detail_tail": ""} for check_id in summary]
    seen = set(summary)
    for row in after:
        check_id = str(row.get("id") or "")
        if check_id in seen:
            continue
        ordered.append(row)
        seen.add(check_id)
    return ordered


def _applied_row(item: dict[str, Any]) -> str:
    files = item.get("files") if isinstance(item.get("files"), list) else []
    names = ", ".join(str(path) for path in files)
    stat = f"{len(files)} file(s), {int(item.get('lines') or 0)} lines"
    return (
        "<tr>"
        f"<td>{_cell(item.get('id') or '')}</td>"
        f"<td>{_cell(item.get('kind') or '')}</td>"
        f"<td>{_cell(names or 'none')}</td>"
        f"<td>{_cell(stat)}</td>"
        "</tr>"
    )


def _reverted_row(item: dict[str, Any]) -> str:
    return (
        "<tr>"
        f"<td>{_cell(item.get('id') or '')}</td>"
        f"<td>{_cell(item.get('failed_check') or '')}</td>"
        f"<td>{_cell(item.get('reason') or '')}</td>"
        "</tr>"
    )


def _audit_counts(row: dict[str, Any]) -> str:
    if "high" not in row and "critical" not in row:
        return "—"
    return f"high={int(row.get('high') or 0)} critical={int(row.get('critical') or 0)}"


def _item_line(item: Any) -> str:
    if not isinstance(item, dict):
        return str(item)
    reason = item.get("reason") or ""
    base = f"{item.get('kind') or 'item'} {item.get('id') or ''}".strip()
    return f"{base}: {reason}" if reason else base


def _repair_line(item: Any) -> str:
    if not isinstance(item, dict):
        return str(item)
    package = item.get("package") or "platform package"
    return f"{package} (install exit {item.get('install_exit')}, test exit {item.get('test_exit')})"
