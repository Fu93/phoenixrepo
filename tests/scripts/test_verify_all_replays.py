"""verify_all_replays checks RECORDED catalog entries with the existing trace verifier."""

from __future__ import annotations

import json
from pathlib import Path

from app.trace_log import HackathonTrace
from scripts.verify_all_replays import main

NEMO = "nvidia/nemotron-3-ultra-550b-a55b"


def _catalog(tmp_path: Path, replays: list[dict]) -> Path:
    root = tmp_path / "replays"
    root.mkdir()
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps({"replays": replays}), encoding="utf-8")
    return manifest


def _recorded(root: Path, replay_id: str, *, served: str, llm_used: bool | None, run_hash: str | None = None) -> str:
    directory = root / replay_id
    directory.mkdir()
    trace = HackathonTrace(replay_id, directory)
    # HackathonTrace writes trace-<run_id>.jsonl. The catalog name is trace.jsonl.
    trace.event(
        agent_name="orchestrator",
        action="LLM_CALL",
        output_summary={"served_model": served, "provider": "nightshift"},
        llm={"requested_model": NEMO, "served_model": served},
    )
    sealed = trace.seal()
    written = directory / f"trace-{replay_id}.jsonl"
    written.replace(directory / "trace.jsonl")
    recorded: dict = {"run_hash": run_hash if run_hash is not None else sealed}
    if llm_used is not None:
        recorded["llm_used"] = llm_used
    record = {"verdict": {"verdict": "CANDIDATE"}, "recorded_run": recorded}
    (directory / "record.json").write_text(json.dumps(record), encoding="utf-8")
    return sealed


def test_recorded_replay_passes_and_sample_is_skipped(tmp_path: Path, capsys):
    manifest = _catalog(tmp_path, [])
    root = manifest.parent
    _recorded(root, "good", served=NEMO, llm_used=True)
    # A SAMPLE entry is not checked, even if its directory is absent.
    replays = [
        {"id": "good", "trace_class": "RECORDED"},
        {"id": "sample-only", "trace_class": "SAMPLE"},
    ]
    manifest.write_text(json.dumps({"replays": replays}), encoding="utf-8")
    assert main(["--manifest", str(manifest)]) == 0
    out = capsys.readouterr().out
    assert "good" in out
    assert "sample-only" not in out
    assert "PASS" in out
    assert NEMO in out
    assert "1 passed, 0 failed, 1 RECORDED" in out


def test_wrong_run_hash_fails(tmp_path: Path, capsys):
    manifest = _catalog(tmp_path, [{"id": "bad-hash", "trace_class": "RECORDED"}])
    _recorded(manifest.parent, "bad-hash", served=NEMO, llm_used=True, run_hash="0" * 64)
    assert main(["--manifest", str(manifest)]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_used_non_nemotron_answer_fails_when_llm_was_used(tmp_path: Path, capsys):
    manifest = _catalog(tmp_path, [{"id": "gemini", "trace_class": "RECORDED"}])
    _recorded(manifest.parent, "gemini", served="gemini-3.1-flash-lite", llm_used=True)
    assert main(["--manifest", str(manifest)]) == 1
    assert "not nemotron" in capsys.readouterr().out


def test_repo_recorded_replays_pass():
    assert main([]) == 0


# verify_all_replays.py checks an id only when manifest trace_class is RECORDED.
# manifest.json and record.json are not hash-covered, so these lists are the tripwire.
# Manifest order on main when the lists were pinned.
PINNED_RECORDED_IDS = (
    "selfdb-io-selfdb",
    "dsregoitia-calendarsyncproject",
    "tharani-2006-invoice-generator-application",
    "goodreasonai-scrapeserv",
    "samuelarogbonlo-knetvis",
    "rianvdm-raindrop-wordpress-linkblog-sync",
    "carvenjizaks-gc-sync-webhook",
    "aa12gq-stack-file-sync-vscode",
    "ri-char-pwatch",
    "dayank-linkedinsynccontacts",
    "shabi-x-go-file-sync",
    "samwho-spacer",
    "oleksiiluchnikov-obsidian-localtable",
    "sealdice-qq-group-files-sync",
    "dhaiwat10-x402-sovereign",
    "0xmh-fichero-printer",
    "zetaris-gate1-tharani-2006-invoice-generator-application",
    "e3-goodreasonai-scrapeserv",
    "e3-dsregoitia-calendarsyncproject",
)
PINNED_SAMPLE_IDS = (
    "invoice-already-exists",
    "hygen-already-exists-quote",
    "mirrormate-human-label",
    "invoice-gate2-sample",
)
_PIN_NOTE = "Changing the set must be a deliberate edit of this list in the same PR as the manifest change."


def _pinned_set_message(label: str, actual: set[str], pinned: set[str]) -> str:
    added = ", ".join(sorted(actual - pinned)) or "none"
    removed = ", ".join(sorted(pinned - actual)) or "none"
    return (
        f"{label} ids do not match the pinned list in this test. {_PIN_NOTE} "
        f"Present but not pinned: {added}. Pinned but absent: {removed}."
    )


def test_repo_trace_class_matches_record_and_pinned_ids():
    """Every replay's trace_class agrees across manifest and record, and both classes are pinned."""
    root = Path(__file__).resolve().parents[2] / "data" / "replays"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    disagreements: list[str] = []
    recorded: set[str] = set()
    sample: set[str] = set()
    unexpected: list[str] = []
    for entry in manifest["replays"]:
        replay_id = entry["id"]
        manifest_class = entry["trace_class"]
        record_path = root / replay_id / "record.json"
        if not record_path.is_file():
            disagreements.append(f"{replay_id}: missing record.json")
            continue
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record_class = record["trace_class"]
        if manifest_class != record_class:
            disagreements.append(
                f"{replay_id}: manifest.json trace_class {manifest_class!r} != record.json trace_class {record_class!r}"
            )
        if manifest_class == "RECORDED":
            recorded.add(replay_id)
        elif manifest_class == "SAMPLE":
            sample.add(replay_id)
        else:
            unexpected.append(f"{replay_id}: {manifest_class!r}")
    assert not disagreements, "trace_class disagrees between manifest.json and record.json:\n" + "\n".join(disagreements)
    assert not unexpected, f"trace_class must be RECORDED or SAMPLE. {_PIN_NOTE} " + "; ".join(unexpected)
    assert recorded == set(PINNED_RECORDED_IDS), _pinned_set_message("RECORDED", recorded, set(PINNED_RECORDED_IDS))
    assert sample == set(PINNED_SAMPLE_IDS), _pinned_set_message("SAMPLE", sample, set(PINNED_SAMPLE_IDS))
