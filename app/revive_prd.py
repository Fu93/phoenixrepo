"""Structured revival PRD.

The model drafts from the repository's own README, manifest, file tree, and baseline output.
Validation is deterministic. A failed draft is retried once, then replaced with a minimal PRD.
The fallback is recorded on the PRD itself so the degradation is never silent.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.quality_gate import CHECK_IDS, render_evidence_html

MAX_FILE_BYTES = 12_000
TREE_LIMIT = 80
PRD_SYSTEM = (
    "You are the architect of a repository revival. Use only the files, file tree, and command output you are given. "
    "Do not invent packages, versions, or paths. Reply with one strict JSON object and nothing else."
)
MEASURABLE_RE = re.compile(
    r"(\d|exit code|exits 0|must pass|\bpasses\b|no high|no critical|\bzero\b|at least|at most|does not edit tests|do not edit tests)",
    re.IGNORECASE,
)
PATH_SUFFIXES = (".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".md", ".toml", ".json", ".yml", ".yaml", ".txt", ".cfg", ".ini")
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


def validate_prd(obj: Any, repo: Path) -> list[str]:
    """Return human-readable errors. An empty list means the PRD is usable."""
    errors: list[str] = []
    if not isinstance(obj, dict):
        return ["PRD is not a JSON object"]
    for key in REQUIRED_KEYS:
        if key not in obj:
            errors.append(f"missing {key}")
    if "problem" in obj and not _nonempty_str(obj.get("problem")):
        errors.append("problem must be a non-empty string")
    if "target_user" in obj and not _nonempty_str(obj.get("target_user")):
        errors.append("target_user must be a non-empty string")
    for key in ("scope", "non_goals", "risks", "upgrade_items", "refactor_items", "docs_items", "acceptance_criteria"):
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
        if isinstance(items, list) and not all(isinstance(item, str) and item.strip() for item in items):
            errors.append(f"{label} entries must be non-empty strings")
    upgrades = obj.get("upgrade_items") if isinstance(obj.get("upgrade_items"), list) else []
    for index, item in enumerate(upgrades):
        if not isinstance(item, dict):
            errors.append(f"upgrade_items[{index}] is not an object")
            continue
        if not _nonempty_str(item.get("id")):
            errors.append(f"upgrade_items[{index}] missing id")
        package = str(item.get("package") or "").strip()
        version = str(item.get("version") or item.get("to_version") or "").strip()
        reason = str(item.get("reason") or "").strip()
        concrete_pin = bool(package and version)
        concrete_reason = len(reason) >= 12
        if not concrete_pin and not concrete_reason:
            errors.append(f"upgrade_items[{index}] needs a package and version, or a concrete reason")
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
        for rel in files:
            if not isinstance(rel, str) or not rel.strip():
                errors.append(f"refactor_items[{index}] has an empty file")
                continue
            if not (repo / rel.replace("\\", "/")).is_file():
                errors.append(f"refactor file does not exist: {rel}")
    docs = obj.get("docs_items") if isinstance(obj.get("docs_items"), list) else []
    for index, item in enumerate(docs):
        if not isinstance(item, dict) or not _nonempty_str(item.get("id")) or not _nonempty_str(item.get("title")):
            errors.append(f"docs_items[{index}] needs an id and a title")
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


def _source_scope(repo: Path) -> list[str]:
    from app.revive_loop import is_protected

    scope = [rel for rel in file_tree(repo, limit=40) if not is_protected(rel)]
    return scope[:12] or ["repository source"]


def minimal_prd(repo: Path, repo_id: str, baseline: Any, reason: str) -> dict[str, Any]:
    """Last-resort PRD. It has no upgrade or refactor work, so a green baseline stays NOTHING_TO_FIX."""
    cmd = getattr(baseline, "cmd", "") or "the test command"
    code = getattr(baseline, "code", None)
    prd = {
        "problem": f"{repo_id} baseline `{cmd}` exited {code}.",
        "target_user": "Maintainers of this repository.",
        "scope": _source_scope(repo),
        "non_goals": ["Do not edit tests or lockfiles.", "Do not add features the failing checks do not require."],
        "upgrade_items": [],
        "refactor_items": [],
        "docs_items": [],
        "acceptance_criteria": [
            {"id": "ac-tests", "statement": "The repository test command exits 0.", "check_id": "tests"},
            {"id": "ac-diff", "statement": "The diff touches at most 8 files and does not edit tests.", "check_id": "diff_scope"},
            {"id": "ac-audit", "statement": "High and critical audit findings stay at or below the baseline count of 0 new findings.", "check_id": "audit"},
        ],
        "risks": ["This PRD is a fallback because the model draft failed validation."],
        "degraded": True,
        "degraded_reason": reason,
    }
    return prd


def _prompt(repo_id: str, description: str, baseline: Any, files: dict[str, str], tree: list[str], attempt: int, errors: list[str]) -> str:
    from app.revive_loop import tail

    file_block = "\n\n".join(f"=== {name} ===\n{body}" for name, body in files.items())
    error_block = "\n".join(f"- {error}" for error in errors) if errors else "(none)"
    output = tail(str(getattr(baseline, "output", "") or ""))
    return (
        f"Repository: {repo_id}\nDescription: {description or '(none)'}\n"
        f"PRD attempt {attempt} of 2.\n\n"
        f"File tree:\n" + "\n".join(tree) + "\n\n"
        f"Baseline command: {getattr(baseline, 'cmd', '')}\nExit code: {getattr(baseline, 'code', '')}\n"
        f"Output (tail):\n{output}\n\n"
        f"Files:\n{file_block or '(none)'}\n\n"
        f"Validation errors from the previous attempt:\n{error_block}\n\n"
        "Write a revival PRD. Schema: "
        '{"problem": str, "target_user": str, "scope": [str], "non_goals": [str], '
        '"upgrade_items": [{"id": str, "package": str, "version": str, "reason": str}], '
        '"refactor_items": [{"id": str, "title": str, "files": [str], "reason": str}], '
        '"docs_items": [{"id": str, "title": str, "files": [str]}], '
        '"acceptance_criteria": [{"id": str, "statement": str, "check_id": str}], "risks": [str]}. '
        "scope and refactor files must be real paths from the tree. "
        "Each upgrade item needs a package plus version, or a concrete reason. "
        "At most 4 upgrade_items and 4 refactor_items. "
        "acceptance_criteria needs at least 3 items. Each statement must be measurable "
        "(include a number, an exit code, or an explicit pass/fail bound) and check_id must be one of "
        + ", ".join(CHECK_IDS)
        + ". Do not invent features."
    )


def draft_prd(client: Any, repo: Path, repo_id: str, description: str, baseline: Any) -> dict[str, Any]:
    """Draft a PRD. Always returns a dict. Sets degraded when the model draft could not be used."""
    files = supporting_files(repo)
    tree = file_tree(repo)
    errors: list[str] = []
    for attempt in (1, 2):
        prompt = _prompt(repo_id, description, baseline, files, tree, attempt, errors)
        captured: list[str] = []

        def validate(obj: dict[str, Any]) -> bool:
            captured.clear()
            captured.extend(validate_prd(obj, repo))
            return not captured

        obj: Any = None
        try:
            obj, *_ = client.complete_json(prompt, PRD_SYSTEM, repo_id=repo_id, step=f"revive_prd_{attempt}", attempts=1, validate=validate)
        except Exception as exc:  # noqa: BLE001 — a failed call must not become a pass
            captured = [f"llm call failed: {type(exc).__name__}"]
            obj = None
        if obj is not None and not captured:
            if "degraded" in obj:
                obj = dict(obj)
                obj.pop("degraded", None)
                obj.pop("degraded_reason", None)
            return obj
        if captured:
            errors = captured
        elif not errors:
            errors = ["model returned no JSON object"]
        else:
            errors = errors + ["model returned no JSON object"]
    reason = "; ".join(dict.fromkeys(errors)) or "model draft failed validation"
    return minimal_prd(repo, repo_id, baseline, reason)


def render_prd_markdown(prd: dict[str, Any] | None, state: str = "", reason: str = "") -> str:
    if not prd:
        return f"# Revival PRD\n\nNo PRD was drafted.\n\nState: {state or 'unknown'}\n\n{reason}\n"
    lines = ["# Revival PRD", ""]
    if prd.get("degraded"):
        lines.append(f"> **DEGRADED PRD.** {prd.get('degraded_reason') or 'The model draft failed validation.'}")
        lines.append("")
    lines.append("## Problem")
    lines.append(str(prd.get("problem") or ""))
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
    (directory / "PRD.md").write_text(render_prd_markdown(prd, state=state, reason=reason), encoding="utf-8")
    payload = result.as_dict() if hasattr(result, "as_dict") else dict(result)
    payload = dict(payload)
    payload.update({"repo": repo, "run_id": run_id, "state": state, "reason": reason})
    quality = payload.get("quality") or {}
    (directory / "quality.json").write_text(json.dumps(quality, indent=2, ensure_ascii=False), encoding="utf-8")
    (directory / "evidence.html").write_text(render_evidence_html(payload), encoding="utf-8")
