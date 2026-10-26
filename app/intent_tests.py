"""Intent tests for a repository whose own suite collected nothing.

The builder may write checks that quote the README, call a real public function, and
avoid mocking that function. The file lives in ``phoenix_synthesized/`` under a name
pytest does not collect on its own. The verifier reports those checks as synthesized.
It never treats them as the repository's coverage.

RESURRECTED requires the smoke checks and the intent tests to pass after a fix, with
at least one failure or error before that fix. Anything else is PARTIAL or
NO_TESTS_TO_VERIFY. A tautology is rejected even when a runner would exit 0.
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

from app.quality_gate import detect_build_commands, detect_smoke
from app.revive_prd import README_EXCERPT_BYTES, clip_text, readme_text

SYNTH_DIR = "phoenix_synthesized"
SYNTH_FILE = "phoenix_synthesized/intent_checks.py"
MARKER = "SYNTHESIZED BY PHOENIXREPO"
HEADER = (
    "# SYNTHESIZED BY PHOENIXREPO\n"
    "# Created by the revival pipeline. Not the repository's own tests.\n"
    "# Do not report this file as the repository's coverage.\n"
    '"""Synthesized intent checks. Not the repository\'s own coverage."""\n'
)
NOT_COVERAGE = "These checks were synthesized by the pipeline. They are not the repository's own tests or coverage."
INTENT_SYSTEM = (
    "You write minimal intent tests for a repository that collected no tests. "
    "Each test quotes one README sentence, imports the repository module, and calls the real public function. "
    "Do not mock that function. Do not write assert True, assert 1 == 1, or an assertion that only checks a mock. "
    "Reply with one strict JSON object and nothing else."
)
MAX_TESTS = 4
MAX_SOURCE = 4_000
MIN_SENTENCE = 12
MAX_FIXES = 2

_TAUTOLOGY_HEAD = re.compile(
    r"^(?:True|False|None|not False|not None|\d+|True\s*==\s*True|False\s*==\s*False|None\s*is\s*None|\d+\s*==\s*\d+|\(\s*True\s*\))$"
)
_ASSERT_LINE = re.compile(r"^\s*assert\b|pytest\.raises\b")
_MOCK_LIB = re.compile(r"\b(MagicMock|NonCallableMock|Mock\(|unittest\.mock|mock\.patch|monkeypatch|mocker\.|jest\.mock)\b")
_DEF_TEST = re.compile(r"^def\s+(test_[A-Za-z0-9_]+)\s*\(", re.M)


def can_synthesize(repo: Path) -> bool:
    """Python modules plus a README sentence. Other languages stay NO_TESTS_TO_VERIFY."""
    return bool(readme_text(repo).strip()) and bool(python_modules(repo))


def python_modules(repo: Path) -> list[str]:
    names: list[str] = []
    for path in sorted(repo.glob("*.py")):
        if path.name.startswith("test_") or path.name.endswith("_test.py"):
            continue
        if path.stem.isidentifier() and path.stem not in {"conftest", "setup"}:
            names.append(path.stem)
    return names


def public_functions(repo: Path, module: str) -> list[str]:
    path = repo / f"{module}.py"
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    found = re.findall(r"^def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", text, re.M)
    return [name for name in found if not name.startswith("_")]


def intent_pytest_command() -> str:
    """Run only the synthesized file. The repository's own pytest command must not collect it."""
    from app.revive_loop import _shell_arg

    py = _shell_arg(sys.executable)
    return (
        f"{py} -m pytest -q -p no:cacheprovider --noconftest --rootdir . "
        "-o python_files=intent_checks.py phoenix_synthesized/intent_checks.py"
    )


def import_command(module: str) -> str:
    from app.revive_loop import _shell_arg

    if not module.isidentifier():
        raise ValueError(module)
    return f"{_shell_arg(sys.executable)} -c {_shell_arg('import ' + module)}"


