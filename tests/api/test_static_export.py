"""The static /ui copy for GitHub Pages: same data as the server, no server needed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import replay_ui
from scripts import export_static_ui


def _embedded(page: str) -> dict:
    start = page.index("var DATA = ") + len("var DATA = ")
    end = page.index(";\n  function reply")
    return json.loads(page[start:end].replace("<\\/", "</"))


def test_export_embeds_every_replay_with_the_server_payload(tmp_path: Path):
    out = tmp_path / "index.html"
    stats = export_static_ui.build(out)
    page = out.read_text(encoding="utf-8")
    data = _embedded(page)
    ids = [row["id"] for row in replay_ui.catalog()["replays"]]
    assert stats["replays"] == len(ids) and sorted(data["replays"]) == sorted(ids)
    for replay_id in ids:
        assert data["replays"][replay_id] == json.loads(json.dumps(replay_ui.replay_payload(replay_id)))
        assert data["tamper"][replay_id]["ok"] is False  # a flipped character always breaks the chain
    assert "Live judging needs the server" in data["live_reason"]
    # The page's own script follows the shim, unchanged.
    assert page.index("window.fetch = function") < page.index('document.getElementById("tamper")')


def test_embedded_json_cannot_close_the_script_tag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    real = replay_ui.replay_payload

    def with_script(replay_id: str) -> dict:
        payload = real(replay_id)
        payload["banner"] = "</script><script>alert(1)</script>"
        return payload

    monkeypatch.setattr(replay_ui, "replay_payload", with_script)
    out = tmp_path / "index.html"
    export_static_ui.build(out)
    page = out.read_text(encoding="utf-8")
    shim = page[page.index("var DATA = "): page.index("window.fetch = function")]
    assert "</script>" not in shim
    assert _embedded(page)["replays"][replay_ui.catalog()["replays"][0]["id"]]["banner"].startswith("</script>")


def test_export_refuses_a_trace_with_a_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(export_static_ui, "secret_in_trace", lambda text: "a token-shaped string")
    with pytest.raises(SystemExit, match="not exported"):
        export_static_ui.build(tmp_path / "index.html")
