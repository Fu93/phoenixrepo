"""Turn a PhoenixRepo JSONL trace into a timeline.

    python scripts/render_trace.py logs/trace-<run_id>.jsonl --format md
    python scripts/render_trace.py logs/trace-<run_id>.jsonl --format html --output logs/trace.html
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


def load_events(path: Path) -> list[dict]:
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events


def _cell(value: object) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


def _hash(value: object) -> str:
    text = str(value or "")
    return text[:12]


def served_model(event: dict) -> str:
    """Prefer the hashed top-level field. Older lines kept it only inside input_summary."""

    if event.get("served_model"):
        return str(event["served_model"])
    summary = event.get("input_summary") or ""
    if isinstance(summary, str) and summary.startswith("{"):
        try:
            payload = json.loads(summary)
        except json.JSONDecodeError:
            return ""
        if isinstance(payload, dict) and payload.get("served_model"):
            return str(payload["served_model"])
    return ""


def render_markdown(events: list[dict], *, title: str) -> str:
    lines = [
        f"# {title}",
        "",
        "Gate 1 verdicts: CANDIDATE, ALREADY_EXISTS, NO_GO, NEEDS_REVIEW, NEEDS_PRIOR_ART, UNJUDGED. Gate 2 (revive loop) states: NOT_ATTEMPTED_GATE1, NOTHING_TO_FIX, NEEDS_REVIEW, RESURRECTED (verified locally by the repository's own test command, not on CI), RESURRECTION_FAILED. In the Zetaris evidence-policy track RESURRECTED means a recorded CI success and CANDIDATE means a policy pass with no such success.",
        "line_hash chains each row to the previous one. RUN_SEAL is the run hash. The chain shows the file was not edited later. It does not show the text is true.",
        "",
        "| # | timestamp | agent | action | status | served_model | line_hash | input | output |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for index, event in enumerate(events, start=1):
        lines.append(
            "| {index} | {timestamp} | {agent} | {action} | {status} | {served} | {digest} | {inp} | {out} |".format(
                index=index,
                timestamp=_cell(event.get("timestamp")),
                agent=_cell(event.get("agent_name")),
                action=_cell(event.get("action")),
                status=_cell(event.get("status")),
                served=_cell(served_model(event)),
                digest=_hash(event.get("line_hash")),
                inp=_cell(event.get("input_summary")),
                out=_cell(event.get("output_summary")),
            )
        )
    if not events:
        lines.append("|  |  |  |  |  |  |  |  | no events |")
    if events and events[-1].get("action") == "RUN_SEAL":
        lines.extend(["", f"Run hash: {events[-1].get('line_hash')}", ""])
    lines.append("")
    return "\n".join(lines)


def render_html(events: list[dict], *, title: str) -> str:
    rows = []
    for index, event in enumerate(events, start=1):
        cells = [
            str(index),
            str(event.get("timestamp") or ""),
            str(event.get("agent_name") or ""),
            str(event.get("action") or ""),
            str(event.get("status") or ""),
            served_model(event),
            _hash(event.get("line_hash")),
            str(event.get("input_summary") or ""),
            str(event.get("output_summary") or ""),
        ]
        rows.append("<tr>" + "".join(f"<td>{html.escape(cell)}</td>" for cell in cells) + "</tr>")
    body = "\n".join(rows) if rows else "<tr><td colspan='9'>no events</td></tr>"
    seal = ""
    if events and events[-1].get("action") == "RUN_SEAL":
        seal = "<p>Run hash: " + html.escape(str(events[-1].get("line_hash") or "")) + "</p>"
    note = (
        "<p>Gate 1 verdicts: CANDIDATE, ALREADY_EXISTS, NO_GO, NEEDS_REVIEW, NEEDS_PRIOR_ART, UNJUDGED. "
        "Gate 2 (revive loop) states: NOT_ATTEMPTED_GATE1, NOTHING_TO_FIX, NEEDS_REVIEW, "
        "RESURRECTED (verified locally by the repository's own test command, not on CI), RESURRECTION_FAILED. "
        "In the Zetaris evidence-policy track RESURRECTED means a recorded CI success and CANDIDATE means a policy pass with no such success. "
        "line_hash chains each row to the previous one. RUN_SEAL is the run hash. "
        "The chain shows the file was not edited later. It does not show the text is true.</p>"
    )
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'><title>"
        + html.escape(title)
        + "</title></head><body><h1>"
        + html.escape(title)
        + "</h1>"
        + note
        + "<table><thead><tr><th>#</th><th>timestamp</th><th>agent</th>"
        + "<th>action</th><th>status</th><th>served_model</th><th>line_hash</th><th>input</th><th>output</th></tr></thead><tbody>"
        + body
        + "</tbody></table>"
        + seal
        + "</body></html>\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a trace JSONL timeline.")
    parser.add_argument("trace")
    parser.add_argument("--format", choices=("md", "html"), default="md")
    parser.add_argument("--output")
    args = parser.parse_args()
    path = Path(args.trace)
    events = load_events(path)
    title = f"Trace {path.name}"
    text = render_markdown(events, title=title) if args.format == "md" else render_html(events, title=title)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    import sys as _sys

    _root = str(Path(__file__).resolve().parents[1])
    if _root not in _sys.path:
        _sys.path.insert(0, _root)
    from app.console import ensure_utf8_stdio

    ensure_utf8_stdio()
    raise SystemExit(main())
