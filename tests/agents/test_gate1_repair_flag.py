"""PHOENIX_GATE1_EVIDENCE_REPAIR is default off, and off means main's behaviour.

The expected "off" values below were produced by running the same offline fixtures against
a clean checkout of main 37ab838 (see docs/GATE1_EVIDENCE_REPAIR.md). "On" keeps the repair.
No network and no model calls.
"""
import json
from datetime import date

import httpx
import pytest

from app.agents import gate1_flag, repo_facts, substitution
from app.agents.alternative_evidence import repair_fields
from app.agents.blocker import evaluate_blocker
from app.agents.relevance import RelevanceClient, judge_alternative_relevance, judge_discovery, search_prior_art
from app.tools import Budget, GitHubTool, NpmTool, PyPiTool, ToolBox, WebSearchTool

FLAG = "PHOENIX_GATE1_EVIDENCE_REPAIR"
QUOTE = "Sync notes between two folders on your own site."
CAND = {"repo_id": "acme/widget", "description": "A small tool that syncs notes between two folders.",
        "readme_excerpt": "A small tool that syncs notes between two folders.", "stars": 20, "free": True, "self_hostable": True}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (FLAG, "PHOENIX_UNKNOWN_BLOCKER_REVIEW", "PHOENIX_STRICT_SUBSTITUTION"):
        monkeypatch.delenv(name, raising=False)


def set_flag(monkeypatch, on):
    if on:
        monkeypatch.setenv(FLAG, "1")


def judged(tmp_path, rows, verdict="CANDIDATE", alt=None, alt_quote=None):
    reply = {"verdict": verdict, "reason": "recorded", "problem_quote": CAND["description"],
             "alternative_repo": alt, "alternative_quote": alt_quote}
    client = RelevanceClient(cache_dir=tmp_path, mock_handler=lambda _p, _s: json.dumps(reply))
    return judge_discovery(CAND, prior_rows=rows, demand_rows=[], client=client)


def row(**extra):
    base = {"alternative_id": "someone/tool", "url": "https://github.com/someone/tool", "source": "github",
            "same_problem": True, "same_job": True, "overlap": "full", "quotes": ["a fabricated sentence"],
            "quote_verified": True, "stars": 500, "pushed_at": "2026-10-01", "archived": False,
            "licence_spdx": "MIT", "page_kind": "product", "description": "other text", "excerpt": "other text"}
    base.update(extra)
    return base


@pytest.mark.parametrize("value,expected", [(None, False), ("", False), ("0", False), ("no", False), ("maybe", False),
                                             ("1", True), ("true", True), (" On ", True), ("ENABLED", True)])
def test_flag_is_explicit_opt_in(monkeypatch, value, expected):
    if value is not None:
        monkeypatch.setenv(FLAG, value)
    assert gate1_flag.enabled() is expected


def test_flag_is_independent_of_the_other_two_switches(monkeypatch):
    monkeypatch.setenv("PHOENIX_STRICT_SUBSTITUTION", "1")
    assert not gate1_flag.enabled() and substitution.enabled()
    monkeypatch.delenv("PHOENIX_STRICT_SUBSTITUTION")
    monkeypatch.setenv("PHOENIX_UNKNOWN_BLOCKER_REVIEW", "1")
    assert not gate1_flag.enabled()
    monkeypatch.delenv("PHOENIX_UNKNOWN_BLOCKER_REVIEW")
    monkeypatch.setenv(FLAG, "1")
    assert gate1_flag.enabled() and not substitution.enabled()


@pytest.mark.parametrize("on", [False, True])
def test_blocking_quote_not_in_collected_text_only_overrules_when_the_repair_is_on(tmp_path, monkeypatch, on):
    set_flag(monkeypatch, on)
    verdict, reason = judged(tmp_path, [row()])
    if on:
        assert verdict == "NEEDS_REVIEW" and reason.startswith("UNVERIFIED_BLOCKER_QUOTE")
    else:  # main: a caller-set quote_verified=True is trusted
        assert verdict == "ALREADY_EXISTS" and "a fabricated sentence" in reason


