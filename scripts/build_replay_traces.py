"""Write the replay JSONL seals from the committed score and audit JSON.

Run this only when those source files change. The web demo reads the files this
writes; it does not rebuild them and it does not call Nightshift.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.replay import committed_run_ids, sealed_trace_text, trace_path_for


def main() -> int:
    for run_id in committed_run_ids():
        path = trace_path_for(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(sealed_trace_text(run_id), encoding="utf-8")
        print(path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