def check_commands(repo: Path, entry_module: str, install_cmd: str | None) -> list[tuple[str, str]]:
    commands: list[tuple[str, str]] = []
    if install_cmd:
        commands.append(("install", install_cmd))
    commands.append(("import", import_command(entry_module)))
    for build in detect_build_commands(repo):
        commands.append(("build", build))
    smoke = detect_smoke(repo)
    if smoke:
        commands.append(("start", smoke[0]))
    commands.append(("intent", intent_pytest_command()))
    return commands


def _tautology(line: str) -> bool:
    stripped = re.sub(r"\s*#.*$", "", line).strip()
    if not stripped.startswith("assert "):
        return False
    expr = stripped[len("assert ") :].strip()
    head = expr.split(",")[0].strip()
    if _TAUTOLOGY_HEAD.fullmatch(head):
        return True
    return bool(re.fullmatch(r"(['\"]).*\1\s*==\s*\1.*\1", head))


def _chunks(source: str) -> list[str]:
    matches = list(_DEF_TEST.finditer(source))
    if not matches:
        return [source]
    preamble = source[: matches[0].start()]
    chunks: list[str] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        chunks.append(preamble + source[match.start() : end])
    return chunks


def _mocked_names(source: str) -> set[str]:
    names: set[str] = set()
    for line in source.splitlines():
        match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)", line)
        if match and _MOCK_LIB.search(match.group(2)):
            names.add(match.group(1))
    return names


def _cites_readme(source: str, readme: str) -> bool:
    for line in (readme or "").splitlines():
        cleaned = " ".join(line.strip().lstrip("#").split())
        if len(cleaned) >= MIN_SENTENCE and cleaned in source:
            return True
    return False


def _assigned_from_call(source: str, entry: str) -> set[str]:
    if entry in _mocked_names(source):
        return set()
    names: set[str] = set()
    call = re.compile(rf"\b{re.escape(entry)}\s*\(")
    for line in source.splitlines():
        match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)", line)
        if match and call.search(match.group(2)) and not _MOCK_LIB.search(match.group(2)):
            names.add(match.group(1))
    return names


def _mocks_entry(source: str, module: str, entry: str) -> bool:
    patterns = (
        rf"patch\(\s*['\"]{re.escape(module)}(?:\.{re.escape(entry)})?['\"]",
        rf"patch\.object\(\s*{re.escape(module)}\s*,\s*['\"]{re.escape(entry)}['\"]",
        rf"\b{re.escape(entry)}\s*=\s*(?:MagicMock|Mock|mocker\.Mock|mocker\.MagicMock)\b",
        rf"jest\.mock\(\s*['\"]{re.escape(module)}['\"]",
    )
    return any(re.search(pattern, source) for pattern in patterns)


def _imports_module(source: str, module: str) -> bool:
    if re.search(rf"^\s*import\s+{re.escape(module)}\b", source, re.M):
        return True
    return bool(re.search(rf"^\s*from\s+{re.escape(module)}\s+import\s+", source, re.M))


def _assert_checks_call(source: str, entry: str) -> bool:
    if entry in _mocked_names(source):
        return False
    names = _assigned_from_call(source, entry)
    call = re.compile(rf"\b{re.escape(entry)}\s*\(")
    for line in source.splitlines():
        if not _ASSERT_LINE.search(line):
            continue
        if call.search(line) and not _MOCK_LIB.search(line):
            return True
        if any(re.search(rf"\b{re.escape(name)}\b", line) for name in names):
            return True
    return False