@pytest.mark.parametrize("on", [False, True])
def test_blocking_quote_present_in_collected_text_still_overrules_either_way(tmp_path, monkeypatch, on):
    set_flag(monkeypatch, on)
    verdict, reason = judged(tmp_path, [row(quotes=[QUOTE], description=QUOTE, excerpt=QUOTE)])
    assert verdict == "ALREADY_EXISTS" and QUOTE in reason


@pytest.mark.parametrize("on", [False, True])
def test_source_error_row_only_holds_the_candidate_when_the_repair_is_on(tmp_path, monkeypatch, on):
    set_flag(monkeypatch, on)
    verdict, reason = judged(tmp_path, [row(quotes=[QUOTE], description=QUOTE, excerpt=QUOTE, stars=None, pushed_at="",
                                            fetch_status="source_error")])
    assert (verdict, reason.startswith("ALTERNATIVE_FACTS_UNAVAILABLE")) == (("NEEDS_REVIEW", True) if on else ("CANDIDATE", False))


@pytest.mark.parametrize("on", [False, True])
def test_model_named_alternative_matching(tmp_path, monkeypatch, on):
    set_flag(monkeypatch, on)
    web = row(alternative_id="web:https://example.com/yt-tool", url="https://example.com/yt-tool", source="web",
              description="yt-cli: sync notes " + QUOTE, excerpt="yt-cli: " + QUOTE, quotes=[QUOTE], licence_spdx="MIT", fetch_status="")
    verdict, reason = judged(tmp_path, [web], verdict="ALREADY_EXISTS", alt="yt-cli", alt_quote=QUOTE)
    assert verdict == "ALREADY_EXISTS"
    # main accepts a substring of the description as the model's named alternative and keeps its reason;
    # the repair requires an exact canonical id or a unique short name and re-derives the refusal.
    assert reason.startswith("verified full-overlap alternative") is on
    assert reason.startswith("recorded") is (not on)


@pytest.mark.parametrize("on", [False, True])
def test_repo_url_parsing(monkeypatch, on):
    set_flag(monkeypatch, on)
    got = [list(repo_facts._api_url(text)) for text in (
        "https://evil.test/github.com/other/converter", "https://github.com/search?q=converter", "other/converter?x=1",
        "https://gitlab.com/a/b/c", "web:https://github.com/o/r.git", "https://github.com/o")]
    main = [["github", "https://api.github.com/repos/other/converter"], ["github", None],
            ["github", "https://api.github.com/repos/other/converter?x=1"], ["gitlab", "https://gitlab.com/api/v4/projects/a%2Fb"],
            ["github", "https://api.github.com/repos/o/r.git"], ["github", None]]
    repaired = [[None, None], [None, None], [None, None], ["gitlab", "https://gitlab.com/api/v4/projects/a%2Fb%2Fc"],
                ["github", "https://api.github.com/repos/o/r"], [None, None]]
    assert got == (repaired if on else main)


@pytest.mark.parametrize("on", [False, True])
def test_string_booleans_in_facts(monkeypatch, on):
    set_flag(monkeypatch, on)
    alt = {"alternative_id": "web:https://x.example/a", "url": "https://x.example/a", "source": "web", "same_problem": True,
           "same_job": True, "overlap": "full", "quote_verified": True, "quotes": ["q"], "pushed_at": "2026-10-01",
           "archived": False, "page_kind": "product", "stars": 900, "free_tier": "false", "licence_spdx": ""}
    blocks = evaluate_blocker(CAND, alt, as_of=date(2026, 10, 27))["blocks"]
    assert blocks is (not on)  # main reads the string "false" as a free tier


def _toolbox():
    ddg = '<html><body><a class="result__a" href="https://github.com/other/found">Found</a><a class="result__snippet">Sync notes</a></body></html>'

    def handler(req):
        url = str(req.url)
        if "api.github.com/search" in url:
            return httpx.Response(200, json={"items": [{"full_name": "o/r", "html_url": "https://github.com/o/r", "description": "sync notes",
                                                         "license": {"spdx_id": "MIT"}, "pushed_at": "2026-10-01T00:00:00Z", "fork": False}]})
        if "api.github.com/repos" in url and "readme" in url:
            return httpx.Response(200, json={"content": "", "encoding": "base64"})
        if "api.github.com/repos" in url:
            return httpx.Response(200, json={"stargazers_count": 7, "pushed_at": "2026-10-01T00:00:00Z", "archived": False, "license": {"spdx_id": "MIT"}})
        if "duckduckgo" in url:
            return httpx.Response(200, text=ddg)
        return httpx.Response(404)

    tools = {name: cls(transport=httpx.MockTransport(handler)) for name, cls in
             (("github", GitHubTool), ("npm", NpmTool), ("pypi", PyPiTool), ("web_search", WebSearchTool))}
    toolbox = ToolBox(tools=tools, budget=Budget(max_calls=20, max_seconds=30.0))
    calls = []
    original = toolbox.call
    toolbox.call = lambda name, args: (calls.append((name, args.get("action"), args.get("repo"))), original(name, args))[1]
    return toolbox, calls


