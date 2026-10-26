"""Independent verification of a revival.

The verifier does not trust the builder's report. It materialises a fresh copy of the
original tree and of the patched tree, re-runs the test command, compares counts and
exit codes, and rejects a diff that deletes, skips, or loosens tests.
"""
from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable

from app.quality_gate import MAX_DIFF_FILES, MAX_DIFF_LINES
from app.revive_loop import CmdResult, is_protected, snapshot_worktree

Runner = Callable[[str, Path, int], CmdResult]
COUNT_RE = re.compile(r"(\d+)\s+(passed|failed|skipped|errors?|error)", re.IGNORECASE)
TEST_DEF_RE = re.compile(r"^\s*(?:def\s+(test_\w+)|it(?:\.(?:only|skip|todo))?\(\s*['\"]([^'\"]+)|test(?:\.(?:only|skip))?\(\s*['\"]([^'\"]+))", re.MULTILINE)
SKIP_RE = re.compile(r"pytest\.skip|@pytest\.mark\.skip|unittest\.skip|it\.skip|\bxit\(|\.skip\s*=\s*true|test\.skip", re.IGNORECASE)
ASSERT_RE = re.compile(r"^\s*assert\b.+", re.MULTILINE)


def _check(check_id: str, status: str, detail: str) -> dict[str, str]:
    if status not in {"PASS", "FAIL", "UNVERIFIED"}:
        status = "UNVERIFIED"
    return {"id": check_id, "status": status, "detail": detail[:500]}


def parse_test_counts(output: str) -> dict[str, int] | None:
    """Pull passed/failed/skipped/errors from pytest or a similar summary. None when the output has no counts."""
    found: dict[str, int] = {}
    for match in COUNT_RE.finditer(output or ""):
        number = int(match.group(1))
        label = match.group(2).lower()
        key = {"passed": "passed", "failed": "failed", "skipped": "skipped", "error": "errors", "errors": "errors"}[label]
        found[key] = found.get(key, 0) + number
    if not found:
        return None
    for key in ("passed", "failed", "skipped", "errors"):
        found.setdefault(key, 0)
    return found


def _materialize(snap: dict[str, bytes], dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for rel, data in snap.items():
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _run_copy(runner: Runner, command: str, snap: dict[str, bytes], timeout: int) -> CmdResult | None:
    if not command:
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="phoenix-verify-") as tmp:
            root = Path(tmp) / "tree"
            _materialize(snap, root)
            return runner(command, root, timeout)
    except Exception as exc:  # noqa: BLE001 — a verifier crash is UNVERIFIED, not a pass
        return CmdResult(command, 1, f"verifier could not re-run: {type(exc).__name__}: {exc}")


def _test_names(text: str) -> set[str]:
    names: set[str] = set()
    for match in TEST_DEF_RE.finditer(text or ""):
        names.add(next(group for group in match.groups() if group))
    return names


def _assert_lines(text: str) -> list[str]:
    return [line.strip() for line in ASSERT_RE.findall(text or "")]


def weakened_test_diff(original: dict[str, bytes], patched: dict[str, bytes]) -> list[str]:
    """Reasons a test was deleted, skipped, or had an assertion loosened. Empty means no weakening."""
    reasons: list[str] = []
    original_tests = {rel for rel in original if is_protected(rel)}
    patched_tests = {rel for rel in patched if is_protected(rel)}
    for rel in sorted(original_tests - patched_tests):
        reasons.append(f"test file removed: {rel}")
    for rel in sorted(original_tests & patched_tests):
        before = original[rel].decode("utf-8", "replace")
        after = patched[rel].decode("utf-8", "replace")
        if before == after:
            continue
        missing = _test_names(before) - _test_names(after)
        for name in sorted(missing):
            reasons.append(f"test removed from {rel}: {name}")
        if len(SKIP_RE.findall(after)) > len(SKIP_RE.findall(before)):
            reasons.append(f"test skip added in {rel}")
        before_asserts = _assert_lines(before)
        after_asserts = _assert_lines(after)
        if before_asserts and len(after_asserts) < len(before_asserts):
            reasons.append(f"assertion removed from {rel}")
        if any(line == "assert True" or line.startswith("assert True ") for line in after_asserts) and any(
            line not in after_asserts for line in before_asserts
        ):
            reasons.append(f"assertion loosened in {rel}")
    return reasons


