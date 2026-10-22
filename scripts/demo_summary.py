"""Format a live-demo markdown summary. No network."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def render_summary(*, checks: list[dict], runs: list[dict]) -> str:
    lines = [
        "# PhoenixRepo live demo",
        "",
        "Reachability is whatever the demo runner recorded. A missing service stays a failed check.",
        "",
        "| Check | Result |",
        "| --- | --- |",
    ]
    for check in checks:
        lines.append(f"| {check.get('name', '')} | {check.get('result', '')} |")
    lines.extend(["", "## Verdicts", ""])
    if not runs:
        lines.append("No `/run` calls were made.")
    for run in runs:
        repository = run.get("repository", "")
        lines.append(f"### {repository}")
        lines.append("")
        lines.append(f"- decision: {run.get('decision', '')}")
        lines.append(f"- state: {run.get('state', '')}")
        lines.append(f"- http_status: {run.get('http_status', '')}")
        reason = str(run.get("reason") or run.get("error") or "")
        lines.append(f"- reason: {reason}")
        log_file = str(run.get("log_file") or "")
        if log_file:
            lines.append(f"- trace: [{log_file}]({log_file})")
        else:
            lines.append("- trace: none")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Write the live demo markdown summary.")
    parser.add_argument("--checks", required=True)
    parser.add_argument("--runs", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    checks = json.loads(Path(args.checks).read_text(encoding="utf-8"))
    runs = json.loads(Path(args.runs).read_text(encoding="utf-8"))
    text = render_summary(checks=checks, runs=runs)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
