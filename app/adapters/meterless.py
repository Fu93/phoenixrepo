"""Meterless Runtime provider. Contract: Power prompt, Meterless Runtime 1.4 / provider contract v4.

Fetched from https://meterless.ai/engines.html on 22 Oct 2026 (POWER → Add Meterless as a provider).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from app.errors import InvestigationError
from config.settings import usable_secret

DEFAULT_BASE_URL = "http://127.0.0.1:8088/v1"
AUTO_DETECT_HOSTS = ("127.0.0.1", "localhost", "::1")
AUTO_DETECT_PORTS = (8088, 8090, 8787, 11434)
REJECTED_TOKEN = "rt-local"
_SUFFIXES = ("/v1/chat/completions", "/v1/models", "/healthz", "/v1")


class MeterlessError(InvestigationError):
    def __init__(self, message: str, *, status_code: int = 502, error_type: str = "MeterlessError") -> None:
        super().__init__(error_type, message, status_code)


@dataclass
class ConnectionStatus:
    state: str
    base_url: str
    detail: str
    models: list[str]


def normalize_meterless_base_url(raw: str) -> str:
    text = raw.strip()
    if not text:
        raise MeterlessError("Meterless base URL is empty.", status_code=400)
    if "://" not in text:
        text = "http://" + text
    changed = True
    while changed:
        changed = False
        for suffix in _SUFFIXES:
            if text.rstrip("/").endswith(suffix):
                text = text.rstrip("/")[: -len(suffix)]
                changed = True
                break
    text = text.rstrip("/")
    if text.endswith("/v1"):
        raise MeterlessError("Refusing to build a /v1/v1 Meterless URL.", status_code=400)
    return text + "/v1"


def parse_connect_link(text: str, *, default_app: str = "phoenixrepo") -> dict[str, str] | None:
    """Return connection fields only for an http(s) URL whose path is /connect and whose fragment has k."""

    try:
        parsed = urlparse(text.strip())
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if (parsed.path.rstrip("/") or "/") != "/connect":
        return None
    fragment = parse_qs(parsed.fragment, keep_blank_values=False)
    key = (fragment.get("k") or [None])[0]
    if not key:
        return None
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return {
        "base_url": origin + "/v1",
        "api_key": key,
        "model": (fragment.get("m") or ["meterless/auto"])[0],
        "app": (fragment.get("n") or [default_app])[0],
        "health_url": origin + "/healthz",
    }


def candidate_base_urls() -> list[str]:
    urls: list[str] = []
    for host in AUTO_DETECT_HOSTS:
        netloc_host = f"[{host}]" if ":" in host else host
        for port in AUTO_DETECT_PORTS:
            urls.append(f"http://{netloc_host}:{port}/v1")
    return urls


def model_ids(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    ids: list[str] = []
    for item in data:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            ids.append(item["id"])
    return ids


def message_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise MeterlessError("Meterless chat returned a non-object body.")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise MeterlessError("Meterless chat returned no choices.")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        text = "".join(parts).strip()
        if text:
            return text
    raise MeterlessError("Meterless chat content was not text.")


class MeterlessRuntime:
    provider_id = "meterless"
    name = "Meterless Runtime"

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        token: str | None = None,
        model: str = "meterless/auto",
        app_id: str = "phoenixrepo",
        timeout_seconds: float = 180,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = normalize_meterless_base_url(base_url)
        self._token = (token or "").strip()
        self.model = model or "meterless/auto"
        self.app_id = app_id or "phoenixrepo"
        self.timeout_seconds = timeout_seconds
        self._transport = transport

    def provider(self) -> str:
        return self.provider_id

    def configured(self) -> bool:
        token = usable_secret(self._token)
        return bool(token) and token != REJECTED_TOKEN

    def enabled(self) -> bool:
        return self.configured()

    def mode(self) -> str:
        return self.model

    def status(self) -> dict[str, Any]:
        return {
            "provider": self.provider_id,
            "name": self.name,
            "configured": self.configured(),
            "enabled": self.enabled(),
            "integrated": True,
            "integration": "OPENAI_COMPATIBLE_RUNTIME",
            "primary": True,
            "mode": self.model,
            "base_url": self.base_url,
        }

    def _headers(self, *, chat: bool) -> dict[str, str]:
        self._require_token()
        headers = {
            "Authorization": f"Bearer {self._token}",
            "meterless-app": self.app_id,
        }
        if chat:
            headers["Content-Type"] = "application/json"
            headers["meterless-scope"] = "user"
        return headers

    def _require_token(self) -> None:
        if self._token == REJECTED_TOKEN:
            raise MeterlessError(
                "The placeholder token rt-local is rejected. Paste the real Nightshift runtime token.",
                status_code=401,
                error_type="MeterlessUnauthorized",
            )
        if usable_secret(self._token) is None:
            raise MeterlessError(
                "METERLESS_RUNTIME_TOKEN is missing. Paste the Nightshift Console token.",
                status_code=401,
                error_type="MeterlessUnauthorized",
            )

    def discover_models(self) -> list[str]:
        status = self.connection_status()
        if status.state == "unauthorized":
            raise MeterlessError(status.detail, status_code=401, error_type="MeterlessUnauthorized")
        if status.state != "online":
            raise MeterlessError(status.detail, status_code=503, error_type="MeterlessOffline")
        return status.models

    def connection_status(self) -> ConnectionStatus:
        """GET /v1/models decides. /healthz is not consulted and is not proof."""

        self._require_token()
        url = self.base_url.rstrip("/") + "/models"
        try:
            with httpx.Client(timeout=min(self.timeout_seconds, 30), transport=self._transport) as client:
                response = client.get(url, headers=self._headers(chat=False))
        except httpx.HTTPError:
            return ConnectionStatus("offline", self.base_url, "Model discovery request failed.", [])
        if response.status_code in (401, 403):
            return ConnectionStatus(
                "unauthorized",
                self.base_url,
                "Runtime rejected the token. Paste the Nightshift Console token.",
                [],
            )
        if response.status_code != 200:
            return ConnectionStatus(
                "offline",
                self.base_url,
                f"Model discovery returned HTTP {response.status_code}.",
                [],
            )
        try:
            ids = model_ids(response.json())
        except ValueError:
            return ConnectionStatus("offline", self.base_url, "Model discovery returned non-JSON.", [])
        meterless_ids = [item for item in ids if item.startswith("meterless/")]
        if not meterless_ids:
            return ConnectionStatus(
                "offline",
                self.base_url,
                "Discovery returned HTTP 200 without any meterless/* route.",
                ids,
            )
        return ConnectionStatus("online", self.base_url, "Runtime model discovery succeeded.", ids)

    def auto_detect(self, candidates: list[str] | None = None) -> ConnectionStatus:
        """Probe candidate base URLs. Saves nothing; the caller stores the hit."""

        self._require_token()
        last = ConnectionStatus("offline", self.base_url, "No runtime responded.", [])
        for base in candidates or candidate_base_urls():
            probe = MeterlessRuntime(
                base_url=base,
                token=self._token,
                model=self.model,
                app_id=self.app_id,
                timeout_seconds=5,
                transport=self._transport,
            )
            status = probe.connection_status()
            if status.state in {"online", "unauthorized"}:
                return status
            last = status
        return last

    def complete(self, messages: list[dict[str, str]], *, model: str | None = None) -> str:
        self._require_token()
        chosen = model or self.model
        if not isinstance(messages, list) or not messages:
            raise MeterlessError("Chat messages are empty.", status_code=400)
        url = self.base_url.rstrip("/") + "/chat/completions"
        body = {"model": chosen, "messages": messages, "stream": False}
        try:
            with httpx.Client(timeout=self.timeout_seconds, transport=self._transport) as client:
                response = client.post(url, headers=self._headers(chat=True), json=body)
        except httpx.TimeoutException as exc:
            raise MeterlessError(
                f"Meterless chat timed out after {self.timeout_seconds}s."
            ) from exc
        except httpx.HTTPError as exc:
            raise MeterlessError("Meterless chat request failed.") from exc
        if response.status_code in (401, 403):
            raise MeterlessError(
                "Meterless rejected the runtime token.",
                status_code=401,
                error_type="MeterlessUnauthorized",
            )
        if response.status_code >= 400:
            raise MeterlessError(f"Meterless chat failed with HTTP {response.status_code}.")
        try:
            payload = response.json()
        except ValueError as exc:
            raise MeterlessError("Meterless chat returned non-JSON.") from exc
        return message_text(payload)
