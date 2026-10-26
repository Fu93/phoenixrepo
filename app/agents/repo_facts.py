"""Cached host-API facts for an alternative or a candidate.

GitHub, Codeberg and GitLab only. The token is read from the environment when the
caller does not pass one. It is sent as a header and is never written to the cache
or to a log line.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx


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
        if host == "github" and self.token:
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
        try:
            with httpx.Client(transport=self.transport, timeout=self.timeout) as client:
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


def _api_url(text: str) -> tuple[str | None, str | None]:
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
