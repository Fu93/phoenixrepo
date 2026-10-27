from __future__ import annotations

from scripts import nvidia_live_smoke as smoke


def test_fixed_claim_is_provenance_only():
    assert "not an evaluation result" in smoke.FIXED_CLAIM_TEMPLATE
    assert "model-quality comparison" in smoke.FIXED_CLAIM_TEMPLATE
    assert "Gate 2 repair claim" in smoke.FIXED_CLAIM_TEMPLATE


def test_smoke_uses_the_ultra_request_by_default():
    assert smoke.DEFAULT_MODEL == "nvidia/nemotron-3-ultra-550b-a55b"


def test_claim_template_has_no_backticks_and_script_is_ascii():
    assert "`" not in smoke.FIXED_CLAIM_TEMPLATE
    assert "requested {requested} and was served by {served}." in smoke.FIXED_CLAIM_TEMPLATE
    source = open(smoke.__file__, encoding="utf-8").read()
    assert source.isascii()
    assert "super-vl" not in (smoke.__doc__ or "")


import json
from functools import partial

import httpx

from app.agents.relevance import RelevanceClient

MOCK_SERVED = "nvidia/nemotron-3-super-120b-a12b"


FAKE_TOKEN = "test-token-not-real"
FAKE_URL = "http://mock-nightshift.invalid/v1"


