"""Cached host-API facts for an alternative or a candidate.

GitHub, Codeberg and GitLab only. The token is read from the environment when the
caller does not pass one. It is sent as a header and is never written to the cache
or to a log line.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from app.agents import gate1_flag


def _token(explicit: str | None) -> str:
    if explicit is not None:
        return explicit
    return os.environ.get("GITHUB_TOKEN") or ""


class RepoFactsClient:
    def __init__(
        self,
        token: str | None = None,
        cache_dir: Path | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 8.0,
    ) -> None:
        self.token = _token(token)
        self.cache_dir = cache_dir
        self.transport = transport
        self.timeout = timeout
        if cache_dir is not None:
            cache_dir.mkdir(parents=True, exist_ok=True)

    def _headers(self, host: str) -> dict[str, str]:
        headers = {"User-Agent": "PhoenixRepo-facts", "Accept": "application/json"}
        from app.github_auth import token_usable

        if host == "github" and token_usable(self.token):
            headers["Authorization"] = f"Bearer {self.token}"
            headers["Accept"] = "application/vnd.github+json"
        return headers

    def _cache_path(self, url: str) -> Path | None:
        if self.cache_dir is None:
            return None
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{key}.json"

    def _get(self, url: str, host: str) -> dict[str, Any] | None:
        cached = self._cache_path(url)
        if cached is not None and cached.exists():
            try:
                return json.loads(cached.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        from app.github_auth import mark_unauthorized

        try:
            with httpx.Client(transport=self.transport, timeout=self.timeout) as client:
                headers = self._headers(host)
                resp = client.get(url, headers=headers)
                if resp.status_code == 401 and "Authorization" in headers:
                    mark_unauthorized(self.token)
                    resp = client.get(url, headers=self._headers(host))
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            return None
        try:
            data = resp.json()
        except json.JSONDecodeError:
            return None
        if cached is not None and isinstance(data, (dict, list)):
            cached.write_text(json.dumps(data), encoding="utf-8")
        return data if isinstance(data, dict) else None

    def fetch_alternative(self, row: dict[str, Any]) -> dict[str, Any]:
        """Copy of ``row`` with stars, archived, pushed_at and licence filled when the host answers."""
        url = str(row.get("url") or "")
        alt = str(row.get("alternative_id") or "")
        host, api = _api_url(url or alt)
        merged = dict(row)
        if host is None or api is None:
            return merged
        if not gate1_flag.enabled():
            # main behaviour: a row that already has facts is kept; host facts only fill blanks.
            if merged.get("stars") not in (None, "") and merged.get("pushed_at") and "archived" in merged and merged.get("licence_spdx"):
                merged["fetch_status"] = merged.get("fetch_status") or "row"
                return merged
            data = self._get(api, host)
            if not data:
                merged["fetch_status"] = "source_error"
                return merged
            facts = _normalise(host, data)
            for key, value in facts.items():
                if value is not None and merged.get(key) in (None, ""):
                    merged[key] = value
            merged["fetch_status"] = "ok"
            merged["host"] = host
            return merged
        web_placeholder = (str(row.get("source") or "").casefold() == "web" or alt.startswith("web:")) and row.get("fetch_status") != "ok"
        known_licence = str(merged.get("licence_spdx") or "").strip().casefold() not in {"", "unknown", "none", "null", "n/a", "noassertion"}
        if not web_placeholder and merged.get("stars") not in (None, "") and merged.get("pushed_at") and "archived" in merged and known_licence:
            merged["fetch_status"] = merged.get("fetch_status") or "row"
            return merged
        data = self._get(api, host)
        if not data:
            merged["fetch_status"] = "source_error"
            return merged
        return with_host_facts(merged, host, data, api, replace_placeholders=web_placeholder)

    def fetch_rescue(self, repo_id: str, *, default_branch: str = "") -> dict[str, Any]:
        """Repo object plus CI, external breakage issues, and open bot PRs."""
        owner = repo_id.split("/")[0] if "/" in repo_id else ""
        repo_url = f"https://api.github.com/repos/{repo_id}"
        repo = self._get(repo_url, "github")
        if not repo:
            return {"fetch_status": "source_error", "source": "github_api"}
        branch = default_branch or str(repo.get("default_branch") or "main")
        workflows = self._get(f"https://api.github.com/repos/{repo_id}/actions/workflows?per_page=1", "github") or {}
        runs = self._get(
            f"https://api.github.com/repos/{repo_id}/actions/runs?per_page=1&branch={quote(branch)}",
            "github",
        ) or {}
        issues = self._get(
            "https://api.github.com/search/issues?q="
            + quote(f"repo:{repo_id} is:issue is:open -author:{owner}")
            + "&per_page=20",
            "github",
        ) or {}
        dependabot = self._get(
            "https://api.github.com/search/issues?q=" + quote(f"repo:{repo_id} is:pr is:open author:app/dependabot"),
            "github",
        ) or {}
        renovate = self._get(
            "https://api.github.com/search/issues?q=" + quote(f"repo:{repo_id} is:pr is:open author:app/renovate"),
            "github",
        ) or {}
        from app.agents.rescue import count_external_breakage

        workflow_count = int((workflows.get("total_count") or 0))
        run_items = runs.get("workflow_runs") or []
        run_count = int(runs.get("total_count") or 0)
        if workflow_count <= 0:
            ci_state = "no_ci"
            ci_date = None
        elif run_count <= 0 or not run_items:
            ci_state = "ci_configured_zero_runs"
            ci_date = None
        else:
            ci_state = str(run_items[0].get("conclusion") or "unknown")
            ci_date = run_items[0].get("created_at")
        licence = ((repo.get("license") or {}) if isinstance(repo.get("license"), dict) else {}).get("spdx_id")
        return {
            "fetch_status": "ok",
            "source": "github_api",
            "stars": repo.get("stargazers_count"),
            "forks": repo.get("forks_count"),
            "open_issues": repo.get("open_issues_count"),
            "pushed_at": repo.get("pushed_at"),
            "archived": repo.get("archived"),
            "licence_spdx": licence,
            "external_breakage": count_external_breakage(list(issues.get("items") or []), owner),
            "ci_state": ci_state,
            "ci_date": ci_date,
            "dependabot_open": int(dependabot.get("total_count") or 0),
            "renovate_open": int(renovate.get("total_count") or 0),
        }


def with_host_facts(row: dict[str, Any], host: str, data: dict[str, Any], api: str, *, replace_placeholders: bool = False) -> dict[str, Any]:
    """Merge only host metadata; source quotes and semantic judgments stay intact."""
    merged = dict(row)
    known_licence = str(merged.get("licence_spdx") or "").strip().casefold() not in {"", "unknown", "none", "null", "n/a", "noassertion"}
    for key, value in _normalise(host, data).items():
        if value is not None and (replace_placeholders or merged.get(key) in (None, "") or (key == "licence_spdx" and not known_licence)):
            merged[key] = value
    merged["fetch_status"] = "ok"
    merged["host"] = host
    merged["facts_source_url"] = api
    merged["facts_sha256"] = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    if host == "github":
        if type(data.get("fork")) is bool:
            merged["fork"] = data["fork"]
        for key, source in (("parent", "parent"), ("source_repo", "source")):
            if isinstance(data.get(source), dict):
                merged[key] = {"full_name": str(data[source].get("full_name") or "")}
    return merged


def _api_url(text: str) -> tuple[str | None, str | None]:
    """Host and API URL for a repository reference. Strict parsing only with the Gate 1 repair on."""
    if gate1_flag.enabled():
        return _api_url_strict(text)
    return _api_url_main(text)


def _api_url_main(text: str) -> tuple[str | None, str | None]:
    raw = text.strip()
    if raw.startswith("web:"):
        raw = raw[4:]
    lower = raw.lower()
    if "codeberg.org/" in lower:
        path = raw.split("codeberg.org/", 1)[1].strip("/")
        parts = [p for p in path.split("/") if p][:2]
        if len(parts) == 2:
            return "codeberg", f"https://codeberg.org/api/v1/repos/{parts[0]}/{parts[1]}"
        return "codeberg", None
    if "gitlab.com/" in lower:
        path = raw.split("gitlab.com/", 1)[1].strip("/")
        parts = [p for p in path.split("/") if p][:2]
        if len(parts) == 2:
            return "gitlab", "https://gitlab.com/api/v4/projects/" + quote(f"{parts[0]}/{parts[1]}", safe="")
        return "gitlab", None
    if "github.com/" in lower:
        path = raw.split("github.com/", 1)[1].strip("/")
        parts = [p for p in path.split("/") if p][:2]
        if len(parts) == 2:
            return "github", f"https://api.github.com/repos/{parts[0]}/{parts[1]}"
        return "github", None
    if raw.count("/") == 1 and not raw.startswith("http"):
        return "github", f"https://api.github.com/repos/{raw}"
    return None, None


def _api_url_strict(text: str) -> tuple[str | None, str | None]:
    raw = text.strip()
    if raw.startswith("web:"):
        raw = raw[4:]
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", raw):
        raw = "https://github.com/" + raw
    try:
        parsed = urlparse(raw)
    except ValueError:
        return None, None
    hosts = {"github.com": "github", "codeberg.org": "codeberg", "gitlab.com": "gitlab"}
    host = hosts.get(parsed.netloc.casefold())
    if parsed.scheme not in {"http", "https"} or host is None or parsed.query or parsed.fragment:
        return None, None
    parts = parsed.path.strip("/").split("/")
    if len(parts) < 2 or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", p) or p in {".", ".."} for p in parts):
        return None, None
    owner, repo = parts[:2]
    repo = repo.removesuffix(".git")
    if not repo:
        return None, None
    if host == "github":
        return host, f"https://api.github.com/repos/{owner}/{repo}"
    if host == "codeberg":
        return host, f"https://codeberg.org/api/v1/repos/{owner}/{repo}"
    if host == "gitlab":
        # Nested namespaces belong to the project identity. Never fetch a
        # different group/project by silently discarding subgroup segments.
        project_parts = parts[:parts.index("-")] if "-" in parts else parts
        if len(project_parts) < 2:
            return None, None
        project_parts[-1] = project_parts[-1].removesuffix(".git")
        return host, "https://gitlab.com/api/v4/projects/" + quote("/".join(project_parts), safe="")
    return None, None


def _normalise(host: str, data: dict[str, Any]) -> dict[str, Any]:
    if host == "github":
        licence = data.get("license") if isinstance(data.get("license"), dict) else {}
        return {
            "stars": data.get("stargazers_count"),
            "archived": data.get("archived"),
            "pushed_at": data.get("pushed_at"),
            "licence_spdx": (licence or {}).get("spdx_id"),
            "forks": data.get("forks_count"),
        }
    if host == "codeberg":
        return {
            "stars": data.get("stars_count"),
            "archived": data.get("archived"),
            "pushed_at": data.get("updated_at") or data.get("pushed_at"),
            "licence_spdx": (data.get("license") or {}).get("spdx_id") if isinstance(data.get("license"), dict) else data.get("license"),
            "forks": data.get("forks_count"),
        }
    # GitLab
    return {
        "stars": data.get("star_count"),
        "archived": data.get("archived"),
        "pushed_at": data.get("last_activity_at"),
        "licence_spdx": None,
        "forks": data.get("forks_count"),
    }
