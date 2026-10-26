#!/usr/bin/env python3
"""Render one-page HTML evidence reports from a scoring run's trace and results.

Each report shows the verdict, the problem the repository claims to solve (with the verbatim
quotes the guard verified), the existing products found (with their verbatim quotes), the
search coverage, and the model and trace hash that produced it. Everything comes from the
sealed trace events and the results JSON. Nothing is written by hand.
"""
from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

VERDICT_STYLE = {
    "CANDIDATE": ("#0a7d33", "Worth reviving"),
    "ALREADY_EXISTS": ("#b42318", "Already solved"),
    "NO_GO": ("#667085", "No-go"),
    "NEEDS_REVIEW": ("#b54708", "Human review needed"),
    "UNJUDGED": ("#475467", "Not judged (infrastructure failure)"),
    "NEEDS_PRIOR_ART": ("#b54708", "Prior art missing"),
    "RESURRECTED": ("#0a7d33", "Resurrected"),
}


def _loads(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def load_events(log_dir: Path, run_ids: list[str] | None = None, prefix: str | None = None) -> list[dict[str, Any]]:
    files: list[Path] = []
    for rid in run_ids or []:
        f = log_dir / f"trace-{rid}.jsonl"
        if f.exists():
            files.append(f)
    if prefix:
        files += [f for f in log_dir.glob(f"trace-{prefix}*.jsonl") if f not in files]
    files.sort(key=lambda f: f.stat().st_mtime)
    events: list[dict[str, Any]] = []
    for f in files:
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            e["input_summary"] = _loads(e.get("input_summary"))
            e["output_summary"] = _loads(e.get("output_summary"))
            e["_file"] = f.name
            events.append(e)
    return events


def events_for_repo(events: list[dict[str, Any]], repo_id: str) -> dict[str, Any]:
    """Latest event of each kind for one repository, plus the seal hash of the file that holds them."""
    out: dict[str, Any] = {"quotes_rejected": []}
    for e in events:
        inp = e.get("input_summary")
        if not isinstance(inp, dict) or inp.get("repo_id") != repo_id:
            continue
        act = e.get("action")
        o = e.get("output_summary")
        if act == "PROBLEM_STATEMENT":
            out["problem"] = o
        elif act == "QUERY_GENERATION" and not inp.get("retry"):
            out["queries"] = o.get("queries") if isinstance(o, dict) else None
        elif act == "PRIOR_ART_SEARCH" and not inp.get("retry"):
            out["search"] = o
        elif act == "ALTERNATIVES_VERIFIED":
            out["alternatives"] = (o.get("alternatives") if isinstance(o, dict) else None) or []
            if not isinstance(o, dict):
                out["alternatives_unreadable"] = True
            out["searched"] = inp.get("searched")
        elif act == "DISCOVERY_FILTER":
            out["filter"] = o
            out["file"] = e.get("_file")
            if isinstance(o, dict) and isinstance(o.get("evidence"), list):
                out["decision_evidence"] = o.get("evidence")
            if isinstance(o, dict) and isinstance(o.get("must_beat"), list):
                out["must_beat"] = o.get("must_beat")
            if isinstance(o, dict) and o.get("evidence_source"):
                out["evidence_source"] = o.get("evidence_source")
        elif act == "zetaris_query":
            item = {
                "sql": str(inp.get("sql") or (o.get("sql") if isinstance(o, dict) else "") or ""),
                "evidence_source": str(
                    inp.get("evidence_source") or (o.get("evidence_source") if isinstance(o, dict) else "") or ""
                ),
                "row_count": o.get("row_count") if isinstance(o, dict) else None,
                "query_id": str(o.get("query_id") or "") if isinstance(o, dict) else "",
                "content_hash": str(o.get("content_hash") or "") if isinstance(o, dict) else "",
                "status": e.get("status"),
            }
            out.setdefault("zetaris_queries", []).append(item)
            if item["evidence_source"]:
                out["evidence_source"] = item["evidence_source"]
        elif act == "RESCUE_POTENTIAL" and isinstance(o, dict):
            out["rescue"] = o
    seals = {}
    for e in events:
        if e.get("action") == "RUN_SEAL":
            seals[e.get("_file")] = e.get("line_hash")
    out["seal"] = seals.get(out.get("file"))
    return out


def _e(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def decision_rows(evidence: list[dict[str, Any]] | None, alternatives: list[dict[str, Any]] | None = None) -> list[dict[str, str]]:
    """Normalise refusal evidence to alternative, overlap, quote, reason.

    `evidence` is the structured list stored on a score row or a DISCOVERY_FILTER event.
    `alternatives` is the verified-alternative list from the trace, used when that event
    did not carry the structured list.
    """
    source = evidence if evidence else (alternatives or [])
    rows: list[dict[str, str]] = []
    for item in source:
        if not isinstance(item, dict):
            continue
        quotes = item.get("quotes") if isinstance(item.get("quotes"), list) else []
        quote = str(item.get("quote") or "").strip()
        if not quote:
            for q in quotes:
                if str(q).strip():
                    quote = str(q).strip()
                    break
        name = str(item.get("alternative") or item.get("alternative_id") or item.get("url") or "").strip()
        overlap = str(item.get("overlap") or "").strip()
        why = str(item.get("reason") or "").strip()
        if not any((name, overlap, quote, why)):
            continue
        rows.append({"alternative": name, "overlap": overlap, "quote": quote, "reason": why})
    return rows


def format_decision_lines(repo_id: str, verdict: str, reason: str, evidence: list[dict[str, Any]] | None) -> list[str]:
    """Markdown a human can read for one ALREADY_EXISTS or NEEDS_REVIEW row."""
    if verdict not in {"ALREADY_EXISTS", "NEEDS_REVIEW"}:
        return []
    rows = decision_rows(evidence)
    title = f"### `{repo_id}` `{verdict}`"
    lines = [title, ""]
    if not rows:
        lines.append(f"No verified alternative was recorded. Reason: {_md_cell(reason)}")
        lines.append("")
        return lines
    lines.append("| Alternative | Overlap | Verified quote | Reason |")
    lines.append("| :--- | :--- | :--- | :--- |")
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                _md_cell(row[k]) for k in ("alternative", "overlap", "quote", "reason")
            )
            + " |"
        )
    lines.append("")
    return lines


