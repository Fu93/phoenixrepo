"""Official Data + Power smoke test.

Checks, in order:
1. Zetaris web UI answers.
2. Zetaris runs SELECT 1 through the REST SQL editor.
3. The configured model route returns a chat completion.

Env names match the participant guide. Meterless Nightshift settings are accepted
as aliases so one .env can drive both this script and the app.

  ZETARIS_REST_URL, ZETARIS_ORG_ID, ZETARIS_API_KEY
  ZETARIS_BASE_URL (optional, default http://localhost:3000)
  MODEL_BASE_URL or METERLESS_BASE_URL
  MODEL_API_KEY or METERLESS_RUNTIME_TOKEN
  NEMOTRON_MODEL or METERLESS_MODEL

The model call sends meterless-app and meterless-scope when the base URL is the
Meterless one. A /healthz response is not used as proof.
"""

from __future__ import annotations

import os
import sys
import uuid

import httpx
from dotenv import load_dotenv

load_dotenv()
RESULTS: list[tuple[str, str, str]] = []


def env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return ""


def need(*names: str) -> None:
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        raise AssertionError(f"{', '.join(missing)} is missing from .env")


def redact(message: str) -> str:
    text = message
    for name in (
        "ZETARIS_API_KEY",
        "MODEL_API_KEY",
        "METERLESS_RUNTIME_TOKEN",
        "NVIDIA_API_KEY",
    ):
        secret = os.environ.get(name)
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text[:180]


def check(name: str, fn) -> None:
    try:
        fn()
    except Exception as exc:
        RESULTS.append(("FAIL", name, redact(str(exc))))
        return
    RESULTS.append(("PASS", name, ""))


def zetaris_web() -> None:
    url = os.environ.get("ZETARIS_BASE_URL", "http://localhost:3000")
    response = httpx.get(url, timeout=10)
    response.raise_for_status()


def zetaris_sql() -> None:
    for name in ("ZETARIS_REST_URL", "ZETARIS_ORG_ID", "ZETARIS_API_KEY"):
        if not os.environ.get(name):
            raise AssertionError(f"{name} is missing from .env")
    response = httpx.post(
        os.environ["ZETARIS_REST_URL"].rstrip("/") + "/sql-editor/sqls/run",
        headers={
            "Authorization": "Bearer " + os.environ["ZETARIS_API_KEY"],
            "X-Org-ID": os.environ["ZETARIS_ORG_ID"],
            "X-Request-ID": str(uuid.uuid4()),
            "Content-Type": "application/json",
        },
        json={
            "queryId": str(uuid.uuid4()),
            "sql": "SELECT 1",
            "source": "SqlEditor",
            "limit": 1,
        },
        timeout=60,
    )
    response.raise_for_status()
    data = response.json().get("data")
    if not data or str(data[0][0]) != "1":
        raise AssertionError("unexpected reply")


def model_reply() -> None:
    base = env("MODEL_BASE_URL", "METERLESS_BASE_URL")
    key = env("MODEL_API_KEY", "METERLESS_RUNTIME_TOKEN")
    model = env("NEMOTRON_MODEL", "METERLESS_MODEL")
    if not base or not key or not model:
        raise AssertionError(
            "MODEL_BASE_URL/METERLESS_BASE_URL, MODEL_API_KEY/METERLESS_RUNTIME_TOKEN, "
            "and NEMOTRON_MODEL/METERLESS_MODEL are required"
        )
    root = base.rstrip("/")
    if not root.endswith("/v1"):
        root += "/v1"
    headers = {
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
    }
    if "METERLESS_BASE_URL" in os.environ or "meterless" in root:
        headers["meterless-app"] = os.environ.get("METERLESS_APP", "phoenixrepo")
        headers["meterless-scope"] = "user"
    response = httpx.post(
        root + "/chat/completions",
        headers=headers,
        json={
            "model": model,
            "messages": [{"role": "user", "content": "Reply with the single word: ready"}],
        },
        timeout=180,
    )
    response.raise_for_status()
    choices = response.json().get("choices")
    if not choices:
        raise AssertionError("no reply from the model")


def main() -> int:
    check("Data: Zetaris web interface", zetaris_web)
    check("Data: Zetaris runs SQL over its REST API", zetaris_sql)
    check("Power: model replies through the configured route", model_reply)
    failed = False
    for status, name, detail in RESULTS:
        print(f"{status} {name} {detail}".rstrip())
        failed = failed or status == "FAIL"
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