def lint_test_source(source: str, *, readme: str, entry_module: str, entry_point: str, name: str = "") -> list[str]:
    """Reject a test that does not encode the README through the real entry point."""
    label = name or "test"
    errors: list[str] = []
    text = str(source or "")
    if len(text) > MAX_SOURCE:
        errors.append(f"{label}: source is too long")
    if not _DEF_TEST.search(text):
        errors.append(f"{label}: missing a test_ function")
    elif name and not re.search(rf"^def\s+{re.escape(name)}\s*\(", text, re.M):
        errors.append(f"{label}: function name does not match {name}")
    if readme and not _cites_readme(text, readme):
        errors.append(f"{label}: test does not cite the README sentence")
    if any(_tautology(line) for line in text.splitlines()):
        errors.append(f"{label}: tautological assertion")
    if not any(_ASSERT_LINE.search(line) for line in text.splitlines()):
        errors.append(f"{label}: no assertion")
    if not _imports_module(text, entry_module):
        errors.append(f"{label}: test never imports the repository code")
    if _mocks_entry(text, entry_module, entry_point):
        errors.append(f"{label}: mocks the thing under test")
    elif _MOCK_LIB.search(text) and not _assert_checks_call(text, entry_point):
        errors.append(f"{label}: assertions only on mocks")
    if entry_point and not re.search(rf"\b{re.escape(entry_point)}\s*\(", text):
        errors.append(f"{label}: does not call the public entry point")
    elif entry_point and not _assert_checks_call(text, entry_point):
        errors.append(f"{label}: assertion does not check the public entry point")
    return errors


def _sentence_errors(sentence: str, readme: str, source: str, label: str) -> list[str]:
    quote = " ".join(str(sentence or "").split())
    if len(quote) < MIN_SENTENCE:
        return [f"{label}: readme_sentence is too short to cite"]
    if quote not in " ".join(readme.split()) and quote not in readme:
        return [f"{label}: readme_sentence is not in the README"]
    if quote not in source and str(sentence or "").strip() not in source:
        return [f"{label}: test does not cite the README sentence"]
    return []


def proposal_errors(obj: Any, repo: Path, readme: str) -> list[str]:
    if not isinstance(obj, dict):
        return ["intent proposal must be an object"]
    module = str(obj.get("entry_module") or "").strip()
    entry = str(obj.get("entry_point") or "").strip()
    modules = python_modules(repo)
    errors: list[str] = []
    if module not in modules:
        errors.append("entry_module is not a public module in the repository")
    if not entry.isidentifier() or entry not in public_functions(repo, module):
        errors.append("entry_point is not a public function in that module")
    tests = obj.get("tests")
    if not isinstance(tests, list) or not tests:
        errors.append("tests must be a non-empty list")
        return errors
    if len(tests) > MAX_TESTS:
        errors.append(f"at most {MAX_TESTS} intent tests")
    if "pass_reason" in obj and obj.get("pass_reason") is not None and not isinstance(obj.get("pass_reason"), str):
        errors.append("pass_reason must be a string")
    seen: set[str] = set()
    for item in tests:
        if not isinstance(item, dict):
            errors.append("each test must be an object")
            continue
        name = str(item.get("name") or "").strip()
        source = str(item.get("source") or "")
        if not re.fullmatch(r"test_[A-Za-z0-9_]+", name):
            errors.append("test name must start with test_")
            label = name or "test"
        else:
            label = name
        if name in seen:
            errors.append(f"{label}: duplicate test name")
        seen.add(name)
        errors.extend(_sentence_errors(str(item.get("readme_sentence") or ""), readme, source, label))
        if module in modules and entry.isidentifier():
            errors.extend(lint_test_source(source, readme=readme, entry_module=module, entry_point=entry, name=name))
    if not errors and module in modules:
        rendered = render_intent_file([item for item in tests if isinstance(item, dict)])
        errors.extend(lint_intent_file(rendered, readme=readme, entry_module=module, entry_point=entry, modules=modules))
    return errors


def render_intent_file(tests: list[dict[str, Any]]) -> str:
    parts = [HEADER.rstrip()]
    for item in tests:
        source = str(item.get("source") or "").strip()
        if source:
            parts.append(source)
    return "\n\n".join(parts) + "\n"