def _offline_client(seen: list[dict], urls: list[str] | None = None, auths: list[str] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        if urls is not None:
            urls.append(str(request.url))
        if auths is not None:
            auths.append(request.headers.get("authorization", ""))
        body = {
            "model": MOCK_SERVED,
            "choices": [{"message": {"role": "assistant", "content": '{"ok":true,"purpose":"provenance-smoke"}'}}],
        }
        return httpx.Response(200, json=body)

    # Same constructor as the script uses; only the transport is replaced, so no network is touched.
    return partial(RelevanceClient, transport=httpx.MockTransport(handler))


def _settings(**values):
    from config.settings import Settings

    params = {"meterless_base_url": FAKE_URL, "meterless_runtime_token": FAKE_TOKEN}
    params.update(values)
    return Settings(_env_file=None, **params)


def _patch(monkeypatch, seen, **kwargs):
    for name in ("MODEL_NAME", "METERLESS_MODEL", "PHOENIX_LLM_EXTRA_BODY", "PHOENIX_DEEP_MODEL", "MODEL_NAME_DEEP", "PHOENIX_LLM_CACHE_DIR"):
        monkeypatch.delenv(name, raising=False)
    settings = kwargs.pop("settings", None) or _settings()
    monkeypatch.setattr(smoke, "prepare_cli", lambda: None)
    monkeypatch.setattr(smoke, "trace_secrets", lambda settings=None: [])
    monkeypatch.setattr(smoke, "_load_settings", lambda: settings)
    monkeypatch.setattr(smoke, "RelevanceClient", _offline_client(seen, **kwargs))


def _run(tmp_path, run_id, *extra):
    return smoke.main(["--run-id", run_id, "--log-dir", str(tmp_path / "logs"), "--cache-dir", str(tmp_path / "cache"), *extra])


def _llm_calls(tmp_path, run_id):
    trace_file = tmp_path / "logs" / f"trace-{run_id}.jsonl"
    lines = [json.loads(line) for line in trace_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [line for line in lines if line.get("action") == "LLM_CALL"]


def test_main_completes_offline_with_mocked_served_model(tmp_path, monkeypatch, capsys):
    seen: list[dict] = []
    _patch(monkeypatch, seen)

    code = _run(tmp_path, "offline-test")

    out = capsys.readouterr().out
    assert code == 0
    assert len(seen) == 1
    assert f"Requested: {smoke.DEFAULT_MODEL}" in out
    assert f"Served: {MOCK_SERVED}" in out
    assert "Policy: require" in out
    assert "Run hash: " in out
    assert smoke.FIXED_CLAIM_TEMPLATE.format(requested=smoke.DEFAULT_MODEL, served=MOCK_SERVED) in out
    assert (tmp_path / "logs" / "trace-offline-test.jsonl").is_file()
    calls = _llm_calls(tmp_path, "offline-test")
    assert len(calls) == 1
    assert calls[0]["served_model"] == MOCK_SERVED
    assert calls[0]["requested_model"] == smoke.DEFAULT_MODEL
    assert "cache_hit" not in calls[0]["output_summary"]
    assert list((tmp_path / "cache" / "offline-test").glob("*.json"))


def test_per_run_cache_directories_differ_for_different_run_ids(tmp_path, monkeypatch, capsys):
    seen: list[dict] = []
    _patch(monkeypatch, seen)

    assert _run(tmp_path, "run-one") == 0
    assert _run(tmp_path, "run-two") == 0
    capsys.readouterr()

    one = tmp_path / "cache" / "run-one"
    two = tmp_path / "cache" / "run-two"
    assert one != two
    assert list(one.glob("*.json")) and list(two.glob("*.json"))
    assert not list((tmp_path / "cache").glob("*.json"))  # nothing is written to the shared root
    # Identical prompt and settings, yet the second run called the endpoint again.
    assert len(seen) == 2
    assert all("cache_hit" not in call["output_summary"] for call in _llm_calls(tmp_path, "run-one") + _llm_calls(tmp_path, "run-two"))


def test_a_cache_filled_by_another_run_is_not_used(tmp_path, monkeypatch, capsys):
    seen: list[dict] = []
    _patch(monkeypatch, seen)
    assert _run(tmp_path, "earlier") == 0
    # Copy the earlier run's finished answer to the cache root and beside the new run.
    for entry in (tmp_path / "cache" / "earlier").glob("*.json"):
        (tmp_path / "cache" / entry.name).write_bytes(entry.read_bytes())
    capsys.readouterr()

    assert _run(tmp_path, "later") == 0

    assert len(seen) == 2  # a real (mocked) request was made again
    assert "cache_hit" not in _llm_calls(tmp_path, "later")[0]["output_summary"]
    assert "Served: " in capsys.readouterr().out


def test_run_refuses_to_reuse_a_directory_that_has_content(tmp_path, monkeypatch, capsys):
    seen: list[dict] = []
    _patch(monkeypatch, seen)
    assert _run(tmp_path, "same-id") == 0
    capsys.readouterr()

    assert _run(tmp_path, "same-id") == smoke.EXIT_NOT_CONFIGURED
    assert "already has content" in capsys.readouterr().err
    assert len(seen) == 1


def test_run_id_cannot_escape_the_cache_root(tmp_path):
    root = tmp_path / "cache"
    directory = smoke._fresh_cache_dir(root, "../../evil/..")
    assert directory.parent == root
    assert directory.is_dir()


def test_cache_hit_in_the_trace_makes_the_script_fail_loudly(tmp_path, monkeypatch, capsys):
    seen: list[dict] = []
    _patch(monkeypatch, seen)
    assert _run(tmp_path, "origin") == 0
    capsys.readouterr()
    # Force the smoke call into a directory that already holds the identical finished answer.
    seeded = tmp_path / "seeded-cache"
    seeded.mkdir()
    for entry in (tmp_path / "cache" / "origin").glob("*.json"):
        (seeded / entry.name).write_bytes(entry.read_bytes())
    monkeypatch.setattr(smoke, "_fresh_cache_dir", lambda root, run_id: seeded)

    code = _run(tmp_path, "poisoned")

    captured = capsys.readouterr()
    assert code != 0
    assert code == smoke.EXIT_CACHE_HIT
    assert "SMOKE FAILED" in captured.err and "cache_hit" in captured.err
    assert smoke.FIXED_CLAIM_TEMPLATE.split("{requested}")[0] not in captured.out  # no provenance claim
    assert len(seen) == 1  # the poisoned run made no request: it really was a cache answer
    assert "cache_hit" in _llm_calls(tmp_path, "poisoned")[0]["output_summary"]
    assert smoke._trace_shows_cache_hit(tmp_path / "logs" / "trace-poisoned.jsonl") is True
    assert smoke._trace_shows_cache_hit(tmp_path / "logs" / "trace-origin.jsonl") is False


def test_endpoint_token_and_model_come_from_settings_like_the_real_judge(tmp_path, monkeypatch, capsys):
    seen: list[dict] = []
    urls: list[str] = []
    auths: list[str] = []
    settings = _settings(MODEL_NAME="nvidia/nemotron-3-super-120b-a12b")
    _patch(monkeypatch, seen, urls=urls, auths=auths, settings=settings)

    assert _run(tmp_path, "from-settings") == 0

    out = capsys.readouterr()
    assert urls == [FAKE_URL + "/chat/completions"]
    assert auths == ["Bearer " + FAKE_TOKEN]
    assert seen[0]["model"] == "nvidia/nemotron-3-super-120b-a12b"  # MODEL_NAME, as for the real judge
    assert FAKE_TOKEN not in out.out and FAKE_TOKEN not in out.err
    assert FAKE_TOKEN not in (tmp_path / "logs" / "trace-from-settings.jsonl").read_text(encoding="utf-8")
    # An explicit --model still wins.
    assert _run(tmp_path, "explicit", "--model", smoke.DEFAULT_MODEL) == 0
    assert seen[1]["model"] == smoke.DEFAULT_MODEL


def test_missing_or_local_token_fails_before_any_request(tmp_path, monkeypatch, capsys):
    seen: list[dict] = []
    _patch(monkeypatch, seen, settings=_settings(meterless_runtime_token=None))
    assert _run(tmp_path, "no-token") == smoke.EXIT_NOT_CONFIGURED
    assert "METERLESS_RUNTIME_TOKEN" in capsys.readouterr().err
    monkeypatch.setattr(smoke, "_load_settings", lambda: _settings(meterless_runtime_token="rt-local"))
    assert _run(tmp_path, "local-token") == smoke.EXIT_NOT_CONFIGURED
    assert seen == []


def test_served_model_reads_the_tuple_and_reports_missing():
    assert smoke._served_model(("text", "req", "served-id")) == "served-id"
    assert smoke._served_model(("text", "req", "")) == "(missing)"
