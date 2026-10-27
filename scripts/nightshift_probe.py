#!/usr/bin/env python3
"""Measure what this machine's Nightshift actually serves, how fast, and with which settings.

Nightshift is a router. A requested model id is not a guarantee: the reply's `model` field is
what answered. This script asks once per (model id, request variant), records the served model,
latency and HTTP status, and prints .env lines for the fastest variant that was served by an
`nvidia/nemotron-*` model. It never prints the runtime token and writes nothing outside logs/.

    python scripts/nightshift_probe.py               # Nemotron ids from GET /v1/models
    python scripts/nightshift_probe.py --models nvidia/nemotron-3.5-lightning-30b-a3b
    python scripts/nightshift_probe.py --quick       # one variant per id

Exit code is 0 when at least one variant was served by Nemotron, 1 otherwise, 2 on a usage
or connection error. It reports; it does not change any configuration.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agents.relevance import routing_meta  # noqa: E402
from app.console import prepare_cli  # noqa: E402
from config.settings import NEMOTRON_FAMILY_PREFIX, model_mismatch  # noqa: E402

# Preferred order when several Nemotron ids are listed: small and fast first. Gate 1 makes
# many short JSON calls, so latency matters more than size for most of them.
PREFERENCE = (
    "nvidia/nemotron-3.5-lightning-30b-a3b",
    "nvidia/nemotron-3-super-120b-a12b",
    "nvidia/nemotron-3-ultra-550b-a55b",
)

# Request variants. One variant sends chat_template_kwargs.enable_thinking false.
# Field names differ by provider, so each spelling is tried and only what this Nightshift accepts is recommended.
VARIANTS: dict[str, dict[str, Any]] = {
    "default": {},
    "thinking_off_template": {"chat_template_kwargs": {"enable_thinking": False}},
    "reasoning_none": {"reasoning_effort": "none"},
}

PROMPT = (
    "You are checking a JSON contract. Reply with ONLY this JSON object and nothing else: "
    '{"ok": true, "task": "phoenix-probe"}'
)


@dataclass
class Attempt:
    requested_model: str
    variant: str
    status: int | None
    seconds: float
    served_model: str = ""
    nemotron: bool = False
    json_ok: bool = False
    error: str = ""
    routing: dict[str, str] = field(default_factory=dict)


def _headers(token: str, app: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "meterless-app": app,
        "meterless-scope": "user",
    }


def list_models(client: httpx.Client, base_url: str, headers: dict[str, str]) -> list[str]:
    resp = client.get(f"{base_url}/models", headers=headers)
    if resp.status_code in (401, 403):
        raise RuntimeError(f"Nightshift rejected the runtime token (HTTP {resp.status_code}).")
    if resp.status_code != 200:
        raise RuntimeError(f"GET /models returned HTTP {resp.status_code}.")
    data = resp.json().get("data") if isinstance(resp.json(), dict) else None
    return [str(item.get("id")) for item in data or [] if isinstance(item, dict) and item.get("id")]


def order_candidates(listed: list[str]) -> list[str]:
    nemotron = [mid for mid in listed if mid.startswith(NEMOTRON_FAMILY_PREFIX)]
    ranked = [mid for mid in PREFERENCE if mid in nemotron]
    return ranked + sorted(mid for mid in nemotron if mid not in ranked)


def probe_once(
    client: httpx.Client,
    base_url: str,
    headers: dict[str, str],
    model: str,
    variant: str,
    *,
    max_tokens: int,
    timeout: float,
) -> Attempt:
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "temperature": 0,
        "stream": False,
        "max_tokens": max_tokens,
        **VARIANTS[variant],
    }
    started = time.monotonic()
    try:
        resp = client.post(f"{base_url}/chat/completions", headers=headers, json=body, timeout=timeout)
    except httpx.TimeoutException:
        return Attempt(model, variant, None, round(time.monotonic() - started, 2), error=f"timeout after {timeout:.0f}s")
    except httpx.HTTPError as exc:
        return Attempt(model, variant, None, round(time.monotonic() - started, 2), error=type(exc).__name__)
    seconds = round(time.monotonic() - started, 2)
    if resp.status_code != 200:
        detail = resp.text[:160].replace("\n", " ")
        return Attempt(model, variant, resp.status_code, seconds, error=detail)
    try:
        data = resp.json()
    except ValueError:
        return Attempt(model, variant, 200, seconds, error="non-JSON body")
    served = str(data.get("model") or "").strip() if isinstance(data, dict) else ""
    text = ""
    choices = data.get("choices") if isinstance(data, dict) else None
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        content = (choices[0].get("message") or {}).get("content")
        text = content if isinstance(content, str) else json.dumps(content or "")
    json_ok = '"phoenix-probe"' in text
    return Attempt(
        model,
        variant,
        200,
        seconds,
        served_model=served,
        nemotron=not model_mismatch(served),
        json_ok=json_ok,
        error="" if text.strip() else "empty content",
        routing=routing_meta(data, resp.headers),
    )


def recommend(attempts: list[Attempt]) -> dict[str, str] | None:
    good = [a for a in attempts if a.status == 200 and a.nemotron and a.json_ok]
    if not good:
        return None
    best = min(good, key=lambda a: a.seconds)
    slowest_good = max(a.seconds for a in good if a.requested_model == best.requested_model and a.variant == best.variant)
    env = {"MODEL_NAME": best.requested_model}
    extra = VARIANTS[best.variant]
    if extra:
        env["PHOENIX_LLM_EXTRA_BODY"] = json.dumps(extra, separators=(",", ":"))
    # Deep tier: the largest Nemotron that answered with its reasoning left on (the default
    # variant), when it is a different model from the fast one. It writes the final verdict.
    rank = {mid: i for i, mid in enumerate(PREFERENCE)}
    deep_pool = [a for a in good if a.variant == "default" and a.requested_model != best.requested_model]
    if deep_pool:
        deep = max(deep_pool, key=lambda a: (rank.get(a.requested_model, -1), -a.seconds))
        env["MODEL_NAME_DEEP"] = deep.requested_model
        slowest_good = max(slowest_good, max(a.seconds for a in deep_pool if a.requested_model == deep.requested_model))
    # A real gate-1 prompt is longer than the probe: 4x the slowest probe latency, between 120 s
    # and 240 s. The upper bound matters: Nightshift's 502 arrives after about 330 s, so a cap
    # above that never cuts a hung call short. Room for one retry in the total.
    attempt_s = int(min(240, max(120, 4 * slowest_good)))
    env["PHOENIX_LLM_ATTEMPT_TIMEOUT_S"] = str(attempt_s)
    env["PHOENIX_LLM_TOTAL_TIMEOUT_S"] = str(attempt_s * 2 + 30)
    return env


def _row(a: Attempt) -> str:
    served = a.served_model or "-"
    mark = "nemotron" if a.nemotron else ("MISMATCH" if a.status == 200 else "-")
    status = str(a.status) if a.status is not None else "ERR"
    return f"{a.requested_model:<42} {a.variant:<22} {status:>4} {a.seconds:>7.1f}s  {mark:<8} {served:<40} {a.error[:60]}"


def main(argv: list[str] | None = None) -> int:
    prepare_cli()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="*", help="Model ids to probe. Default: every nvidia/nemotron-* id GET /models lists.")
    ap.add_argument("--variants", nargs="*", choices=sorted(VARIANTS), help="Request variants to try (default: all).")
    ap.add_argument("--quick", action="store_true", help="Only the default variant.")
    ap.add_argument("--repeat", type=int, default=1, help="Calls per (model, variant). Routing can differ call to call.")
    ap.add_argument("--max-tokens", type=int, default=1024)
    ap.add_argument("--timeout", type=float, default=180.0, help="Seconds per call (default 180).")
    ap.add_argument("--budget", type=float, default=900.0, help="Stop starting new calls after this many seconds (default 900).")
    ap.add_argument("--out", type=Path, default=None, help="JSON report path (default logs/nightshift_probe-<utc>.json).")
    args = ap.parse_args(argv)

    from config.settings import Settings

    settings = Settings()
    token = settings.meterless_runtime_token or ""
    if not token or token == "rt-local":
        sys.stderr.write("METERLESS_RUNTIME_TOKEN is missing. Paste the Nightshift Console token into .env.\n")
        return 2
    base_url = settings.meterless_base_url.rstrip("/")
    headers = _headers(token, settings.meterless_app)
    variants = ["default"] if args.quick else (args.variants or list(VARIANTS))

    started = time.monotonic()
    attempts: list[Attempt] = []
    with httpx.Client(timeout=httpx.Timeout(connect=10.0, read=args.timeout, write=30.0, pool=10.0)) as client:
        try:
            listed = list_models(client, base_url, headers)
        except (RuntimeError, httpx.HTTPError, ValueError) as exc:
            sys.stderr.write(f"Could not list models at {base_url}: {exc}\n")
            return 2
        models = args.models or order_candidates(listed)
        print(f"Nightshift {base_url}: {len(listed)} model ids listed, {sum(1 for m in listed if m.startswith(NEMOTRON_FAMILY_PREFIX))} nvidia/nemotron-*")
        unlisted = [m for m in models if m not in listed]
        if unlisted:
            print(f"Not in GET /models (Nightshift may reroute these): {', '.join(unlisted)}")
        if not models:
            print("No nvidia/nemotron-* id is listed. Add the NVIDIA provider in Nightshift's Providers tab, then rerun.")
            return 1
        print(f"{'requested':<42} {'variant':<22} {'http':>4} {'latency':>8}  {'served':<8} {'served model':<40} note")
        for model in models:
            for variant in variants:
                for _ in range(max(1, args.repeat)):
                    if time.monotonic() - started > args.budget:
                        print(f"Budget of {args.budget:.0f}s reached; stopping.")
                        break
                    attempt = probe_once(client, base_url, headers, model, variant, max_tokens=args.max_tokens, timeout=args.timeout)
                    attempts.append(attempt)
                    print(_row(attempt), flush=True)

    env = recommend(attempts)
    report = {
        "probed_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "listed_nemotron": [m for m in listed if m.startswith(NEMOTRON_FAMILY_PREFIX)],
        "attempts": [asdict(a) for a in attempts],
        "recommended_env": env,
    }
    out = args.out or ROOT / "logs" / f"nightshift_probe-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    served_counts: dict[str, int] = {}
    for a in attempts:
        if a.status == 200:
            served_counts[a.served_model or "(missing)"] = served_counts.get(a.served_model or "(missing)", 0) + 1
    print(f"\nServed models on HTTP 200: {json.dumps(served_counts, ensure_ascii=False)}")
    print(f"Report: {out}")
    if env is None:
        print("No variant was served by nvidia/nemotron-* with a usable reply. Keep MODEL_MISMATCH labelling honest;")
        print("in Nightshift, disable the non-NVIDIA providers (or pin the route to a Nemotron lane) and rerun.")
        return 1
    print("\nAdd to .env (fastest variant that Nightshift actually served with Nemotron):")
    for key, value in env.items():
        print(f"{key}={value}")
    if "MODEL_NAME_DEEP" not in env:
        print("# Only one Nemotron model answered; every step uses it. Leave MODEL_NAME_DEEP unset.")
    print("# Optional: PHOENIX_NEMOTRON_POLICY=require discards any non-Nemotron answer;")
    print("# PHOENIX_NEMOTRON_POLICY=fallback-nvidia replaces it with the same model at the NVIDIA API (needs NVIDIA_API_KEY).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