def _md_cell(value: Any) -> str:
    return str(value or "").replace("|", "/").replace("\n", " ").strip()


def served_model_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per served model for a run. A blank served_model stays `(missing)`.

    Only lines that recorded requested_model (an LLM call) are counted. The requested
    id is never substituted for a missing serve.
    """

    counts: dict[str, dict[str, int]] = {}
    for event in events:
        if "requested_model" not in event:
            continue
        served = event.get("served_model")
        label = served.strip() if isinstance(served, str) and served.strip() else "(missing)"
        row = counts.setdefault(label, {"calls": 0, "mismatches": 0})
        row["calls"] += 1
        if "MODEL_MISMATCH" in (event.get("flags") or []):
            row["mismatches"] += 1
    return [
        {"served_model": name, "calls": info["calls"], "mismatches": info["mismatches"]}
        for name, info in sorted(counts.items())
    ]


def render_evidence_source(ev: dict[str, Any]) -> str:
    """Show whether gate 1 read Zetaris or an explicit CSV snapshot, and the SQL when it did."""

    queries = ev.get("zetaris_queries") if isinstance(ev.get("zetaris_queries"), list) else []
    source = str(ev.get("evidence_source") or "")
    if not source and queries:
        source = str((queries[0] or {}).get("evidence_source") or "")
    if not source and not queries:
        return ""
    parts = ["<h2>Evidence source</h2>", f"<p>evidence_source: <code>{_e(source)}</code></p>"]
    if queries:
        parts.append("<table><tr><th>SQL</th><th>Rows</th><th>Query id</th><th>Content hash</th></tr>")
        for query in queries:
            if not isinstance(query, dict):
                continue
            count = query.get("row_count")
            parts.append(
                "<tr>"
                f"<td><code>{_e(query.get('sql'))}</code></td>"
                f"<td>{_e('' if count is None else count)}</td>"
                f"<td><code>{_e(query.get('query_id'))}</code></td>"
                f"<td><code>{_e(query.get('content_hash'))}</code></td>"
                "</tr>"
            )
        parts.append("</table>")
    elif source == "csv":
        parts.append("<p>Local CSV snapshot. Zetaris was not queried.</p>")
    return "\n".join(parts)


def render_served_model_table(rows: list[dict[str, Any]] | None) -> str:
    if rows is None:
        return ""
    mismatches = sum(int(row.get("mismatches") or 0) for row in rows)
    parts = ["<h2>Served models</h2>", f"<p>MODEL_MISMATCH: {mismatches}</p>"]
    if not rows:
        parts.append('<p class="muted">No LLM calls were recorded for this run.</p>')
        return "\n".join(parts)
    parts.append("<table><tr><th>Served model</th><th>Calls</th><th>MODEL_MISMATCH</th></tr>")
    for row in rows:
        parts.append(
            "<tr>"
            f"<td><code>{_e(row.get('served_model'))}</code></td>"
            f"<td>{_e(row.get('calls'))}</td>"
            f"<td>{_e(row.get('mismatches'))}</td>"
            "</tr>"
        )
    parts.append("</table>")
    return "\n".join(parts)


def render_repo_html(
    repo_id: str,
    verdict: str,
    reason: str,
    ev: dict[str, Any],
    run_id: str = "",
    run_hash: str = "",
    model_rows: list[dict[str, Any]] | None = None,
) -> str:
    color, label = VERDICT_STYLE.get(verdict, ("#475467", verdict))
    parts: list[str] = []
    parts.append(f"<h1>{_e(repo_id)}</h1>")
    parts.append(
        f'<p><span class="badge" style="background:{color}">{_e(verdict)}</span> <span class="muted">{_e(label)}</span></p>'
    )
    parts.append(f"<h2>Why</h2><p>{_e(reason)}</p>")
    if verdict in {"ALREADY_EXISTS", "NEEDS_REVIEW"}:
        rows = decision_rows(ev.get("decision_evidence"), ev.get("alternatives") if isinstance(ev.get("alternatives"), list) else None)
        parts.append("<h2>Why this verdict</h2>")
        parts.append("<table><tr><th>Alternative</th><th>Overlap</th><th>Verified quote</th><th>Reason</th></tr>")
        if not rows:
            parts.append(f"<tr><td colspan=\"4\">No verified alternative was recorded. {_e(reason)}</td></tr>")
        for row in rows:
            parts.append(
                "<tr>"
                f"<td>{_e(row['alternative'])}</td>"
                f"<td>{_e(row['overlap'])}</td>"
                f"<td>{_e(row['quote'])}</td>"
                f"<td>{_e(row['reason'])}</td>"
                "</tr>"
            )
        parts.append("</table>")
    notes = ev.get("must_beat") if isinstance(ev.get("must_beat"), list) else []
    if notes:
        parts.append("<h2>Alternatives that did not block</h2>")
        parts.append("<table><tr><th>Alternative</th><th>Overlap</th><th>Verified quote</th><th>Why it did not block</th></tr>")
        for note in notes:
            parts.append(
                "<tr>"
                f"<td>{_e(note.get('alternative'))}</td>"
                f"<td>{_e(note.get('overlap'))}</td>"
                f"<td>{_e(note.get('quote'))}</td>"
                f"<td>{_e(note.get('reason'))}</td>"
                "</tr>"
            )
        parts.append("</table>")
    rescue = ev.get("rescue") if isinstance(ev.get("rescue"), dict) else None
    if rescue:
        comp = rescue.get("components") or {}
        parts.append("<h2>Rescue potential</h2>")
        parts.append("<p>This score does not change the verdict.</p>")
        parts.append(
            "<table><tr><th>Score</th><th>Stars</th><th>Forks</th><th>Stale days</th><th>Stars vs staleness</th>"
            "<th>CI</th><th>External breakage</th><th>Dependabot</th><th>Renovate</th></tr>"
            f"<tr><td>{_e(rescue.get('score'))}</td><td>{_e(comp.get('stars'))}</td><td>{_e(comp.get('forks'))}</td>"
            f"<td>{_e(comp.get('staleness_days'))}</td><td>{_e(comp.get('stars_vs_staleness'))}</td>"
            f"<td>{_e(comp.get('ci_state'))}</td><td>{_e(comp.get('external_breakage'))}</td>"
            f"<td>{_e(comp.get('dependabot_open'))}</td><td>{_e(comp.get('renovate_open'))}</td></tr></table>"
        )

    prob = ev.get("problem")
    if isinstance(prob, dict):
        parts.append("<h2>What the repository says it solves</h2>")
        parts.append(f"<p><b>Problem:</b> {_e(prob.get('problem'))}</p>")
        parts.append(f"<p><b>Target user:</b> {_e(prob.get('target_user'))} &nbsp; <b>Category:</b> {_e(prob.get('category'))}</p>")
        for q in prob.get("quotes") or []:
            parts.append(f"<blockquote>{_e(q)}<div class=\"muted\">verified verbatim in the repository README</div></blockquote>")
    else:
        parts.append('<p class="muted">No problem statement was recorded for this repository.</p>')

    alts = ev.get("alternatives")
    if isinstance(alts, list):
        parts.append(f"<h2>What already exists ({len(alts)} verified)</h2>")
        if not alts:
            parts.append('<p class="muted">No verified same-problem product was found in the searched sources. Absence in a search is not proof of absence.</p>')
        for a in alts:
            url = str(a.get("url") or "")
            link = f'<a href="{_e(url)}">{_e(a.get("alternative_id") or url)}</a>' if url.startswith("http") else _e(a.get("alternative_id"))
            parts.append(
                f'<div class="alt"><b>{link}</b> <span class="tag">{_e(a.get("source") or "")}</span> '
                f'<span class="tag">overlap: {_e(a.get("overlap"))}</span><p>{_e(a.get("reason"))}</p>'
            )
            for q in a.get("quotes") or []:
                parts.append(f"<blockquote>{_e(q)}<div class=\"muted\">verified verbatim in that product's own text</div></blockquote>")
            parts.append("</div>")

    search = ev.get("search")
    if isinstance(search, dict):
        counts = search.get("source_counts") or {}
        status = search.get("source_status") or {}
        parts.append("<h2>Search coverage</h2>")
        parts.append(f"<p>Queries: {_e('; '.join(map(str, ev.get('queries') or [])))}</p>")
        rows = "".join(
            f"<tr><td>{_e(k)}</td><td>{_e(counts.get(k))}</td><td>{_e(status.get(k))}</td></tr>"
            for k in sorted(set(counts) | set(status))
        )
        parts.append(f"<table><tr><th>Source</th><th>Results</th><th>Status</th></tr>{rows}</table>")

    parts.append(render_evidence_source(ev))
    parts.append(render_served_model_table(model_rows))
    parts.append("<h2>Evidence trail</h2>")
    meta = []
    if isinstance(prob, dict):
        meta.append(f"model requested: {_e(prob.get('requested_model'))}, served: {_e(prob.get('served_model'))}")
    if run_id:
        meta.append(f"run: {_e(run_id)}")
    if ev.get("seal") or run_hash:
        meta.append(f"trace seal (sha256): <code>{_e(ev.get('seal') or run_hash)}</code>")
    parts.append("<p class=\"muted\">" + "<br>".join(meta or ["no trace metadata"]) + "</p>")
    parts.append('<p class="muted">The seal shows the record was not altered after the run. It does not prove the record is true; every quote above was checked as an exact substring of the fetched text.</p>')
    return _page(f"{repo_id} - PhoenixRepo evidence report", "\n".join(parts))


def _page(title: str, body: str) -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(title)}</title><style>
body{{font:16px/1.5 system-ui,Segoe UI,sans-serif;max-width:860px;margin:2rem auto;padding:0 1rem;color:#101828}}
h1{{font-size:1.6rem;margin-bottom:.25rem}} h2{{margin-top:1.8rem;border-bottom:1px solid #eaecf0;padding-bottom:.25rem}}
.badge{{color:#fff;padding:.15rem .6rem;border-radius:999px;font-weight:600}} .muted{{color:#667085;font-size:.9rem}}
blockquote{{margin:.6rem 0;padding:.5rem .9rem;border-left:4px solid #d0d5dd;background:#f9fafb}}
.alt{{margin:1rem 0;padding:.6rem .9rem;border:1px solid #eaecf0;border-radius:8px}}
.tag{{background:#f2f4f7;border-radius:6px;padding:0 .4rem;font-size:.8rem;margin-left:.3rem}}
table{{border-collapse:collapse}} td,th{{border:1px solid #eaecf0;padding:.25rem .6rem;text-align:left}} code{{word-break:break-all}}
</style></head><body>{body}</body></html>"""


