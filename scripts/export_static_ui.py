#!/usr/bin/env python3
"""Export /ui as one static HTML page that needs no server, for GitHub Pages.

    python scripts/export_static_ui.py                 # writes docs/demo/index.html
    python scripts/export_static_ui.py --out site/index.html

The page is app/ui/index.html with the replay catalog, every replay payload, and the
tamper results embedded as JSON. Every value comes from the same functions the server
uses (app.replay_ui): a RECORDED trace is verified here at export time against
recorded_run.run_hash when record.json has one. A SAMPLE replay is not compared to
that hash. The tamper result is the real verify_trace result on a one-character copy,
computed here.
The page also embeds exported_at and source_commit (GITHUB_SHA when that is set,
otherwise git rev-parse HEAD, otherwise the string unknown). When a commit is named,
the run hash check is anchored to the commit this page was exported from. It does
not defend against the author rewriting history. When the commit is unknown, the
page says so and does not claim that anchor.
Live judging needs a server and a Nightshift token, so the static page says so instead.
The export refuses to embed a trace that contains a secret (same check as import_replay).
"""
from __future__ import annotations

import argparse
import html
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import replay_ui  # noqa: E402
from scripts.import_replay import secret_in_trace  # noqa: E402

STATIC_LIVE_REASON = (
    "This is the static GitHub Pages copy. The replays, the chain check and the tamper result were "
    "computed by scripts/verify_trace.py when this page was exported and are embedded in it. Live "
    "judging needs the server (python main.py) and a Nightshift token."
)

# Answers the page's fetch() calls from the embedded data. Nothing is sent anywhere.
SHIM = """<script>
(function () {
  var DATA = __PHOENIX_DATA__;
  window.__phoenixExport = {
    exported_at: DATA.exported_at,
    source_commit: DATA.source_commit,
    export_note: DATA.export_note
  };
  function reply(body, status) {
    return Promise.resolve(new Response(JSON.stringify(body), {
      status: status || 200, headers: { "Content-Type": "application/json" }
    }));
  }
  window.fetch = function (input, init) {
    var url = typeof input === "string" ? input : input.url;
    var path = url.replace(/^https?:\\/\\/[^/]+/, "");
    if (path === "/api/replays") return reply(DATA.catalog);
    if (path === "/api/live") return reply({ enabled: false, reason: DATA.live_reason });
    var m = path.match(/^\\/api\\/replays\\/([^/]+)(\\/tamper)?$/);
    if (m) {
      var id = decodeURIComponent(m[1]);
      if (m[2]) return DATA.tamper[id] ? reply(DATA.tamper[id]) : reply({ error: "no trace to tamper" }, 409);
      return DATA.replays[id] ? reply(DATA.replays[id]) : reply({ error: "unknown replay" }, 404);
    }
    if (path === "/judge") return reply({ error: { type: "StaticPage", message: DATA.live_reason } }, 503);
    return reply({ error: "not available in the static copy" }, 404);
  };
})();
</script>
"""


_EXPORT_NOTE = '<p id="export-note" class="muted"></p>'


def source_commit() -> str:
    """Commit this export was built from.

    ``GITHUB_SHA`` when that variable is set and non-empty. Otherwise
    ``git rev-parse HEAD``. Otherwise the string ``unknown``.
    """

    configured = os.environ.get("GITHUB_SHA", "").strip()
    if configured:
        return configured
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    sha = (completed.stdout or "").strip()
    if completed.returncode != 0 or not sha:
        return "unknown"
    return sha


def export_note(exported_at: str, commit: str) -> str:
    """What the embedded commit proves, and what it does not prove."""

    if commit == "unknown":
        anchor = (
            "Source commit: unknown. git rev-parse HEAD and GITHUB_SHA were not available, "
            "so this page does not anchor the run hash to a commit."
        )
    else:
        anchor = (
            f"Source commit {commit}. "
            "The run hash is anchored to the commit this page was exported from."
        )
    return f"Exported at {exported_at}. {anchor} It does not defend against the author rewriting history."


def build(out: Path) -> dict[str, int | str]:
    catalog = replay_ui.catalog()
    replays: dict[str, dict] = {}
    tamper: dict[str, dict] = {}
    for row in catalog.get("replays") or []:
        replay_id = str(row.get("id") or "")
        trace = replay_ui.REPLAYS_ROOT / replay_id / "trace.jsonl"
        if trace.is_file():
            leak = secret_in_trace(trace.read_text(encoding="utf-8"))
            if leak:
                raise SystemExit(f"{replay_id}: trace contains {leak}; not exported")
        replays[replay_id] = replay_ui.replay_payload(replay_id)
        if trace.is_file():
            result = replay_ui.tamper_replay(replay_id)
            result["changed"] = "one character in a copy, verified with scripts/verify_trace.py when this page was exported"
            tamper[replay_id] = result
    exported_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    commit = source_commit()
    note = export_note(exported_at, commit)
    data = {
        "catalog": catalog,
        "replays": replays,
        "tamper": tamper,
        "live_reason": STATIC_LIVE_REASON,
        "exported_at": exported_at,
        "source_commit": commit,
        "export_note": note,
    }
    # "</" cannot appear inside the embedded JSON, or a trace string could close the script tag.
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    page = replay_ui.ui_path().read_text(encoding="utf-8")
    if _EXPORT_NOTE not in page:
        raise SystemExit("app/ui/index.html has no export-note placeholder")
    page = page.replace(_EXPORT_NOTE, f'<p id="export-note" class="muted">{html.escape(note)}</p>', 1)
    marker = "<script>"
    if marker not in page:
        raise SystemExit("app/ui/index.html has no <script> tag to put the data before")
    index = page.index(marker)
    page = page[:index] + SHIM.replace("__PHOENIX_DATA__", blob) + page[index:]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    return {
        "replays": len(replays),
        "tamper": len(tamper),
        "bytes": len(page.encode("utf-8")),
        "source_commit": commit,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "demo" / "index.html")
    args = ap.parse_args(argv)
    stats = build(args.out)
    print(json.dumps({"out": str(args.out), **stats}))
    return 0


if __name__ == "__main__":
    from app.console import prepare_cli

    prepare_cli()
    raise SystemExit(main())
