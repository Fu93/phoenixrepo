"""Builtin tools for the Phoenix shared toolbox."""

from __future__ import annotations

import base64
import os
from datetime import datetime, timezone
from typing import Any

import httpx

from app.tools.base import ToolResult


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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

        if action == "issues" or query:
            q = query or f"repo:{repo}"
            url = f"https://api.github.com/search/issues?q={q}"
        elif action == "readme" or args.get("kind") == "readme":
            if not repo:
                return ToolResult(ok=False, data="", error="repo required for readme", source_url="", fetched_at=now)
            url = f"https://api.github.com/repos/{repo}/readme"
        else:
            if not repo:
                return ToolResult(ok=False, data="", error="repo or query required", source_url="", fetched_at=now)
            url = f"https://api.github.com/repos/{repo}"

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

                # If README was requested and content is base64 encoded
                if "/readme" in url and isinstance(payload, dict) and "content" in payload:
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
