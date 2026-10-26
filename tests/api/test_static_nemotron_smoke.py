"""The exported static page must show the Nemotron badge and reasoning card.

The badge is rendered by the page script from the embedded catalog. This check fails
when that catalog has no used Nemotron answers or no Nemotron reasoning text, and when
the page template drops the badge or the reasoning heading.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from scripts import export_static_ui

BADGE = re.compile(r"\d+ of \d+ answers by NVIDIA Nemotron")


def _embedded(page: str) -> dict:
    start = page.index("var DATA = ") + len("var DATA = ")
    end = page.index(";\n  function reply")
    return json.loads(page[start:end].replace("<\\/", "</"))


def test_exported_static_ui_has_the_nemotron_badge_and_reasoning_card(tmp_path: Path):
    out = tmp_path / "index.html"
    export_static_ui.build(out)
    page = out.read_text(encoding="utf-8")
    data = _embedded(page)
    badges: list[str] = []
    cards: list[dict] = []
    for payload in data["replays"].values():
        models = payload["models"]
        used = int(models["answers_used"] or 0)
        nemotron = int(models["answers_by_nemotron"] or 0)
        if used:
            badges.append(f"{nemotron} of {used} answers by NVIDIA Nemotron")
        thought = payload.get("verdict_reasoning") or {}
        if thought.get("nemotron") and str(thought.get("text") or "").strip():
            cards.append(thought)
    fixture = data["replays"]["nemotron-ui-fixture"]
    assert fixture["trace_class"] == "SAMPLE"
    assert "not a Nightshift" in (fixture.get("banner") or "")
    assert badges, "exported page has no N of M answers by NVIDIA Nemotron badge"
    assert any(BADGE.fullmatch(badge) for badge in badges)
    assert "answers by NVIDIA Nemotron" in page
    assert cards, "exported page has no Nemotron reasoning card"
    assert "Nemotron's reasoning" in page
    first_id = data["catalog"]["replays"][0]["id"]
    first = data["replays"][first_id]
    assert first["models"]["answers_by_nemotron"] == first["models"]["answers_used"] > 0
    assert first["verdict_reasoning"]["nemotron"] is True
