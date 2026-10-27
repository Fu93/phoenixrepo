"""The static /ui copy for GitHub Pages: same data as the server, no server needed."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app import replay_ui
from scripts import export_static_ui


def _embedded(page: str) -> dict:
    start = page.index("var DATA = ") + len("var DATA = ")
    end = page.index(";\n  window.__phoenixExport")
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


def test_exported_html_contains_the_source_sha(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    sha = "0123456789abcdef0123456789abcdef01234567"
    monkeypatch.setenv("GITHUB_SHA", sha)
    out = tmp_path / "index.html"
    stats = export_static_ui.build(out)
    page = out.read_text(encoding="utf-8")
    data = _embedded(page)
    assert stats["source_commit"] == sha
    assert data["source_commit"] == sha
    assert sha in page
    assert data["exported_at"]
    note = data["export_note"]
    assert f"Exported at {data['exported_at']}." in note
    assert f"Source commit {sha}." in note
    assert "The run hash is anchored to the commit this page was exported from." in note
    assert "It does not defend against the author rewriting history." in note
    assert note in page


def test_export_says_unknown_when_no_commit_is_available(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GITHUB_SHA", raising=False)

    def no_git(*args, **kwargs):
        raise OSError("git not available")

    monkeypatch.setattr(export_static_ui.subprocess, "run", no_git)
    out = tmp_path / "index.html"
    export_static_ui.build(out)
    page = out.read_text(encoding="utf-8")
    data = _embedded(page)
    assert data["source_commit"] == "unknown"
    assert "Source commit: unknown." in data["export_note"]
    assert "does not anchor the run hash to a commit" in data["export_note"]
    assert "It does not defend against the author rewriting history." in data["export_note"]
    assert data["export_note"] in page


def test_source_commit_prefers_github_sha_then_head_then_unknown(monkeypatch: pytest.MonkeyPatch):
    real_run = subprocess.run
    monkeypatch.setenv("GITHUB_SHA", "from-actions")

    def git_must_not_run(*args, **kwargs):
        raise AssertionError("git rev-parse should not run when GITHUB_SHA is set")

    monkeypatch.setattr(export_static_ui.subprocess, "run", git_must_not_run)
    assert export_static_ui.source_commit() == "from-actions"

    monkeypatch.delenv("GITHUB_SHA", raising=False)
    monkeypatch.setattr(export_static_ui.subprocess, "run", real_run)
    head = real_run(
        ["git", "rev-parse", "HEAD"],
        cwd=export_static_ui.ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert export_static_ui.source_commit() == head

    monkeypatch.setenv("GITHUB_SHA", "   ")
    monkeypatch.setattr(
        export_static_ui.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, "", "fatal"),
    )
    assert export_static_ui.source_commit() == "unknown"
