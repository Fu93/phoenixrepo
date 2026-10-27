"""Two Nemotron tiers, the Nemotron-only policy, the NVIDIA-direct fallback, and reasoning capture."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.agents.relevance import RelevanceClient, RelevanceLLMError, reasoning_excerpt

FAST = "nvidia/nemotron-3.5-lightning-30b-a3b"
DEEP = "nvidia/nemotron-3-ultra-550b-a55b"
ENV = (
    "PHOENIX_DEEP_MODEL", "MODEL_NAME_DEEP", "PHOENIX_DEEP_STEPS", "PHOENIX_LLM_DEEP_EXTRA_BODY",
    "PHOENIX_NEMOTRON_POLICY", "NVIDIA_API_KEY", "NVIDIA_BASE_URL", "NVIDIA_MODEL",
    "PHOENIX_LLM_EXTRA_BODY", "PHOENIX_LLM_ATTEMPT_TIMEOUT_S", "PHOENIX_LLM_TOTAL_TIMEOUT_S",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("app.agents.relevance.time.sleep", lambda _s: None)


class _Trace:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def event(self, **kwargs):
        self.events.append(kwargs)


def _reply(model: str, content: str = '{"verdict": "NO_GO"}', **message) -> httpx.Response:
    return httpx.Response(200, json={"model": model, "choices": [{"message": {"content": content, **message}}]})


def _client(tmp_path: Path, handler, **kwargs) -> RelevanceClient:
    kwargs.setdefault("model", FAST)
    return RelevanceClient(
        base_url="http://nightshift.test/v1",
        token="rt-real",
        cache_dir=tmp_path / "cache",
        transport=httpx.MockTransport(handler),
        total_timeout=60,
        **kwargs,
    )


def test_deep_steps_use_the_deep_model_and_its_own_body(tmp_path: Path):
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        return _reply(body["model"])

    client = _client(
        tmp_path, handler, deep_model=DEEP,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        deep_extra_body={"chat_template_kwargs": {"enable_thinking": True}},
    )
    client.complete("p1", step="alternative")
    client.complete("p2", step="discovery")
    assert [b["model"] for b in seen] == [FAST, DEEP]
    assert seen[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert seen[1]["chat_template_kwargs"] == {"enable_thinking": True}


def test_without_a_deep_model_every_step_uses_one_model(tmp_path: Path):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["model"])
        return _reply(FAST)

    client = _client(tmp_path, handler)
    client.complete("p", step="discovery")
    assert seen == [FAST]


def test_env_configures_the_deep_tier(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MODEL_NAME_DEEP", DEEP)
    monkeypatch.setenv("PHOENIX_DEEP_STEPS", "discovery, problem")
    client = RelevanceClient(cache_dir=tmp_path, model=FAST)
    assert client.route("problem")[0] == DEEP
    assert client.route("alternative")[0] == FAST


def test_label_policy_uses_and_flags_a_non_nemotron_answer(tmp_path: Path):
    client = _client(tmp_path, lambda r: _reply("gemini-3.1-flash-lite"))
    text, requested, served = client.complete("p", step="alternative")
    assert served == "gemini-3.1-flash-lite"
    assert client.mismatch_count == 1


def test_require_policy_discards_a_non_nemotron_answer_and_retries(tmp_path: Path):
    served = iter(["gemini-3.1-flash-lite", FAST])
    trace = _Trace()
    client = _client(tmp_path, lambda r: _reply(next(served)), nemotron_policy="require", trace=trace)
    text, _, got = client.complete("p", step="alternative")
    assert got == FAST
    assert client.rejected_serves == 1 and client.mismatch_count == 0
    statuses = [(e["output_summary"]["served_model"], e["status"]) for e in trace.events]
    assert statuses == [("gemini-3.1-flash-lite", "rejected"), (FAST, "ok")]


def test_require_policy_never_returns_a_non_nemotron_answer(tmp_path: Path):
    client = _client(tmp_path, lambda r: _reply("gemini-3.1-flash-lite"), nemotron_policy="require")
    with pytest.raises(RelevanceLLMError, match="not Nemotron"):
        client.complete("p", step="alternative")
    assert client.mismatch_count == 0


def test_fallback_calls_nvidia_directly_and_labels_it(tmp_path: Path):
    direct_seen: dict = {}

    def nvidia(request: httpx.Request) -> httpx.Response:
        direct_seen["auth"] = request.headers["Authorization"]
        direct_seen["body"] = json.loads(request.content)
        direct_seen["url"] = str(request.url)
        return _reply(FAST, '{"verdict": "ALREADY_EXISTS"}')

    trace = _Trace()
    client = _client(
        tmp_path, lambda r: _reply("gemini-3.1-flash-lite"), nemotron_policy="fallback-nvidia",
        nvidia_api_key="nvapi-test-key-000000000000", nvidia_transport=httpx.MockTransport(nvidia), trace=trace,
    )
    text, requested, served = client.complete("p", step="alternative")
    assert "ALREADY_EXISTS" in text and served == FAST and requested == FAST
    assert direct_seen["url"] == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert direct_seen["auth"] == "Bearer nvapi-test-key-000000000000"
    assert direct_seen["body"]["model"] == FAST
    assert client.direct_calls == 1 and client.mismatch_count == 0
    used = [e for e in trace.events if e["status"] == "ok"]
    assert used[-1]["output_summary"]["provider"] == "nvidia-direct"
    record = json.loads(next((tmp_path / "cache").glob("*.json")).read_text(encoding="utf-8"))
    assert record["provider"] == "nvidia-direct"
    assert "nvapi-test-key" not in json.dumps(record)


def test_fallback_also_covers_a_nightshift_502(tmp_path: Path):
    client = _client(
        tmp_path, lambda r: httpx.Response(502, text="_empty_response"), nemotron_policy="fallback-nvidia",
        nvidia_api_key="k", nvidia_transport=httpx.MockTransport(lambda r: _reply(FAST)),
    )
    assert client.complete("p", step="alternative")[2] == FAST
    assert client.direct_calls == 1


def test_fallback_without_a_key_behaves_like_require(tmp_path: Path):
    client = _client(tmp_path, lambda r: _reply("gemini-3.1-flash-lite"), nemotron_policy="fallback-nvidia", nvidia_api_key="")
    with pytest.raises(RelevanceLLMError, match="not Nemotron"):
        client.complete("p", step="alternative")


def test_a_cached_non_nemotron_answer_is_not_reused_under_require(tmp_path: Path):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return _reply("gemini-3.1-flash-lite" if calls["n"] == 1 else FAST)

    _client(tmp_path, handler).complete("p", step="alternative")  # label policy caches the Gemini answer
    strict = _client(tmp_path, handler, nemotron_policy="require")
    assert strict.complete("p", step="alternative")[2] == FAST
    assert calls["n"] == 2


def test_reasoning_is_traced_for_the_deep_tier_only(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        return _reply(model, '{"verdict": "ALREADY_EXISTS"}', reasoning_content="The alternative's README says it does the same job.")

    trace = _Trace()
    client = _client(tmp_path, handler, deep_model=DEEP, trace=trace)
    client.complete("p1", step="alternative")
    client.complete("p2", step="discovery")
    fast, deep = trace.events
    assert "reasoning" not in fast["output_summary"]
    assert deep["output_summary"]["reasoning"].startswith("The alternative's README")
    assert deep["output_summary"]["tier"] == "deep"
    assert client.last_reasoning.startswith("The alternative's README")
    # A cache hit replays the same reasoning.
    client.complete("p2", step="discovery")
    assert trace.events[-1]["output_summary"]["reasoning"] == deep["output_summary"]["reasoning"]


def test_reasoning_excerpt_reads_think_blocks_and_keeps_head_and_tail():
    assert reasoning_excerpt({}, "<think>check the quote</think>{\"a\":1}") == "check the quote"
    long = "a" * 1000 + "MIDDLE" + "b" * 1000
    out = reasoning_excerpt({"reasoning": long}, "")
    assert out.startswith("a") and out.endswith("b") and "MIDDLE" not in out and "..." in out


def test_reasoning_excerpt_accepts_a_lone_closing_think_tag():
    """Nightshift Ultra puts reasoning in content and closes it with </think> only."""
    from app.llm.parsing import parse_model_json

    content = 'The quote is on the docs page.</think>{"verdict": "ALREADY_EXISTS"}'
    assert reasoning_excerpt({}, content) == "The quote is on the docs page."
    assert "ALREADY_EXISTS" not in reasoning_excerpt({}, content)
    # The JSON answer after the tag is still what the parser returns. This extractor
    # does not change that parse.
    assert parse_model_json(content)["verdict"] == "ALREADY_EXISTS"
    draft_then_answer = 'draft {"decision": "NO_GO"}</think>{"verdict": "ALREADY_EXISTS"}'
    assert parse_model_json(draft_then_answer)["decision"] == "NO_GO"
    # A complete pair still yields only the inside, not a preamble and not the JSON answer.
    paired = 'preamble <think>check the quote</think>{"verdict": "CANDIDATE"}'
    assert reasoning_excerpt({}, paired) == "check the quote"
    # A separate field still wins over tags in the content.
    assert reasoning_excerpt({"reasoning_content": "from the field"}, content) == "from the field"
    assert reasoning_excerpt({"reasoning": "from reasoning"}, content) == "from reasoning"
    # No think tags yields none.
    assert reasoning_excerpt({}, '{"verdict": "CANDIDATE"}') == ""
    assert reasoning_excerpt({}, "plain prose with no tags") == ""
    # A </think> inside the answer is not a reasoning boundary. The excerpt must
    # not be a slice of that answer.
    answer = '{"verdict": "CANDIDATE", "reason": "the readme shows </think>"}'
    assert reasoning_excerpt({}, answer) == ""
    both = 'saw </think> in a quote</think>{"verdict": "CANDIDATE"}'
    excerpt = reasoning_excerpt({}, both)
    assert excerpt == "saw </think> in a quote"
    assert "CANDIDATE" not in excerpt


def test_extract_json_object_accepts_a_lone_think_closing_marker():
    from app.agents.relevance import extract_json_object

    assert extract_json_object('</think>\n{"verdict":"CANDIDATE"}') == {"verdict": "CANDIDATE"}


def test_invalid_policy_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError):
        RelevanceClient(cache_dir=tmp_path, nemotron_policy="maybe")


def test_evidence_report_shows_the_deep_tier_reasoning():
    from scripts.evidence_report import events_for_repo, render_repo_html

    events = [
        {"action": "LLM_CALL", "input_summary": {"repo_id": "o/r", "step": "alternative"}, "output_summary": {"served_model": FAST}},
        {
            "action": "LLM_CALL",
            "input_summary": {"repo_id": "o/r", "step": "discovery"},
            "output_summary": {"served_model": DEEP, "reasoning": "The <alt> docs page matches the job.", "tier": "deep"},
        },
    ]
    page = render_repo_html("o/r", "ALREADY_EXISTS", "full overlap", events_for_repo(events, "o/r"))
    assert "Model reasoning for the verdict" in page
    assert "The &lt;alt&gt; docs page matches the job." in page
    assert DEEP in page


def test_llm_lines_carry_latency_and_the_report_shows_median_and_max(tmp_path: Path):
    from scripts.evidence_report import served_model_rows

    trace = _Trace()
    client = _client(tmp_path, lambda r: _reply(FAST), trace=trace)
    client.complete("p1", step="alternative")
    client.complete("p1", step="alternative")  # cache hit
    fresh, cached = trace.events
    assert isinstance(fresh["output_summary"]["elapsed_s"], float)
    assert cached["output_summary"]["cache_hit"] is True and "elapsed_s" not in cached["output_summary"]
    events = [
        {"requested_model": FAST, "served_model": FAST, "output_summary": {"elapsed_s": 12.0}},
        {"requested_model": FAST, "served_model": FAST, "output_summary": json.dumps({"elapsed_s": 40.5})},
        {"requested_model": FAST, "served_model": FAST, "output_summary": {"elapsed_s": 1.0, "cache_hit": True}},
    ]
    (row,) = served_model_rows(events)
    assert row["calls"] == 3 and row["max_s"] == 40.5 and row["median_s"] == 40.5


def test_probe_timeouts_stay_below_the_nightshift_502_wait():
    from scripts import nightshift_probe as probe

    slow = [probe.Attempt(FAST, "default", 200, 57.0, FAST, True, True)]
    env = probe.recommend(slow)
    assert env["PHOENIX_LLM_ATTEMPT_TIMEOUT_S"] == "228"
    very_slow = [probe.Attempt(FAST, "default", 200, 90.0, FAST, True, True)]
    assert probe.recommend(very_slow)["PHOENIX_LLM_ATTEMPT_TIMEOUT_S"] == "240"
