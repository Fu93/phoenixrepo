"""Structured revival PRD.

The model sees a short prompt: a README excerpt, the file tree, the baseline tail, and the
outdated-dependency list. It does not receive file contents. Diagnosis, work items, and
acceptance commands are separate calls. Each accepted piece is stored in the run directory
and reused. When the model is down, a deterministic PRD is built from the baseline and the
outdated list.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

from app.quality_gate import CHECK_IDS, render_evidence_html

MAX_FILE_BYTES = 12_000
TREE_LIMIT = 80
README_EXCERPT_BYTES = 6_000
BASELINE_TAIL_CHARS = 1_500
TREE_PROMPT_LIMIT = 60
PROMPT_BUDGET_BYTES = 12_000
PRD_SYSTEM = (
    "You are the architect of a repository revival. Use only the files, file tree, and command output you are given. "
    "Do not invent packages, versions, or paths. Reply with one strict JSON object and nothing else."
)
MEASURABLE_RE = re.compile(
    r"(\d|exit code|exits 0|must pass|\bpasses\b|no high|no critical|\bzero\b|at least|at most|does not edit tests|do not edit tests)",
    re.IGNORECASE,
)
PATH_SUFFIXES = (".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".md", ".toml", ".json", ".yml", ".yaml", ".txt", ".cfg", ".ini")
SOURCE_SUFFIXES = (".py", ".js", ".ts", ".tsx", ".jsx", ".go")
_BASELINE_GOAL_RE = re.compile(r"pytest|baseline command|test command exit|make pytest", re.IGNORECASE)
_BOT_OR_CI_RE = re.compile(
    r"(^|/)\.github(/|$)|(^|/)dependabot\.(yml|yaml)$|(^|/)renovate\.json$|(^|/)\.renovaterc$|"
    r"(^|/)codecov\.yml$|(^|/)\.travis\.yml$|(^|/)appveyor\.yml$|(^|/)azure-pipelines\.yml$|"
    r"(^|/)\.circleci(/|$)|(^|/)\.pre-commit-config\.yaml$",
    re.IGNORECASE,
)
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".pytest_cache"}
REQUIRED_KEYS = (
    "problem",
    "target_user",
    "scope",
    "non_goals",
    "upgrade_items",
    "refactor_items",
    "docs_items",
    "acceptance_criteria",
    "risks",
)


def file_tree(repo: Path, limit: int = TREE_LIMIT) -> list[str]:
    found: list[str] = []
    if not repo.exists():
        return found
    for path in sorted(repo.rglob("*")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_file():
            found.append(path.relative_to(repo).as_posix())
        if len(found) >= limit:
            break
    return found


def supporting_files(repo: Path) -> dict[str, str]:
    names = ("README.md", "README.rst", "README.txt", "package.json", "pyproject.toml", "requirements.txt", "setup.py", "go.mod", "tsconfig.json")
    files: dict[str, str] = {}
    for name in names:
        path = repo / name
        if path.is_file():
            files[name] = path.read_text(encoding="utf-8", errors="replace")[:MAX_FILE_BYTES]
    return files


def _looks_like_path(text: str) -> bool:
    value = text.strip().replace("\\", "/")
    if not value or " " in value or value in {".", "/"}:
        return False
    if "/" in value:
        return True
    return value.endswith(PATH_SUFFIXES)


def _nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


EVIDENCE_RE = re.compile(
    r"\d|exit|error|fail|outdated|deprecated|vulnerab|assert|baseline|missing|broken|\bCI\b|ENOENT|ModuleNotFound",
    re.IGNORECASE,
)


def readme_text(repo: Path) -> str:
    for name in ("README.md", "README.rst", "README.txt"):
        path = repo / name
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")[:MAX_FILE_BYTES]
    return ""


def quote_snippet(text: str) -> str:
    """A short contiguous slice of the README or description that a PRD can quote."""
    for line in (text or "").splitlines():
        cleaned = line.strip().lstrip("#").strip()
        if len(cleaned) >= 8:
            return cleaned[:180]
    compact = " ".join((text or "").split())
    return compact[:180]


def _repo_corpus(repo: Path) -> str:
    parts: list[str] = []
    for rel in file_tree(repo, limit=200):
        path = repo / rel
        try:
            if path.is_file() and path.stat().st_size <= 200_000:
                parts.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return "\n".join(parts)


def _acceptance_error(item: dict[str, Any], label: str) -> str | None:
    command = str(item.get("acceptance_command") or "").strip()
    expected = str(item.get("expected") or "").strip()
    if not command:
        return f"{label} has no acceptance command"
    if not expected or not MEASURABLE_RE.search(expected):
        return f"{label} acceptance result is not measurable"
    return None


def _intent_error(obj: dict[str, Any], repo: Path, description: str) -> str | None:
    if "original_intent" not in obj:
        return "missing original_intent"
    intent = str(obj.get("original_intent") or "").strip()
    if not intent:
        return "original_intent must quote the README or description"
    source = (readme_text(repo) + "\n" + (description or "")).strip()
    if not source:
        if intent == "(no README or description)":
            return None
        return "original_intent must quote the README or description"
    snippet = quote_snippet(source)
    bare = intent.strip().strip('"')
    if snippet and snippet in intent:
        return None
    if bare and bare in source:
        return None
    return "original_intent is not a quote from the README or description"


def _diagnosis_error(obj: dict[str, Any], repo: Path) -> str | None:
    if "diagnosis" not in obj:
        return "missing diagnosis"
    diagnosis = str(obj.get("diagnosis") or "").strip()
    if not diagnosis:
        return "diagnosis must cite the engineering failure"
    readme = " ".join(readme_text(repo).split())
    compact = " ".join(diagnosis.split())
    problem = " ".join(str(obj.get("problem") or "").split())
    evidence = EVIDENCE_RE.search(diagnosis)
    if readme and (compact == readme or f"{problem} {compact}".strip() == readme) and not evidence:
        return "PRD restates the README only"
    if readme and compact in readme and not evidence:
        return "PRD restates the README only"
    if not evidence:
        return "diagnosis does not cite real evidence"
    return None


def collected_nothing(baseline: Any) -> bool:
    """pytest exit 5, or an output that says nothing was collected, is not a failing product baseline."""
    from app.revive_loop import is_no_tests

    code = getattr(baseline, "code", None)
    if not isinstance(code, int):
        code = 0
    return is_no_tests(str(getattr(baseline, "cmd", "") or ""), str(getattr(baseline, "output", "") or ""), code)


def _is_baseline_test_goal(item: dict[str, Any]) -> bool:
    """A repair whose aim is 'make the test command exit 0'. An evidence note is not a goal."""
    if str(item.get("kind") or "") == "evidence":
        return False
    if str(item.get("id") or "") == "fix-baseline":
        return True
    text = " ".join(str(item.get(key) or "") for key in ("id", "kind", "title"))
    return bool(_BASELINE_GOAL_RE.search(text))


def _is_bot_or_ci(rel: str) -> bool:
    return bool(_BOT_OR_CI_RE.search(rel.replace("\\", "/").lstrip("./")))


def _acceptance_criteria(nothing: bool) -> list[dict[str, str]]:
    tests = (
        {"id": "ac-tests", "statement": "The repository test command collects no tests and exits 5.", "check_id": "tests"}
        if nothing
        else {"id": "ac-tests", "statement": "The repository test command exits 0.", "check_id": "tests"}
    )
    return [
        tests,
        {"id": "ac-diff", "statement": "The diff touches at most 8 files and does not edit tests.", "check_id": "diff_scope"},
        {"id": "ac-audit", "statement": "High and critical audit findings stay at or below the baseline count of 0 new findings.", "check_id": "audit"},
    ]


def _repair_targets(repo: Path, scope: list[str]) -> list[str]:
    """A product file the failure could actually be about. CI and bot config is not one."""
    files = [rel for rel in scope if (repo / rel).is_file() and not _is_bot_or_ci(rel)]
    preferred = [rel for rel in files if rel.endswith(SOURCE_SUFFIXES)]
    return (preferred or files)[:1]


def _goal_errors(item: dict[str, Any], label: str, repo: Path, baseline: Any) -> list[str]:
    """Reject a target that cannot serve the goal, and a goal the builder is not allowed to achieve."""
    from app.revive_loop import is_protected

    errors: list[str] = []
    goal = _is_baseline_test_goal(item)
    for rel in item.get("files") or []:
        if not isinstance(rel, str) or not rel.strip():
            continue
        norm = rel.replace("\\", "/")
        if is_protected(norm):
            errors.append(f"{label} targets {rel}, which the builder is not allowed to edit")
            continue
        if goal and _is_bot_or_ci(norm):
            errors.append(f"{label} targets {rel}, which is unrelated to its pytest goal")
    if goal and collected_nothing(baseline):
        errors.append(f"{label} is unachievable: no tests were collected and the builder cannot add tests")
    return errors


def validate_prd(obj: Any, repo: Path, description: str = "", baseline: Any = None) -> list[str]:
    """Return human-readable errors. An empty list means the PRD is usable."""
    errors: list[str] = []
    if not isinstance(obj, dict):
        return ["PRD is not a JSON object"]
    for key in ("original_intent", "diagnosis", "work_items", *REQUIRED_KEYS):
        if key not in obj:
            errors.append(f"missing {key}")
    if "problem" in obj and not _nonempty_str(obj.get("problem")):
        errors.append("problem must be a non-empty string")
    if "target_user" in obj and not _nonempty_str(obj.get("target_user")):
        errors.append("target_user must be a non-empty string")
    for key in ("scope", "non_goals", "risks", "upgrade_items", "refactor_items", "docs_items", "acceptance_criteria", "work_items"):
        if key in obj and not isinstance(obj.get(key), list):
            errors.append(f"{key} must be a list")
    scope = obj.get("scope") if isinstance(obj.get("scope"), list) else []
    if "scope" in obj and isinstance(obj.get("scope"), list) and not scope:
        errors.append("scope must name at least one path or area")
    for entry in scope:
        if not isinstance(entry, str) or not entry.strip():
            errors.append("scope entries must be non-empty strings")
            continue
        if _looks_like_path(entry) and not (repo / entry.replace("\\", "/")).exists():
            errors.append(f"scope path does not exist: {entry}")
    for label, items in (("non_goals", obj.get("non_goals")), ("risks", obj.get("risks"))):
        if isinstance(items, list) and not any(isinstance(item, str) and item.strip() for item in items):
            errors.append(f"{label} must name at least one explicit entry")
        elif isinstance(items, list) and not all(isinstance(item, str) and item.strip() for item in items):
            errors.append(f"{label} entries must be non-empty strings")
    intent_error = _intent_error(obj, repo, description) if isinstance(obj, dict) else None
    if intent_error and "original_intent" in obj:
        errors.append(intent_error)
    diagnosis_error = _diagnosis_error(obj, repo) if isinstance(obj, dict) else None
    if diagnosis_error and "diagnosis" in obj:
        errors.append(diagnosis_error)
    corpus = _repo_corpus(repo)
    upgrades = obj.get("upgrade_items") if isinstance(obj.get("upgrade_items"), list) else []
    for index, item in enumerate(upgrades):
        if not isinstance(item, dict):
            errors.append(f"upgrade_items[{index}] is not an object")
            continue
        if not _nonempty_str(item.get("id")):
            errors.append(f"upgrade_items[{index}] missing id")
        package = str(item.get("package") or "").strip()
        version = str(item.get("version") or item.get("to_version") or "").strip()
        if not package or not version:
            errors.append(f"upgrade_items[{index}] needs a package and a target version")
        elif package not in corpus:
            errors.append(f"upgrade_items[{index}] cites package {package} which is not in the repo")
        acceptance = _acceptance_error(item, f"upgrade_items[{index}]")
        if acceptance:
            errors.append(acceptance)
    refactors = obj.get("refactor_items") if isinstance(obj.get("refactor_items"), list) else []
    for index, item in enumerate(refactors):
        if not isinstance(item, dict):
            errors.append(f"refactor_items[{index}] is not an object")
            continue
        if not _nonempty_str(item.get("id")):
            errors.append(f"refactor_items[{index}] missing id")
        files = item.get("files")
        if not isinstance(files, list) or not files:
            errors.append(f"refactor_items[{index}] must name one or more files")
            continue
        goal_errors = _goal_errors(item, f"refactor_items[{index}]", repo, baseline)
        errors.extend(goal_errors)
        for rel in files:
            if not isinstance(rel, str) or not rel.strip():
                errors.append(f"refactor_items[{index}] has an empty file")
                continue
            norm = rel.replace("\\", "/")
            if any(norm in error or rel in error for error in goal_errors):
                continue
            if not (repo / norm).is_file():
                errors.append(f"refactor file does not exist: {rel}")
        acceptance = _acceptance_error(item, f"refactor_items[{index}]")
        if acceptance:
            errors.append(acceptance)
        api = str(item.get("api") or "").strip()
        if api and api not in corpus:
            errors.append(f"refactor_items[{index}] cites API {api} which is not in the repo")
    docs = obj.get("docs_items") if isinstance(obj.get("docs_items"), list) else []
    for index, item in enumerate(docs):
        if not isinstance(item, dict) or not _nonempty_str(item.get("id")) or not _nonempty_str(item.get("title")):
            errors.append(f"docs_items[{index}] needs an id and a title")
            continue
        acceptance = _acceptance_error(item, f"docs_items[{index}]")
        if acceptance:
            errors.append(acceptance)
    work = obj.get("work_items") if isinstance(obj.get("work_items"), list) else []
    if "work_items" in obj and isinstance(obj.get("work_items"), list) and not work:
        errors.append("work_items must list at least one prioritised item")
    for index, item in enumerate(work):
        if not isinstance(item, dict):
            errors.append(f"work_items[{index}] is not an object")
            continue
        if not _nonempty_str(item.get("id")):
            errors.append(f"work_items[{index}] missing id")
        acceptance = _acceptance_error(item, f"work_items[{index}]")
        if acceptance:
            errors.append(acceptance)
        package = str(item.get("package") or "").strip()
        if package and package not in corpus:
            errors.append(f"work_items[{index}] cites package {package} which is not in the repo")
        api = str(item.get("api") or "").strip()
        if api and api not in corpus:
            errors.append(f"work_items[{index}] cites API {api} which is not in the repo")
        goal_errors = _goal_errors(item, f"work_items[{index}]", repo, baseline)
        errors.extend(goal_errors)
        for rel in item.get("files") or []:
            if not isinstance(rel, str) or not _looks_like_path(rel):
                continue
            norm = rel.replace("\\", "/")
            if any(norm in error or rel in error for error in goal_errors):
                continue
            if not (repo / norm).exists():
                errors.append(f"work_items[{index}] cites path {rel} which is not in the repo")
    criteria = obj.get("acceptance_criteria") if isinstance(obj.get("acceptance_criteria"), list) else []
    if "acceptance_criteria" in obj and isinstance(obj.get("acceptance_criteria"), list) and len(criteria) < 3:
        errors.append("acceptance_criteria needs at least 3 measurable checks")
    for index, item in enumerate(criteria):
        if not isinstance(item, dict):
            errors.append(f"acceptance_criteria[{index}] is not an object")
            continue
        if not _nonempty_str(item.get("id")):
            errors.append(f"acceptance_criteria[{index}] missing id")
        statement = item.get("statement")
        if not _nonempty_str(statement):
            errors.append(f"acceptance_criteria[{index}] missing statement")
        elif not MEASURABLE_RE.search(str(statement)):
            errors.append(f"acceptance_criteria[{index}] is not measurable")
        check_id = str(item.get("check_id") or "")
        if check_id not in CHECK_IDS:
            errors.append(f"acceptance_criteria[{index}] check_id {check_id or '(missing)'} is not a known quality check")
    return errors


def intent_quote(repo: Path, description: str = "") -> str:
    source = readme_text(repo).strip() or (description or "").strip()
    if not source:
        return "(no README or description)"
    return f'The README says "{quote_snippet(source)}".'


def _source_scope(repo: Path) -> list[str]:
    from app.revive_loop import is_protected

    scope = [rel for rel in file_tree(repo, limit=40) if not is_protected(rel)]
    return scope[:12] or ["repository source"]


def minimal_prd(repo: Path, repo_id: str, baseline: Any, reason: str) -> dict[str, Any]:
    """Last-resort PRD. It has no upgrade or refactor work, so a green baseline stays NOTHING_TO_FIX."""
    cmd = getattr(baseline, "cmd", "") or "the test command"
    code = getattr(baseline, "code", None)
    output = str(getattr(baseline, "output", "") or "").strip()
    evidence = output[:240] if output else "no command output was captured"
    scope = _source_scope(repo)
    nothing = collected_nothing(baseline)
    files = [] if nothing else _repair_targets(repo, scope)
    work_items = [
        {
            "id": "no-tests-collected",
            "kind": "evidence",
            "title": "No tests were collected. Exit 5 is not a failing baseline.",
            "acceptance_command": cmd,
            "expected": f"exit code {code}",
            "files": [],
        }
    ] if nothing else [
        {
            "id": "fix-baseline",
            "kind": "repair",
            "title": "Make the baseline command exit 0",
            "acceptance_command": cmd,
            "expected": "exit code 0",
            "files": files,
        }
    ]
    prd = {
        "problem": f"{repo_id} baseline `{cmd}` exited {code}.",
        "original_intent": intent_quote(repo),
        "diagnosis": f"Baseline `{cmd}` exited {code}. Evidence: {evidence}",
        "target_user": "Maintainers of this repository.",
        "scope": scope,
        "non_goals": ["Do not edit tests or lockfiles.", "Do not add features the failing checks do not require."],
        "work_items": work_items,
        "upgrade_items": [],
        "refactor_items": [],
        "docs_items": [],
        "acceptance_criteria": _acceptance_criteria(nothing),
        "risks": ["This PRD is a fallback because the model draft failed validation."],
        "degraded": True,
        "degraded_reason": reason,
    }
    return prd


def clip_text(text: str, limit: int = README_EXCERPT_BYTES) -> str:
    """Keep at most `limit` bytes. A cut never splits a prompt instruction that follows it."""
    raw = (text or "").encode("utf-8")
    if len(raw) <= limit:
        return text or ""
    return raw[:limit].decode("utf-8", errors="ignore")


def readme_excerpt(repo: Path, description: str = "", limit: int = README_EXCERPT_BYTES) -> str:
    """At most `limit` bytes of the README or description. No other file body is included."""
    text = readme_text(repo).strip() or (description or "").strip()
    if not text:
        return "(none)"
    return clip_text(text, limit)


def _baseline_tail(baseline: Any) -> str:
    from app.revive_loop import tail

    return tail(str(getattr(baseline, "output", "") or ""), BASELINE_TAIL_CHARS)


def _outdated_block(items: list[dict[str, Any]]) -> str:
    if not items:
        return "(none)"
    lines = []
    for item in items[:8]:
        lines.append(f"{item.get('package')} {item.get('from_version') or '?'} -> {item.get('version')}")
    return "\n".join(lines)


def load_outdated(repo: Path, runner: Any, timeout: int = 20) -> list[dict[str, Any]]:
    """Minor and patch upgrades the ecosystem already reported. No model, no invented versions."""
    if runner is None:
        return []
    try:
        from app.revive_loop import deterministic_upgrade_items

        return list(deterministic_upgrade_items(repo, runner, max(5, min(20, int(timeout or 20)))))
    except Exception:  # noqa: BLE001 — a missing outdated list is not a verdict
        return []


def _shared_context(repo: Path, repo_id: str, description: str, baseline: Any, outdated: list[dict[str, Any]]) -> str:
    tree = file_tree(repo, limit=TREE_PROMPT_LIMIT)
    return (
        f"Repository: {repo_id}\n"
        f"README excerpt:\n{readme_excerpt(repo, description)}\n\n"
        f"File tree:\n" + "\n".join(tree) + "\n\n"
        f"Baseline command: {getattr(baseline, 'cmd', '')}\nExit code: {getattr(baseline, 'code', '')}\n"
        f"Output (tail):\n{_baseline_tail(baseline)}\n\n"
        f"Outdated dependencies:\n{_outdated_block(outdated)}\n"
    )


def _fit(body: str, tail: str) -> str:
    """Cap the shared context and always keep the piece instructions that follow it."""
    reserve = len(tail.encode("utf-8"))
    return clip_text(body, max(0, PROMPT_BUDGET_BYTES - reserve)) + tail


def _error_block(errors: list[str]) -> str:
    return "\n".join(f"- {error}" for error in errors) if errors else "(none)"


def _is_infra(exc: BaseException) -> bool:
    if getattr(exc, "status_code", None) == 502:
        return True
    text = str(exc)
    return "HTTP 502" in text or "status code 502" in text


def _piece_path(run_dir: Path | None, name: str) -> Path | None:
    if run_dir is None:
        return None
    return Path(run_dir) / f"{name}.json"


def load_piece(run_dir: Path | None, name: str) -> dict[str, Any] | None:
    path = _piece_path(run_dir, name)
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save_piece(run_dir: Path | None, name: str, payload: dict[str, Any]) -> None:
    path = _piece_path(run_dir, name)
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _call_piece(client: Any, prompt: str, repo_id: str, step: str, accept) -> tuple[Any, list[str], bool]:
    """One model call. A 502 is already retried by the client wrapper; do not treat it as a bad draft."""
    captured: list[str] = []

    def validate(obj: dict[str, Any]) -> bool:
        captured.clear()
        captured.extend(accept(obj))
        return not captured

    try:
        obj, *_ = client.complete_json(prompt, PRD_SYSTEM, repo_id=repo_id, step=step, attempts=1, validate=validate)
    except Exception as exc:  # noqa: BLE001 — a failed call must not become a pass
        return None, [f"llm call failed: {type(exc).__name__}"], _is_infra(exc)
    if isinstance(obj, dict) and not captured:
        return obj, [], False
    return None, captured or ["model returned no JSON object"], False


def _diagnosis_prompt(context: str, attempt: int, errors: list[str]) -> str:
    tail = (
        f"\nPRD attempt {attempt} of 2. Piece: diagnosis.\n"
        f"Validation errors from the previous attempt:\n{_error_block(errors)}\n\n"
        "Reply with only this JSON object: "
        '{"original_intent": str, "diagnosis": str, "problem": str, "target_user": str}. '
        "original_intent must quote the README excerpt. "
        "diagnosis must cite the baseline output or the outdated list and must not restate the README. "
        "Do not include file contents."
    )
    return _fit(context, tail)


def _work_prompt(context: str, diagnosis: dict[str, Any], attempt: int, errors: list[str]) -> str:
    brief = json.dumps({key: diagnosis.get(key) for key in ("problem", "diagnosis")}, ensure_ascii=False)[:800]
    tail = (
        f"\nAccepted diagnosis: {brief}\n"
        f"PRD work attempt {attempt} of 2.\n"
        f"Validation errors from the previous attempt:\n{_error_block(errors)}\n\n"
        "Reply with only this JSON object: "
        '{"scope": [str], "non_goals": [str], "risks": [str], '
        '"work_items": [{"id": str, "kind": str, "title": str, "files": [str], "package": str}], '
        '"upgrade_items": [{"id": str, "package": str, "version": str, "reason": str}], '
        '"refactor_items": [], "docs_items": []}. '
        "Use only packages and paths from the tree and the outdated list. "
        "At most 4 upgrade items. Do not include file contents."
    )
    return _fit(context, tail)


def _acceptance_prompt(context: str, work: dict[str, Any], baseline: Any, attempt: int, errors: list[str]) -> str:
    ids = []
    for key in ("work_items", "upgrade_items", "refactor_items", "docs_items"):
        for item in work.get(key) or []:
            if isinstance(item, dict) and item.get("id"):
                ids.append(str(item.get("id")))
    tail = (
        f"\nItem ids: {', '.join(ids[:12]) or '(none)'}\n"
        f"Suggested acceptance command: {getattr(baseline, 'cmd', '')}\n"
        f"PRD acceptance attempt {attempt} of 2.\n"
        f"Validation errors from the previous attempt:\n{_error_block(errors)}\n\n"
        "Reply with only this JSON object: "
        '{"acceptance_criteria": [{"id": str, "statement": str, "check_id": str}], '
        '"items": [{"id": str, "acceptance_command": str, "expected": str}]}. '
        "At least 3 acceptance_criteria. check_id must be one of "
        + ", ".join(CHECK_IDS)
        + ". expected must be measurable (a number, an exit code, or a pass/fail bound)."
    )
    return _fit(context, tail)


def _accept_diagnosis(obj: Any, repo: Path, description: str, baseline: Any = None) -> list[str]:
    if not isinstance(obj, dict):
        return ["diagnosis is not an object"]
    if not validate_prd(obj, repo, description, baseline):
        return []
    errors: list[str] = []
    for key in ("original_intent", "diagnosis", "problem", "target_user"):
        if key not in obj:
            errors.append(f"missing {key}")
    intent_error = _intent_error(obj, repo, description) if "original_intent" in obj else None
    if intent_error:
        errors.append(intent_error)
    diagnosis_error = _diagnosis_error(obj, repo) if "diagnosis" in obj else None
    if diagnosis_error:
        errors.append(diagnosis_error)
    if "problem" in obj and not _nonempty_str(obj.get("problem")):
        errors.append("problem must be a non-empty string")
    if "target_user" in obj and not _nonempty_str(obj.get("target_user")):
        errors.append("target_user must be a non-empty string")
    return errors


def _shell_from(repo: Path, description: str, baseline: Any, outdated: list[dict[str, Any]], overlay: dict[str, Any]) -> dict[str, Any]:
    shell = deterministic_prd(repo, "repo", baseline, outdated, description=description)
    for key in ("scope", "non_goals", "risks", "work_items", "upgrade_items", "refactor_items", "docs_items", "acceptance_criteria"):
        if key in overlay:
            shell[key] = copy.deepcopy(overlay[key])
    for extra in ("degraded", "degraded_reason", "drafted_by", "fallback_reason", "label", "deterministic_pieces"):
        shell.pop(extra, None)
    return shell


def _accept_work(obj: Any, repo: Path, description: str, baseline: Any, outdated: list[dict[str, Any]]) -> list[str]:
    if not isinstance(obj, dict):
        return ["work items are not an object"]
    if not validate_prd(obj, repo, description, baseline):
        return []
    for key in ("scope", "non_goals", "risks", "work_items", "upgrade_items"):
        if key not in obj:
            return [f"missing {key}"]
    shell = _shell_from(repo, description, baseline, outdated, obj)
    # Commands arrive in the next piece. Fill them only on this validation copy.
    _fill_acceptance(shell, baseline)
    return validate_prd(shell, repo, description, baseline)


def _accept_acceptance(obj: Any, repo: Path, description: str, baseline: Any, outdated: list[dict[str, Any]], work: dict[str, Any]) -> list[str]:
    if not isinstance(obj, dict):
        return ["acceptance is not an object"]
    if not validate_prd(obj, repo, description, baseline):
        return []
    if not isinstance(obj.get("acceptance_criteria"), list):
        return ["missing acceptance_criteria"]
    commands = obj.get("items") if isinstance(obj.get("items"), list) else []
    by_id = {str(item["id"]): item for item in commands if isinstance(item, dict) and item.get("id")}
    errors: list[str] = []
    for key in ("work_items", "upgrade_items", "refactor_items", "docs_items"):
        for item in (work or {}).get(key) or []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            item_id = str(item["id"])
            incoming = by_id.get(item_id)
            command = str(incoming.get("acceptance_command") or "").strip() if isinstance(incoming, dict) else ""
            expected = str(incoming.get("expected") or "").strip() if isinstance(incoming, dict) else ""
            if not command:
                errors.append(f"{item_id} has no acceptance command")
            elif not expected or not MEASURABLE_RE.search(expected):
                errors.append(f"{item_id} acceptance result is not measurable")
    shell = _shell_from(repo, description, baseline, outdated, work or {})
    shell["acceptance_criteria"] = obj["acceptance_criteria"]
    _stamp_commands(shell, commands)
    _fill_acceptance(shell, baseline)
    errors.extend(validate_prd(shell, repo, description, baseline))
    return errors


def _stamp_commands(prd: dict[str, Any], commands: list[Any]) -> None:
    by_id = {}
    for item in commands:
        if isinstance(item, dict) and item.get("id"):
            by_id[str(item["id"])] = item
    for key in ("work_items", "upgrade_items", "refactor_items", "docs_items"):
        stamped = []
        for item in prd.get(key) or []:
            if not isinstance(item, dict):
                continue
            incoming = by_id.get(str(item.get("id") or ""))
            if isinstance(incoming, dict):
                if incoming.get("acceptance_command"):
                    item["acceptance_command"] = incoming["acceptance_command"]
                if incoming.get("expected"):
                    item["expected"] = incoming["expected"]
            stamped.append(item)
        prd[key] = stamped


def _fill_acceptance(prd: dict[str, Any], baseline: Any) -> None:
    command = str(getattr(baseline, "cmd", "") or "the test command")
    for key in ("work_items", "upgrade_items", "refactor_items", "docs_items"):
        for item in prd.get(key) or []:
            if isinstance(item, dict):
                item.setdefault("acceptance_command", command)
                item.setdefault("expected", "exit code 0")


def deterministic_prd(
    repo: Path,
    repo_id: str,
    baseline: Any,
    outdated: list[dict[str, Any]] | None = None,
    reason: str = "",
    description: str = "",
) -> dict[str, Any]:
    """A PRD with no model call. Upgrade items copy verified versions from the outdated list."""
    outdated = list(outdated or [])
    cmd = str(getattr(baseline, "cmd", "") or "the test command")
    code = getattr(baseline, "code", None)
    evidence = _baseline_tail(baseline).strip() or "no command output was captured"
    evidence = evidence[:240]
    outdated_text = _outdated_block(outdated)
    diagnosis = f"Baseline `{cmd}` exited {code}. Evidence: {evidence}"
    if outdated_text != "(none)":
        diagnosis += f" Outdated dependencies: {outdated_text.replace(chr(10), ', ')}."
    scope = _source_scope(repo)
    manifest = next((name for name in ("package.json", "requirements.txt", "pyproject.toml") if (repo / name).is_file()), "")
    upgrades = []
    work = []
    for item in outdated[:4]:
        package = str(item.get("package") or "")
        version = str(item.get("version") or "")
        if not package or not version:
            continue
        upgrades.append(
            {
                "id": str(item.get("id") or f"upgrade-{package}"),
                "package": package,
                "version": version,
                "from_version": str(item.get("from_version") or ""),
                "reason": str(item.get("reason") or "minor or patch upgrade reported by the outdated list"),
                "source": "deterministic",
                "acceptance_command": cmd,
                "expected": "exit code 0",
            }
        )
        work.append(
            {
                "id": str(item.get("id") or f"upgrade-{package}"),
                "kind": "upgrade",
                "title": f"Upgrade {package} to {version}",
                "acceptance_command": cmd,
                "expected": "exit code 0",
                "package": package,
                "files": [manifest] if manifest else [],
            }
        )
    nothing = collected_nothing(baseline)
    if nothing:
        # Exit 5 is not a product failure. Do not invent a file to edit.
        pass
    elif code not in (0, None):
        work.insert(
            0,
            {
                "id": "fix-baseline",
                "kind": "repair",
                "title": "Make the baseline command exit 0",
                "acceptance_command": cmd,
                "expected": "exit code 0",
                "files": _repair_targets(repo, scope),
            },
        )
    if not work and nothing:
        work.append(
            {
                "id": "no-tests-collected",
                "kind": "evidence",
                "title": "No tests were collected. Exit 5 is not a failing baseline.",
                "acceptance_command": cmd,
                "expected": f"exit code {code}",
                "files": [],
            }
        )
    elif not work:
        work.append(
            {
                "id": "confirm-baseline",
                "kind": "repair",
                "title": "Confirm the baseline command still exits 0",
                "acceptance_command": cmd,
                "expected": "exit code 0",
                "files": _repair_targets(repo, scope),
            },
        )
    return {
        "problem": f"{repo_id} baseline `{cmd}` exited {code}.",
        "original_intent": intent_quote(repo, description),
        "diagnosis": diagnosis,
        "target_user": "Maintainers of this repository.",
        "scope": scope,
        "non_goals": ["Do not edit tests or lockfiles.", "Do not add features the failing checks do not require."],
        "work_items": work,
        "upgrade_items": upgrades,
        "refactor_items": [],
        "docs_items": [],
        "acceptance_criteria": _acceptance_criteria(nothing),
        "risks": ["Versions come from the ecosystem outdated list and may still fail the test command."],
        "drafted_by": "deterministic",
        "label": "deterministic PRD",
        "fallback_reason": reason,
    }


def _ask_until_accepted(client: Any, repo: Path, repo_id: str, steps: tuple[str, str], prompts, accept, baseline: Any = None) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    for attempt, step in enumerate(steps, start=1):
        prompt = prompts(attempt, errors)
        obj, captured, infra = _call_piece(client, prompt, repo_id, step, accept)
        if isinstance(obj, dict) and not validate_prd(obj, repo, "", baseline):
            return obj, []
        if isinstance(obj, dict) and not accept(obj):
            return obj, []
        errors = captured
        if infra:
            break
    return None, errors


def draft_prd(
    client: Any,
    repo: Path,
    repo_id: str,
    description: str,
    baseline: Any,
    *,
    runner: Any = None,
    run_dir: Path | None = None,
    timeout: int = 20,
) -> dict[str, Any]:
    """Draft a PRD in three small calls. A complete valid object still short-circuits. Disk pieces are reused."""
    outdated = load_outdated(repo, runner, timeout)
    context = _shared_context(repo, repo_id, description, baseline, outdated)
    failures: list[str] = []

    diagnosis = load_piece(run_dir, "prd-diagnosis")
    if diagnosis is not None and _accept_diagnosis(diagnosis, repo, description, baseline):
        diagnosis = None
    if diagnosis is None:
        found, errors = _ask_until_accepted(
            client,
            repo,
            repo_id,
            ("revive_prd_1", "revive_prd_2"),
            lambda attempt, errs: _diagnosis_prompt(context, attempt, errs),
            lambda obj: _accept_diagnosis(obj, repo, description, baseline),
            baseline,
        )
        if isinstance(found, dict) and not validate_prd(found, repo, description, baseline):
            return _model_prd(found)
        if isinstance(found, dict):
            diagnosis = {key: found.get(key) for key in ("original_intent", "diagnosis", "problem", "target_user")}
            save_piece(run_dir, "prd-diagnosis", diagnosis)
        else:
            failures.extend(errors)
            diagnosis = None

    work = load_piece(run_dir, "prd-work")
    if work is not None and _accept_work(work, repo, description, baseline, outdated):
        work = None
    if work is None and diagnosis is not None:
        found, errors = _ask_until_accepted(
            client,
            repo,
            repo_id,
            ("revive_prd_work_1", "revive_prd_work_2"),
            lambda attempt, errs: _work_prompt(context, diagnosis, attempt, errs),
            lambda obj: _accept_work(obj, repo, description, baseline, outdated),
            baseline,
        )
        if isinstance(found, dict) and not validate_prd(found, repo, description, baseline):
            return _model_prd(found)
        if isinstance(found, dict):
            work = {key: found.get(key) for key in ("scope", "non_goals", "risks", "work_items", "upgrade_items", "refactor_items", "docs_items") if key in found}
            save_piece(run_dir, "prd-work", work)
        else:
            failures.extend(errors)
            work = None

    acceptance = load_piece(run_dir, "prd-acceptance")
    work_for_acceptance = work or {}
    if acceptance is not None and _accept_acceptance(acceptance, repo, description, baseline, outdated, work_for_acceptance):
        acceptance = None
    if acceptance is None and work is not None:
        found, errors = _ask_until_accepted(
            client,
            repo,
            repo_id,
            ("revive_prd_acceptance_1", "revive_prd_acceptance_2"),
            lambda attempt, errs: _acceptance_prompt(context, work, baseline, attempt, errs),
            lambda obj: _accept_acceptance(obj, repo, description, baseline, outdated, work),
            baseline,
        )
        if isinstance(found, dict) and not validate_prd(found, repo, description, baseline):
            return _model_prd(found)
        if isinstance(found, dict):
            acceptance = {"acceptance_criteria": found.get("acceptance_criteria"), "items": found.get("items") or []}
            save_piece(run_dir, "prd-acceptance", acceptance)
        else:
            failures.extend(errors)
            acceptance = None

    return _assemble(repo, repo_id, description, baseline, outdated, diagnosis, work, acceptance, failures)


def _model_prd(obj: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(obj)
    cleaned.pop("degraded", None)
    cleaned.pop("degraded_reason", None)
    return cleaned


def _assemble(
    repo: Path,
    repo_id: str,
    description: str,
    baseline: Any,
    outdated: list[dict[str, Any]],
    diagnosis: dict[str, Any] | None,
    work: dict[str, Any] | None,
    acceptance: dict[str, Any] | None,
    failures: list[str],
) -> dict[str, Any]:
    prd = deterministic_prd(repo, repo_id, baseline, outdated, reason="; ".join(dict.fromkeys(failures)), description=description)
    sources = {"diagnosis": "deterministic", "work": "deterministic", "acceptance": "deterministic"}
    if diagnosis:
        for key in ("original_intent", "diagnosis", "problem", "target_user"):
            if key in diagnosis and diagnosis.get(key):
                prd[key] = diagnosis[key]
        sources["diagnosis"] = "model"
    if work:
        for key in ("scope", "non_goals", "risks", "work_items", "upgrade_items", "refactor_items", "docs_items"):
            if key in work:
                prd[key] = work[key]
        sources["work"] = "model"
    if acceptance and isinstance(acceptance.get("acceptance_criteria"), list):
        prd["acceptance_criteria"] = acceptance["acceptance_criteria"]
        _stamp_commands(prd, acceptance.get("items") if isinstance(acceptance.get("items"), list) else [])
        sources["acceptance"] = "model"
    _fill_acceptance(prd, baseline)
    for extra in ("degraded", "degraded_reason"):
        prd.pop(extra, None)
    if validate_prd(prd, repo, description, baseline):
        prd = deterministic_prd(repo, repo_id, baseline, outdated, reason="; ".join(dict.fromkeys(failures)) or "assembled pieces failed validation", description=description)
        sources = {"diagnosis": "deterministic", "work": "deterministic", "acceptance": "deterministic"}
    used = set(sources.values())
    if used == {"model"}:
        prd.pop("drafted_by", None)
        prd.pop("label", None)
        prd.pop("fallback_reason", None)
        return prd
    if used == {"deterministic"}:
        prd["drafted_by"] = "deterministic"
        prd["label"] = "deterministic PRD"
    else:
        prd["drafted_by"] = "mixed"
        prd["label"] = "deterministic PRD"
        prd["deterministic_pieces"] = [name for name, source in sources.items() if source == "deterministic"]
    if not validate_prd(prd, repo, description, baseline):
        return prd
    return minimal_prd(repo, repo_id, baseline, "; ".join(dict.fromkeys(failures)) or "model draft failed validation")


def render_prd_markdown(prd: dict[str, Any] | None, state: str = "", reason: str = "") -> str:
    if not prd:
        return f"# Revival PRD\n\nNo PRD was drafted.\n\nState: {state or 'unknown'}\n\n{reason}\n"
    lines = ["# Revival PRD", ""]
    if prd.get("drafted_by") == "deterministic":
        lines.append("> **deterministic PRD.** Built from the baseline output and the outdated-dependency list, without a model call.")
        if prd.get("fallback_reason"):
            lines.append(f"> {prd.get('fallback_reason')}")
        lines.append("")
    elif prd.get("drafted_by") == "mixed":
        missing = ", ".join(prd.get("deterministic_pieces") or [])
        lines.append(f"> **deterministic PRD** pieces ({missing}) filled in after the model call failed. Kept model pieces were not rewritten.")
        lines.append("")
    if prd.get("degraded"):
        lines.append(f"> **DEGRADED PRD.** {prd.get('degraded_reason') or 'The model draft failed validation.'}")
        lines.append("")
    lines.append("## Original intent")
    lines.append(str(prd.get("original_intent") or ""))
    lines.append("")
    lines.append("## Diagnosis")
    lines.append(str(prd.get("diagnosis") or ""))
    lines.append("")
    lines.append("## Problem")
    lines.append(str(prd.get("problem") or ""))
    lines.append("")
    lines.append("## Work list")
    lines.append("| id | kind | acceptance command | expected |")
    lines.append("| --- | --- | --- | --- |")
    for item in prd.get("work_items") or []:
        if isinstance(item, dict):
            lines.append(
                f"| {item.get('id')} | {item.get('kind') or ''} | `{item.get('acceptance_command') or ''}` | {item.get('expected') or ''} |"
            )
    if not prd.get("work_items"):
        lines.append("| | | | |")
    lines.append("")
    lines.append("## Target user")
    lines.append(str(prd.get("target_user") or ""))
    lines.append("")
    lines.append("## Scope")
    for entry in prd.get("scope") or []:
        lines.append(f"- {entry}")
    lines.append("")
    lines.append("## Non-goals")
    for entry in prd.get("non_goals") or []:
        lines.append(f"- {entry}")
    lines.append("")
    lines.append("## Upgrades")
    upgrades = prd.get("upgrade_items") or []
    if not upgrades:
        lines.append("None.")
    for item in upgrades:
        if isinstance(item, dict):
            lines.append(f"- `{item.get('id')}` {item.get('package') or ''} {item.get('version') or ''} — {item.get('reason') or ''}".rstrip())
    lines.append("")
    lines.append("## Refactors")
    refactors = prd.get("refactor_items") or []
    if not refactors:
        lines.append("None.")
    for item in refactors:
        if isinstance(item, dict):
            files = ", ".join(item.get("files") or [])
            lines.append(f"- `{item.get('id')}` {item.get('title') or ''} ({files})")
    lines.append("")
    lines.append("## Docs")
    docs = prd.get("docs_items") or []
    if not docs:
        lines.append("None.")
    for item in docs:
        if isinstance(item, dict):
            lines.append(f"- `{item.get('id')}` {item.get('title') or ''}")
    lines.append("")
    lines.append("## Acceptance criteria")
    lines.append("| id | check | statement |")
    lines.append("| --- | --- | --- |")
    for item in prd.get("acceptance_criteria") or []:
        if isinstance(item, dict):
            lines.append(f"| {item.get('id')} | {item.get('check_id')} | {item.get('statement')} |")
    lines.append("")
    lines.append("## Risks")
    for entry in prd.get("risks") or []:
        lines.append(f"- {entry}")
    if not prd.get("risks"):
        lines.append("None.")
    lines.append("")
    return "\n".join(lines)


def write_reports(directory: Path, result: Any, repo: str, run_id: str) -> None:
    """Write PRD.md, quality.json, and evidence.html for one revival run."""
    import json

    directory.mkdir(parents=True, exist_ok=True)
    prd = getattr(result, "prd", None)
    state = getattr(result, "state", "")
    reason = getattr(result, "reason", "")
    markdown = render_prd_markdown(prd, state=state, reason=reason)
    (directory / "PRD.md").write_text(markdown, encoding="utf-8")
    (directory / "prd.md").write_text(markdown, encoding="utf-8")
    payload = result.as_dict() if hasattr(result, "as_dict") else dict(result)
    payload = dict(payload)
    payload.update({"repo": repo, "run_id": run_id, "state": state, "reason": reason, "prd_href": "prd.md"})
    quality = payload.get("quality") or {}
    (directory / "quality.json").write_text(json.dumps(quality, indent=2, ensure_ascii=False), encoding="utf-8")
    (directory / "verification.json").write_text(
        json.dumps(payload.get("verification") or {"checks": [], "resurrection_allowed": False, "blocked_reason": "verification was not run"}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (directory / "evidence.html").write_text(render_evidence_html(payload), encoding="utf-8")