def lint_intent_file(source: str, *, readme: str, entry_module: str, entry_point: str, modules: list[str]) -> list[str]:
    errors: list[str] = []
    if MARKER not in (source or ""):
        errors.append("file is not marked as synthesized by the pipeline")
    if NOT_COVERAGE.split(".")[0] not in (source or "") and "Not the repository's own tests" not in (source or ""):
        errors.append("file does not say it is not the repository's own tests")
    if entry_module not in modules and modules:
        errors.append("entry_module is not a public module in the repository")
    if not list(_DEF_TEST.finditer(source or "")):
        errors.append("no intent test function")
    for chunk in _chunks(source or ""):
        match = _DEF_TEST.search(chunk)
        name = match.group(1) if match else "test"
        if match is None and chunk.strip() == HEADER.strip():
            continue
        if match is None:
            continue
        errors.extend(lint_test_source(chunk, readme=readme, entry_module=entry_module, entry_point=entry_point, name=name))
    if any(_tautology(line) for line in (source or "").splitlines()):
        if not any("tautological" in error for error in errors):
            errors.append("tautological assertion")
    return errors


def classify_status(check_id: str, code: int, output: str) -> str:
    if code == 0:
        return "pass"
    if check_id == "intent" and re.search(r"\d+\s+failed", output or ""):
        return "fail"
    return "error"


