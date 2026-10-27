"""Rescue plan: the PRD gate 2 writes for every CANDIDATE, whether or not it has tests.

Gate 1 already holds the evidence: the candidate's own text, the alternatives it checked, the
ones it must beat and why each did not block, demand signals, and the scorer's assessments. The
plan is written from that evidence alone, so it needs no clone and no test command:

- positioning: the user, the job, and the gap the must-beat alternatives leave open (the VRIO
  "inimitable" question);
- three stages: REVIVE (make the stated job work again and verifiable), OPTIMISE (refactor,
  dependencies, tests, docs), UPGRADE (close the gap against must-beat);
- organised: whether gate 2's execution loop could verify the work (a test command is visible in
  the repository root), and if not, a REVIVE item to add one.

Nemotron drafts the plan (the step is ``rescue_plan``). The code verifies it: every work item
must cite evidence ids that exist and carry a quote found verbatim in a cited text. Items that
fail are kept under ``rejected_items`` with the reason and never shown as plan. When the model
is unavailable or nothing survives, a deterministic plan is built from the same evidence and
labelled so. Every step is a line in the hash-chained trace.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

from app.agents.relevance import _complete_json, is_client_configured, quote_in_text
from app.prompts import load_prompt

STAGES = ("REVIVE", "OPTIMISE", "UPGRADE")
MAX_ITEMS_PER_STAGE = 4
EVIDENCE_TEXT_LIMIT = 900

MANIFESTS = {
    "package.json": "npm",
    "pyproject.toml": "python",
    "setup.py": "python",
    "requirements.txt": "python",
    "go.mod": "go",
    "Cargo.toml": "rust",
    "pom.xml": "maven",
    "build.gradle": "gradle",
    "Gemfile": "ruby",
    "composer.json": "php",
    "pubspec.yaml": "dart",
}
TEST_NAMES = re.compile(r"^(tests?|spec|__tests__|test_.*\.py|.*_test\.go|pytest\.ini|tox\.ini|jest\.config\..*|vitest\.config\..*)$", re.I)


# ---------------------------------------------------------------- evidence


def _criterion_text(name: str, crit: Any) -> str:
    if isinstance(crit, dict):
        return f"{name}: score {crit.get('score')}. {crit.get('rationale') or ''}".strip()
    return ""


def evidence_index(row: dict[str, Any], tree: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Every entry the plan may cite, keyed by id. Kinds: candidate, prior, demand, must_beat,
    assessment (the scorer's rationale, itself model-assisted) and repo (the root listing)."""
    index: dict[str, dict[str, Any]] = {}
    for item in row.get("evidence") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        ident = str(item["id"])
        index[ident] = {
            "id": ident,
            "kind": ident.split(":", 1)[0] if ":" in ident else "evidence",
            "url": str(item.get("url") or ""),
            "text": str(item.get("text") or ""),
            "content_hash": str(item.get("content_hash") or ""),
        }
    for beat in row.get("must_beat") or []:
        if not isinstance(beat, dict):
            continue
        alt = str(beat.get("alternative") or "").strip()
        if not alt:
            continue
        text = " ".join(part for part in (str(beat.get("reason") or ""), str(beat.get("quote") or "")) if part)
        index[f"must_beat:{alt}"] = {
            "id": f"must_beat:{alt}", "kind": "must_beat", "url": alt if alt.startswith("http") else "",
            "text": f"{alt} (overlap {beat.get('overlap') or 'unknown'}): {text}", "content_hash": "",
        }
    for name, crit in (row.get("criteria") or {}).items():
        text = _criterion_text(name, crit)
        if text:
            index[f"assessment:{name}"] = {"id": f"assessment:{name}", "kind": "assessment", "url": "", "text": text, "content_hash": ""}
    if tree:
        index["repo:tree"] = tree
    return index


