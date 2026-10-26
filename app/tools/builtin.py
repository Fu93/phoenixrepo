"""Builtin tools for the Phoenix shared toolbox."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import os
import urllib.parse
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import httpx

from app.tools.base import ToolResult


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tool_cache_path(tool_name: str, query: str, cache_dir: Path | str | None = None) -> Path:
    if cache_dir is None and os.environ.get("PHOENIX_TOOL_CACHE_DIR"):
        cache_dir = Path(os.environ["PHOENIX_TOOL_CACHE_DIR"]) / tool_name
    if cache_dir is None:
        cache_dir = Path(__file__).resolve().parent.parent.parent / ".cache" / "tools" / tool_name
    else:
        cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    q_hash = hashlib.sha256(query.strip().lower().encode("utf-8")).hexdigest()
    return cache_dir / f"{q_hash}.json"


class PyPiParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._cur: dict[str, str] | None = None
        self._active_class: str = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        cls = str(attr_dict.get("class") or "")
        if tag == "a" and "package-snippet" in cls:
            href = str(attr_dict.get("href") or "")
            if href.startswith("/"):
                href = f"https://pypi.org{href}"
            self._cur = {"url": href, "name": "", "description": "", "version": ""}
        if "package-snippet__name" in cls:
            self._active_class = "name"
        elif "package-snippet__description" in cls:
            self._active_class = "desc"
        elif "package-snippet__version" in cls:
            self._active_class = "ver"

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._cur:
            if self._cur.get("name"):
                self.results.append(self._cur)
            self._cur = None
        self._active_class = ""

    def handle_data(self, data: str) -> None:
        if self._cur and self._active_class:
            val = data.strip()
            if self._active_class == "name":
                self._cur["name"] += (" " + val if self._cur["name"] and val else val)
            elif self._active_class == "desc":
                self._cur["description"] += (" " + val if self._cur["description"] and val else val)
            elif self._active_class == "ver":
                self._cur["version"] += val


class DDGParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._cur: dict[str, str] | None = None
        self._active: str = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        cls = str(attr_dict.get("class") or "")
        if "result__title" in cls or "result__a" in cls:
            if tag == "a":
                href = str(attr_dict.get("href") or "")
                if "uddg=" in href:
                    parsed = urllib.parse.urlparse(href)
                    qs = urllib.parse.parse_qs(parsed.query)
                    if "uddg" in qs:
                        href = urllib.parse.unquote(qs["uddg"][0])
                if href.startswith("//"):
                    href = f"https:{href}"
                if self._cur is None:
                    self._cur = {"url": href, "title": "", "snippet": ""}
                else:
                    self._cur["url"] = href
                self._active = "title"
        elif "result__snippet" in cls:
            self._active = "snippet"

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._active == "title":
            self._active = ""
        elif self._active == "snippet" and (tag == "a" or tag == "div"):
            self._active = ""
            if self._cur and self._cur.get("url") and (self._cur.get("title") or self._cur.get("snippet")):
                self.results.append(self._cur)
                self._cur = None

    def handle_data(self, data: str) -> None:
        if self._cur:
            val = data.strip()
            if not val:
                return
            if self._active == "title":
                self._cur["title"] = (self._cur["title"] + " " + val).strip()
            elif self._active == "snippet":
                self._cur["snippet"] = (self._cur["snippet"] + " " + val).strip()


def parse_npm_json(data: dict[str, Any] | str) -> list[dict[str, Any]]:
    if isinstance(data, str):
        data = json.loads(data)
    if not isinstance(data, dict):
        raise ValueError("Invalid npm JSON: expected dict")

    if "objects" not in data:
        if "name" in data:
            return [
                {
                    "name": str(data.get("name")),
                    "description": str(data.get("description") or ""),
                    "url": f"https://www.npmjs.com/package/{data.get('name')}",
                    "license": str(data.get("license") or ""),
                    "version": str(data.get("version") or ""),
                }
            ]
        raise ValueError("Invalid npm JSON: 'objects' missing and not single package")

    results: list[dict[str, Any]] = []
    for obj in data.get("objects") or []:
        pkg = obj.get("package") or {}
        name = pkg.get("name")
        if not name:
            continue
        links = pkg.get("links") or {}
        url = links.get("npm") or f"https://www.npmjs.com/package/{name}"
        desc = pkg.get("description") or ""
        lic = pkg.get("license") or ""
        ver = pkg.get("version") or ""
        results.append(
            {
                "name": name,
                "description": desc,
                "url": url,
                "license": lic,
                "version": ver,
            }
        )
    return results


def parse_pypi_html(html_text: str) -> list[dict[str, Any]]:
    text = html_text.strip()
    if not text:
        raise ValueError("SOURCE_ERROR: empty PyPI response")

    if text.startswith("{"):
        try:
            data = json.loads(text)
            info = data.get("info") or data
            name = info.get("name")
            if name:
                return [
                    {
                        "name": name,
                        "description": info.get("summary") or info.get("description") or "",
                        "url": f"https://pypi.org/project/{name}/",
                        "version": info.get("version") or "",
                    }
                ]
        except Exception:
            pass

    if "<html" not in text.lower() and "<body" not in text.lower() and "<!doctype" not in text.lower():
        raise ValueError("SOURCE_ERROR: response is not valid HTML")

    parser = PyPiParser()
    try:
        parser.feed(text)
    except Exception as exc:
        raise ValueError(f"SOURCE_ERROR: PyPI HTML parse failed: {exc}") from exc

    return parser.results


def parse_ddg_html(html_text: str) -> list[dict[str, Any]]:
    text = html_text.strip()
    if not text:
        raise ValueError("SOURCE_ERROR: empty DuckDuckGo response")

    if "<html" not in text.lower() and "<body" not in text.lower() and "<!doctype" not in text.lower():
        raise ValueError("SOURCE_ERROR: response is not valid HTML")

    parser = DDGParser()
    try:
        parser.feed(text)
    except Exception as exc:
        raise ValueError(f"SOURCE_ERROR: DuckDuckGo HTML parse failed: {exc}") from exc

    return parser.results


def search_github(
    query: str,
    token: str | None = None,
    transport: httpx.BaseTransport | None = None,
    timeout_seconds: float = 10.0,
    cache_dir: Path | str | None = None,
    per_page: int = 10,
) -> tuple[list[dict[str, Any]], str]:
    q = query.strip()
    if not q:
        return [], "ok"
    cache_file = _tool_cache_path("github", q, cache_dir)
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            return cached.get("items", []), cached.get("status", "ok")
        except Exception:
            pass

    url = f"https://api.github.com/search/repositories?q={urllib.parse.quote_plus(q)}&per_page={per_page}"
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "PhoenixRepo-Toolbox/1.0",
    }
    tok = token or os.environ.get("GITHUB_TOKEN") or ""
    if tok:
        headers["Authorization"] = f"Bearer {tok}"

    try:
        with httpx.Client(transport=transport, timeout=timeout_seconds) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code in {403, 429} or resp.status_code >= 400:
                return [], "SOURCE_ERROR"
            data = resp.json()
            items = data.get("items") or []
            cache_file.write_text(json.dumps({"items": items, "status": "ok"}, indent=2), encoding="utf-8")
            return items, "ok"
    except Exception:
        return [], "SOURCE_ERROR"


def search_npm(
    query: str,
    transport: httpx.BaseTransport | None = None,
    timeout_seconds: float = 10.0,
    cache_dir: Path | str | None = None,
    size: int = 10,
) -> tuple[list[dict[str, Any]], str]:
    q = query.strip()
    if not q:
        return [], "ok"
    cache_file = _tool_cache_path("npm", q, cache_dir)
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            return cached.get("items", []), cached.get("status", "ok")
        except Exception:
            pass

    url = f"https://registry.npmjs.org/-/v1/search?text={urllib.parse.quote_plus(q)}&size={size}"
    try:
        with httpx.Client(transport=transport, timeout=timeout_seconds) as client:
            resp = client.get(url)
            if resp.status_code >= 400:
                return [], "SOURCE_ERROR"
            items = parse_npm_json(resp.json())
            cache_file.write_text(json.dumps({"items": items, "status": "ok"}, indent=2), encoding="utf-8")
            return items, "ok"
    except Exception:
        return [], "SOURCE_ERROR"


def search_pypi(
    query: str,
    transport: httpx.BaseTransport | None = None,
    timeout_seconds: float = 10.0,
    cache_dir: Path | str | None = None,
) -> tuple[list[dict[str, Any]], str]:
    q = query.strip()
    if not q:
        return [], "ok"
    cache_file = _tool_cache_path("pypi", q, cache_dir)
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            return cached.get("items", []), cached.get("status", "ok")
        except Exception:
            pass

    url = f"https://pypi.org/search/?q={urllib.parse.quote_plus(q)}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    try:
        with httpx.Client(transport=transport, timeout=timeout_seconds) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code >= 400:
                return [], "SOURCE_ERROR"
            items = parse_pypi_html(resp.text)
            cache_file.write_text(json.dumps({"items": items, "status": "ok"}, indent=2), encoding="utf-8")
            return items, "ok"
    except Exception:
        return [], "SOURCE_ERROR"


EXA_MCP_URL = "https://mcp.exa.ai/mcp"
HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search"


def parse_exa_mcp(body: str, source: str = "exa") -> list[dict[str, Any]]:
    """Parse a keyless Exa MCP ``web_search_exa`` response (JSON or SSE) into items.

    Raises ValueError("SOURCE_ERROR: ...") on any malformed, error or empty response:
    a rate limit or an empty answer must never look like "no prior art".
    """
    text = (body or "").strip()
    if not text:
        raise ValueError("SOURCE_ERROR: empty Exa response")
    payload: Any = None
    if text.startswith("{"):
        payload = json.loads(text)
    else:
        for line in text.splitlines():
            if line.startswith("data:"):
                chunk = line[5:].strip()
                if chunk:
                    payload = json.loads(chunk)
                    break
    if not isinstance(payload, dict):
        raise ValueError("SOURCE_ERROR: Exa response has no JSON payload")
    if payload.get("error"):
        raise ValueError(f"SOURCE_ERROR: Exa error {payload['error']}")
    result = payload.get("result") or {}
    if result.get("isError"):
        raise ValueError("SOURCE_ERROR: Exa tool returned isError")
    blocks = [str(c.get("text") or "") for c in (result.get("content") or []) if isinstance(c, dict)]
    joined = "\n".join(blocks)
    items: list[dict[str, Any]] = []
    parts = re.split(r"(?m)^Title: ", joined)
    for part in parts[1:]:
        lines = part.splitlines()
        title = lines[0].strip() if lines else ""
        m = re.search(r"(?m)^URL: (\S+)", part)
        if not m:
            continue
        url = m.group(1).strip()
        snippet = ""
        hm = re.search(r"(?m)^(?:Highlights|Snippets):", part)
        if hm:
            snippet = " ".join(part[hm.end():].split())[:800]
        items.append({"url": url, "title": title, "snippet": snippet, "source": source})
    if not items:
        raise ValueError("SOURCE_ERROR: Exa returned no parseable results")
    return items


def _search_exa(q: str, client: httpx.Client, num_results: int = 8) -> list[dict[str, Any]]:
    resp = client.post(
        EXA_MCP_URL,
        headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "web_search_exa", "arguments": {"query": q, "numResults": num_results}},
        },
    )
    if resp.status_code != 200:
        raise ValueError(f"SOURCE_ERROR: Exa HTTP {resp.status_code}")
    return parse_exa_mcp(resp.text)


PARALLEL_MCP_URL = "https://search.parallel.ai/mcp"
KEENABLE_MCP_URL = "https://api.keenable.ai/mcp"
_MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _mcp_payload(resp: httpx.Response, label: str) -> dict[str, Any]:
    if resp.status_code not in (200, 202):
        raise ValueError(f"SOURCE_ERROR: {label} HTTP {resp.status_code}")
    text = (resp.text or "").strip()
    if not text:
        return {}
    if text.startswith("{"):
        return json.loads(text)
    for line in text.splitlines():
        if line.startswith("data:") and line[5:].strip():
            return json.loads(line[5:].strip())
    raise ValueError(f"SOURCE_ERROR: {label} response has no JSON payload")


def _mcp_tool_call(
    client: httpx.Client, url: str, tool: str, arguments: dict[str, Any], label: str
) -> dict[str, Any]:
    """Run one keyless MCP tool call (initialize, initialized, tools/call). Raises SOURCE_ERROR."""
    init = client.post(
        url,
        headers=_MCP_HEADERS,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "phoenixrepo", "version": "1"},
            },
        },
    )
    _mcp_payload(init, label)
    headers = dict(_MCP_HEADERS)
    sid = init.headers.get("Mcp-Session-Id")
    if sid:
        headers["Mcp-Session-Id"] = sid
    try:
        client.post(url, headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    except Exception:
        pass
    resp = client.post(
        url,
        headers=headers,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool, "arguments": arguments}},
    )
    payload = _mcp_payload(resp, label)
    if payload.get("error"):
        raise ValueError(f"SOURCE_ERROR: {label} error {payload['error']}")
    result = payload.get("result") or {}
    if result.get("isError"):
        raise ValueError(f"SOURCE_ERROR: {label} tool returned isError")
    return result


def _mcp_text(result: dict[str, Any]) -> str:
    return "\n".join(str(c.get("text") or "") for c in (result.get("content") or []) if isinstance(c, dict))


def parse_parallel_result(text: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(text)
    except Exception as exc:
        raise ValueError("SOURCE_ERROR: Parallel response is not JSON") from exc
    items: list[dict[str, Any]] = []
    for r in (data.get("results") or []) if isinstance(data, dict) else []:
        url = str(r.get("url") or "").strip()
        if not url:
            continue
        excerpts = r.get("excerpts") or []
        snippet = " ".join(" ".join(str(e) for e in excerpts).split())[:800]
        items.append(
            {"url": url, "title": " ".join(str(r.get("title") or "").split()), "snippet": snippet, "source": "parallel"}
        )
    if not items:
        raise ValueError("SOURCE_ERROR: Parallel returned no parseable results")
    return items


def _search_parallel(q: str, client: httpx.Client) -> list[dict[str, Any]]:
    result = _mcp_tool_call(
        client,
        PARALLEL_MCP_URL,
        "web_search",
        {"objective": q, "search_queries": [q]},
        "Parallel",
    )
    return parse_parallel_result(_mcp_text(result))


def _search_keenable(q: str, client: httpx.Client, max_results: int = 8) -> list[dict[str, Any]]:
    result = _mcp_tool_call(
        client,
        KEENABLE_MCP_URL,
        "search_web_pages",
        {"query": q, "max_results": max_results},
        "Keenable",
    )
    body = _mcp_text(result)
    items = _parse_titled_blocks(body, "keenable")
    if not items:
        raise ValueError("SOURCE_ERROR: Keenable returned no parseable results")
    return items


def _parse_titled_blocks(joined: str, source: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for part in re.split(r"(?m)^Title: ", joined)[1:]:
        lines = part.splitlines()
        title = lines[0].strip() if lines else ""
        m = re.search(r"(?m)^URL: (\S+)", part)
        if not m:
            continue
        hm = re.search(r"(?m)^(?:Highlights|Snippets):", part)
        snippet = " ".join(part[hm.end():].split())[:800] if hm else ""
        items.append({"url": m.group(1).strip(), "title": title, "snippet": snippet, "source": source})
    return items


def _search_hn(q: str, client: httpx.Client, limit: int = 3) -> list[dict[str, Any]]:
    """Supplementary Hacker News stories (Show HN / launches). Failures are ignored."""
    try:
        resp = client.get(HN_SEARCH_URL, params={"query": q, "tags": "story", "hitsPerPage": limit})
        if resp.status_code != 200:
            return []
        out = []
        for hit in (resp.json().get("hits") or [])[:limit]:
            url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
            title = str(hit.get("title") or "").strip()
            if title:
                out.append({"url": url, "title": title, "snippet": f"Hacker News story: {title}", "source": "hn"})
        return out
    except Exception:
        return []


def _search_ddg(q: str, client: httpx.Client) -> list[dict[str, Any]]:
    url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(q)}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    resp = client.get(url, headers=headers)
    if resp.status_code != 200:
        # DuckDuckGo answers 202 with a challenge page when it rate-limits bots.
        raise ValueError(f"SOURCE_ERROR: DuckDuckGo HTTP {resp.status_code}")
    items = parse_ddg_html(resp.text)
    if not items:
        # A 200 page with no results is treated as a block page, never as "no prior art".
        raise ValueError("SOURCE_ERROR: DuckDuckGo returned no results")
    for it in items:
        it.setdefault("source", "duckduckgo")
    return items


def search_web(
    query: str,
    transport: httpx.BaseTransport | None = None,
    timeout_seconds: float = 30.0,
    cache_dir: Path | str | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Whole-web search: Exa (keyless MCP) first, then Parallel and Keenable keyless MCP
    (the same free engines the dsh-free-search plugin uses), DuckDuckGo HTML last, with
    Hacker News as a supplement. Bing is deliberately not used (bot-degraded junk).

    Returns (items, "ok") or ([], "SOURCE_ERROR"). Only non-empty ok results are cached.
    """
    q = query.strip()
    if not q:
        return [], "ok"
    cache_file = _tool_cache_path("web", q, cache_dir)
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if cached.get("status") == "ok" and cached.get("items"):
                return cached["items"], "ok"
        except Exception:
            pass

    items: list[dict[str, Any]] = []
    try:
        with httpx.Client(transport=transport, timeout=timeout_seconds, follow_redirects=True) as client:
            for engine in (_search_exa, _search_parallel, _search_keenable, _search_ddg):
                try:
                    items = engine(q, client)
                except Exception:
                    items = []
                    continue
                if items:
                    break
            if not items:
                return [], "SOURCE_ERROR"
            if items[0].get("source") != "duckduckgo":
                seen = {i["url"] for i in items}
                items += [h for h in _search_hn(q, client) if h["url"] not in seen]
    except Exception:
        return [], "SOURCE_ERROR"

    cache_file.write_text(json.dumps({"items": items, "status": "ok"}, indent=2), encoding="utf-8")
    return items, "ok"


