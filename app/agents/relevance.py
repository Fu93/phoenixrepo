"""Semantic relevance extraction and judgment via Nightshift LLM.

Follows the pattern of app/agents/equivalence.py: the LLM judges meaning,
deterministic code strictly verifies every quote and invariant against stored evidence.

Functions:
- extract_capabilities: extracts up to 5 user-facing capabilities with exact README quotes.
- judge_demand: evaluates if an external issue/discussion expresses real demand for a capability.
- judge_alternative_coverage: evaluates if a maintained free alternative covers a capability.
- judge_discovery: evaluates whether a candidate repo is CANDIDATE, ALREADY_EXISTS, or NO_GO.
- extract_problem_statement: extracts problem statement, target user, and search queries (step="problem").
- search_prior_art: searches GitHub repositories (and npm/PyPI) via toolbox.
- judge_alternative_relevance: evaluates alternative relevance and overlap (step="alternative").
- filter_relevant_alternatives: filters search results to same_problem=true with verified quotes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from app.errors import InvestigationError
from app.prompts import load_prompt

ROOT = Path(__file__).resolve().parents[2]

GENERIC_WORDS = frozenset(
    {
        "code",
        "config",
        "https",
        "http",
        "python",
        "main",
        "install",
        "source",
    }
)

STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "to",
        "for",
        "and",
        "or",
        "in",
        "on",
        "with",
        "of",
        "is",
        "it",
        "by",
        "as",
        "at",
        "from",
        "this",
        "that",
    }
)

RETRY_DELAYS = (0, 3, 8, 20, 45)


class RelevanceLLMError(InvestigationError):
    def __init__(self, message: str, *, status_code: int = 502) -> None:
        super().__init__("RelevanceLLMError", message, status_code)


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Robustly extract a JSON object from text or markdown code fences."""
    raw = text.strip()
    if "```" in raw:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw)
        if match:
            raw = match.group(1).strip()
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            data = json.loads(raw[start : end + 1])
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return None


def _complete_json(client, prompt, system_prompt, repo_id, step, validate=None):
    """Use client.complete_json when available (retries unusable answers), else one plain call."""
    fn = getattr(client, "complete_json", None)
    if callable(fn):
        return fn(prompt, system_prompt, repo_id=repo_id, step=step, validate=validate)
    text, req_m, srv_m = client.complete(prompt, system_prompt, repo_id=repo_id, step=step)
    obj = extract_json_object(text)
    if obj is not None and validate is not None and not validate(obj):
        obj = None
    return obj, text, req_m, srv_m


