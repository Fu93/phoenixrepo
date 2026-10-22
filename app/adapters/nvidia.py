"""Secondary OpenAI-compatible NVIDIA developer endpoint.

This is a dev fallback. The hackathon route is Nightshift, with the NVIDIA key stored
in Nightshift's Providers tab rather than in this application's environment.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.adapters.meterless import DEFAULT_MAX_TOKENS, message_text
from app.errors import InvestigationError

DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"


class NvidiaError(InvestigationError):
    def __init__(self, message: str, *, status_code: int = 502) -> None:
        super().__init__("NvidiaError", message, status_code)


def normalize_nvidia_base_url(raw: str) -> str:
    text = (raw or DEFAULT_BASE_URL).strip().rstrip("/")
    if "://" not in text:
        text = "https://" + text
    if not text.endswith("/v1"):
        text += "/v1"
    return text


class NvidiaDirect:
    provider_id = "nvidia"
    name = "NVIDIA direct (secondary dev fallback)"

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        model: str = "",
        timeout_seconds: float = 90,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise NvidiaError("NVIDIA_API_KEY is missing.", status_code=400)
        if not model:
            raise NvidiaError(
                "NVIDIA_MODEL is empty. Set the model id from your NVIDIA catalog. "
                "Prefer a Nemotron 3 Super id; nemotron-3.5-super-vl-120b was reported flaky.",
                status_code=400,
            )
        self._api_key = api_key
        self.base_url = normalize_nvidia_base_url(base_url)
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens
        self._transport = transport
        self.last_requested_model = model
        self.last_served_model: str | None = None

    def provider(self) -> str:
        return self.provider_id

    def status(self) -> dict[str, Any]:
        return {
            "provider": self.provider_id,
            "name": self.name,
            "configured": True,
            "enabled": True,
            "integrated": True,
            "integration": "OPENAI_COMPATIBLE_DIRECT",
            "primary": False,
            "secondary_dev_fallback": True,
            "mode": self.model,
            "base_url": self.base_url,
        }

    def complete(self, messages: list[dict[str, str]], *, model: str | None = None) -> str:
        url = self.base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        chosen = model or self.model
        self.last_requested_model = chosen
        body = {"model": chosen, "messages": messages, "stream": False, "max_tokens": self.max_tokens}
        try:
            with httpx.Client(timeout=self.timeout_seconds, transport=self._transport) as client:
                response = client.post(url, headers=headers, json=body)
        except httpx.TimeoutException as exc:
            raise NvidiaError(f"NVIDIA chat timed out after {self.timeout_seconds}s.") from exc
        except httpx.HTTPError as exc:
            raise NvidiaError("NVIDIA chat request failed.") from exc
        if response.status_code in (401, 403):
            raise NvidiaError("NVIDIA rejected the API key.", status_code=401)
        if response.status_code >= 400:
            raise NvidiaError(f"NVIDIA chat failed with HTTP {response.status_code}.")
        try:
            payload = response.json()
        except ValueError as exc:
            raise NvidiaError("NVIDIA chat returned non-JSON.") from exc
        served = payload.get("model") if isinstance(payload, dict) else None
        self.last_served_model = served.strip() if isinstance(served, str) and served.strip() else None
        try:
            return message_text(payload)
        except InvestigationError as exc:
            raise NvidiaError(exc.message) from exc