def repo_tree(repo_id: str, getter: Callable[[str], Any] | None = None) -> dict[str, Any] | None:
    """The repository root listing from the GitHub API, as one evidence entry. None on any failure."""
    url = f"https://api.github.com/repos/{repo_id}/contents"
    try:
        if getter is None:
            import os

            import httpx

            from app.github_auth import token_usable

            token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
            headers = {"Accept": "application/vnd.github+json"}
            if token_usable(token):
                headers["Authorization"] = f"Bearer {token}"
            resp = httpx.get(url, headers=headers, timeout=20.0)
            if resp.status_code != 200:
                return None
            data = resp.json()
        else:
            data = getter(url)
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    names = sorted(str(entry.get("name") or "") for entry in data if isinstance(entry, dict) and entry.get("name"))
    if not names:
        return None
    return {
        "id": "repo:tree", "kind": "repo", "url": f"https://github.com/{repo_id}",
        "text": "repository root: " + ", ".join(names), "content_hash": "", "names": names,
    }


def organised(tree: dict[str, Any] | None) -> dict[str, Any]:
    """Could gate 2's execution loop verify this revival? Read from the root listing only."""
    if not tree:
        return {"status": "unknown", "detail": "The repository root could not be listed, so a test command was not checked.",
                "manifests": [], "test_paths": []}
    names = list(tree.get("names") or [])
    manifests = [n for n in names if n in MANIFESTS]
    tests = [n for n in names if TEST_NAMES.match(n)]
    if tests:
        status, detail = "verifiable", "A test path is visible in the root, so gate 2's execution loop can verify the REVIVE stage."
    elif manifests:
        status, detail = "needs_test_command", "A manifest is present but no test path is visible in the root: gate 2 would stop at NEEDS_REVIEW until a test command exists."
    else:
        status, detail = "needs_test_command", "No manifest and no test path in the root: gate 2 would stop at NEEDS_REVIEW until a build and test command exist."
    return {"status": status, "detail": detail, "manifests": manifests, "test_paths": tests}


# ---------------------------------------------------------------- verification