@dataclass
class RelevanceClient:
    base_url: str = "http://127.0.0.1:8088/v1"
    token: str | None = None
    model: str = "meterless/auto"
    cache_dir: Path | None = None
    timeout: float | httpx.Timeout = 90.0
    wall_clock_timeout: float = 120.0
    total_timeout: float = 420.0
    transport: httpx.BaseTransport | None = None
    mock_handler: Callable[[str, str], str] | None = None
    mock_served_model: str | None = None

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        if self.token is None:
            self.token = os.environ.get("METERLESS_RUNTIME_TOKEN") or ""
        if self.cache_dir is None and os.environ.get("PHOENIX_LLM_CACHE_DIR"):
            self.cache_dir = Path(os.environ["PHOENIX_LLM_CACHE_DIR"])
        if self.cache_dir is None:
            self.cache_dir = ROOT / ".cache" / "relevance"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.served_models: set[str] = set()
        self.model_calls: dict[str, int] = {}
        self.repo_mismatches: set[str] = set()
        self.last_cache_hit: bool = False
        self.last_cache_file: Path | None = None

    def discard_last(self) -> None:
        """Delete the cache entry of the last call, so an unusable answer is never replayed."""
        f = self.last_cache_file
        self.last_cache_file = None
        if f is not None:
            try:
                f.unlink()
            except OSError:
                pass

    def complete_json(
        self,
        prompt: str,
        system_prompt: str = "",
        repo_id: str = "",
        step: str = "",
        attempts: int = 3,
        validate: Callable[[dict[str, Any]], bool] | None = None,
    ) -> tuple[dict[str, Any] | None, str, str, str]:
        """Call the model until it returns a usable JSON object.

        An unusable answer (invalid JSON, or failing `validate`) is removed from the cache and
        the call is repeated with a short corrective note, so the cache key differs and a bad
        answer can never be replayed. Returns (obj_or_None, text, requested_model, served_model).
        Transport errors propagate to the caller.
        """
        last = ("", self.model, "")
        for n in range(max(1, attempts)):
            p = prompt if n == 0 else (
                prompt + f"\n\n(Retry {n}: your previous reply could not be used. Reply with ONLY one strict "
                "JSON object that follows the schema exactly: no prose, no code fences.)"
            )
            text, req_m, srv_m = self.complete(p, system_prompt, repo_id=repo_id, step=step)
            obj = extract_json_object(text)
            if obj is not None and (validate is None or validate(obj)):
                return obj, text, req_m, srv_m
            self.discard_last()
            last = (text, req_m, srv_m)
        return None, last[0], last[1], last[2]

    def complete(
        self,
        prompt: str,
        system_prompt: str = "",
        repo_id: str = "",
        step: str = "",
    ) -> tuple[str, str, str]:
        """Return (response_text, requested_model, served_model)."""
        call_start = time.monotonic()
        prompt_hash = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()
        input_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        cache_key = hashlib.sha256(f"{self.model}\n---\n{prompt_hash}\n---\n{input_hash}".encode("utf-8")).hexdigest()
        cache_file = self.cache_dir / f"{cache_key}.json"

        if cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                served = cached.get("served_model") or self.model
                if served.startswith("local-runtime/"):
                    raise RelevanceLLMError(
                        f"Nightshift served local model {served}; refusing to score with a small local model"
                    )
                self.served_models.add(served)
                self.model_calls[served] = self.model_calls.get(served, 0) + 1
                req_m = cached.get("requested_model", self.model)
                if req_m and served and req_m != served:
                    if repo_id:
                        self.repo_mismatches.add(repo_id)
                self.last_cache_hit = True
                self.last_cache_file = cache_file
                elapsed = time.monotonic() - call_start
                sys.stderr.write(f"[{repo_id or 'unknown'}] step={step or 'unknown'} cache_hit served={served} {elapsed:.2f}s\n")
                sys.stderr.flush()
                return cached.get("response_text", ""), req_m, served
            except RelevanceLLMError:
                raise
            except Exception:
                pass

        if self.mock_handler is not None:
            served = self.mock_served_model or self.model
            if served.startswith("local-runtime/"):
                raise RelevanceLLMError(
                    f"Nightshift served local model {served}; refusing to score with a small local model"
                )
            resp_text = self.mock_handler(prompt, system_prompt)
            record = {
                "cache_key": cache_key,
                "model": self.model,
                "prompt_sha256": prompt_hash,
                "input_sha256": input_hash,
                "requested_model": self.model,
                "served_model": served,
                "response_text": resp_text,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            cache_file.write_text(json.dumps(record, indent=2), encoding="utf-8")
            self.served_models.add(served)
            self.model_calls[served] = self.model_calls.get(served, 0) + 1
            if self.model and served and self.model != served:
                if repo_id:
                    self.repo_mismatches.add(repo_id)
            self.last_cache_hit = False
            self.last_cache_file = cache_file
            elapsed = time.monotonic() - call_start
            sys.stderr.write(f"[{repo_id or 'unknown'}] step={step or 'unknown'} served={served} {elapsed:.2f}s\n")
            sys.stderr.flush()
            return resp_text, self.model, served

        if not self.token or self.token == "rt-local":
            raise RelevanceLLMError("METERLESS_RUNTIME_TOKEN is missing or invalid.")

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "meterless-app": "phoenixrepo",
            "meterless-scope": "user",
        }
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "stream": False,
            "max_tokens": 4096,
        }

        if isinstance(self.timeout, (int, float)):
            client_timeout = httpx.Timeout(connect=10.0, read=float(self.timeout), write=30.0, pool=10.0)
        elif isinstance(self.timeout, httpx.Timeout):
            client_timeout = self.timeout
        else:
            client_timeout = httpx.Timeout(connect=10.0, read=90.0, write=30.0, pool=10.0)

        def _do_post() -> httpx.Response:
            with httpx.Client(timeout=client_timeout, transport=self.transport) as client:
                return client.post(url, headers=headers, json=payload)

        last_exc: Exception | None = None
        for delay in RETRY_DELAYS:
            now = time.monotonic()
            if now - call_start + delay > self.total_timeout:
                raise RelevanceLLMError(
                    f"Nightshift call exceeded total timeout limit of {self.total_timeout}s across retries."
                )
            if delay > 0:
                time.sleep(delay)

            time_left = max(0.01, min(self.wall_clock_timeout, self.total_timeout - (time.monotonic() - call_start)))

            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_do_post)
                try:
                    resp = future.result(timeout=time_left)
                except FutureTimeoutError:
                    raise RelevanceLLMError(
                        f"Nightshift request timed out (wall-clock cap of {self.wall_clock_timeout}s exceeded)."
                    )
                except httpx.HTTPError as exc:
                    last_exc = RelevanceLLMError(f"Nightshift request failed: {exc}")
                    continue
                except Exception as exc:
                    if isinstance(exc, RelevanceLLMError):
                        raise
                    last_exc = RelevanceLLMError(str(exc))
                    continue

            if resp.status_code == 200:
                data = resp.json()
                served = str(data.get("model") or "").strip() or self.model
                if served.startswith("local-runtime/"):
                    raise RelevanceLLMError(
                        f"Nightshift served local model {served}; refusing to score with a small local model"
                    )
                choices = data.get("choices") or []
                text = ""
                if choices and isinstance(choices[0], dict):
                    msg = choices[0].get("message") or {}
                    content = msg.get("content") or ""
                    if isinstance(content, list):
                        text = "".join(part.get("text", "") for part in content if isinstance(part, dict))
                    else:
                        text = str(content)

                record = {
                    "cache_key": cache_key,
                    "model": self.model,
                    "prompt_sha256": prompt_hash,
                    "input_sha256": input_hash,
                    "requested_model": self.model,
                    "served_model": served,
                    "response_text": text,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                cache_file.write_text(json.dumps(record, indent=2), encoding="utf-8")
                self.served_models.add(served)
                self.model_calls[served] = self.model_calls.get(served, 0) + 1
                if self.model and served and self.model != served:
                    if repo_id:
                        self.repo_mismatches.add(repo_id)
                self.last_cache_hit = False
                self.last_cache_file = cache_file
                elapsed = time.monotonic() - call_start
                sys.stderr.write(f"[{repo_id or 'unknown'}] step={step or 'unknown'} served={served} {elapsed:.2f}s\n")
                sys.stderr.flush()
                return text, self.model, served

            if resp.status_code in (401, 403):
                raise RelevanceLLMError(f"Nightshift returned HTTP {resp.status_code}: Unauthorized")
            last_exc = RelevanceLLMError(f"Nightshift returned HTTP {resp.status_code}")

        raise last_exc or RelevanceLLMError("Nightshift request failed after retries.")


def is_valid_demand_signal_url(url: str, candidate_repo_id: str) -> tuple[bool, str]:
    """Validate signal URL: must be issue/discussion/forum, not PR, code file, repo root or candidate repo."""
    clean = str(url or "").strip()
    if not clean:
        return False, "Signal URL is empty"
    low = clean.lower()

    # Reject candidate's own repository
    cand_low = candidate_repo_id.lower().strip()
    if cand_low and cand_low in low:
        return False, f"Signal URL is from candidate repo {candidate_repo_id}"

    # Reject PRs
    if "/pull/" in low or "/pulls/" in low:
        return False, "Signal URL is a pull request"

    # Reject code files and trees
    if "/blob/" in low or "/tree/" in low:
        return False, "Signal URL is a code file or directory"

    # For GitHub URLs: must be issue or discussion
    if "github.com" in low or "api.github.com" in low:
        if not any(k in low for k in ("/issues", "/discussions", "is:issue")):
            return False, "Signal URL is a repository page, not an issue or discussion"

    return True, ""


def extract_capabilities(
    candidate: dict[str, Any],
    client: RelevanceClient | None = None,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Extract up to 5 user-facing capabilities with exact README quotes.

    Code rejects:
    - Any quote not an exact substring of stored README.
    - Any capability made only of repo-name/owner tokens or generic words.
    """
    repo_id = str(candidate.get("repo_id") or "").strip()
    readme = str(candidate.get("readme_excerpt") or "").strip()
    if not readme:
        # Fallback to description + problem excerpt if readme excerpt is empty
        readme = str(candidate.get("description") or candidate.get("problem_excerpt") or "").strip()
    if not readme:
        return [], {"requested_model": "", "served_model": "", "fallback": False}

    if client is None:
        client = RelevanceClient()

    prompt_record = load_prompt("relevance_capabilities")
    user_prompt = (
        f"Candidate Repository: {repo_id}\n"
        f"Stored README Excerpt:\n```\n{readme}\n```\n\n"
        "Extract up to 5 user-facing capabilities as strict JSON."
    )

    try:
        text, req_m, srv_m = client.complete(user_prompt, prompt_record.text, repo_id=repo_id, step="capabilities")
    except Exception as exc:
        if isinstance(exc, RelevanceLLMError) and "refusing to score with a small local model" in str(exc):
            raise
        return [], {
            "requested_model": getattr(client, "model", "meterless/auto"),
            "served_model": "",
            "fallback": True,
            "error": str(exc),
        }

    obj = extract_json_object(text)
    if obj is None or not isinstance(obj.get("capabilities"), list):
        # Retry once for strict JSON
        try:
            retry_prompt = user_prompt + "\n\nError: Previous output was not valid JSON. Output strictly JSON matching the schema."
            text, req_m, srv_m = client.complete(retry_prompt, prompt_record.text, repo_id=repo_id, step="capabilities")
            obj = extract_json_object(text)
        except Exception as exc:
            if isinstance(exc, RelevanceLLMError) and "refusing to score with a small local model" in str(exc):
                raise

    if obj is None or not isinstance(obj.get("capabilities"), list):
        return [], {
            "requested_model": req_m,
            "served_model": srv_m,
            "fallback": True,
            "error": "LLM output invalid JSON",
        }

    # Extract tokens from repo name and owner
    repo_tokens: set[str] = set()
    for segment in repo_id.lower().replace("-", " ").replace("_", " ").split():
        repo_tokens.update(re.findall(r"[a-z0-9]+", segment))
    for t in list(repo_tokens):
        repo_tokens.update(re.findall(r"[a-z]+|[0-9]+", t))

    verified: list[dict[str, str]] = []
    seen_names: set[str] = set()

    for item in obj.get("capabilities", []):
        if len(verified) >= 5:
            break
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        quote = str(item.get("quote") or "").strip()
        if not name or not quote:
            continue
        if name.lower() in seen_names:
            continue

        # 1. Quote must be exact substring of stored text
        if quote not in readme:
            continue

        # 2. Reject if made only of repo-name/owner tokens or generic words
        words = set(re.findall(r"[a-z0-9]+", name.lower()))
        meaningful = words - STOPWORDS
        if not meaningful:
            continue
        if meaningful.issubset(repo_tokens | GENERIC_WORDS):
            continue

        seen_names.add(name.lower())
        verified.append({"name": name, "quote": quote})

    return verified, {"requested_model": req_m, "served_model": srv_m, "fallback": False}


def judge_demand(
    candidate_capabilities: list[dict[str, str]],
    signal: dict[str, Any],
    candidate_repo_id: str,
    client: RelevanceClient | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Judge if an external user signal represents demand for any candidate capability.

    Code accepts only if quote is an exact substring of signal text,
    capability is in candidate_capabilities, and URL is valid non-candidate issue/discussion.
    """
    url = str(signal.get("url") or "").strip()
    valid, reason = is_valid_demand_signal_url(url, candidate_repo_id)
    if not valid:
        return None, {"error": reason, "fallback": False}

    if not candidate_capabilities:
        return None, {"error": "No verified candidate capabilities to match", "fallback": False}

    signal_text = str(signal.get("text") or signal.get("excerpt") or "").strip()
    if not signal_text:
        return None, {"error": "Signal text is empty", "fallback": False}

    if client is None:
        client = RelevanceClient()

    prompt_record = load_prompt("relevance_demand")
    caps_str = "\n".join(f"- {c['name']} (quote: \"{c['quote']}\")" for c in candidate_capabilities)
    user_prompt = (
        f"Candidate Capabilities:\n{caps_str}\n\n"
        f"Signal URL: {url}\n"
        f"Signal Text:\n```\n{signal_text}\n```\n\n"
        "Judge if this signal represents demand for one of the candidate capabilities. Strict JSON output."
    )

    try:
        text, req_m, srv_m = client.complete(
            user_prompt, prompt_record.text, repo_id=candidate_repo_id, step="demand"
        )
    except Exception as exc:
        if isinstance(exc, RelevanceLLMError) and "refusing to score with a small local model" in str(exc):
            raise
        return None, {
            "requested_model": getattr(client, "model", "meterless/auto"),
            "served_model": "",
            "fallback": True,
            "error": str(exc),
        }

    obj = extract_json_object(text)
    if obj is None:
        return None, {
            "requested_model": req_m,
            "served_model": srv_m,
            "fallback": True,
            "error": "LLM output invalid JSON",
        }

    relevant = bool(obj.get("relevant"))
    if not relevant:
        return None, {
            "requested_model": req_m,
            "served_model": srv_m,
            "fallback": False,
            "reason": "Judged not relevant",
        }

    quote = str(obj.get("quote") or "").strip()
    if not quote or quote not in signal_text:
        return None, {
            "requested_model": req_m,
            "served_model": srv_m,
            "fallback": False,
            "reason": "Quote not an exact substring of signal text",
        }

    cap_name = str(obj.get("capability") or "").strip().lower()
    matched_cap = None
    for c in candidate_capabilities:
        c_low = c["name"].lower()
        if c_low == cap_name or c_low in cap_name or cap_name in c_low:
            matched_cap = c
            break

    if not matched_cap:
        return None, {
            "requested_model": req_m,
            "served_model": srv_m,
            "fallback": False,
            "reason": f"Capability '{obj.get('capability')}' not in candidate capability list",
        }

    result = {
        "relevant": True,
        "capability": matched_cap["name"],
        "quote": quote,
        "why": str(obj.get("why") or ""),
        "url": url,
        "signal_id": str(signal.get("signal_id") or ""),
    }
    return result, {"requested_model": req_m, "served_model": srv_m, "fallback": False}


def judge_alternative_coverage(
    capability: dict[str, str],
    alternative: dict[str, Any],
    client: RelevanceClient | None = None,
    repo_id: str = "",
) -> tuple[bool, str, dict[str, Any]]:
    """Judge if a maintained free alternative covers a candidate capability.

    Code verifies the quote is an exact substring of alternative's text.
    """
    alt_text = str(alternative.get("excerpt") or alternative.get("text") or "").strip()
    if not alt_text:
        return False, "", {"error": "Alternative text is empty", "fallback": False}

    if client is None:
        client = RelevanceClient()

    prompt_record = load_prompt("relevance_coverage")
    user_prompt = (
        f"Candidate Capability: {capability['name']}\n"
        f"Candidate Quote: \"{capability['quote']}\"\n\n"
        f"Alternative ({alternative.get('alternative_id', '')}) Text:\n```\n{alt_text}\n```\n\n"
        "Judge if the alternative covers this capability. Strict JSON output."
    )

    try:
        text, req_m, srv_m = client.complete(
            user_prompt, prompt_record.text, repo_id=repo_id, step="coverage"
        )
    except Exception as exc:
        if isinstance(exc, RelevanceLLMError) and "refusing to score with a small local model" in str(exc):
            raise
        return False, "", {
            "requested_model": getattr(client, "model", "meterless/auto"),
            "served_model": "",
            "fallback": True,
            "error": str(exc),
        }

    obj = extract_json_object(text)
    if obj is None:
        return False, "", {
            "requested_model": req_m,
            "served_model": srv_m,
            "fallback": True,
            "error": "LLM output invalid JSON",
        }

    covered = bool(obj.get("covered"))
    if not covered:
        return False, "", {
            "requested_model": req_m,
            "served_model": srv_m,
            "fallback": False,
            "why": str(obj.get("why") or "Not covered"),
        }

    quote = str(obj.get("quote") or "").strip()
    if not quote or quote not in alt_text:
        return False, "", {
            "requested_model": req_m,
            "served_model": srv_m,
            "fallback": False,
            "reason": "Covered quote not found in alternative text",
        }

    return True, quote, {
        "requested_model": req_m,
        "served_model": srv_m,
        "fallback": False,
        "why": str(obj.get("why") or ""),
    }


def is_client_configured(client: RelevanceClient | None) -> bool:
    """Check if a RelevanceClient is configured and able to make LLM calls."""
    if client is None:
        token = os.environ.get("METERLESS_RUNTIME_TOKEN")
        return bool(token and token != "rt-local")
    if client.mock_handler is not None:
        return True
    return bool(client.token and client.token != "rt-local")


def normalize_whitespace(text: str) -> str:
    """Normalize repeated whitespace and line breaks to single spaces."""
    return " ".join(str(text or "").split())


_MARKUP_TRANS = str.maketrans({
    "*": " ", "_": " ", "`": " ", "#": " ", ">": " ",
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-",
})


def _normalize_for_quote(text: str) -> str:
    """Strip Markdown emphasis/heading marks and typographic quotes, then collapse whitespace.

    Deterministic: the words and their order must still match exactly. This only stops
    "**Rammingen** is ..." in a README from failing against the quote "Rammingen is ...".
    """
    return normalize_whitespace(str(text or "").translate(_MARKUP_TRANS)).casefold()


def quote_in_text(quote: str, text: str) -> bool:
    """Deterministic quote verification: case-insensitive substring check after normalising
    whitespace, Markdown emphasis marks and typographic quotes."""
    if not quote or not text:
        return False
    if normalize_whitespace(quote).casefold() in normalize_whitespace(text).casefold():
        return True
    nq = _normalize_for_quote(quote)
    return bool(nq) and nq in _normalize_for_quote(text)


def build_search_queries(candidate: dict[str, Any], problem_statement: dict[str, Any]) -> list[str]:
    """Build 4-8 problem-oriented search queries describing the job, not repo words.

    Must include at least:
    - <platform> cli
    - <platform> <job> tool (e.g. 'youtrack export markdown')
    - <platform> api client
    - official-feature query (<platform> export <format> / <platform> built-in <job>)
    """
    known_platforms = [
        "youtrack", "jira", "github", "gitlab", "notion", "confluence", "slack",
        "discord", "obsidian", "trello", "linear", "clickup", "airtable", "salesforce",
        "zendesk", "shopify", "stripe", "docker", "kubernetes", "aws", "gcp",
        "azure", "postgres", "sqlite", "mysql", "redis", "mongodb", "elastic",
        "grafana", "prometheus", "sentry", "bitbucket", "jenkins", "circleci",
        "ansible", "terraform", "vault", "kafka", "rabbitmq",
    ]
    known_formats = ["markdown", "json", "csv", "pdf", "html", "yaml", "xml", "text", "excel"]
    known_jobs = ["export", "sync", "import", "backup", "edit", "fetch", "generate", "publish", "migrate", "archive"]

    category = str(problem_statement.get("category") or "").strip().lower()
    desc = str(candidate.get("description") or "").lower()
    readme = str(candidate.get("readme_excerpt") or "").lower()
    prob = str(problem_statement.get("problem") or "").lower()
    user = str(problem_statement.get("target_user") or "").lower()
    cand_id = str(candidate.get("repo_id") or "").lower()
    cand_text = f"{cand_id} {desc} {readme} {prob} {user} {category}"

    # 1. Identify platform
    platform = ""
    for p in known_platforms:
        if re.search(r"\b" + re.escape(p) + r"\b", cand_text):
            platform = p
            break
    if not platform:
        m = re.search(r"(?:using|for|with|in)\s+([a-zA-Z0-9_-]+)", f"{user} {prob}", re.I)
        if m and m.group(1).lower() not in {"the", "a", "an", "all", "our", "their", "your", "my", "this", "that"}:
            platform = m.group(1).lower()
    if not platform and candidate.get("core_terms"):
        terms = [t.strip().lower() for t in str(candidate.get("core_terms")).split(",") if t.strip()]
        generic = {"tool", "cli", "app", "sync", "export", "generator", "manager", "service", "system", "client", "library", "markdown", "python", "javascript", "workflow", "workflows"}
        for t in terms:
            if t not in generic and len(t) > 2:
                platform = t
                break
    if not platform:
        platform = category.split("-")[0].lower() if category else "developer"

    # 2. Identify format
    format_str = ""
    for f in known_formats:
        if re.search(r"\b" + re.escape(f) + r"\b", cand_text) or (f == "markdown" and re.search(r"\bmd\b", cand_text)):
            format_str = f
            break
    if not format_str:
        format_str = "markdown" if "markdown" in cand_text else "json"

    # 3. Identify job
    job_word = ""
    for j in known_jobs:
        if re.search(r"\b" + re.escape(j) + r"\b", cand_text):
            job_word = j
            break
    if not job_word:
        job_word = "export" if "export" in cand_text else "sync"

    # Required patterns
    pattern_queries: list[str] = [
        f"{platform} cli",
        f"{platform} {job_word} {format_str}" if format_str in cand_text else f"{platform} {job_word} tool",
        f"{platform} api client",
        f"{platform} export {format_str}",
        f"{platform} built-in {job_word}",
    ]

    # Raw search queries from LLM
    raw_queries = problem_statement.get("search_queries") or []
    llm_queries = [str(q).strip() for q in raw_queries if str(q).strip()]

    # Self-hosted / open-source queries
    extra_queries: list[str] = []
    is_self_hosted = any(k in cand_text for k in ("self-hosted", "self hosted", "selfhosted"))
    if is_self_hosted and category:
        extra_queries.append(f"self-hosted {category}")
        extra_queries.append(f"open source {category}")

    stack_components = {
        "postgres", "sqlite", "mysql", "redis", "mongodb", "elastic", "docker", "kubernetes",
        "aws", "gcp", "azure", "kafka", "rabbitmq", "developer",
    }
    identity_text = f"{cand_id} {desc}"
    if platform in stack_components or not re.search(r"\b" + re.escape(platform) + r"\b", identity_text):
        # A database/infra word is how the repo is built, not the job it does; and a platform
        # only mentioned somewhere in the README (e.g. "github") is not what the tool is for.
        pattern_queries = []
    # Interleave so both the user's job (LLM) and platform patterns get searched early.
    interleaved: list[str] = []
    for i in range(max(len(llm_queries), len(pattern_queries))):
        if i < len(llm_queries):
            interleaved.append(llm_queries[i])
        if i < len(pattern_queries):
            interleaved.append(pattern_queries[i])
    all_queries = interleaved + extra_queries

    # Deduplicate preserving order
    seen = set()
    dedup_queries: list[str] = []
    for q in all_queries:
        qk = q.lower()
        if qk not in seen:
            seen.add(qk)
            dedup_queries.append(q)

    # Product-discovery queries: the way a person looks for an existing solution ("best X tools",
    # "X alternatives"). They surface mainstream products and comparison pages that keyword
    # queries about the job miss. Placed early so the 8-query cap never drops them.
    if llm_queries:
        product_queries = []
        for raw_job in llm_queries[:2]:
            job = re.sub(r"\s+", " ", raw_job).strip()
            job = re.sub(r"\s+(tools?|software|apps?)$", "", job, flags=re.I)
            if job:
                product_queries.append(f"best {job} tools")
        for pq in reversed(product_queries):
            if pq.lower() not in seen:
                seen.add(pq.lower())
                dedup_queries.insert(min(2, len(dedup_queries)), pq)

    # Ensure 4 to 8 queries
    if len(dedup_queries) < 4:
        if platform in stack_components:
            cat_words = category.replace("-", " ").strip()
            fallbacks = [f for f in [f"{cat_words} open source" if cat_words else "", f"{llm_queries[0]} alternative" if llm_queries else "", f"{cat_words} saas" if cat_words else ""] if f]
        else:
            fallbacks = [f"{platform} tool", f"{platform} open source", f"{category} tool"]
        for fallback in fallbacks:
            if fallback.lower() not in seen:
                seen.add(fallback.lower())
                dedup_queries.append(fallback)
            if len(dedup_queries) >= 4:
                break

    return dedup_queries[:8]


def broaden_queries(queries: list[str], category: str = "", candidate_repo_id: str = "") -> list[str]:
    """Generate broader search queries by dropping the most specific term and adding category."""
    broader: list[str] = []
    cand_tokens = {t.lower() for t in re.findall(r"\w+", candidate_repo_id)}
    cat_clean = category.strip().lower()

    for q in queries:
        words = q.split()
        if len(words) <= 1:
            if cat_clean and cat_clean not in q.lower():
                broader.append(f"{q} {cat_clean}".strip())
            continue

        # Drop the most specific term: first check if any word matches candidate tokens
        dropped = False
        remaining = []
        for w in words:
            if not dropped and w.lower() in cand_tokens:
                dropped = True
                continue
            remaining.append(w)

        # If no candidate token was in the query, drop the longest word (most specific)
        if not dropped and remaining:
            longest = max(remaining, key=len)
            remaining.remove(longest)

        broader_q = " ".join(remaining).strip()
        if broader_q:
            broader.append(broader_q)
            if cat_clean and cat_clean not in broader_q.lower():
                broader.append(f"{broader_q} {cat_clean}".strip())

    if cat_clean and not any(cat_clean == b.lower() for b in broader):
        broader.append(cat_clean)

    # Deduplicate while preserving order and excluding original queries
    orig_set = {q.lower() for q in queries}
    seen = set()
    dedup_broader: list[str] = []
    for b in broader:
        bk = b.lower()
        if bk not in seen and bk not in orig_set:
            seen.add(bk)
            dedup_broader.append(b)
    return dedup_broader


def judge_discovery(
    candidate: dict[str, Any],
    prior_rows: list[dict[str, Any]],
    demand_rows: list[dict[str, Any]],
    client: RelevanceClient | None = None,
    problem_alternatives: list[dict[str, Any]] | None = None,
    source_status: dict[str, str] | None = None,
    source_counts: dict[str, int] | None = None,
    insufficient_coverage: bool = False,
) -> tuple[str, str]:
    """Judge whether candidate is CANDIDATE, ALREADY_EXISTS, or NO_GO using LLM with deterministic quote verification.

    Never uses repository stars or forks.
    Verifies alternative_repo against the union of prior_rows and problem_alternatives.
    Falls back to phrase/keyword decide_discovery when no RelevanceClient is configured.
    Enforces that absence is not evidence: insufficient coverage blocks CANDIDATE verdict.
    """
    from app.agents.market_filter import decide_discovery

    # Union of (a) prior_rows and (b) problem_alternatives
    combined_priors: list[dict[str, Any]] = []
    seen_ids: dict[str, dict[str, Any]] = {}

    for r in list(prior_rows or []) + list(problem_alternatives or []):
        alt_id = str(r.get("alternative_id") or r.get("url") or "").strip()
        alt_key = alt_id.casefold()
        if not alt_key:
            continue
        if alt_key in seen_ids:
            existing = seen_ids[alt_key]
            if r.get("overlap") and not existing.get("overlap"):
                existing["overlap"] = r["overlap"]
            if r.get("same_problem") is not None and existing.get("same_problem") is None:
                existing["same_problem"] = r["same_problem"]
            if r.get("excerpt") and not existing.get("excerpt"):
                existing["excerpt"] = r["excerpt"]
            if r.get("description") and not existing.get("description"):
                existing["description"] = r["description"]
            if r.get("readme") and not existing.get("readme"):
                existing["readme"] = r["readme"]
            continue
        seen_ids[alt_key] = r
        combined_priors.append(r)

    # Check multi-source coverage guards
    has_source_error = source_status is not None and any(st == "SOURCE_ERROR" for st in source_status.values())
    zero_results = source_counts is not None and sum(source_counts.values()) == 0 and len(combined_priors) == 0
    fewer_than_two = source_status is not None and len(source_status) >= 2 and sum(1 for st in source_status.values() if st == "ok") < 2 and len(combined_priors) == 0
    coverage_blocked = (insufficient_coverage and len(combined_priors) == 0) or has_source_error or zero_results or fewer_than_two

    if has_source_error:
        err_sources = [s for s, st in source_status.items() if st == "SOURCE_ERROR"]
        coverage_reason = f"INSUFFICIENT_PRIOR_ART_COVERAGE: source error in {', '.join(err_sources)}"
    elif zero_results:
        coverage_reason = "INSUFFICIENT_PRIOR_ART_COVERAGE: zero alternatives found across all sources"
    elif fewer_than_two:
        coverage_reason = "INSUFFICIENT_PRIOR_ART_COVERAGE: fewer than 2 sources succeeded"
    else:
        coverage_reason = "INSUFFICIENT_PRIOR_ART_COVERAGE: insufficient prior art coverage"

    def _is_web_row(r: dict[str, Any]) -> bool:
        return str(r.get("source") or "") == "web" or str(r.get("alternative_id") or "").startswith("web:")

    mainstream_overlap = [
        r for r in combined_priors
        if _is_web_row(r) and r.get("same_problem") is True and str(r.get("overlap") or "") in {"partial", "full"}
    ]
    # Show same-problem alternatives (web first) to the judge before unrelated rows.
    combined_priors.sort(key=lambda r: (r.get("same_problem") is not True, not _is_web_row(r)))

    if not is_client_configured(client):
        verdict, reason = decide_discovery(candidate, combined_priors, demand_rows)
        clean_reason = reason.replace("Method: phrase/keyword.", "").rstrip().rstrip(".")
        return verdict, f"{clean_reason}. Method: phrase/keyword (fallback)."

    if client is None:
        client = RelevanceClient()

    repo_id = str(candidate.get("repo_id") or "").strip()
    readme = str(candidate.get("readme_excerpt") or "").strip()
    desc = str(candidate.get("description") or "").strip()
    prob = str(candidate.get("problem_excerpt") or "").strip()
    cand_text = f"{desc}\n{readme}\n{prob}".strip()

    prompt_record = load_prompt("relevance_discovery")

    user_prompt_lines = [
        f"Candidate Repository: {repo_id}",
        f"Description: {desc}",
        f"README Excerpt:\n```\n{readme}\n```",
    ]
    if prob:
        user_prompt_lines.append(f"Problem Excerpt: {prob}")

    if combined_priors:
        priors_desc = []
        for r in combined_priors[:10]:
            alt_id = r.get("alternative_id") or r.get("url")
            lic = r.get("licence_spdx") or "unknown"
            exc = (r.get("excerpt") or r.get("description") or r.get("text") or "")[:200]
            priors_desc.append(f"- Alternative: {alt_id} (licence: {lic})\n  Excerpt: {exc}")
        user_prompt_lines.append("Prior Art Alternatives Checked:\n" + "\n".join(priors_desc))
    else:
        user_prompt_lines.append("Prior Art Alternatives Checked: None")

    if demand_rows:
        demands_desc = []
        for d in demand_rows[:5]:
            url = d.get("url") or d.get("signal_id")
            exc = (d.get("excerpt") or d.get("text") or "")[:200]
            demands_desc.append(f"- Demand Signal: {url}\n  Excerpt: {exc}")
        user_prompt_lines.append("External Demand Signals:\n" + "\n".join(demands_desc))
    else:
        user_prompt_lines.append("External Demand Signals: None")

    user_prompt_lines.append(
        "Judge whether this candidate is CANDIDATE, ALREADY_EXISTS, or NO_GO according to the rubric. Output strict JSON."
    )
    user_prompt = "\n\n".join(user_prompt_lines)

    text, req_m, srv_m = client.complete(
        user_prompt, prompt_record.text, repo_id=repo_id, step="discovery"
    )

    obj = extract_json_object(text)
    if obj is None:
        verdict = "NEEDS_REVIEW"
        reason = "unverified LLM quote: LLM response was not valid JSON"
        return verdict, f"{reason}. Method: llm+verified quote (requested={req_m}, served={srv_m})."

    raw_verdict = str(obj.get("verdict") or "NO_GO").strip().upper()
    reason = str(obj.get("reason") or "").strip()
    problem_quote = str(obj.get("problem_quote") or "").strip()
    alt_repo = str(obj.get("alternative_repo") or "").strip() if obj.get("alternative_repo") else None
    alt_quote = str(obj.get("alternative_quote") or "").strip() if obj.get("alternative_quote") else None

    # Deterministic verification
    if has_source_error and raw_verdict != "ALREADY_EXISTS":
        verdict = "NEEDS_REVIEW"
        reason = coverage_reason
    elif zero_results:
        verdict = "NEEDS_REVIEW"
        reason = coverage_reason
    elif raw_verdict == "CANDIDATE":
        if coverage_blocked:
            verdict = "NEEDS_REVIEW"
            reason = coverage_reason
        elif not problem_quote or not quote_in_text(problem_quote, cand_text):
            verdict = "NEEDS_REVIEW"
            reason = f"unverified LLM quote: problem_quote '{problem_quote}' not found in candidate README or description"
        elif mainstream_overlap:
            # An official feature, doc page or commercial product on the web solves the
            # same problem. A unique feature mix is not proof of novelty: a human must
            # confirm the remaining gap before this repo can be a CANDIDATE.
            m = mainstream_overlap[0]
            verdict = "NEEDS_REVIEW"
            reason = (
                f"MAINSTREAM_OVERLAP: web result {m.get('url') or m.get('alternative_id')} solves the same problem "
                f"(overlap {m.get('overlap')}); a human must confirm the remaining gap before CANDIDATE"
            )
        else:
            verdict = "CANDIDATE"
    elif raw_verdict == "ALREADY_EXISTS":
        # Check alternative repo matches one in combined_priors
        matched_row = None
        if alt_repo:
            for r in combined_priors:
                r_id = str(r.get("alternative_id") or "").strip()
                r_name = r_id.split("/")[-1] if "/" in r_id else r_id
                r_url = str(r.get("url") or "").strip()
                r_desc = str(r.get("description") or "").strip()
                if (
                    alt_repo.casefold() == r_id.casefold()
                    or alt_repo.casefold() == r_name.casefold()
                    or alt_repo.casefold() in r_id.casefold()
                    or r_id.casefold() in alt_repo.casefold()
                    or (r_url and alt_repo.casefold() in r_url.casefold())
                    or (r_desc and alt_repo.casefold() in r_desc.casefold())
                ):
                    matched_row = r
                    break
        if matched_row is None:
            verdict = "NEEDS_REVIEW"
            reason = f"unverified LLM quote: alternative_repo '{alt_repo}' not found in candidate prior-art"
        else:
            alt_text = f"{matched_row.get('excerpt', '')} {matched_row.get('description', '')} {matched_row.get('text', '')} {matched_row.get('readme', '')}"
            if not alt_quote or not quote_in_text(alt_quote, alt_text):
                verdict = "NEEDS_REVIEW"
                reason = f"unverified LLM quote: alternative_quote '{alt_quote}' not found in alternative {matched_row.get('alternative_id')} text"
            elif matched_row.get("overlap") and matched_row.get("overlap") != "full":
                verdict = "NEEDS_REVIEW"
                reason = f"unverified ALREADY_EXISTS: alternative '{matched_row.get('alternative_id')}' has overlap '{matched_row.get('overlap')}', requires verified full overlap"
            elif any(r.get("overlap") for r in combined_priors) and not any(r.get("overlap") == "full" for r in combined_priors) and matched_row.get("overlap") != "full" and not matched_row.get("licence_spdx"):
                verdict = "NEEDS_REVIEW"
                reason = "unverified ALREADY_EXISTS: requires at least one verified full-overlap alternative"
            else:
                verdict = "ALREADY_EXISTS"
    elif raw_verdict == "NO_GO":
        if problem_quote and problem_quote.casefold() not in {"unknown", "none", "null", ""}:
            if not quote_in_text(problem_quote, cand_text):
                verdict = "NEEDS_REVIEW"
                reason = f"unverified LLM quote: problem_quote '{problem_quote}' not found in candidate README or description"
            else:
                verdict = "NO_GO"
        else:
            verdict = "NO_GO"
    else:
        verdict = "NEEDS_REVIEW"
        reason = f"unverified LLM quote: unexpected verdict '{raw_verdict}'"

    clean_reason = reason.rstrip().rstrip(".")
    labeled_reason = f"{clean_reason}. Method: llm+verified quote (requested={req_m}, served={srv_m})."
    return verdict, labeled_reason


def extract_problem_statement(
    candidate: dict[str, Any],
    client: RelevanceClient | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Extract candidate problem statement, target user, category, search queries, and supporting quotes.

    Verifies every quote verbatim in candidate README/description.
    Returns (problem_data, metadata). If verification fails or JSON invalid, problem_data is None.
    """
    repo_id = str(candidate.get("repo_id") or "").strip()
    readme = str(candidate.get("readme_excerpt") or "").strip()
    desc = str(candidate.get("description") or "").strip()
    prob = str(candidate.get("problem_excerpt") or "").strip()
    cand_text = f"{desc}\n{readme}\n{prob}".strip()

    if not cand_text:
        return None, {"error": "Candidate text is empty", "fallback": False}

    if client is None:
        client = RelevanceClient()

    prompt_record = load_prompt("relevance_problem")
    user_prompt = (
        f"Candidate Repository: {repo_id}\n"
        f"Description: {desc}\n"
        f"README Excerpt:\n```\n{readme}\n```\n\n"
        "Extract the problem statement, target user, category, 3-5 problem-oriented search queries, "
        "and verbatim supporting quotes from the candidate README as strict JSON."
    )

    try:
        obj, text, req_m, srv_m = _complete_json(
            client, user_prompt, prompt_record.text, repo_id, "problem"
        )
    except Exception as exc:
        if isinstance(exc, RelevanceLLMError) and "refusing to score with a small local model" in str(exc):
            raise
        return None, {
            "requested_model": getattr(client, "model", "meterless/auto"),
            "served_model": "",
            "fallback": True,
            "error": str(exc),
        }

    if obj is None:
        return None, {
            "requested_model": req_m,
            "served_model": srv_m,
            "fallback": True,
            "error": "LLM output invalid JSON",
        }

    quotes = obj.get("quotes")
    if not isinstance(quotes, list) or len(quotes) == 0:
        return None, {
            "error": "unverified LLM quote: no quotes provided in problem statement",
            "unverified_quote": "",
            "requested_model": req_m,
            "served_model": srv_m,
            "fallback": False,
        }

    for q in quotes:
        q_str = str(q or "").strip()
        if not q_str or not quote_in_text(q_str, cand_text):
            return None, {
                "error": f"unverified LLM quote: problem quote '{q_str}' not found in candidate README or description",
                "unverified_quote": q_str,
                "requested_model": req_m,
                "served_model": srv_m,
                "fallback": False,
            }

    raw_queries = obj.get("search_queries") or []
    search_queries = [str(sq).strip() for sq in raw_queries if str(sq).strip()]

    problem_data = {
        "problem": str(obj.get("problem") or "").strip(),
        "target_user": str(obj.get("target_user") or "").strip(),
        "category": str(obj.get("category") or "").strip(),
        "search_queries": search_queries[:5],
        "quotes": [str(q).strip() for q in quotes if str(q).strip()],
    }
    return problem_data, {"requested_model": req_m, "served_model": srv_m, "fallback": False}


class SearchResultsList(list):
    """List of candidate alternative dicts with multi-source coverage metadata."""

    def __init__(
        self,
        items: list[dict[str, Any]] | None = None,
        source_counts: dict[str, int] | None = None,
        source_status: dict[str, str] | None = None,
        insufficient_coverage: bool = False,
    ) -> None:
        super().__init__(items or [])
        self.source_counts: dict[str, int] = source_counts or {}
        self.source_status: dict[str, str] = source_status or {}
        self.insufficient_coverage: bool = insufficient_coverage


def search_prior_art(
    candidate: dict[str, Any],
    queries: list[str],
    toolbox: Any,
    language: str | None = None,
) -> SearchResultsList:
    """Search for potential prior-art alternatives across queries via the existing toolbox.

    Queries multi-sources (GitHub, npm, PyPI, and Web/DuckDuckGo).
    Deduplicates results, excludes candidate itself and its forks, keeps at most 15 results.
    Never uses candidate's own stars or forks anywhere.
    Tracks per-source counts and status.
    """
    cand_id = str(candidate.get("repo_id") or "").strip()
    cand_name = cand_id.split("/")[-1].casefold() if "/" in cand_id else cand_id.casefold()
    cand_url = str(candidate.get("html_url") or "").strip().casefold().rstrip("/")
    lang = (language or candidate.get("primary_language") or candidate.get("language") or "").strip()

    results: list[dict[str, Any]] = []
    seen_ids: set[str] = {cand_id.casefold()}
    if cand_url:
        seen_ids.add(cand_url)

    # Detect active sources in toolbox
    active_sources: set[str] = set()
    if toolbox is not None:
        if hasattr(toolbox, "has"):
            for s in ["github", "npm", "pypi", "web_search", "web"]:
                if toolbox.has(s):
                    active_sources.add(s)
        if hasattr(toolbox, "tools") and isinstance(toolbox.tools, dict):
            for s in ["github", "npm", "pypi", "web_search", "web"]:
                if s in toolbox.tools:
                    active_sources.add(s)
    if not active_sources:
        active_sources = {"github", "npm", "pypi", "web"}

    source_counts: dict[str, int] = {"github": 0, "npm": 0, "pypi": 0, "web": 0}
    source_status: dict[str, str] = {}
    for s in ["github", "npm", "pypi", "web"]:
        if s in active_sources or (s == "web" and "web_search" in active_sources):
            source_status[s] = "ok"

    # Collect one bucket per query, then merge round-robin so the first query
    # cannot fill every slot (e.g. a tech-stack query crowding out the job query).
    max_results = 18
    buckets: list[list[tuple[str, dict[str, Any]]]] = []
    if "pypi" in source_status:
        # PyPI's search page is behind a bot challenge; package metadata lookups still work,
        # but search is not a reliable source, so it is not counted as a search source.
        source_status.pop("pypi", None)

    for idx, q in enumerate(queries):
        if toolbox is None or toolbox.budget.is_exhausted():
            break
        bucket: list[tuple[str, dict[str, Any]]] = []

        # 1. GitHub repositories (every query; README fetched later for selected rows only)
        if "github" in source_status:
            gh_res = toolbox.call("github", {"action": "repositories", "query": q, "per_page": 5})
            if gh_res.ok and isinstance(gh_res.data, dict):
                for it in gh_res.data.get("items") or []:
                    full_name = str(it.get("full_name") or it.get("name") or "").strip()
                    if not full_name:
                        continue
                    fn_lower = full_name.casefold()
                    if fn_lower == cand_id.casefold():
                        continue
                    is_fork = bool(it.get("fork")) or bool(it.get("is_fork"))
                    parent_fn = str((it.get("parent") or {}).get("full_name") or "").casefold()
                    source_fn = str((it.get("source") or {}).get("full_name") or "").casefold()
                    it_name = str(it.get("name") or "").casefold()
                    it_desc = str(it.get("description") or "").casefold()
                    if parent_fn == cand_id.casefold() or source_fn == cand_id.casefold():
                        continue
                    if is_fork and (it_name == cand_name or f"forked from {cand_id.casefold()}" in it_desc):
                        continue
                    if it.get("is_fork_of_candidate"):
                        continue
                    lic = it.get("license") or {}
                    spdx = str(lic.get("spdx_id") or "") if isinstance(lic, dict) else str(lic or "")
                    bucket.append((fn_lower, {
                        "alternative_id": full_name,
                        "url": str(it.get("html_url") or it.get("url") or f"https://github.com/{full_name}").strip(),
                        "description": str(it.get("description") or "").strip(),
                        "readme": str(it.get("readme") or ""),
                        "licence_spdx": spdx,
                        "stars": it.get("stargazers_count", 0),
                        "pushed_at": str(it.get("pushed_at") or ""),
                        "source": "github",
                        "query": q,
                    }))
            elif not gh_res.ok:
                err_str = str(gh_res.error or "")
                if "SOURCE_ERROR" in err_str:
                    source_status["github"] = "SOURCE_ERROR"

        # 2. npm registry (first 3 queries)
        if "npm" in source_status and idx < 3 and not toolbox.budget.is_exhausted():
            npm_res = toolbox.call("npm", {"action": "search", "query": q})
            if npm_res.ok and isinstance(npm_res.data, dict):
                for it in (npm_res.data.get("items") or [])[:5]:
                    pkg_name = str(it.get("name") or "").strip()
                    if not pkg_name or pkg_name.casefold() == cand_name:
                        continue
                    alt_id = f"npm:{pkg_name}"
                    bucket.append((alt_id.casefold(), {
                        "alternative_id": alt_id,
                        "url": it.get("url") or f"https://www.npmjs.com/package/{pkg_name}",
                        "description": str(it.get("description") or ""),
                        "readme": "",
                        "licence_spdx": str(it.get("license") or ""),
                        "stars": 0,
                        "pushed_at": "",
                        "source": "npm",
                        "query": q,
                    }))
            elif not npm_res.ok:
                source_status["npm"] = "SOURCE_ERROR"

        # 3. Web (Exa first, every query) - surfaces official features, docs and commercial products
        if "web" in source_status and not toolbox.budget.is_exhausted():
            tool_name = "web_search" if (hasattr(toolbox, "has") and toolbox.has("web_search")) or (hasattr(toolbox, "tools") and "web_search" in toolbox.tools) else "web"
            web_res = toolbox.call(tool_name, {"query": q})
            if web_res.ok and isinstance(web_res.data, dict):
                for it in (web_res.data.get("items") or [])[:6]:
                    url = str(it.get("url") or "").strip()
                    title = str(it.get("title") or "").strip()
                    snippet = str(it.get("snippet") or "").strip()
                    if not url:
                        continue
                    url_lower = url.casefold().rstrip("/")
                    if (cand_url and cand_url in url_lower) or cand_id.casefold() in url_lower:
                        continue
                    desc = f"{title} - {snippet}" if title and snippet else (title or snippet)
                    bucket.append((url_lower, {
                        "alternative_id": f"web:{url}",
                        "url": url,
                        "description": desc,
                        "readme": snippet,
                        "licence_spdx": "",
                        "stars": 0,
                        "pushed_at": "",
                        "source": "web",
                        "query": q,
                    }))
            elif not web_res.ok:
                source_status["web"] = "SOURCE_ERROR"

        # One bucket per (query, source) so GitHub rows cannot crowd out npm and web rows.
        for src in ("github", "npm", "web"):
            buckets.append([x for x in bucket if x[1]["source"] == src])

    # Merge: round-robin across queries within each source, then per-source quotas
    # so GitHub/npm rows cannot crowd out web rows (official features and commercial
    # products), then fill any unused slots from what is left.
    quotas = {"web": 9, "github": 6, "npm": 3}
    per_source: dict[str, list[tuple[str, dict[str, Any]]]] = {"github": [], "npm": [], "web": []}
    for src in ("github", "npm", "web"):
        src_buckets = [bk for bk in buckets if bk and bk[0][1]["source"] == src]
        cur = [0] * len(src_buckets)
        moved = True
        while moved:
            moved = False
            for bi, bk in enumerate(src_buckets):
                if cur[bi] < len(bk):
                    per_source[src].append(bk[cur[bi]])
                    cur[bi] += 1
                    moved = True
    leftovers: list[tuple[str, dict[str, Any]]] = []
    for src in ("web", "github", "npm"):
        taken = 0
        for key, row in per_source[src]:
            if key in seen_ids:
                continue
            if taken < quotas[src] and len(results) < max_results:
                seen_ids.add(key)
                results.append(row)
                source_counts[row["source"]] = source_counts.get(row["source"], 0) + 1
                taken += 1
            else:
                leftovers.append((key, row))
    for key, row in leftovers:
        if len(results) >= max_results:
            break
        if key in seen_ids:
            continue
        seen_ids.add(key)
        results.append(row)
        source_counts[row["source"]] = source_counts.get(row["source"], 0) + 1

    # README excerpts for selected GitHub rows only
    for row in results:
        if row["source"] != "github" or row["readme"] or toolbox is None:
            continue
        if toolbox.budget.is_exhausted() or toolbox.budget.remaining_calls <= 1:
            break
        rm_res = toolbox.call("github", {"action": "readme", "repo": row["alternative_id"]})
        if rm_res.ok and isinstance(rm_res.data, dict):
            row["readme"] = str(rm_res.data.get("decoded_text") or "")[:2000]

    # Compute coverage status
    successful_sources = sum(1 for st in source_status.values() if st == "ok")
    has_source_error = any(st == "SOURCE_ERROR" for st in source_status.values())
    zero_found = len(results) == 0

    if len(source_status) >= 2:
        insufficient_coverage = zero_found or has_source_error or (successful_sources < 2)
    else:
        insufficient_coverage = zero_found or has_source_error

    return SearchResultsList(
        results[:15],
        source_counts=source_counts,
        source_status=source_status,
        insufficient_coverage=insufficient_coverage,
    )


def judge_alternative_relevance(
    problem_statement: dict[str, Any],
    candidate_repo_id: str,
    search_result: dict[str, Any],
    client: RelevanceClient | None = None,
    trace: Any | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Judge if a search result solves the same problem as the candidate (step="alternative").

    Verifies quotes verbatim against result description/README.
    Unverified results are dropped and recorded in trace.
    """
    alt_id = str(search_result.get("alternative_id") or "").strip()
    alt_desc = str(search_result.get("description") or "").strip()
    alt_readme = str(search_result.get("readme") or "").strip()
    result_text = f"{alt_desc}\n{alt_readme}".strip()

    if not result_text:
        return None, {"error": "Alternative text is empty", "dropped": True}

    if client is None:
        client = RelevanceClient()

    prompt_record = load_prompt("relevance_alternative")
    user_prompt = (
        f"Candidate Problem: {problem_statement.get('problem', '')}\n"
        f"Target User: {problem_statement.get('target_user', '')}\n"
        f"Category: {problem_statement.get('category', '')}\n\n"
        f"Alternative Repository: {alt_id}\n"
        f"URL: {search_result.get('url', '')}\n"
        f"Description and README Excerpt:\n```\n{result_text}\n```\n\n"
        "Judge whether this alternative solves the same core user problem, the overlap degree (full, partial, none), "
        "and provide verbatim supporting quotes as strict JSON."
    )

    try:
        text, req_m, srv_m = client.complete(
            user_prompt, prompt_record.text, repo_id=candidate_repo_id, step="alternative"
        )
    except Exception as exc:
        if isinstance(exc, RelevanceLLMError) and "refusing to score with a small local model" in str(exc):
            raise
        return None, {
            "requested_model": getattr(client, "model", "meterless/auto"),
            "served_model": "",
            "fallback": True,
            "error": str(exc),
            "dropped": True,
        }

    obj = extract_json_object(text)
    if obj is None:
        return None, {
            "requested_model": req_m,
            "served_model": srv_m,
            "fallback": True,
            "error": "LLM output invalid JSON",
            "dropped": True,
        }

    same_problem = bool(obj.get("same_problem"))
    overlap = str(obj.get("overlap") or "none").strip().lower()
    reason = str(obj.get("reason") or "").strip()
    quotes = obj.get("quotes") or []

    # Verify quotes verbatim
    if same_problem:
        if not isinstance(quotes, list) or len(quotes) == 0:
            if trace is not None:
                trace.event(
                    agent_name="orchestrator",
                    action="UNVERIFIED_ALTERNATIVE_QUOTE",
                    input_summary={"alternative": alt_id},
                    output_summary={"error": "no quotes provided for same_problem alternative"},
                    status="error",
                )
            return None, {"error": "unverified LLM quote: no quotes provided", "dropped": True}

        for q in quotes:
            q_str = str(q or "").strip()
            if not q_str or not quote_in_text(q_str, result_text):
                if trace is not None:
                    trace.event(
                        agent_name="orchestrator",
                        action="UNVERIFIED_ALTERNATIVE_QUOTE",
                        input_summary={"alternative": alt_id, "quote": q_str},
                        output_summary={"error": f"unverified quote '{q_str}' not found in alternative text"},
                        status="error",
                    )
                return None, {"error": f"unverified LLM quote: '{q_str}' not found in alternative text", "dropped": True}

    if not same_problem:
        return None, {
            "same_problem": False,
            "overlap": overlap,
            "reason": reason,
            "requested_model": req_m,
            "served_model": srv_m,
        }

    alt_row = {
        "repo_id": candidate_repo_id,
        "alternative_id": alt_id,
        "url": str(search_result.get("url") or f"https://github.com/{alt_id}"),
        "licence_spdx": str(search_result.get("licence_spdx") or ""),
        "stars": search_result.get("stars", 0),
        "pushed_at": str(search_result.get("pushed_at") or ""),
        "excerpt": result_text[:1000],
        "candidate_excerpt": str(problem_statement.get("problem") or ""),
        "required_terms": "",
        "same_problem": True,
        "overlap": overlap,
        "reason": reason,
        "quotes": [str(q).strip() for q in quotes if str(q).strip()],
    }
    return alt_row, {
        "same_problem": True,
        "overlap": overlap,
        "reason": reason,
        "requested_model": req_m,
        "served_model": srv_m,
    }


BATCH_INSTRUCTION = (
    "\n\nBATCH MODE: you will receive several numbered alternatives. Judge EACH one independently "
    "with the rules above. Output strict JSON only, in the form "
    '{"results": [{"index": <number>, "same_problem": true|false, "overlap": "full"|"partial"|"none", '
    '"reason": "...", "quotes": ["verbatim quote from THAT alternative\'s text"]}]} '
    "with exactly one entry per alternative index."
)


BATCH_CALL_ATTEMPTS = 2


def judge_alternatives_batch(
    problem_statement: dict[str, Any],
    candidate_repo_id: str,
    search_results: list[dict[str, Any]],
    client: RelevanceClient | None = None,
    trace: Any | None = None,
    max_chars_per_item: int = 900,
) -> list[dict[str, Any]] | None:
    """Judge all alternatives in ONE LLM call (step="alternative_batch").

    Same deterministic rules as judge_alternative_relevance: a same_problem verdict is kept
    only if every quote is found verbatim in that alternative's own text. Returns the list
    of verified same-problem rows, or None when the batch answer is unusable (exception,
    invalid JSON, missing indexes) so the caller can fall back to one call per alternative.
    """
    if client is None:
        client = RelevanceClient()
    items: list[tuple[int, dict[str, Any], str]] = []
    for i, res in enumerate(search_results):
        text = f"{str(res.get('description') or '').strip()}\n{str(res.get('readme') or '').strip()}".strip()
        if text:
            items.append((i, res, text))
    if not items:
        return []

    prompt_record = load_prompt("relevance_alternative")
    blocks = []
    for i, res, text in items:
        blocks.append(
            f"[{i}] Alternative: {res.get('alternative_id', '')}\nURL: {res.get('url', '')}\n"
            f"Description and README Excerpt:\n```\n{text[:max_chars_per_item]}\n```"
        )
    user_prompt = (
        f"Candidate Problem: {problem_statement.get('problem', '')}\n"
        f"Target User: {problem_statement.get('target_user', '')}\n"
        f"Category: {problem_statement.get('category', '')}\n\n"
        + "\n\n".join(blocks)
        + "\n\nFor every alternative index, judge whether it solves the same core user problem, the overlap "
        "degree (full, partial, none), and give verbatim supporting quotes. Output strict JSON."
    )
    wanted = {i for i, _, _ in items}

    def _batch_ok(o: dict[str, Any]) -> bool:
        rs = o.get("results")
        if not isinstance(rs, list):
            return False
        got = set()
        for e in rs:
            if isinstance(e, dict):
                try:
                    got.add(int(e.get("index")))
                except (TypeError, ValueError):
                    pass
        return wanted <= got

    def _fallback(reason: str) -> None:
        if trace is not None:
            trace.event(
                agent_name="orchestrator",
                action="ALTERNATIVE_BATCH_FALLBACK",
                input_summary={"repo_id": candidate_repo_id, "alternatives": len(items)},
                output_summary={"reason": reason},
                status="error",
            )

    obj = None
    for attempt in range(BATCH_CALL_ATTEMPTS):
        try:
            obj, text, req_m, srv_m = _complete_json(
                client, user_prompt, prompt_record.text + BATCH_INSTRUCTION, candidate_repo_id, "alternative_batch",
                validate=_batch_ok,
            )
            break
        except Exception as exc:
            if isinstance(exc, RelevanceLLMError) and "refusing to score with a small local model" in str(exc):
                raise
            if attempt + 1 < BATCH_CALL_ATTEMPTS:
                # A transient provider error (for example HTTP 502) is retried once before the
                # much slower one-call-per-alternative fallback.
                if trace is not None:
                    trace.event(
                        agent_name="orchestrator",
                        action="ALTERNATIVE_BATCH_RETRY",
                        input_summary={"repo_id": candidate_repo_id, "attempt": attempt + 1},
                        output_summary={"reason": str(exc)[:160]},
                        status="error",
                    )
                continue
            _fallback(f"batch call failed after {BATCH_CALL_ATTEMPTS} attempts: {str(exc)[:160]}")
            return None
    if obj is None:
        _fallback("batch answer unusable after retries (invalid JSON or missing indexes)")
        return None
    by_index: dict[int, dict[str, Any]] = {}
    for entry in obj["results"]:
        if isinstance(entry, dict):
            try:
                by_index[int(entry.get("index"))] = entry
            except (TypeError, ValueError):
                continue
    if any(i not in by_index for i, _, _ in items):
        return None

    verified: list[dict[str, Any]] = []
    for i, res, rtext in items:
        entry = by_index[i]
        if not bool(entry.get("same_problem")):
            continue
        alt_id = str(res.get("alternative_id") or "").strip()
        quotes = entry.get("quotes") or []
        bad = None
        if not isinstance(quotes, list) or not quotes:
            bad = "no quotes provided for same_problem alternative"
        else:
            for q in quotes:
                q_str = str(q or "").strip()
                if not q_str or not quote_in_text(q_str, rtext):
                    bad = f"unverified quote '{q_str}' not found in alternative text"
                    break
        if bad:
            if trace is not None:
                trace.event(
                    agent_name="orchestrator",
                    action="UNVERIFIED_ALTERNATIVE_QUOTE",
                    input_summary={"alternative": alt_id, "mode": "batch"},
                    output_summary={"error": bad},
                    status="error",
                )
            continue
        verified.append({
            "repo_id": candidate_repo_id,
            "alternative_id": alt_id,
            "url": str(res.get("url") or f"https://github.com/{alt_id}"),
            "licence_spdx": str(res.get("licence_spdx") or ""),
            "stars": res.get("stars", 0),
            "pushed_at": str(res.get("pushed_at") or ""),
            "excerpt": rtext[:1000],
            "candidate_excerpt": str(problem_statement.get("problem") or ""),
            "required_terms": "",
            "same_problem": True,
            "overlap": str(entry.get("overlap") or "none").strip().lower(),
            "reason": str(entry.get("reason") or "").strip(),
            "quotes": [str(q).strip() for q in quotes if str(q).strip()],
            "source": str(res.get("source") or ""),
        })
    return verified


def filter_relevant_alternatives(
    problem_statement: dict[str, Any],
    candidate_repo_id: str,
    search_results: list[dict[str, Any]],
    client: RelevanceClient | None = None,
    trace: Any | None = None,
) -> list[dict[str, Any]]:
    """Evaluate candidate search results and return only those with same_problem=true and verified quotes.

    Tries one batched LLM call first; falls back to one call per alternative when the batch
    answer is unusable.
    """
    if len(search_results) > 1:
        batch = judge_alternatives_batch(problem_statement, candidate_repo_id, search_results, client=client, trace=trace)
        if batch is not None:
            return batch
    verified_alts = []
    for res in search_results:
        alt_row, meta = judge_alternative_relevance(
            problem_statement=problem_statement,
            candidate_repo_id=candidate_repo_id,
            search_result=res,
            client=client,
            trace=trace,
        )
        if alt_row is not None and alt_row.get("same_problem"):
            verified_alts.append(alt_row)
    return verified_alts