@pytest.mark.parametrize("on", [False, True])
def test_search_rows_and_tool_calls(monkeypatch, on):
    set_flag(monkeypatch, on)
    toolbox, calls = _toolbox()
    rows = {r["alternative_id"]: r for r in search_prior_art(candidate={"repo_id": "acme/widget", "description": CAND["description"]},
                                                              queries=["notes sync"], toolbox=toolbox)}
    web, gh = rows["web:https://github.com/other/found"], rows["o/r"]
    if on:
        assert ("github", "repo", "other/found") in calls and web["stars"] == 7 and gh["stars"] is None and web["title"] == "Found"
    else:  # main: no extra host lookup, placeholder stars, no title / archived keys
        assert calls == [("github", "repositories", None), ("npm", "search", None), ("web_search", None, None), ("github", "readme", "o/r")]
        assert web["stars"] == 0 and gh["stars"] == 0 and "title" not in web and "archived" not in gh and "fetch_status" not in web


@pytest.mark.parametrize("on", [False, True])
def test_model_row_extras_and_prompt_cache_key(tmp_path, monkeypatch, on):
    set_flag(monkeypatch, on)
    seen = []

    def handler(prompt, system):
        seen.append((system, prompt))
        return json.dumps({"same_problem": True, "overlap": "full", "reason": "r", "quotes": [QUOTE]})

    source = {"alternative_id": "o/r", "url": "https://github.com/o/r", "source": "github", "description": QUOTE,
              "stars": 5, "pushed_at": "2026-10-01", "archived": False, "licence_spdx": "MIT", "self_hosted": True, "title": "t"}
    prob = {"problem": CAND["description"], "target_user": "u", "category": "c"}
    got, _ = judge_alternative_relevance(prob, "acme/widget", source, RelevanceClient(cache_dir=tmp_path, mock_handler=handler))
    assert ("text" in got) is on and ("self_hosted" in got) is on
    # The prompt text never depends on this flag, so the cache key of a default run is unchanged.
    assert len(seen) == 1 and "QUOTED SUBSTITUTION" not in seen[0][0]
    assert repair_fields(source, "x") == ({**{k: v for k, v in source.items() if k != "alternative_id" and k != "url" and k != "source" and k != "description"}, "text": "x"} if on else {})


@pytest.mark.parametrize("on", [False, True])
def test_prompts_are_identical_with_the_flag_on_or_off(tmp_path, monkeypatch, on):
    set_flag(monkeypatch, on)
    seen = []
    client = RelevanceClient(cache_dir=tmp_path, mock_handler=lambda p, s: (seen.append((s, p)), json.dumps(
        {"verdict": "CANDIDATE", "reason": "r", "problem_quote": CAND["description"]}))[1])
    judge_discovery(CAND, prior_rows=[row(quotes=[QUOTE], description=QUOTE, excerpt=QUOTE)], demand_rows=[], client=client)
    import hashlib
    digest = hashlib.sha256(json.dumps(seen, sort_keys=True).encode()).hexdigest()
    monkeypatch.delenv(FLAG, raising=False)
    seen_off = []
    client_off = RelevanceClient(cache_dir=tmp_path / "off", mock_handler=lambda p, s: (seen_off.append((s, p)), json.dumps(
        {"verdict": "CANDIDATE", "reason": "r", "problem_quote": CAND["description"]}))[1])
    judge_discovery(CAND, prior_rows=[row(quotes=[QUOTE], description=QUOTE, excerpt=QUOTE)], demand_rows=[], client=client_off)
    assert digest == hashlib.sha256(json.dumps(seen_off, sort_keys=True).encode()).hexdigest()