def _execute(root: Path, commands: list[tuple[str, str]], runner: Callable[..., Any], timeout: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for check_id, cmd in commands:
        try:
            result = runner(cmd, root, timeout)
            code = int(getattr(result, "code", 1))
            output = str(getattr(result, "output", "") or "")
        except Exception as exc:  # noqa: BLE001 — a crashed runner is not a pass
            code, output = 1, f"runner error: {type(exc).__name__}"
        rows.append(
            {
                "id": check_id,
                "origin": "synthesized",
                "cmd": cmd,
                "code": code,
                "status": classify_status(check_id, code, output),
                "detail": (output or "")[-400:],
            }
        )
    return rows


def _materialize(snap: dict[str, bytes]) -> tuple[tempfile.TemporaryDirectory[str], Path]:
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    for rel, data in snap.items():
        if not rel or rel.startswith("/") or ".." in rel.split("/"):
            continue
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return tmp, root


def run_checks(snap: dict[str, bytes], commands: list[tuple[str, str]], runner: Callable[..., Any], timeout: int) -> list[dict[str, Any]]:
    tmp, root = _materialize(snap)
    try:
        return _execute(root, commands, runner, timeout)
    finally:
        tmp.cleanup()


def _failed(rows: list[dict[str, Any]]) -> bool:
    return any(str(row.get("status")) in {"fail", "error"} for row in rows)


def _all_pass(rows: list[dict[str, Any]]) -> bool:
    return bool(rows) and all(str(row.get("status")) == "pass" for row in rows)


def decide_synthesis_state(before: list[dict[str, Any]], after: list[dict[str, Any]], *, pass_reason: str, lint_errors: list[str]) -> tuple[str, str]:
    if lint_errors:
        return "NO_TESTS_TO_VERIFY", "no tests were collected, and the intent tests were rejected: " + "; ".join(lint_errors)
    reason_text = " ".join(str(pass_reason or "").split())
    if _all_pass(after) and _failed(before):
        return (
            "RESURRECTED",
            "synthesized smoke checks and intent tests pass after the fix, and at least one failed or errored before; these checks are not the repository's own coverage",
        )
    if _all_pass(after) and not _failed(before):
        if len(reason_text) >= 20:
            return (
                "PARTIAL",
                "synthesized intent tests passed before any fix for a stated reason, so no defect was exposed and the revival is partial",
            )
        return (
            "NO_TESTS_TO_VERIFY",
            "synthesized tests passed before any change and gave no reason, so nothing could be verified automatically",
        )
    if _failed(before) or _failed(after):
        return (
            "PARTIAL",
            "synthesized intent tests or smoke checks still fail after the fix attempts, so the revival is partial",
        )
    return "NO_TESTS_TO_VERIFY", "no tests were collected, so nothing could be verified automatically"


def verify_intent_tests(
    source: str,
    *,
    readme: str,
    entry_module: str,
    entry_point: str,
    modules: list[str],
    before_snap: dict[str, bytes],
    after_snap: dict[str, bytes],
    commands: list[tuple[str, str]],
    runner: Callable[..., Any],
    timeout: int,
    pass_reason: str = "",
) -> dict[str, Any]:
    """Re-lint and re-run. A green runner cannot rescue a tautology or a test that never calls the repo."""
    lint_errors = lint_intent_file(source, readme=readme, entry_module=entry_module, entry_point=entry_point, modules=modules)
    before = run_checks(before_snap, commands, runner, timeout)
    after = run_checks(after_snap, commands, runner, timeout)
    state, reason = decide_synthesis_state(before, after, pass_reason=pass_reason, lint_errors=lint_errors)
    allowed = state == "RESURRECTED" and not lint_errors
    return {
        "state": state,
        "reason": reason,
        "resurrection_allowed": allowed,
        "blocked_reason": "" if allowed else reason,
        "origin": "synthesized",
        "not_repo_coverage": True,
        "before": before,
        "after": after,
        "rejected": lint_errors,
        "checks": [
            {
                "id": "synthesized_intent",
                "status": "PASS" if allowed else "FAIL",
                "detail": reason,
            },
            {
                "id": "synthesized_origin",
                "status": "PASS",
                "detail": NOT_COVERAGE,
            },
        ],
    }


def _intent_plan(obj: Any) -> bool:
    return isinstance(obj, dict) and bool(str(obj.get("entry_module") or "").strip()) and bool(str(obj.get("entry_point") or "").strip()) and not obj.get("tests")


def _ask_proposal(client: Any, repo: Path, repo_id: str, readme: str, errors: list[str], attempt: int) -> tuple[dict[str, Any] | None, list[str]]:
    modules = python_modules(repo)
    catalog = []
    for module in modules:
        catalog.append(f"{module}: {', '.join(public_functions(repo, module)) or '(no public functions)'}")
    quoted = ""
    if errors:
        quoted = "Validation errors from the previous attempt:\n" + "\n".join(f"- {error}" for error in errors) + "\n\n"
    excerpt = clip_text(readme, README_EXCERPT_BYTES)
    prompt = (
        f"Repository: {repo_id}\n\n"
        f"README:\n{excerpt}\n\n"
        f"Public modules:\n" + "\n".join(catalog) + "\n\n"
        f"{quoted}"
        "Write intent tests for the README's stated purpose. "
        'Reply with {"entry_module": str, "entry_point": str, "pass_reason": str, '
        '"tests": [{"name": str, "readme_sentence": str, "source": str}]}. '
        "readme_sentence must be copied from the README and must appear in the test source. "
        "The test must import the module and assert the result of calling the real entry point. "
        "Leave pass_reason empty unless the behavior already matches the README. "
        "Do not include module source. "
        'Or reply with {"entry_module": str, "entry_point": str} and the tests will be requested next.'
    )
    captured: list[str] = []

    def validate(obj: dict[str, Any]) -> bool:
        captured.clear()
        if _intent_plan(obj):
            return True
        captured.extend(proposal_errors(obj, repo, readme))
        return not captured

    obj: Any = None
    try:
        obj, *_ = client.complete_json(prompt, INTENT_SYSTEM, repo_id=repo_id, step=f"revive_intent_{attempt}", attempts=1, validate=validate)
    except Exception as exc:  # noqa: BLE001 — a failed call must not become a pass
        return None, [f"llm call failed: {type(exc).__name__}"]
    if isinstance(obj, dict) and not proposal_errors(obj, repo, readme):
        return obj, []
    if not _intent_plan(obj):
        return None, captured or ["model returned no JSON object"]
    module = str(obj.get("entry_module") or "")
    entry = str(obj.get("entry_point") or "")
    follow = (
        f"Repository: {repo_id}\n\n"
        f"README:\n{excerpt}\n\n"
        f"Entry module: {module}\nEntry point: {entry}\n\n"
        "Write the intent tests for that entry point. "
        'Reply with {"entry_module": str, "entry_point": str, "pass_reason": str, '
        '"tests": [{"name": str, "readme_sentence": str, "source": str}]}. '
        "Do not include the module source."
    )
    follow_captured: list[str] = []

    def validate_tests(payload: dict[str, Any]) -> bool:
        follow_captured.clear()
        follow_captured.extend(proposal_errors(payload, repo, readme))
        return not follow_captured

    try:
        follow_obj, *_ = client.complete_json(
            follow,
            INTENT_SYSTEM,
            repo_id=repo_id,
            step=f"revive_intent_{attempt}_tests",
            attempts=1,
            validate=validate_tests,
        )
    except Exception as exc:  # noqa: BLE001
        return None, [f"llm call failed: {type(exc).__name__}"]
    if isinstance(follow_obj, dict) and not proposal_errors(follow_obj, repo, readme):
        return follow_obj, []
    return None, follow_captured or ["model returned no JSON object"]


def _write_suite(repo: Path, tests: list[dict[str, Any]]) -> str:
    text = render_intent_file(tests)
    target = repo / SYNTH_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")
    return text


def synthesize_for_no_tests(
    repo: Path,
    client: Any,
    runner: Callable[..., Any],
    *,
    repo_id: str,
    baseline: Any,
    timeout: int,
    trace: Any,
    install_cmd: str | None,
    commit_each_item: bool = False,
) -> dict[str, Any]:
    """Ask for intent tests, run them, and allow one small product fix. Never hand-write a test."""
    from app.revive_loop import (
        CmdResult,
        _line_delta,
        apply_edits,
        context_files,
        is_no_tests,
        propose_patch,
        restore_worktree,
        snapshot_worktree,
    )

    readme = readme_text(repo)
    errors: list[str] = []
    proposal: dict[str, Any] | None = None
    for attempt in (1, 2):
        proposal, errors = _ask_proposal(client, repo, repo_id, readme, errors, attempt)
        if proposal is not None:
            break
    if proposal is None:
        reason = "no tests were collected, and the intent tests were rejected: " + "; ".join(dict.fromkeys(errors or ["model returned no JSON object"]))
        if trace is not None:
            trace.event(agent_name="builder", action="REVIVE_INTENT", input_summary=repo_id, output_summary={"rejected": errors, "path": SYNTH_FILE}, status="failed")
        return _report("NO_TESTS_TO_VERIFY", reason, baseline, rejected=errors)

    module = str(proposal["entry_module"])
    entry = str(proposal["entry_point"])
    tests = [item for item in proposal["tests"] if isinstance(item, dict)]
    pass_reason = str(proposal.get("pass_reason") or "")
    pre = snapshot_worktree(repo)
    source = _write_suite(repo, tests)
    modules = python_modules(repo)
    commands = check_commands(repo, module, install_cmd)
    before_snap = snapshot_worktree(repo)
    if trace is not None:
        trace.event(
            agent_name="builder",
            action="REVIVE_INTENT",
            input_summary=repo_id,
            output_summary={"path": SYNTH_FILE, "entry_module": module, "entry_point": entry, "tests": [item.get("name") for item in tests]},
            status="ok",
        )

    iterations: list[dict[str, Any]] = []
    history: list[str] = []
    kept_files: list[str] = []
    rolled: list[dict[str, Any]] = []
    before_rows = _execute(repo, commands, runner, timeout)
    if trace is not None:
        trace.event(agent_name="validator", action="REVIVE_INTENT_RUN", input_summary="before", output_summary={"rows": _brief(before_rows)}, status="ok" if _all_pass(before_rows) else "failed")

    if _failed(before_rows):
        for attempt in range(1, MAX_FIXES + 1):
            failed = next(row for row in before_rows if row["status"] != "pass")
            last = CmdResult(str(failed.get("cmd") or ""), int(failed.get("code") or 1), str(failed.get("detail") or ""))
            files = context_files(repo, last.output)
            module_path = f"{module}.py"
            module_file = repo / module_path
            if module_file.is_file() and module_path not in files:
                files[module_path] = module_file.read_text(encoding="utf-8", errors="replace")[:12_000]
            patch = propose_patch(
                client,
                repo_id,
                None,
                f"intent-{attempt}",
                last,
                files,
                history,
                note=(
                    "The failing checks are synthesized intent tests in phoenix_synthesized/. "
                    "Do not edit phoenix_synthesized or any other test. Fix the product code."
                ),
            )
            if not patch:
                history.append(f"intent fix {attempt}: the model returned no usable patch")
                iterations.append({"n": attempt, "patch": None, "accepted": False, "kind": "intent"})
                break
            attempt_snap = snapshot_worktree(repo)
            changed, rejected = apply_edits(repo, [edit for edit in patch.get("edits") or [] if isinstance(edit, dict)])
            if not changed:
                history.append(f"intent fix {attempt}: every edit was rejected: {'; '.join(rejected)}")
                iterations.append({"n": attempt, "changed": [], "rejected": rejected, "accepted": False, "kind": "intent"})
                continue
            after_try = _execute(repo, commands, runner, timeout)
            accepted = _all_pass(after_try)
            iterations.append({"n": attempt, "changed": changed, "rejected": rejected, "accepted": accepted, "exit": after_try[-1]["code"] if after_try else 1, "kind": "intent"})
            if trace is not None:
                trace.event(
                    agent_name="builder",
                    action="REVIVE_PATCH",
                    input_summary={"iteration": f"intent-{attempt}"},
                    output_summary={"changed": changed, "rejected": rejected, "accepted": accepted},
                    status="ok" if accepted else "rejected",
                )
            if accepted:
                kept_files = list(changed)
                break
            restore_worktree(repo, attempt_snap)
            history.append(f"intent fix {attempt}: rolled back because synthesized checks still fail")
            rolled.append({"id": f"intent-fix-{attempt}", "kind": "repair", "status": "ROLLED_BACK", "reason": "synthesized checks still failed", "files": changed})

    after_snap = snapshot_worktree(repo)
    source_now = after_snap.get(SYNTH_FILE, b"").decode("utf-8", "replace")
    verified = verify_intent_tests(
        source_now or source,
        readme=readme,
        entry_module=module,
        entry_point=entry,
        modules=modules,
        before_snap=before_snap,
        after_snap=after_snap,
        commands=commands,
        runner=runner,
        timeout=timeout,
        pass_reason=pass_reason,
    )
    state, reason = str(verified["state"]), str(verified["reason"])
    own = runner(str(getattr(baseline, "cmd", "") or ""), repo, timeout)
    own_code = int(getattr(own, "code", 1))
    own_output = str(getattr(own, "output", "") or "")
    if not is_no_tests(str(getattr(own, "cmd", "") or ""), own_output, own_code):
        restore_worktree(repo, before_snap)
        state = "NO_TESTS_TO_VERIFY"
        reason = "synthesized tests were collected by the repository's own test command, so they are not a separate check"
        verified = dict(verified)
        verified["resurrection_allowed"] = False
        verified["state"] = state
        verified["blocked_reason"] = reason

    post = snapshot_worktree(repo)
    files_changed = sorted(rel for rel in set(pre) | set(post) if pre.get(rel) != post.get(rel))
    lines = sum(_line_delta(pre.get(rel), post.get(rel)) for rel in files_changed)
    done: list[dict[str, Any]] = []
    if kept_files and state == "RESURRECTED":
        done.append(
            {
                "id": "intent-fix",
                "kind": "repair",
                "status": "KEPT",
                "files": kept_files,
                "lines": sum(_line_delta(pre.get(rel), post.get(rel)) for rel in kept_files),
                "acceptance": "pass",
            }
        )
        _commit(repo, "intent-fix", kept_files + [SYNTH_FILE], commit_each_item)
    final = CmdResult(str(getattr(own, "cmd", "") or getattr(baseline, "cmd", "")), own_code, (own_output or "")[-500:])
    rejected = list(verified.get("rejected") or [])
    if state == "NO_TESTS_TO_VERIFY" and not rejected:
        rejected = [reason]
    synthesized = {
        "origin": "synthesized",
        "not_repo_coverage": True,
        "path": SYNTH_FILE,
        "entry_module": module,
        "entry_point": entry,
        "pass_reason": pass_reason,
        "note": NOT_COVERAGE,
        "tests": [{"name": item.get("name"), "readme_sentence": item.get("readme_sentence")} for item in tests],
        "before": verified["before"],
        "after": verified["after"],
        "rejected": rejected,
    }
    verification = {
        "checks": verified["checks"],
        "resurrection_allowed": bool(verified["resurrection_allowed"]) and state == "RESURRECTED",
        "blocked_reason": "" if state == "RESURRECTED" else reason,
        "kind": "synthesized",
    }
    if trace is not None:
        trace.event(agent_name="validator", action="REVIVE_INTENT_VERIFY", input_summary=repo_id, output_summary={"state": state, "resurrection_allowed": verification["resurrection_allowed"]}, status="ok" if state == "RESURRECTED" else "failed")
    return _report(
        state,
        reason,
        final,
        synthesized=synthesized,
        verification=verification,
        files_changed=files_changed,
        lines_changed=lines,
        iterations=iterations,
        prd_items_done=done,
        prd_items_not_done=rolled,
        quality={
            "before": [],
            "after": [],
            "files_changed": files_changed,
            "lines_changed": lines,
            "regressed": [],
            "verified_criteria": [],
        },
    )


def _brief(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"id": row.get("id"), "status": row.get("status"), "code": row.get("code")} for row in rows]