def _clean_item(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    out = {key: str(item.get(key) or "").strip() for key in ("title", "why", "acceptance", "quote")}
    ev = item.get("evidence")
    out["evidence"] = [str(e).strip() for e in (ev if isinstance(ev, list) else [ev] if ev else []) if str(e).strip()]
    return out


def check_item(item: dict[str, Any], index: dict[str, dict[str, Any]]) -> str:
    """Empty string when the item stands; otherwise why it was dropped."""
    if not item.get("title"):
        return "no title"
    if not item["evidence"]:
        return "cites no evidence"
    unknown = [e for e in item["evidence"] if e not in index]
    if unknown:
        return "cites unknown evidence " + ", ".join(unknown[:3])
    quote = item.get("quote") or ""
    if not quote or quote.casefold() == "unknown":
        return "no quote"
    if not any(quote_in_text(quote, index[e]["text"]) for e in item["evidence"]):
        return "quote not found verbatim in the cited evidence"
    return ""


def _upgrade_targets_a_gap(item: dict[str, Any]) -> bool:
    return any(e.startswith("must_beat:") or e == "assessment:differentiation" for e in item["evidence"])


def verify_plan(obj: dict[str, Any], index: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rejected: list[dict[str, Any]] = []
    stages: list[dict[str, Any]] = []
    raw_stages = {str(s.get("stage") or "").upper(): s for s in (obj.get("stages") or []) if isinstance(s, dict)}
    for stage in STAGES:
        kept = []
        for raw in (raw_stages.get(stage, {}).get("items") or [])[: MAX_ITEMS_PER_STAGE * 2]:
            item = _clean_item(raw)
            if item is None:
                continue
            why = check_item(item, index)
            if not why and stage == "UPGRADE" and not _upgrade_targets_a_gap(item):
                why = "UPGRADE item cites no must-beat gap or differentiation assessment"
            if why:
                rejected.append({"stage": stage, "title": item.get("title") or "", "reason": why})
            elif len(kept) < MAX_ITEMS_PER_STAGE:
                kept.append(item)
        stages.append({"stage": stage, "items": kept})
    pos = obj.get("positioning") if isinstance(obj.get("positioning"), dict) else {}
    pos_ev = [e for e in (pos.get("evidence") or []) if isinstance(e, str) and e in index]
    positioning = {key: str(pos.get(key) or "unknown").strip() for key in ("target_user", "job", "gap")}
    positioning["evidence"] = pos_ev
    if not pos_ev:
        positioning = {"target_user": "unknown", "job": "unknown", "gap": "unknown", "evidence": []}
        rejected.append({"stage": "POSITIONING", "title": "positioning", "reason": "cites no known evidence"})
    beats = []
    for beat in obj.get("must_beat") or []:
        if not isinstance(beat, dict):
            continue
        ev = [e for e in (beat.get("evidence") or []) if isinstance(e, str) and e in index]
        if ev:
            beats.append({"alternative": str(beat.get("alternative") or ""), "how_to_win": str(beat.get("how_to_win") or ""), "evidence": ev})
        else:
            rejected.append({"stage": "MUST_BEAT", "title": str(beat.get("alternative") or ""), "reason": "cites no known evidence"})
    risks = [str(r).strip() for r in (obj.get("risks") or []) if str(r).strip() and str(r).strip().casefold() != "unknown"][:6]
    return {"positioning": positioning, "stages": stages, "must_beat": beats, "risks": risks}, rejected


# ---------------------------------------------------------------- deterministic plan


def _first_sentence(text: str, limit: int = 160) -> str:
    one = " ".join(str(text or "").split())
    cut = re.split(r"(?<=[.!?])\s", one, maxsplit=1)[0]
    return cut[:limit].rstrip()


def deterministic_plan(row: dict[str, Any], index: dict[str, dict[str, Any]]) -> dict[str, Any]:
    repo_id = str(row.get("repo_id") or "")
    cand = index.get(f"candidate:{repo_id}") or next((v for v in index.values() if v["kind"] == "candidate"), None)
    revive: list[dict[str, Any]] = []
    optimise: list[dict[str, Any]] = []
    upgrade: list[dict[str, Any]] = []
    if cand and cand["text"]:
        quote = _first_sentence(cand["text"])
        revive.append({"title": "Make the stated job work again on current dependencies", "why": "This is the job gate 1 found worth rescuing.",
                       "acceptance": "The README's main workflow runs end to end on a clean machine.", "evidence": [cand["id"]], "quote": quote})
    feas = index.get("assessment:feasibility")
    if feas:
        optimise.append({"title": "Refactor around the existing working core", "why": "The scorer's feasibility assessment of the codebase.",
                         "acceptance": "The core paths named in the assessment keep working after the refactor.", "evidence": [feas["id"]],
                         "quote": _first_sentence(feas["text"].split(". ", 1)[-1])})
    for key, beat in index.items():
        if beat["kind"] != "must_beat" or len(upgrade) >= MAX_ITEMS_PER_STAGE:
            continue
        alt = key.split(":", 1)[1]
        upgrade.append({"title": f"Keep or widen the gap against {alt}", "why": "Recorded by gate 1 as an alternative that did not block.",
                        "acceptance": f"A side-by-side check shows the revival still does what {alt} does not.", "evidence": [key],
                        "quote": beat["text"].split(": ", 1)[-1][:160] or alt})
    diff = index.get("assessment:differentiation")
    if not upgrade and diff:
        upgrade.append({"title": "Keep the capabilities the checked alternatives lack", "why": "Gate 1's differentiation assessment names them.",
                        "acceptance": "Each capability named in the assessment works in the revival and is shown in its README.",
                        "evidence": [diff["id"]], "quote": _first_sentence(diff["text"].split(". ", 1)[-1], 220)})
    plan = {
        "positioning": {"target_user": "unknown", "job": _first_sentence(cand["text"]) if cand else "unknown",
                        "gap": "the reasons the must-beat alternatives did not block" if upgrade else "unknown",
                        "evidence": [cand["id"]] if cand else []},
        "stages": [{"stage": "REVIVE", "items": revive}, {"stage": "OPTIMISE", "items": optimise}, {"stage": "UPGRADE", "items": upgrade}],
        "must_beat": [{"alternative": k.split(":", 1)[1], "how_to_win": "keep the capability its recorded reason says it lacks", "evidence": [k]}
                      for k, v in index.items() if v["kind"] == "must_beat"],
        "risks": [],
    }
    # Every deterministic item is checked by the same guard as a model item.
    verified, _ = verify_plan(plan, index)
    return verified


def _ensure_test_item(plan: dict[str, Any], org: dict[str, Any], index: dict[str, dict[str, Any]]) -> None:
    """When gate 2 could not verify, REVIVE starts with adding a test command (cites the root listing)."""
    if org["status"] != "needs_test_command" or "repo:tree" not in index:
        return
    revive = next(s for s in plan["stages"] if s["stage"] == "REVIVE")
    if any(re.search(r"\btests?\b", f"{i['title']} {i['acceptance']}", re.I) for i in revive["items"]):
        return
    names = index["repo:tree"].get("names") or []
    anchor = (org["manifests"] or names or ["repository root"])[0]
    revive["items"].insert(0, {
        "title": "Add a runnable test command for the stated job",
        "why": "Without one, gate 2's execution loop cannot verify any revival and stops at NEEDS_REVIEW.",
        "acceptance": "One command runs the tests and exits 0 on a clean checkout.",
        "evidence": ["repo:tree"], "quote": anchor, "added_by": "code",
    })
    del revive["items"][MAX_ITEMS_PER_STAGE:]


# ---------------------------------------------------------------- build


def _prompt(row: dict[str, Any], index: dict[str, dict[str, Any]], org: dict[str, Any]) -> str:
    lines = [f"Candidate repository: {row.get('repo_id')}", f"Gate 1 verdict: {row.get('verdict')}",
             f"Organised (can gate 2 verify?): {org['status']} — {org['detail']}", "", "Evidence (cite these ids only):"]
    for entry in index.values():
        lines.append(f"- id: {entry['id']}\n  kind: {entry['kind']}\n  text: {entry['text'][:EVIDENCE_TEXT_LIMIT]}")
    lines.append("\nWrite the rescue plan as strict JSON in the schema.")
    return "\n".join(lines)


def build_plan(
    row: dict[str, Any],
    *,
    client: Any | None = None,
    trace: Any | None = None,
    tree: dict[str, Any] | None = None,
    use_llm: bool = True,
) -> dict[str, Any]:
    repo_id = str(row.get("repo_id") or "")
    if str(row.get("verdict") or "") != "CANDIDATE":
        raise ValueError(f"a rescue plan is written only for a CANDIDATE; {repo_id} is {row.get('verdict')}")
    index = evidence_index(row, tree)
    org = organised(tree)
    if trace is not None:
        trace.event(agent_name="rescue_planner", action="RESCUE_PLAN_INPUT", input_summary={"repo_id": repo_id},
                    output_summary={"evidence": [{"id": e["id"], "content_hash": e["content_hash"]} for e in index.values()],
                                    "organised": org["status"]}, summary_limit=4000)
    drafted_by, requested, served, rejected = "deterministic", "", "", []
    plan: dict[str, Any] | None = None
    if use_llm and client is not None and is_client_configured(client):
        if getattr(client, "deep_model", None):
            client.deep_steps = tuple(sorted(set(client.deep_steps or ()) | {"rescue_plan"}))
        if trace is not None and getattr(client, "trace", None) is None:
            client.trace = trace
        prompt = load_prompt("rescue_plan")
        try:
            obj, _text, requested, served = _complete_json(
                client, _prompt(row, index, org), prompt.text, repo_id, "rescue_plan",
                validate=lambda o: isinstance(o.get("stages"), list),
            )
        except Exception as exc:  # a model outage gives the deterministic plan, never a guess
            obj = None
            rejected.append({"stage": "MODEL", "title": "rescue_plan call", "reason": f"{type(exc).__name__}: {str(exc)[:160]}"})
        if obj is not None:
            candidate_plan, rejected_items = verify_plan(obj, index)
            rejected += rejected_items
            if any(s["items"] for s in candidate_plan["stages"]):
                plan, drafted_by = candidate_plan, "model"
    if plan is None:
        plan = deterministic_plan(row, index)
        drafted_by = "deterministic"
    _ensure_test_item(plan, org, index)
    result = {
        "repo_id": repo_id,
        "gate1": {"verdict": row.get("verdict"), "total": row.get("total"), "flags": row.get("flags") or []},
        "drafted_by": drafted_by,
        "requested_model": requested,
        "served_model": served,
        **plan,
        "organised": org,
        "rejected_items": rejected,
        "evidence": [{k: e.get(k, "") for k in ("id", "kind", "url", "content_hash")} for e in index.values()],
    }
    if trace is not None:
        trace.event(agent_name="rescue_planner", action="RESCUE_PLAN", input_summary={"repo_id": repo_id},
                    output_summary={"drafted_by": drafted_by, "served_model": served,
                                    "items": {s["stage"]: len(s["items"]) for s in plan["stages"]},
                                    "rejected": len(rejected), "organised": org["status"]}, summary_limit=2000)
    return result


# ---------------------------------------------------------------- render


def render_markdown(plan: dict[str, Any], *, run_id: str = "", run_hash: str = "") -> str:
    by = {"model": f"Nemotron (served `{plan.get('served_model') or 'unknown'}`), verified by code", "deterministic": "deterministic, from the same evidence"}
    p = plan["positioning"]
    out = [
        f"# Rescue plan: {plan['repo_id']}", "",
        f"Gate 1: **{plan['gate1']['verdict']}**. Drafted by: {by.get(plan['drafted_by'], plan['drafted_by'])}.",
    ]
    if run_id:
        out.append(f"Trace: `logs/trace-{run_id}.jsonl`" + (f", run hash `{run_hash}`." if run_hash else "."))
    out += ["", "Every item below cites evidence that gate 1 recorded, and its quote was found verbatim in that evidence. "
            "Items that failed the check are listed at the end and are not part of the plan.", "",
            "## Positioning", "", f"- **User:** {p['target_user']}", f"- **Job:** {p['job']}", f"- **Gap to own:** {p['gap']}",
            f"- Evidence: {', '.join('`' + e + '`' for e in p['evidence']) or 'none'}", "",
            "## Can gate 2 verify it?", "", f"**{plan['organised']['status']}.** {plan['organised']['detail']}", ""]
    titles = {"REVIVE": "Stage 1 — REVIVE", "OPTIMISE": "Stage 2 — OPTIMISE", "UPGRADE": "Stage 3 — UPGRADE"}
    for stage in plan["stages"]:
        out += [f"## {titles[stage['stage']]}", ""]
        if not stage["items"]:
            out += ["No verified item.", ""]
        for n, item in enumerate(stage["items"], start=1):
            tag = " *(added by code)*" if item.get("added_by") == "code" else ""
            out += [f"### {stage['stage'][0]}{n}. {item['title']}{tag}", "",
                    f"- **Why:** {item['why'] or 'unknown'}", f"- **Acceptance:** {item['acceptance'] or 'unknown'}",
                    f"- **Evidence:** {', '.join('`' + e + '`' for e in item['evidence'])} — “{item['quote']}”", ""]
    out += ["## Must beat", ""]
    if not plan["must_beat"]:
        out += ["Gate 1 recorded no alternative to beat.", ""]
    for beat in plan["must_beat"]:
        out.append(f"- **{beat['alternative']}**: {beat['how_to_win'] or 'unknown'} ({', '.join('`' + e + '`' for e in beat['evidence'])})")
    out += ["", "## Risks", ""] + ([f"- {r}" for r in plan["risks"]] or ["- None stated by the evidence."])
    out += ["", "## Evidence", "", "| id | kind | source | content hash |", "| --- | --- | --- | --- |"]
    for e in plan["evidence"]:
        out.append(f"| `{e['id']}` | {e['kind']} | {e['url'] or '—'} | {(e['content_hash'] or '—')[:16]} |")
    if plan["rejected_items"]:
        out += ["", "## Dropped by the check", ""] + [f"- {r['stage']}: {r['title'] or '(untitled)'} — {r['reason']}" for r in plan["rejected_items"]]
    return "\n".join(out) + "\n"


def to_json(plan: dict[str, Any]) -> str:
    return json.dumps(plan, indent=2, ensure_ascii=False) + "\n"