class WebSearchTool:
    """Whole-web search tool: Exa keyless MCP, Hacker News supplement, DuckDuckGo fallback."""

    name = "web_search"

    def __init__(
        self,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 30.0,
        cache_dir: Path | str | None = None,
    ) -> None:
        self.transport = transport
        self.timeout_seconds = timeout_seconds
        self.cache_dir = cache_dir

    def call(self, args: dict[str, Any]) -> ToolResult:
        now = _utc_now()
        query = str(args.get("query") or args.get("q") or "").strip()
        if not query:
            return ToolResult(ok=False, data="", error="query required", source_url="", fetched_at=now)
        items, status = search_web(
            query=query,
            transport=self.transport,
            timeout_seconds=self.timeout_seconds,
            cache_dir=self.cache_dir,
        )
        url = f"{EXA_MCP_URL}?q={urllib.parse.quote_plus(query)}"
        if status == "SOURCE_ERROR":
            return ToolResult(
                ok=False,
                data="",
                error="SOURCE_ERROR: web search failed (Exa, Parallel, Keenable and DuckDuckGo)",
                source_url=url,
                fetched_at=now,
            )
        return ToolResult(
            ok=True,
            data={"items": items},
            error="",
            source_url=url,
            fetched_at=now,
        )


class GitHubTool:
    """GitHub API tool: repo metadata, README, and issues search.

    Never reads stars/forks as a demand or quality signal for the candidate repo.
    """

    name = "github"

    def __init__(
        self,
        token: str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.token = token or os.environ.get("GITHUB_TOKEN") or ""
        self.transport = transport
        self.timeout_seconds = timeout_seconds

    def call(self, args: dict[str, Any]) -> ToolResult:
        now = _utc_now()
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "PhoenixRepo-Toolbox/1.0",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        action = str(args.get("action") or "").lower()
        query = str(args.get("query") or "")
        repo = str(args.get("repo") or "").strip()

        if action == "file":
            path = str(args.get("path") or "").strip().lstrip("/")
            if not repo or not path:
                return ToolResult(ok=False, data="", error="repo and path required for file", source_url="", fetched_at=now)
            url = (
                f"https://api.github.com/repos/{urllib.parse.quote(repo, safe='/')}"
                f"/contents/{urllib.parse.quote(path, safe='/')}"
            )
        # Queries come from model-extracted problem text ("C# tools", "Q&A bot"): an unencoded
        # '&', '#' or '+' silently cut or changed the search, so every value is URL-encoded.
        elif action in {"repositories", "search_repos", "search_repositories"}:
            q = urllib.parse.quote_plus(query)
            per_page = int(args.get("per_page") or 10)
            url = f"https://api.github.com/search/repositories?q={q}&per_page={per_page}"
        elif action == "issues" or (query and action not in {"repo", "readme", "file"}):
            q = urllib.parse.quote_plus(query or f"repo:{repo}")
            url = f"https://api.github.com/search/issues?q={q}"
        elif action == "readme" or args.get("kind") == "readme":
            if not repo:
                return ToolResult(ok=False, data="", error="repo required for readme", source_url="", fetched_at=now)
            url = f"https://api.github.com/repos/{urllib.parse.quote(repo, safe='/')}/readme"
        else:
            if not repo:
                return ToolResult(ok=False, data="", error="repo or query required", source_url="", fetched_at=now)
            url = f"https://api.github.com/repos/{urllib.parse.quote(repo, safe='/')}"

        try:
            with httpx.Client(transport=self.transport, timeout=self.timeout_seconds) as client:
                resp = client.get(url, headers=headers)
                if resp.status_code in {403, 429}:
                    return ToolResult(
                        ok=False,
                        data="",
                        error=f"GitHub HTTP {resp.status_code}: RATE_LIMITED: {resp.text[:300]}",
                        source_url=url,
                        fetched_at=now,
                    )
                if resp.status_code >= 400:
                    return ToolResult(
                        ok=False,
                        data="",
                        error=f"GitHub HTTP {resp.status_code}: {resp.text[:300]}",
                        source_url=url,
                        fetched_at=now,
                    )
                payload = resp.json()

                # README and contents responses carry the file as base64.
                if isinstance(payload, dict) and "content" in payload and ("/readme" in url or "/contents/" in url):
                    try:
                        raw_content = base64.b64decode(payload["content"]).decode("utf-8", errors="replace")
                        payload["decoded_text"] = raw_content[:10000]
                    except Exception:
                        pass

                return ToolResult(
                    ok=True,
                    data=payload,
                    error="",
                    source_url=url,
                    fetched_at=now,
                )
        except Exception as exc:
            return ToolResult(
                ok=False,
                data="",
                error=f"GitHub request error: {exc}",
                source_url=url,
                fetched_at=now,
            )


class NpmTool:
    """NPM registry package metadata tool."""

    name = "npm"

    def __init__(
        self,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.transport = transport
        self.timeout_seconds = timeout_seconds

    def call(self, args: dict[str, Any]) -> ToolResult:
        now = _utc_now()
        action = str(args.get("action") or "").lower()
        query = str(args.get("query") or args.get("q") or "").strip()
        if action == "search" or (query and not args.get("package") and not args.get("name")):
            items, status = search_npm(
                query=query,
                transport=self.transport,
                timeout_seconds=self.timeout_seconds,
            )
            url = f"https://registry.npmjs.org/-/v1/search?text={urllib.parse.quote_plus(query)}"
            if status == "SOURCE_ERROR":
                return ToolResult(
                    ok=False,
                    data="",
                    error="SOURCE_ERROR: npm search failed",
                    source_url=url,
                    fetched_at=now,
                )
            return ToolResult(
                ok=True,
                data={"items": items},
                error="",
                source_url=url,
                fetched_at=now,
            )

        package = str(args.get("package") or args.get("name") or "").strip()
        if not package:
            return ToolResult(ok=False, data="", error="package name required", source_url="", fetched_at=now)

        url = f"https://registry.npmjs.org/{package}"
        try:
            with httpx.Client(transport=self.transport, timeout=self.timeout_seconds) as client:
                resp = client.get(url)
                if resp.status_code == 404:
                    return ToolResult(
                        ok=False,
                        data="",
                        error=f"NPM package '{package}' not found (404)",
                        source_url=url,
                        fetched_at=now,
                    )
                if resp.status_code >= 400:
                    return ToolResult(
                        ok=False,
                        data="",
                        error=f"NPM HTTP {resp.status_code}",
                        source_url=url,
                        fetched_at=now,
                    )
                payload = resp.json()
                summary = {
                    "name": payload.get("name"),
                    "description": payload.get("description"),
                    "license": payload.get("license"),
                    "versions_count": len(payload.get("versions") or {}),
                    "homepage": payload.get("homepage"),
                }
                return ToolResult(
                    ok=True,
                    data=summary,
                    error="",
                    source_url=url,
                    fetched_at=now,
                )
        except Exception as exc:
            return ToolResult(
                ok=False,
                data="",
                error=f"NPM request error: {exc}",
                source_url=url,
                fetched_at=now,
            )


class PyPiTool:
    """PyPI package metadata tool."""

    name = "pypi"

    def __init__(
        self,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.transport = transport
        self.timeout_seconds = timeout_seconds

    def call(self, args: dict[str, Any]) -> ToolResult:
        now = _utc_now()
        action = str(args.get("action") or "").lower()
        query = str(args.get("query") or args.get("q") or "").strip()
        if action == "search" or (query and not args.get("package") and not args.get("name")):
            items, status = search_pypi(
                query=query,
                transport=self.transport,
                timeout_seconds=self.timeout_seconds,
            )
            url = f"https://pypi.org/search/?q={urllib.parse.quote_plus(query)}"
            if status == "SOURCE_ERROR":
                return ToolResult(
                    ok=False,
                    data="",
                    error="SOURCE_ERROR: PyPI search failed",
                    source_url=url,
                    fetched_at=now,
                )
            return ToolResult(
                ok=True,
                data={"items": items},
                error="",
                source_url=url,
                fetched_at=now,
            )

        package = str(args.get("package") or args.get("name") or "").strip()
        if not package:
            return ToolResult(ok=False, data="", error="package name required", source_url="", fetched_at=now)

        url = f"https://pypi.org/pypi/{package}/json"
        try:
            with httpx.Client(transport=self.transport, timeout=self.timeout_seconds) as client:
                resp = client.get(url)
                if resp.status_code == 404:
                    return ToolResult(
                        ok=False,
                        data="",
                        error=f"PyPI package '{package}' not found (404)",
                        source_url=url,
                        fetched_at=now,
                    )
                if resp.status_code >= 400:
                    return ToolResult(
                        ok=False,
                        data="",
                        error=f"PyPI HTTP {resp.status_code}",
                        source_url=url,
                        fetched_at=now,
                    )
                payload = resp.json()
                info = payload.get("info") or {}
                summary = {
                    "name": info.get("name"),
                    "summary": info.get("summary"),
                    "license": info.get("license"),
                    "home_page": info.get("home_page"),
                    "project_urls": info.get("project_urls"),
                }
                return ToolResult(
                    ok=True,
                    data=summary,
                    error="",
                    source_url=url,
                    fetched_at=now,
                )
        except Exception as exc:
            return ToolResult(
                ok=False,
                data="",
                error=f"PyPI request error: {exc}",
                source_url=url,
                fetched_at=now,
            )


class WebFetchTool:
    """Web fetch tool: GET a URL with text-only extraction, size cap, and timeout."""

    name = "web_fetch"

    def __init__(
        self,
        transport: httpx.BaseTransport | None = None,
        max_bytes: int = 50_000,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.transport = transport
        self.max_bytes = max_bytes
        self.timeout_seconds = timeout_seconds

    def call(self, args: dict[str, Any]) -> ToolResult:
        now = _utc_now()
        url = str(args.get("url") or "").strip()
        if not url or not (url.startswith("http://") or url.startswith("https://")):
            return ToolResult(ok=False, data="", error="Valid http(s) url required", source_url=url, fetched_at=now)

        try:
            with httpx.Client(transport=self.transport, timeout=self.timeout_seconds) as client:
                resp = client.get(url, follow_redirects=True)
                if resp.status_code >= 400:
                    return ToolResult(
                        ok=False,
                        data="",
                        error=f"Web fetch HTTP {resp.status_code}",
                        source_url=url,
                        fetched_at=now,
                    )
                content = resp.text[: self.max_bytes]
                return ToolResult(
                    ok=True,
                    data=content,
                    error="",
                    source_url=url,
                    fetched_at=now,
                )
        except Exception as exc:
            return ToolResult(
                ok=False,
                data="",
                error=f"Web fetch error: {exc}",
                source_url=url,
                fetched_at=now,
            )


class ZetarisSqlTool:
    """Zetaris SQL tool reusing the repo's existing Zetaris client."""

    name = "zetaris_sql"

    def __init__(self, client: Any = None) -> None:
        # No silent fallback to sample data: a live run must pass a real client.
        self.client = client

    def call(self, args: dict[str, Any]) -> ToolResult:
        now = _utc_now()
        sql = str(args.get("sql") or "").strip()
        limit = int(args.get("limit") or 100)
        if not sql:
            return ToolResult(ok=False, data="", error="sql required", source_url="zetaris://sql", fetched_at=now)
        if self.client is None:
            return ToolResult(
                ok=False, data="", error="zetaris client not configured", source_url="zetaris://sql", fetched_at=now
            )

        try:
            result = self.client.run_sql(sql, limit=limit)
            url = getattr(self.client, "rest_url", "zetaris://sql-editor")
            data = {
                "columns": result.columns,
                "rows": result.rows,
                "query_id": result.query_id,
                "content_hash": result.content_hash,
            }
            return ToolResult(
                ok=True,
                data=data,
                error="",
                source_url=f"{url}?query_id={result.query_id}",
                fetched_at=now,
            )
        except Exception as exc:
            return ToolResult(
                ok=False,
                data="",
                error=f"Zetaris SQL error: {exc}",
                source_url="zetaris://sql",
                fetched_at=now,
            )


class N8nTool:
    """n8n webhook execution tool."""

    name = "n8n"

    def __init__(
        self,
        webhook_url: str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.webhook_url = webhook_url or os.environ.get("N8N_WEBHOOK_URL") or ""
        self.transport = transport
        self.timeout_seconds = timeout_seconds

    def call(self, args: dict[str, Any]) -> ToolResult:
        now = _utc_now()
        if not self.webhook_url:
            return ToolResult(
                ok=False,
                data="",
                error="n8n disabled",
                source_url="",
                fetched_at=now,
            )

        try:
            with httpx.Client(transport=self.transport, timeout=self.timeout_seconds) as client:
                resp = client.post(self.webhook_url, json=args)
                if resp.status_code >= 400:
                    return ToolResult(
                        ok=False,
                        data="",
                        error=f"n8n webhook HTTP {resp.status_code}",
                        source_url=self.webhook_url,
                        fetched_at=now,
                    )
                try:
                    data = resp.json()
                except Exception:
                    data = resp.text
                return ToolResult(
                    ok=True,
                    data=data,
                    error="",
                    source_url=self.webhook_url,
                    fetched_at=now,
                )
        except Exception as exc:
            return ToolResult(
                ok=False,
                data="",
                error=f"n8n webhook error: {exc}",
                source_url=self.webhook_url,
                fetched_at=now,
            )
