"""Zetaris SQL Editor REST client.

Contract from the Open Agent Hackathon participant guide (21 Oct 2026):
POST {ZETARIS_REST_URL}/sql-editor/sqls/run
Authorization: Bearer token, X-Org-ID, fresh X-Request-ID,
body {queryId, sql, source: SqlEditor, limit}. One statement per call.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from app.errors import ZetarisError

_TRAILING = re.compile(r";\s*$")


@dataclass
class ZetarisResult:
    query_id: str
    sql: str
    columns: list[str]
    rows: list[dict[str, Any]]
    source: str
    mode: str


def assert_single_statement(sql: str) -> str:
    text = sql.strip()
    if _TRAILING.search(text):
        text = _TRAILING.sub("", text).strip()
    if ";" in text:
        raise ZetarisError(
            "Zetaris accepts one SQL statement per call. Split the script and retry.",
            status_code=400,
        )
    if not text:
        raise ZetarisError("SQL is empty.", status_code=400)
    return text


def rows_from_payload(payload: Any) -> tuple[list[str], list[dict[str, Any]]]:
    if not isinstance(payload, dict):
        raise ZetarisError("Zetaris returned a non-object body.")
    if payload.get("error"):
        detail = payload["error"]
        message = detail.get("message") if isinstance(detail, dict) else str(detail)
        raise ZetarisError(f"Zetaris error: {message[:300]}")
    data = payload.get("data")
    if data is None:
        data = payload.get("rows")
    headers = payload.get("headers") or payload.get("columns")
    if not isinstance(data, list):
        raise ZetarisError("Zetaris result did not include a data array.")
    if not data:
        columns = _header_names(headers) if isinstance(headers, list) else []
        return columns, []
    if isinstance(data[0], dict):
        columns = _header_names(headers) if isinstance(headers, list) else list(data[0].keys())
        return columns, [dict(row) for row in data]
    if isinstance(data[0], (list, tuple)):
        columns = _header_names(headers) if isinstance(headers, list) else [
            f"col_{index}" for index in range(len(data[0]))
        ]
        return columns, [dict(zip(columns, row)) for row in data]
    raise ZetarisError("Unrecognised Zetaris row shape.")


def _header_names(headers: list[Any]) -> list[str]:
    names: list[str] = []
    for item in headers:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict) and "name" in item:
            names.append(str(item["name"]))
        else:
            names.append(str(item))
    return names


class ZetarisClient:
    """Live REST adapter. Credentials are sent and never logged."""

    def __init__(
        self,
        rest_url: str,
        org_id: str,
        api_key: str,
        timeout_seconds: float = 30,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ZetarisError("ZETARIS_API_KEY is missing.", status_code=400)
        self.rest_url = rest_url.rstrip("/")
        self.org_id = str(org_id)
        self._api_key = api_key
        self.timeout_seconds = timeout_seconds
        self._transport = transport

    def provider(self) -> str:
        return "zetaris"

    def configured(self) -> bool:
        return bool(self._api_key)

    def enabled(self) -> bool:
        return self.configured()

    def mode(self) -> str:
        return "live"

    def status(self) -> dict[str, Any]:
        return {
            "provider": self.provider(),
            "configured": self.configured(),
            "enabled": self.enabled(),
            "integrated": True,
            "integration": "REST",
            "load_bearing": True,
            "mode": self.mode(),
            "rest_url": self.rest_url,
        }

    def run_sql(self, sql: str, limit: int = 100) -> ZetarisResult:
        statement = assert_single_statement(sql)
        query_id = str(uuid.uuid4())
        request_id = str(uuid.uuid4())
        url = f"{self.rest_url}/sql-editor/sqls/run"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "X-Org-ID": self.org_id,
            "X-Request-ID": request_id,
            "Content-Type": "application/json",
        }
        body = {
            "queryId": query_id,
            "sql": statement,
            "source": "SqlEditor",
            "limit": limit,
        }
        try:
            with httpx.Client(timeout=self.timeout_seconds, transport=self._transport) as client:
                response = client.post(url, headers=headers, json=body)
        except httpx.TimeoutException as exc:
            raise ZetarisError(
                f"Zetaris SQL timed out after {self.timeout_seconds}s (query {query_id})."
            ) from exc
        except httpx.HTTPError as exc:
            raise ZetarisError(f"Zetaris SQL request failed (query {query_id}).") from exc
        if response.status_code in (401, 403):
            raise ZetarisError(
                f"Zetaris rejected the API key (HTTP {response.status_code}, query {query_id}).",
                status_code=401,
            )
        if response.status_code >= 400:
            raise ZetarisError(
                f"Zetaris SQL failed with HTTP {response.status_code} (query {query_id}).",
                status_code=502,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ZetarisError(f"Zetaris returned non-JSON (query {query_id}).") from exc
        columns, rows = rows_from_payload(payload)
        source = _source_name(statement)
        return ZetarisResult(
            query_id=query_id,
            sql=statement,
            columns=columns,
            rows=rows,
            source=source,
            mode="zetaris-rest",
        )


def _source_name(sql: str) -> str:
    match = re.search(r"from\s+([a-zA-Z0-9_.]+)", sql, flags=re.IGNORECASE)
    return match.group(1) if match else "zetaris"
