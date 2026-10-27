"""The internal LLM cache identity covers every inference setting that is sent; the public trace does not."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx

from app.agents.relevance import RelevanceClient
from app.trace_log import HackathonTrace, inference_settings_canonical, inference_settings_sha256

MODEL = "nvidia/nemotron-3-super-120b-a12b"


def _client(cache_dir: Path, answer: str, **kwargs) -> RelevanceClient:
    return RelevanceClient(model="m", cache_dir=cache_dir, mock_handler=lambda p, s: answer, **kwargs)


def test_different_seed_does_not_hit_the_cache(tmp_path: Path):
    cache = tmp_path / "cache"
    first = _client(cache, "one", extra_body={"seed": 1, "min_p": 0.05})
    assert first.complete("prompt", "system")[0] == "one"
    other_seed = _client(cache, "two", extra_body={"seed": 2, "min_p": 0.05})
    assert other_seed.complete("prompt", "system")[0] == "two"
    assert other_seed.last_cache_hit is False
    other_min_p = _client(cache, "three", extra_body={"seed": 1, "min_p": 0.1})
    assert other_min_p.complete("prompt", "system")[0] == "three"
    assert other_min_p.last_cache_hit is False
    no_seed = _client(cache, "four")
    assert no_seed.complete("prompt", "system")[0] == "four"
    assert no_seed.last_cache_hit is False
    assert len(list(cache.glob("*.json"))) == 4
    # The very same settings still hit.
    again = _client(cache, "must not be called", extra_body={"seed": 1, "min_p": 0.05})
    assert again.complete("prompt", "system")[0] == "one"
    assert again.last_cache_hit is True


def test_same_settings_in_another_key_order_still_hit(tmp_path: Path):
    cache = tmp_path / "cache"
    first = _client(
        cache,
        "one",
        extra_body={
            "seed": 7,
            "min_p": 0.05,
            "chat_template_kwargs": {"enable_thinking": False, "x": 1, "a": 2},
        },
    )
    assert first.complete("prompt", "system")[0] == "one"
    reordered = _client(
        cache,
        "must not be called",
        extra_body={
            "chat_template_kwargs": {"a": 2, "x": 1, "enable_thinking": False},
            "min_p": 0.05,
            "seed": 7,
        },
    )
    assert reordered.complete("prompt", "system")[0] == "one"
    assert reordered.last_cache_hit is True
    assert len(list(cache.glob("*.json"))) == 1
    assert inference_settings_sha256({"b": 1, "a": {"d": 1, "c": 2}}) == inference_settings_sha256({"a": {"c": 2, "d": 1}, "b": 1})


def test_different_temperature_or_max_tokens_miss(tmp_path: Path):
    cache = tmp_path / "cache"
    base = _client(cache, "base")
    assert base.complete("prompt", "system")[0] == "base"
    hot = _client(cache, "hot", extra_body={"temperature": 0.7})
    assert hot.complete("prompt", "system")[0] == "hot"
    assert hot.last_cache_hit is False
    short = _client(cache, "short", max_tokens=33)
    assert short.complete("prompt", "system")[0] == "short"
    assert short.last_cache_hit is False
    other_model = RelevanceClient(model="m2", cache_dir=cache, mock_handler=lambda p, s: "m2")
    assert other_model.complete("prompt", "system")[0] == "m2"
    assert other_model.last_cache_hit is False
    assert len(list(cache.glob("*.json"))) == 4


def test_cache_record_and_hash_input_hold_no_credential_header_or_prompt_text(tmp_path: Path):
    cache = tmp_path / "cache"
    secrets = {
        "api_key": "nvapi-" + "a" * 30,
        "Authorization": "Bearer top-secret-value",
        "headers": {"Authorization": "Bearer top-secret-value", "meterless-app": "x"},
        "base_url": "https://integrate.api.nvidia.com/v1",
        "access_token": "tok-" + "b" * 20,
        "messages": [{"role": "user", "content": "PROMPT-MARKER-XYZ"}],
        "prompt": "PROMPT-MARKER-XYZ",
    }
    client = _client(cache, "the answer", extra_body={"seed": 5, **secrets}, token="rt-never-store-me")
    client.complete("PROMPT-MARKER-XYZ user text", "SYSTEM-MARKER-XYZ")
    canonical = inference_settings_canonical(
        {
            "model": "m",
            "temperature": 0,
            "stream": False,
            "max_tokens": 4096,
            "seed": 5,
            **secrets,
        }
    )
    assert json.loads(canonical) == {"max_tokens": 4096, "model": "m", "seed": 5, "stream": False, "temperature": 0}
    # Credential-like keys do not change the identity: same file name as a client without them.
    plain = _client(tmp_path / "plain", "x", extra_body={"seed": 5})
    plain.complete("PROMPT-MARKER-XYZ user text", "SYSTEM-MARKER-XYZ")
    assert plain.last_cache_file.name == client.last_cache_file.name
    record_text = client.last_cache_file.read_text(encoding="utf-8")
    for forbidden in (
        "nvapi-",
        "top-secret",
        "Bearer",
        "integrate.api.nvidia.com",
        "tok-",
        "rt-never-store-me",
        "meterless-app",
        "SYSTEM-MARKER-XYZ",
        "PROMPT-MARKER-XYZ",
        "Authorization",
        "api_key",
    ):
        assert forbidden not in canonical
        assert forbidden not in record_text
    # The settings themselves are not stored in the record, only their hash.
    assert '"seed"' not in record_text and "min_p" not in record_text
    record = json.loads(record_text)
    assert len(record["inference_settings_sha256"]) == 64
    assert record["inference_settings_sha256"] == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert not any(key in record for key in ("request_settings", "inference_settings", "body", "headers", "messages"))
    # Token-shaped values on a harmless key name are dropped from the identity as well.
    assert "nvapi-" not in inference_settings_canonical({"note": "nvapi-" + "c" * 30, "seed": 1})


def test_a_legacy_entry_with_only_the_sanitized_settings_hash_is_a_miss(tmp_path: Path):
    cache = tmp_path / "cache"
    first = _client(cache, "fresh", extra_body={"seed": 1})
    first.complete("prompt", "system")
    path = first.last_cache_file
    record = json.loads(path.read_text(encoding="utf-8"))
    del record["inference_settings_sha256"]  # what the previous revision wrote at this key
    path.write_text(json.dumps(record), encoding="utf-8")
    again = _client(cache, "second", extra_body={"seed": 1})
    assert again.complete("prompt", "system")[0] == "second"
    assert again.last_cache_hit is False


def test_trace_keeps_only_allowlisted_settings_while_seed_is_sent_and_keys_the_cache(tmp_path: Path, monkeypatch):
    for name in ("PHOENIX_LLM_EXTRA_BODY", "PHOENIX_LLM_MAX_TOKENS", "PHOENIX_NEMOTRON_POLICY", "NVIDIA_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"model": MODEL, "choices": [{"message": {"content": '{"ok": true}'}}]})

    trace = HackathonTrace("inference-key", tmp_path / "logs")
    cache = tmp_path / "cache"

    def make(seed: int) -> RelevanceClient:
        return RelevanceClient(
            token="rt-test",
            model=MODEL,
            cache_dir=cache,
            transport=httpx.MockTransport(handler),
            trace=trace,
            max_tokens=64,
            extra_body={"seed": seed, "min_p": 0.05, "top_p": 0.9},
        )

    make(1).complete("p", "s", repo_id="o/r", step="problem")
    make(2).complete("p", "s", repo_id="o/r", step="problem")
    replay = make(1)
    replay.complete("p", "s", repo_id="o/r", step="problem")
    assert replay.last_cache_hit is True
    assert [body["seed"] for body in bodies] == [1, 2]  # the seed really was sent; seed 1 was replayed
    assert all(body["min_p"] == 0.05 for body in bodies)
    trace_text = trace.path.read_text(encoding="utf-8")
    assert '"seed"' not in trace_text and "min_p" not in trace_text
    lines = [json.loads(line) for line in trace_text.splitlines()]
    llm = [line for line in lines if line.get("action") == "LLM_CALL"]
    assert llm[0]["request_settings"] == {"max_tokens": 64, "temperature": 0, "top_p": 0.9}
    assert llm[1]["request_settings"] == llm[0]["request_settings"]
    assert llm[0]["request_settings_sha256"] == llm[1]["request_settings_sha256"]
    assert "inference_settings_sha256" not in trace_text
    assert "request_settings" not in llm[2]  # the replay posted nothing