def _slug(repo_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "__", repo_id)


def render_all(results: dict[str, Any], events: list[dict[str, Any]], out_dir: Path, run_id: str = "") -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    model_rows = served_model_rows(events)
    rows: list[tuple[str, str, str]] = []
    written: list[Path] = []
    entries = [(c.get("repo_id"), "CANDIDATE", c.get("reason") or "") for c in results.get("candidates") or []]
    entries += [(r.get("repo_id"), str(r.get("verdict")), r.get("reason") or "") for r in results.get("ineligible") or []]
    for repo_id, verdict, reason in entries:
        if not repo_id:
            continue
        ev = events_for_repo(events, str(repo_id))
        path = out_dir / f"{_slug(str(repo_id))}.html"
        path.write_text(
            render_repo_html(
                str(repo_id),
                verdict,
                reason,
                ev,
                run_id=run_id or str(results.get("run_id") or ""),
                run_hash=str(results.get("run_hash") or ""),
                model_rows=model_rows,
            ),
            encoding="utf-8",
        )
        written.append(path)
        rows.append((str(repo_id), verdict, path.name))
    items = "".join(
        f'<tr><td><a href="{_e(n)}">{_e(r)}</a></td><td>{_e(v)}</td></tr>' for r, v, n in sorted(rows, key=lambda x: (x[1], x[0]))
    )
    index_body = (
        "<h1>PhoenixRepo evidence reports</h1>"
        + render_served_model_table(model_rows)
        + f"<table><tr><th>Repository</th><th>Verdict</th></tr>{items}</table>"
    )
    index = _page("PhoenixRepo evidence reports", index_body)
    (out_dir / "index.html").write_text(index, encoding="utf-8")
    written.append(out_dir / "index.html")
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results", type=Path, required=True, help="logs/<run>_scores.json")
    ap.add_argument("--log-dir", type=Path, default=ROOT / "logs")
    ap.add_argument("--run-id", required=True, help="Run id prefix; every logs/trace-<run-id>*.jsonl is read.")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    results = json.loads(args.results.read_text(encoding="utf-8"))
    events = load_events(args.log_dir, prefix=args.run_id)
    files = render_all(results, events, args.out, run_id=args.run_id)
    print(f"wrote {len(files)} files to {args.out}")
    return 0


if __name__ == "__main__":
    import sys as _sys

    _root = str(Path(__file__).resolve().parents[1])
    if _root not in _sys.path:
        _sys.path.insert(0, _root)
    from app.console import ensure_utf8_stdio

    ensure_utf8_stdio()
    raise SystemExit(main())