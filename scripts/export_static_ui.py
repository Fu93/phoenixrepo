#!/usr/bin/env python3
"""Export /ui as one static HTML page that needs no server, for GitHub Pages.

    python scripts/export_static_ui.py                 # writes docs/demo/index.html
    python scripts/export_static_ui.py --out site/index.html

The page is app/ui/index.html with the replay catalog, every replay payload, and the
tamper results embedded as JSON. Every value comes from the same functions the server
uses (app.replay_ui): the hash chain of each trace is verified here at export time, and
the tamper result is the real verify_trace result on a one-character copy, computed here.
Live judging needs a server and a Nightshift token, so the static page says so instead.
The export refuses to embed a trace that contains a secret (same check as import_replay).
"""
from __future__ import annotations

import argparse
import json
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
  var DATA = %s;
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


def build(out: Path) -> dict[str, int]:
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
    data = {
        "catalog": catalog,
        "replays": replays,
        "tamper": tamper,
        "live_reason": STATIC_LIVE_REASON,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    # "</" cannot appear inside the embedded JSON, or a trace string could close the script tag.
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    page = replay_ui.ui_path().read_text(encoding="utf-8")
    marker = "<script>"
    if marker not in page:
        raise SystemExit("app/ui/index.html has no <script> tag to put the data before")
    index = page.index(marker)
    page = page[:index] + (SHIM % blob) + page[index:]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    return {"replays": len(replays), "tamper": len(tamper), "bytes": len(page.encode("utf-8"))}


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
