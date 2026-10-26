from pathlib import Path

import pytest


# --- Exa whole-web search (real fixture captured 2026-10-23) ---------------

import httpx as _httpx

from app.tools.builtin import parse_exa_mcp, search_web

_FIX = Path(__file__).resolve().parents[1] / "fixtures" / "prior_art"


def test_parse_exa_real_fixture_finds_official_youtrack_export():
    items = parse_exa_mcp((_FIX / "exa_search_results.sse").read_text(encoding="utf-8"))
    assert len(items) >= 2
    assert all(i["url"].startswith("http") and i["title"] for i in items)
    assert any("jetbrains.com/help/youtrack" in i["url"] for i in items)


@pytest.mark.parametrize("body", ["", "event: message\ndata: {}", '{"jsonrpc":"2.0","id":1,"error":{"code":-32000,"message":"rate limit"}}',
                                  '{"jsonrpc":"2.0","id":1,"result":{"isError":true,"content":[]}}',
                                  '{"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"nothing"}]}}'])
def test_parse_exa_errors_and_empty_are_source_errors(body):
    with pytest.raises(ValueError, match="SOURCE_ERROR"):
        parse_exa_mcp(body)


def _transport(exa_status=200, exa_body=None, ddg_status=202, ddg_body=""):
    sse = (_FIX / "exa_search_results.sse").read_text(encoding="utf-8")

    def handler(request):
        u = str(request.url)
        if "mcp.exa.ai" in u:
            return _httpx.Response(exa_status, text=sse if exa_body is None else exa_body)
        if "hn.algolia.com" in u:
            return _httpx.Response(200, json={"hits": [{"title": "Show HN: ytkb", "url": "https://example.org/ytkb", "objectID": "1"}]})
        if "duckduckgo.com" in u:
            return _httpx.Response(ddg_status, text=ddg_body)
        return _httpx.Response(404)
    return _httpx.MockTransport(handler)


def test_search_web_uses_exa_and_hn_supplement(tmp_path):
    items, status = search_web("youtrack kb", transport=_transport(), cache_dir=tmp_path)
    assert status == "ok"
    assert items[0]["source"] == "exa"
    assert any(i["source"] == "hn" for i in items)


def test_search_web_exa_rate_limit_and_ddg_block_is_source_error_and_not_cached(tmp_path):
    items, status = search_web("q", transport=_transport(exa_status=429), cache_dir=tmp_path)
    assert (items, status) == ([], "SOURCE_ERROR")
    assert not list(tmp_path.rglob("*.json"))


def test_search_web_falls_back_to_ddg_when_exa_fails(tmp_path):
    ddg = (_FIX / "ddg_search_results.html").read_text(encoding="utf-8")
    items, status = search_web("q", transport=_transport(exa_status=500, ddg_status=200, ddg_body=ddg), cache_dir=tmp_path)
    assert status == "ok" and items and items[0]["source"] == "duckduckgo"


# --- Parallel / Keenable keyless MCP fallbacks (same free engines as dsh-free-search) ----

import json as _json


def _mcp_transport(exa_status=429, parallel=None, keenable=None):
    """parallel / keenable: tool text, or None for HTTP 500."""

    def reply(text):
        return _httpx.Response(200, json={"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": text}]}},
                               headers={"Mcp-Session-Id": "s1"})

    def handler(request):
        u = str(request.url)
        if "mcp.exa.ai" in u:
            return _httpx.Response(exa_status, text="")
        if "hn.algolia.com" in u:
            return _httpx.Response(200, json={"hits": []})
        for host, text in (("search.parallel.ai", parallel), ("api.keenable.ai", keenable)):
            if host in u:
                if text is None:
                    return _httpx.Response(500, text="")
                body = _json.loads(request.content.decode("utf-8"))
                if body.get("method") == "tools/call":
                    return reply(text)
                return _httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}}, headers={"Mcp-Session-Id": "s1"})
        return _httpx.Response(404)

    return _httpx.MockTransport(handler)


PARALLEL_BODY = _json.dumps({"results": [{"url": "https://github.com/plopjs/plop", "title": "Plop", "excerpts": ["Plop is a micro-generator framework"]}]})
KEENABLE_BODY = "Title: Best Hygen Alternatives\nURL: https://example.org/alt\nSnippets:\nPlop and Yeoman are alternatives"


def test_search_web_falls_back_to_parallel_when_exa_rate_limited(tmp_path):
    items, status = search_web("q", transport=_mcp_transport(parallel=PARALLEL_BODY), cache_dir=tmp_path)
    assert status == "ok" and items[0]["source"] == "parallel"
    assert "micro-generator" in items[0]["snippet"]


def test_search_web_falls_back_to_keenable_when_exa_and_parallel_fail(tmp_path):
    items, status = search_web("q", transport=_mcp_transport(parallel=None, keenable=KEENABLE_BODY), cache_dir=tmp_path)
    assert status == "ok" and items[0]["source"] == "keenable"
    assert items[0]["url"] == "https://example.org/alt" and "Yeoman" in items[0]["snippet"]


def test_search_web_empty_parallel_is_source_error_not_no_prior_art(tmp_path):
    items, status = search_web("q", transport=_mcp_transport(parallel=_json.dumps({"results": []}), keenable=None), cache_dir=tmp_path)
    assert (items, status) == ([], "SOURCE_ERROR")
    assert not list(tmp_path.rglob("*.json"))