def _diff_stat(original: dict[str, bytes], patched: dict[str, bytes]) -> tuple[list[str], int]:
    files: list[str] = []
    lines = 0
    for rel in sorted(set(original) | set(patched)):
        if original.get(rel) == patched.get(rel):
            continue
        files.append(rel)
        before = (original.get(rel) or b"").decode("utf-8", "replace").splitlines()
        after = (patched.get(rel) or b"").decode("utf-8", "replace").splitlines()
        lines += abs(len(after) - len(before)) or 1
    return files, lines


def _acceptance_ok(result: CmdResult, expected: str) -> bool:
    text = expected or ""
    if re.search(r"exit code 0|exits 0", text, re.IGNORECASE):
        return result.ok
    if text and text in (result.output or ""):
        return result.ok
    return result.ok


def _manifest_has(patched: dict[str, bytes], package: str, version: str) -> bool:
    blob = "\n".join(data.decode("utf-8", "replace") for rel, data in patched.items() if rel.endswith((".json", ".txt", ".toml", ".lock")))
    if package not in blob:
        return False
    return not version or version in blob


def verify_revival(
    original: dict[str, bytes],
    patched: dict[str, bytes],
    runner: Runner,
    *,
    test_cmd: str = "",
    build_cmd: str = "",
    prd: dict[str, Any] | None = None,
    items: list[dict[str, Any]] | None = None,
    baseline_code: int | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    """Re-run checks on fresh copies. Status is PASS, FAIL, or UNVERIFIED. Never inferred from the builder."""
    checks: list[dict[str, str]] = []
    prd = prd or {}
    items = items or []
    kept = [item for item in items if isinstance(item, dict) and item.get("status") in {"KEPT", "applied"}]

    baseline_run = _run_copy(runner, test_cmd, original, timeout)
    final_run = _run_copy(runner, test_cmd, patched, timeout)
    if baseline_run is None:
        checks.append(_check("baseline_tests", "UNVERIFIED", "no test command"))
    else:
        checks.append(_check("baseline_tests", "PASS" if baseline_run.ok else "FAIL", f"exit {baseline_run.code}"))
    if final_run is None:
        checks.append(_check("final_tests", "UNVERIFIED", "no test command"))
    else:
        checks.append(_check("final_tests", "PASS" if final_run.ok else "FAIL", f"exit {final_run.code}"))

    base_counts = parse_test_counts(baseline_run.output) if baseline_run else None
    final_counts = parse_test_counts(final_run.output) if final_run else None
    if base_counts is None or final_counts is None:
        checks.append(_check("test_counts", "UNVERIFIED", "the test output did not report passed/failed/skipped/errors"))
        counts_fail = False
    else:
        worse = final_counts["failed"] + final_counts["errors"] > base_counts["failed"] + base_counts["errors"]
        fewer_passed = final_counts["passed"] < base_counts["passed"] and not (
            final_counts["failed"] < base_counts["failed"]
        )
        detail = f"baseline {base_counts} -> final {final_counts}"
        counts_fail = worse or fewer_passed
        checks.append(_check("test_counts", "FAIL" if counts_fail else "PASS", detail))

    build_worse = False
    if not build_cmd:
        checks.append(_check("build", "UNVERIFIED", "no build command was detected"))
    else:
        base_build = _run_copy(runner, build_cmd, original, timeout)
        final_build = _run_copy(runner, build_cmd, patched, timeout)
        if base_build is None or final_build is None:
            checks.append(_check("build", "UNVERIFIED", "build command did not run"))
        elif final_build.ok:
            checks.append(_check("build", "PASS", f"baseline exit {base_build.code}, final exit {final_build.code}"))
        elif base_build.ok:
            build_worse = True
            checks.append(_check("build", "FAIL", f"baseline exit {base_build.code}, final exit {final_build.code}"))
        else:
            checks.append(_check("build", "UNVERIFIED", f"baseline exit {base_build.code}, final exit {final_build.code}"))

    weakened = weakened_test_diff(original, patched)
    checks.append(_check("weakened_tests", "FAIL" if weakened else "PASS", "; ".join(weakened) or "no test was deleted, skipped, or loosened"))

    files, lines = _diff_stat(original, patched)
    protected = [rel for rel in files if is_protected(rel)]
    too_wide = len(files) > MAX_DIFF_FILES or lines > MAX_DIFF_LINES or bool(protected)
    detail = f"{len(files)} file(s), {lines} lines"
    if protected:
        detail += f"; protected paths changed: {', '.join(protected)}"
    checks.append(_check("diff_scope", "FAIL" if too_wide else "PASS", detail))

    kept_pass = False
    for item in kept:
        command = str(item.get("acceptance_command") or (item.get("acceptance") or {}).get("command") or test_cmd)
        expected = str(item.get("expected") or (item.get("acceptance") or {}).get("expected") or "exit code 0")
        item_id = str(item.get("id") or "item")
        run = _run_copy(runner, command, patched, timeout)
        if run is None:
            checks.append(_check(f"acceptance:{item_id}", "UNVERIFIED", "no acceptance command"))
            continue
        ok = _acceptance_ok(run, expected)
        kept_pass = kept_pass or ok
        checks.append(_check(f"acceptance:{item_id}", "PASS" if ok else "FAIL", f"exit {run.code}; expected {expected}"))

    for item in prd.get("work_items") or []:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "work")
        if any(row["id"] == f"acceptance:{item_id}" for row in checks):
            continue
        command = str(item.get("acceptance_command") or "")
        expected = str(item.get("expected") or "")
        if not command:
            checks.append(_check(f"acceptance:{item_id}", "FAIL", "no acceptance command"))
            continue
        run = _run_copy(runner, command, patched, timeout)
        if run is None:
            checks.append(_check(f"acceptance:{item_id}", "UNVERIFIED", "command was not run"))
            continue
        ok = _acceptance_ok(run, expected)
        checks.append(_check(f"acceptance:{item_id}", "PASS" if ok else "FAIL", f"exit {run.code}; expected {expected}"))

    for item in kept:
        if str(item.get("kind") or "") != "upgrade" and not item.get("package"):
            continue
        package = str(item.get("package") or "")
        version = str(item.get("version") or item.get("to_version") or "")
        if not package:
            continue
        present = _manifest_has(patched, package, version)
        checks.append(_check(f"upgrade:{package}", "PASS" if present else "FAIL", f"{package}@{version or '?'} in the manifest" if present else f"{package}@{version or '?'} is not in the manifest or lockfile"))

    baseline_broken = baseline_run is not None and not baseline_run.ok
    if baseline_code is not None and baseline_run is None:
        baseline_broken = baseline_code != 0
    outdated = any(str(item.get("kind") or "") == "upgrade" or item.get("package") for item in kept)
    baseline_problem = baseline_broken or outdated
    checks.append(_check("baseline_problem", "PASS" if baseline_problem else "FAIL", "baseline failed or a dependency upgrade was kept" if baseline_problem else "baseline already passed and no dependency upgrade was kept"))

    final_worse = bool(final_run is not None and baseline_run is not None and baseline_run.ok and not final_run.ok) or counts_fail or build_worse
    blocked: list[str] = []
    if weakened:
        blocked.append("tests were weakened")
    if final_worse:
        blocked.append("final checks are worse than the baseline")
    if not kept_pass:
        blocked.append("no kept item has a passing acceptance check")
    if not baseline_problem:
        blocked.append("baseline was not broken or outdated")
    if too_wide:
        blocked.append("diff scope failed")
    return {
        "resurrection_allowed": not blocked,
        "blocked_reason": "; ".join(blocked),
        "baseline_broken_or_outdated": baseline_problem,
        "kept_acceptance_passed": kept_pass,
        "files_changed": files,
        "lines_changed": lines,
        "checks": checks,
    }