def _commit(repo: Path, item_id: str, files: list[str], enabled: bool) -> None:
    import subprocess

    if not enabled or not files or not (repo / ".git").is_dir():
        return
    subprocess.run(["git", "add", "--", *files], cwd=str(repo), capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=PhoenixRepo", "-c", "user.email=phoenix@example.invalid", "commit", "-q", "-m", f"revive: {item_id}"],
        cwd=str(repo),
        capture_output=True,
    )


def _report(state: str, reason: str, final: Any, **extra: Any) -> dict[str, Any]:
    payload = {
        "state": state,
        "reason": reason,
        "final": final,
        "synthesized": extra.get("synthesized"),
        "verification": extra.get("verification"),
        "files_changed": extra.get("files_changed") or [],
        "lines_changed": extra.get("lines_changed") or 0,
        "iterations": extra.get("iterations") or [],
        "prd_items_done": extra.get("prd_items_done") or [],
        "prd_items_not_done": extra.get("prd_items_not_done") or [],
        "quality": extra.get("quality") or {},
        "rejected": extra.get("rejected") or [],
    }
    if state == "NO_TESTS_TO_VERIFY" and payload["synthesized"] is None and payload["rejected"]:
        payload["synthesized"] = {
            "origin": "synthesized",
            "not_repo_coverage": True,
            "path": SYNTH_FILE,
            "note": NOT_COVERAGE,
            "rejected": list(payload["rejected"]),
            "before": [],
            "after": [],
            "tests": [],
        }
        payload["verification"] = {
            "checks": [
                {"id": "synthesized_intent", "status": "FAIL", "detail": reason},
                {"id": "synthesized_origin", "status": "PASS", "detail": NOT_COVERAGE},
            ],
            "resurrection_allowed": False,
            "blocked_reason": reason,
            "kind": "synthesized",
        }
    return payload
