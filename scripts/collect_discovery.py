"""Normalise the committed discovery snapshot into Zetaris CSVs.

Also supports live collection of abandoned idea repositories from GitHub
search API into dated snapshots (e.g. data/discovery/2026-10-22/).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    csv.field_size_limit(2147483647)
except OverflowError:
    csv.field_size_limit(sys.maxsize)

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agents.market_filter import (  # noqa: E402
    days_between,
    decide_discovery,
    is_boilerplate,
)
from app.sql_catalog import DEMAND_COLUMNS, DISCOVERY_COLUMNS, PRIOR_ART_COLUMNS  # noqa: E402

RAW = ROOT / "data" / "discovery" / "raw" / "snapshot.json"
REAL = ROOT / "data" / "real"
SAMPLE = ROOT / "data" / "sample"

NON_PRODUCT_PATTERNS = (
    "tutorial",
    "course",
    "homework",
    "assignment",
    "exercise",
    "dotfile",
    "awesome",
    "portfolio",
    "resume",
    "curriculum-vitae",
    "starter",
    "boilerplate",
    "playground",
    "learning",
    "bootcamp",
    "leetcode",
    "hackerrank",
    "codewars",
    "interview",
    "cheatsheet",
    "roadmap",
    "academy",
    "official code",
    "official implementation",
    "paper",
    "[cvpr",
    "[iclr",
    "[neurips",
    "[eccv",
)

TARGET_LANGUAGES = frozenset({"TypeScript", "JavaScript", "Python", "Go", "Rust"})

PROBLEM_KEYWORDS = (
    "sync notes",
    "sync bookmarks",
    "calendar sync",
    "contacts sync",
    "file sync",
    "self-hosted",
    "cli tool",
    "dashboard",
    "automation",
    "markdown",
    "notion",
    "obsidian",
    "invoice",
    "habit",
    "budget",
    "scraper",
)

CONFIG_EXTENSIONS = frozenset(
    {
        ".toml",
        ".lock",
        ".json",
        ".yaml",
        ".yml",
        ".xml",
        ".ini",
        ".cfg",
        ".env",
        ".md",
        ".txt",
    }
)


def get_github_token() -> str:
    """Safely obtain GitHub token from env or gh CLI without ever printing it."""
    token = os.environ.get("GITHUB_TOKEN") or ""
    if not token:
        try:
            import subprocess

            out = subprocess.getoutput("gh auth token").strip()
            if out and not out.startswith("error") and "not logged in" not in out.lower():
                token = out
        except Exception:
            token = ""
    return token


def is_non_product(name: str, description: str, readme: str = "") -> bool:
    """Drop obvious non-products: tutorials, homework, dotfiles, awesome-lists, templates."""
    desc = (description or "").strip()
    if not desc or len(desc) < 10:
        return True
    # Drop academic paper release repos without product scope
    if re.search(r"^\[(?:iclr|cvpr|neurips|eccv|icml|acl|emnlp|aaai)\]", desc, re.IGNORECASE):
        return True

    text = f"{name} {desc} {readme[:300]}".lower()
    for pat in NON_PRODUCT_PATTERNS:
        if pat in text:
            return True
    return False


def check_discovery_exclusion(repo: dict[str, Any], readme_text: str = "") -> str | None:
    """Check if repository should be excluded from abandoned idea discovery.

    Returns exclusion reason if excluded, or None if valid candidate.
    """
    fn = str(repo.get("full_name") or "")
    desc = str(repo.get("description") or "").strip()
    readme_sample = readme_text[:2500].lower()
    text = f"{fn} {desc} {readme_sample}".lower()

    # 1. Research model code or paper drops
    research_terms = (
        "paper",
        "arxiv",
        "cvpr",
        "iclr",
        "neurips",
        "eccv",
        "icml",
        "emnlp",
        "acl",
        "aaai",
        "model weights",
        "checkpoint",
        "checkpoints",
        "pretrained",
        "pre-trained",
        "talking head",
        "lip sync",
        "lip-sync",
        "stable diffusion",
        "sadtalker",
        "wav2lip",
        "latentsync",
    )
    for term in research_terms:
        if term in text:
            return f"research model code or paper (mentions '{term}')"

    # 2. Organisation owner with a website or maintained product
    owner = repo.get("owner") or {}
    is_org = owner.get("type") == "Organization"
    homepage = str(repo.get("homepage") or "").strip()
    owner_blog = str(owner.get("blog") or "").strip()
    org_login = str(owner.get("login") or "").lower()
    known_corp_orgs = {
        "figma",
        "cloudflare",
        "vercel-labs",
        "vercel",
        "getumbrel",
        "bytedance",
        "orgexyz",
        "browserable",
        "caching-tools",
        "vinceanalytics",
        "aspen-cloud",
        "tractr",
        "structuredllm",
        "google",
        "facebook",
        "meta",
        "microsoft",
        "apple",
    }
    if is_org and (homepage or owner_blog or repo.get("has_pages") or org_login in known_corp_orgs):
        return f"organisation owner with a website/product ({owner.get('login')}, homepage={homepage or owner_blog})"

    # 3. Maintained releases / stall window (last push within 180 days)
    days = days_between(repo.get("pushed_at"), "2026-10-22T23:00:00+00:00")
    if days is not None and days < 180:
        return f"maintained product: pushed {days} days before collection (< 180d stall window)"

    # 4. Example repos
    if any(ex in fn.lower() for ex in ("-example", "_example", "/example")):
        return f"example or sample repository ('{fn}')"

    # 5. Non-product patterns (tutorials, homework, templates, etc.)
    for pat in NON_PRODUCT_PATTERNS:
        if pat in text:
            return f"non-product pattern ('{pat}')"

    if not desc or len(desc) < 10:
        return "empty or insufficient description (<10 chars)"

    return None


def is_real_source_path(path: str) -> bool:
    """Check if path is a genuine source code path beyond templates and configs."""
    if is_boilerplate(path):
        return False
    lower = path.lower()
    for ext in CONFIG_EXTENSIONS:
        if lower.endswith(ext):
            return False
    return True


def extract_feature_capabilities(repo_id: str, readme_text: str, desc: str) -> list[str]:
    """Extract feature capabilities strictly from README feature statements (verbs/features).

    NEVER includes repo name or owner tokens. Drops any term appearing in the repo name.
    """
    owner, repo_name = repo_id.split("/") if "/" in repo_id else ("", repo_id)
    name_tokens = set(re.findall(r"[a-z0-9]+", (owner + " " + repo_name).lower()))

    # Find feature lines in README
    feature_lines = []
    for line in readme_text.splitlines():
        line_str = line.strip()
        if line_str.startswith(("-", "*", "•", "1.", "2.", "3.", "4.")):
            clean = re.sub(r"^[-*•\d\.\s\[\]xX]+", "", line_str).strip()
            clean = re.sub(r"^\*\*[^*]+\*\*[:\s]*", "", clean).strip()
            if len(clean) >= 10:
                feature_lines.append(clean)
    if not feature_lines:
        feature_lines = [s.strip() for s in desc.split(".") if len(s.strip()) >= 10]

    stopwords = {
        "a",
        "an",
        "the",
        "and",
        "or",
        "for",
        "with",
        "in",
        "to",
        "of",
        "is",
        "tool",
        "app",
        "application",
        "using",
        "built",
        "simple",
        "easy",
        "fast",
        "lightweight",
        "modern",
        "based",
        "from",
        "into",
        "your",
        "this",
        "that",
        "these",
        "those",
        "have",
        "been",
        "will",
        "more",
        "all",
        "out",
        "also",
        "only",
        "can",
        "new",
        "get",
        "use",
    }

    capabilities: list[str] = []
    # 2-word verb-noun or noun-noun phrases from feature statements
    for line in feature_lines:
        words = re.findall(r"\b[a-zA-Z]{3,15}\b", line)
        for i in range(len(words) - 1):
            w1 = words[i].lower()
            w2 = words[i + 1].lower()
            if w1 in stopwords or w2 in stopwords:
                continue
            # Drop any word that matches repo name or owner tokens!
            if any(tok in w1 or w1 in tok for tok in name_tokens if len(tok) >= 3):
                continue
            if any(tok in w2 or w2 in tok for tok in name_tokens if len(tok) >= 3):
                continue
            phrase = f"{w1} {w2}"
            if phrase not in capabilities and phrase in (desc + "\n" + readme_text).lower():
                capabilities.append(phrase)

    # Individual meaningful feature words if < 2 phrases
    if len(capabilities) < 2:
        for line in feature_lines:
            words = re.findall(r"\b[a-zA-Z]{4,15}\b", line)
            for w in words:
                wl = w.lower()
                if wl in stopwords:
                    continue
                if any(tok in wl or wl in tok for tok in name_tokens if len(tok) >= 3):
                    continue
                if (
                    wl not in capabilities
                    and not any(wl in c for c in capabilities)
                    and wl in (desc + "\n" + readme_text).lower()
                ):
                    capabilities.append(wl)
                    if len(capabilities) >= 2:
                        break

    return capabilities


def extract_multi_term_quote(text: str, terms: list[str], max_len: int = 200) -> str:
    """Extract a snippet of <= max_len chars that contains all given terms."""
    if not text or not terms:
        return ""
    text_clean = " ".join(text.split())
    lower = text_clean.lower()

    # Check all terms exist in text
    indices = [lower.find(t.lower()) for t in terms]
    if any(idx == -1 for idx in indices):
        return ""

    min_idx = min(indices)
    max_end = max(idx + len(t) for idx, t in zip(indices, terms))
    span_len = max_end - min_idx

    if span_len > max_len:
        return ""

    padding = (max_len - span_len) // 2
    start = max(0, min_idx - padding)
    end = min(len(text_clean), max_end + padding)
    snippet = text_clean[start:end].strip()

    snippet_lower = snippet.lower()
    if all(t.lower() in snippet_lower for t in terms):
        return snippet
    return ""


def search_alternatives_for_candidate(
    client: httpx.Client,
    candidate_id: str,
    terms: list[str],
    headers: dict[str, str],
) -> list[dict[str, Any]]:
    """Search for maintained or prior-art alternatives via GitHub, npm, and PyPI."""
    alts = []
    seen = set()

    # 1. GitHub search with query fallbacks
    queries = []
    if len(terms) >= 2:
        queries.append(f'"{terms[0]}" "{terms[1]}" stars:>10')
        queries.append(f"{terms[0]} {terms[1]} stars:>10")
    if terms:
        queries.append(f'"{terms[0]}" stars:>10')
        queries.append(f"{terms[0]} stars:>10")

    for q in queries:
        resp = request_with_retry(
            client,
            "https://api.github.com/search/repositories",
            headers=headers,
            params={"q": q, "per_page": 3},
        )
        if resp and resp.status_code == 200:
            for it in resp.json().get("items", []):
                fn = it.get("full_name") or ""
                if fn and fn.lower() != candidate_id.lower() and fn not in seen:
                    alts.append(
                        {
                            "id": fn,
                            "url": it.get("html_url") or f"https://github.com/{fn}",
                            "licence_spdx": str((it.get("license") or {}).get("spdx_id") or ""),
                            "stars": it.get("stargazers_count", 0),
                            "pushed_at": it.get("pushed_at") or "",
                            "excerpt": str(it.get("description") or ""),
                            "repo_meta": it,
                        }
                    )
                    seen.add(fn)
        if len(alts) >= 2:
            break

    # 2. npm search
    if len(alts) < 2 and terms:
        npm_q = terms[0].replace(" ", "-").lower()
        npm_resp = request_with_retry(
            client,
            f"https://registry.npmjs.org/-/v1/search?text={npm_q}&size=2",
            headers={"Accept": "application/json"},
        )
        if npm_resp and npm_resp.status_code == 200:
            for obj in npm_resp.json().get("objects", []):
                pkg = obj.get("package", {})
                p_name = pkg.get("name") or ""
                p_id = f"npm:{p_name}"
                if p_name and p_id not in seen:
                    alts.append(
                        {
                            "id": p_id,
                            "url": pkg.get("links", {}).get("npm") or f"https://www.npmjs.com/package/{p_name}",
                            "licence_spdx": str(pkg.get("license") or ""),
                            "stars": 0,
                            "pushed_at": pkg.get("date") or "",
                            "excerpt": str(pkg.get("description") or ""),
                        }
                    )
                    seen.add(p_id)

    # 3. PyPI search
    if len(alts) < 2 and terms:
        pypi_name = terms[0].replace(" ", "-").lower()
        pypi_resp = request_with_retry(
            client,
            f"https://pypi.org/pypi/{pypi_name}/json",
            headers={"Accept": "application/json"},
        )
        if pypi_resp and pypi_resp.status_code == 200:
            info = pypi_resp.json().get("info", {})
            p_name = info.get("name") or pypi_name
            p_id = f"pypi:{p_name}"
            if p_id not in seen:
                alts.append(
                    {
                        "id": p_id,
                        "url": info.get("package_url") or f"https://pypi.org/project/{p_name}/",
                        "licence_spdx": str(info.get("license") or ""),
                        "stars": 0,
                        "pushed_at": "",
                        "excerpt": str(info.get("summary") or ""),
                    }
                )
                seen.add(p_id)

    return alts


def search_demand_for_candidate(
    client: httpx.Client,
    candidate_id: str,
    terms: list[str],
    headers: dict[str, str],
) -> list[dict[str, Any]]:
    """Search for external demand signals mentioning the candidate's problem with >= 2 core terms."""
    if len(terms) < 2:
        return []
    queries = [
        f'"{terms[0]}" "{terms[1]}" is:issue comments:>1',
        f"{terms[0]} {terms[1]} is:issue comments:>1",
    ]
    demand = []
    seen_urls = set()
    for q in queries:
        resp = request_with_retry(
            client,
            "https://api.github.com/search/issues",
            headers=headers,
            params={"q": q, "per_page": 2},
        )
        if resp and resp.status_code == 200:
            for iss in resp.json().get("items", []):
                url = iss.get("html_url") or ""
                if not url or url in seen_urls:
                    continue
                if candidate_id.lower() in url.lower():
                    continue
                title = str(iss.get("title") or "")
                body = str(iss.get("body") or "")
                full_text = f"{title}\n{body}"
                # Must not come from a fork of an unrelated project
                if "forked from" in full_text.lower() or "fork of" in full_text.lower():
                    continue
                # Must contain both terms
                if terms[0].lower() in full_text.lower() and terms[1].lower() in full_text.lower():
                    quote = extract_multi_term_quote(full_text, terms[:2], max_len=200)
                    if quote and terms[0].lower() in quote.lower() and terms[1].lower() in quote.lower():
                        demand.append(
                            {
                                "repo_id": candidate_id,
                                "signal_id": f"issue-{iss.get('id')}",
                                "kind": "github_issue",
                                "url": url,
                                "excerpt": quote,
                                "comments": iss.get("comments", 0),
                                "reactions": (iss.get("reactions") or {}).get("total_count", 0),
                                "stars": "",
                                "pushed_at": "",
                                "query": q,
                            }
                        )
                        seen_urls.add(url)
        if demand:
            break
    return demand


def extract_candidate_features(
    repo: dict[str, Any],
    readme_text: str,
    tree_paths: list[str],
    collected_at: str,
    cohort: str = "discovery",
) -> dict[str, Any] | None:
    """Extract candidate fields ensuring problem/user/scope excerpts are exact substrings of idea."""
    desc = str(repo.get("description") or "").strip()
    if not desc:
        return None
    readme_excerpt = readme_text[:2000].strip()
    idea = desc + "\n" + readme_excerpt

    # 1. problem_excerpt = desc (guaranteed exact substring)
    problem_excerpt = desc

    # 2. user_excerpt: look for target user
    user_excerpt = ""
    user_patterns = [
        r"(?:for|designed for|built for|helps|allowing)\s+([a-zA-Z0-9\s,-]{3,35})(?:\.|\n|,|\sto\b)",
        r"\b(developers|freelancers|businesses|teams|engineers|users|creators|students)\b",
    ]
    for pat in user_patterns:
        m = re.search(pat, idea, re.IGNORECASE)
        if m:
            matched = m.group(0).strip()
            idx = idea.lower().find(matched.lower())
            if idx != -1:
                user_excerpt = idea[idx : idx + len(matched)]
                break
    if not user_excerpt:
        user_excerpt = desc[: min(40, len(desc))]

    # 3. scope_excerpt: look for feature or capability bullet
    scope_excerpt = ""
    lines = [line.strip() for line in readme_excerpt.split("\n") if line.strip()]
    for line in lines:
        if line.startswith(("-", "*", "•", "1.", "2.", "3.", "4.")):
            clean = re.sub(r"^[-*•\d\.\s\[\]x]+", "", line).strip()
            if len(clean) >= 15:
                idx = idea.find(clean)
                if idx != -1:
                    scope_excerpt = idea[idx : idx + len(clean)]
                    break
    if not scope_excerpt:
        for line in lines[1:]:
            if len(line) >= 15 and not line.startswith("#"):
                idx = idea.find(line)
                if idx != -1:
                    scope_excerpt = idea[idx : idx + len(line)]
                    break
    if not scope_excerpt:
        scope_excerpt = desc

    real_paths = [p for p in tree_paths if is_real_source_path(p)]
    if len(real_paths) < 2:
        return None

    terms = extract_feature_capabilities(repo["full_name"], readme_text, desc)
    if len(terms) < 2:
        return None

    days = days_between(repo.get("pushed_at"), collected_at)
    if days is None:
        return None

    return {
        "repo_id": repo["full_name"],
        "readme_excerpt": readme_excerpt,
        "problem_excerpt": problem_excerpt,
        "user_excerpt": user_excerpt,
        "scope_excerpt": scope_excerpt,
        "code_paths": real_paths[:6],
        "core_terms": terms,
        "cohort": cohort,
    }


def request_with_retry(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    params: dict[str, Any] | None = None,
    max_retries: int = 3,
) -> httpx.Response | None:
    """Perform HTTP GET with rate limit handling (respects Retry-After and X-RateLimit-Reset)."""
    for attempt in range(max_retries):
        try:
            resp = client.get(url, headers=headers, params=params)
            if resp.status_code in {403, 429}:
                retry_after = resp.headers.get("Retry-After")
                reset_val = resp.headers.get("X-RateLimit-Reset")
                sleep_s = 2.0
                if retry_after:
                    try:
                        sleep_s = float(retry_after)
                    except ValueError:
                        sleep_s = 5.0
                elif reset_val:
                    try:
                        diff = int(reset_val) - int(time.time())
                        if 0 < diff <= 65:
                            sleep_s = diff + 1.0
                    except ValueError:
                        pass
                sleep_s = min(sleep_s, 60.0)
                time.sleep(sleep_s)
                continue
            return resp
        except Exception:
            if attempt == max_retries - 1:
                return None
            time.sleep(1.0)
    return None


def collect_live_discovery(
    date_str: str = "2026-10-22",
    output_dir: Path | None = None,
    min_raw_repos: int = 40,
    target_candidates_min: int = 12,
    target_candidates_max: int = 18,
    resume: bool = True,
    token: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Collect abandoned idea repos from GitHub search API and construct dated snapshot tables."""
    out_dir = output_dir or (ROOT / "data" / "discovery" / date_str)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_path = out_dir / "snapshot_cache.json"

    collected_at = f"{date_str}T23:00:00+00:00"
    origin = f"github_rest_api:{date_str}T23:00:00Z"

    # Seed benchmark repos from committed raw snapshot
    benchmark_raw = load_snapshot(RAW)
    repos: dict[str, Any] = dict(benchmark_raw.get("repos") or {})
    candidates_specs: list[dict[str, Any]] = list(benchmark_raw.get("candidates") or [])
    prior_art_specs: list[dict[str, Any]] = list(benchmark_raw.get("prior_art") or [])
    demand_specs: list[dict[str, Any]] = list(benchmark_raw.get("demand") or [])

    benchmark_repo_ids = {spec["repo_id"] for spec in candidates_specs}
    seen_repo_ids = set(benchmark_repo_ids)

    if resume and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            for k, v in (cached.get("repos") or {}).items():
                repos[k] = v
            for spec in cached.get("candidates") or []:
                fn = spec["repo_id"]
                if fn not in benchmark_repo_ids:
                    r_desc = str((repos.get(fn) or {}).get("description") or "")
                    r_readme = str(spec.get("readme_excerpt") or "")
                    repo_obj = repos.get(fn) or {}
                    reason = check_discovery_exclusion(repo_obj, r_readme)
                    if reason:
                        excluded_repos[fn] = reason
                        continue
                if fn not in seen_repo_ids:
                    candidates_specs.append(spec)
                    seen_repo_ids.add(fn)

            seen_prior = {(r["repo_id"], r["alternative_id"]) for r in prior_art_specs}
            for spec in cached.get("prior_art") or []:
                key = (spec.get("repo_id"), spec.get("alternative_id"))
                if spec.get("repo_id") in seen_repo_ids and key not in seen_prior:
                    prior_art_specs.append(spec)
                    seen_prior.add(key)

            seen_demand = {(r["repo_id"], r["signal_id"]) for r in demand_specs}
            for spec in cached.get("demand") or []:
                key = (spec.get("repo_id"), spec.get("signal_id"))
                if spec.get("repo_id") in seen_repo_ids and key not in seen_demand:
                    demand_specs.append(spec)
                    seen_demand.add(key)
        except Exception:
            pass

    auth_token = token or get_github_token()
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "PhoenixRepo-Collector/1.0",
    }
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    commands_log: list[str] = list(benchmark_raw.get("commands") or [])
    excluded_repos: dict[str, str] = {}

    # Evaluate how many candidates we currently have
    def _count_candidates() -> int:
        test_snap = {
            "collected_at": collected_at,
            "data_origin": origin,
            "repos": repos,
            "candidates": candidates_specs,
            "prior_art": prior_art_specs,
            "demand": demand_specs,
        }
        cand_rows, prior_rows, demand_rows = build_tables(test_snap)
        count = 0
        for crow in cand_rows:
            prows = [r for r in prior_rows if r["repo_id"] == crow["repo_id"]]
            drows = [r for r in demand_rows if r["repo_id"] == crow["repo_id"]]
            v, _ = decide_discovery(crow, prows, drows)
            if v == "CANDIDATE":
                count += 1
        return count

    current_candidate_count = _count_candidates()

    if len(candidates_specs) < min_raw_repos or current_candidate_count < target_candidates_min:
        with httpx.Client(transport=transport, timeout=15.0) as client:
            for kw in PROBLEM_KEYWORDS:
                if len(candidates_specs) >= min_raw_repos and current_candidate_count >= target_candidates_min:
                    break

                for page in (1, 2):
                    if len(candidates_specs) >= min_raw_repos and current_candidate_count >= target_candidates_min:
                        break

                    q = f'"{kw}" created:>=2023-01-01 pushed:<2026-04-25 fork:false archived:false size:>50'
                    cmd_entry = f"gh api search/repositories?q={q}&page={page}"
                    if cmd_entry not in commands_log:
                        commands_log.append(cmd_entry)

                    resp = request_with_retry(
                        client,
                        "https://api.github.com/search/repositories",
                        headers=headers,
                        params={"q": q, "page": page, "per_page": 25},
                    )
                    if not resp or resp.status_code != 200:
                        continue

                    items = resp.json().get("items", [])
                    for item in items:
                        fn = item.get("full_name") or ""
                        if not fn or fn in seen_repo_ids:
                            continue

                        lang = item.get("language")
                        if lang not in TARGET_LANGUAGES:
                            continue

                        desc = item.get("description") or ""
                        if is_non_product(fn, desc):
                            continue

                        # Fetch repo metadata
                        meta_resp = request_with_retry(
                            client,
                            f"https://api.github.com/repos/{fn}",
                            headers=headers,
                        )
                        if not meta_resp or meta_resp.status_code != 200:
                            continue
                        repo_meta = meta_resp.json()

                        # Fetch README
                        readme_resp = request_with_retry(
                            client,
                            f"https://api.github.com/repos/{fn}/readme",
                            headers={**headers, "Accept": "application/vnd.github.raw"},
                        )
                        readme_text = readme_resp.text if readme_resp and readme_resp.status_code == 200 else ""
                        if not readme_text or len(readme_text.strip()) < 40:
                            continue

                        ex_reason = check_discovery_exclusion(repo_meta, readme_text)
                        if ex_reason:
                            excluded_repos[fn] = ex_reason
                            continue

                        # Fetch git tree
                        branch = repo_meta.get("default_branch") or "main"
                        tree_resp = request_with_retry(
                            client,
                            f"https://api.github.com/repos/{fn}/git/trees/{branch}",
                            headers=headers,
                            params={"recursive": "1"},
                        )
                        tree_items = (
                            tree_resp.json().get("tree", []) if tree_resp and tree_resp.status_code == 200 else []
                        )
                        paths = [it.get("path") for it in tree_items if it.get("type") == "blob" and it.get("path")]

                        cand_spec = extract_candidate_features(
                            repo_meta, readme_text, paths, collected_at=collected_at
                        )
                        if not cand_spec:
                            continue

                        # Add candidate to repos & specs
                        repos[fn] = repo_meta
                        candidates_specs.append(cand_spec)
                        seen_repo_ids.add(fn)

                        terms = cand_spec["core_terms"]

                        # Search for free maintained alternatives (Prior Art) via GitHub, npm, PyPI
                        alts = search_alternatives_for_candidate(client, fn, terms, headers)
                        for alt in alts:
                            alt_fn = alt["id"]
                            if alt.get("repo_meta"):
                                repos[alt_fn] = alt["repo_meta"]
                            prior_art_specs.append(
                                {
                                    "repo_id": fn,
                                    "alternative_id": alt_fn,
                                    "url": alt.get("url") or f"https://github.com/{alt_fn}",
                                    "licence_spdx": alt.get("licence_spdx") or "",
                                    "stars": alt.get("stars", 0),
                                    "pushed_at": alt.get("pushed_at") or "",
                                    "excerpt": alt.get("excerpt") or "",
                                    "candidate_excerpt": cand_spec["problem_excerpt"],
                                    "required_terms": terms,
                                }
                            )
                            # If alternative has stars >= 100 and maintained, also record related_repo demand
                            st_count = alt.get("stars") or 0
                            alt_pushed = alt.get("pushed_at") or ""
                            alt_days = days_between(alt_pushed, collected_at)
                            if st_count >= 100 and alt_days is not None and alt_days <= 180:
                                demand_specs.append(
                                    {
                                        "repo_id": fn,
                                        "signal_id": f"{alt_fn.replace('/', '-')}-related-repo",
                                        "kind": "related_repo",
                                        "url": alt.get("url") or f"https://github.com/{alt_fn}",
                                        "excerpt": alt.get("excerpt") or "",
                                        "metric_name": "",
                                        "metric_value": "",
                                        "comments": "",
                                        "reactions": "",
                                        "stars": st_count,
                                        "pushed_at": alt_pushed,
                                        "query": "",
                                    }
                                )

                        # Search for external demand signals
                        demand_items = search_demand_for_candidate(client, fn, terms, headers)
                        for dem in demand_items:
                            demand_specs.append(dem)

                        # Periodically save snapshot cache
                        if len(candidates_specs) % 5 == 0:
                            interim_data = {
                                "collected_at": collected_at,
                                "data_origin": origin,
                                "token_used": bool(auth_token),
                                "token_written": False,
                                "commands": commands_log,
                                "repos": repos,
                                "candidates": candidates_specs,
                                "prior_art": prior_art_specs,
                                "demand": demand_specs,
                            }
                            cache_path.write_text(
                                json.dumps(interim_data, indent=2, ensure_ascii=False) + "\n",
                                encoding="utf-8",
                            )

                        current_candidate_count = _count_candidates()
                        print(
                            f"[{len(candidates_specs)} raw repos] Processed {fn} ({lang}) | "
                            f"Eligible CANDIDATEs so far: {current_candidate_count}"
                        )
                        time.sleep(0.5)
                        if (
                            len(candidates_specs) >= min_raw_repos
                            and current_candidate_count >= target_candidates_min
                        ):
                            break

    # Ensure candidates have prior-art alternatives checked when collecting
    checked_candidates = {r["repo_id"] for r in prior_art_specs}
    missing_prior_specs = [s for s in candidates_specs if s["repo_id"] not in checked_candidates]
    if missing_prior_specs and target_candidates_min > 0:
        with httpx.Client(transport=transport, timeout=15.0) as client:
            for cand_spec in missing_prior_specs:
                fn = cand_spec["repo_id"]
                terms = cand_spec.get("core_terms") or []
                alts = search_alternatives_for_candidate(client, fn, terms, headers)
                for alt in alts:
                    alt_fn = alt["id"]
                    if alt.get("repo_meta"):
                        repos[alt_fn] = alt["repo_meta"]
                    prior_art_specs.append(
                        {
                            "repo_id": fn,
                            "alternative_id": alt_fn,
                            "url": alt.get("url") or f"https://github.com/{alt_fn}",
                            "licence_spdx": alt.get("licence_spdx") or "",
                            "stars": alt.get("stars", 0),
                            "pushed_at": alt.get("pushed_at") or "",
                            "excerpt": alt.get("excerpt") or "",
                            "candidate_excerpt": cand_spec["problem_excerpt"],
                            "required_terms": terms,
                        }
                    )

    snapshot_data = {
        "collected_at": collected_at,
        "data_origin": origin,
        "token_used": bool(auth_token),
        "token_written": False,
        "commands": commands_log,
        "repos": repos,
        "candidates": candidates_specs,
        "prior_art": prior_art_specs,
        "demand": demand_specs,
    }

    # Write snapshot cache
    cache_path.write_text(json.dumps(snapshot_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # Write exclusions
    exclusions_path = out_dir / "exclusions.json"
    exclusions_path.write_text(json.dumps(excluded_repos, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # Build CSV tables
    cand_rows, prior_rows, demand_rows = build_tables(snapshot_data)

    _write_csv(out_dir / "discovery_candidates.csv", DISCOVERY_COLUMNS, cand_rows)
    _write_csv(out_dir / "prior_art.csv", PRIOR_ART_COLUMNS, prior_rows)
    _write_csv(out_dir / "demand_signals.csv", DEMAND_COLUMNS, demand_rows)

    prov_data = {
        "collected_at": collected_at,
        "data_origin": origin,
        "token_used": bool(auth_token),
        "token_written": False,
        "commands": commands_log,
        "raw_sha256": hashlib.sha256(cache_path.read_bytes()).hexdigest(),
        "exclusions_count": len(excluded_repos),
        "note": "Broadened live discovery snapshot with GitHub search API.",
    }
    (out_dir / "discovery_provenance.json").write_text(
        json.dumps(prov_data, indent=2) + "\n",
        encoding="utf-8",
    )

    verdicts: dict[str, int] = {}
    for crow in cand_rows:
        prows = [r for r in prior_rows if r["repo_id"] == crow["repo_id"]]
        drows = [r for r in demand_rows if r["repo_id"] == crow["repo_id"]]
        v, _ = decide_discovery(crow, prows, drows)
        verdicts[v] = verdicts.get(v, 0) + 1

    return {
        "output_dir": str(out_dir),
        "raw_repos_count": len(candidates_specs),
        "verdicts": verdicts,
        "candidate_count": verdicts.get("CANDIDATE", 0),
        "already_exists_count": verdicts.get("ALREADY_EXISTS", 0),
        "no_go_count": verdicts.get("NO_GO", 0),
    }


def load_snapshot(path: Path = RAW) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_tables(snapshot: dict) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    collected_at = str(snapshot["collected_at"])
    origin = str(snapshot["data_origin"])
    repos = snapshot["repos"]
    candidates = []
    for spec in snapshot["candidates"]:
        repo = repos[spec["repo_id"]]
        licence = repo.get("license") or {}
        spdx = ""
        if isinstance(licence, dict):
            spdx = str(licence.get("spdx_id") or "")
            if spdx in {"NOASSERTION", "null", "None"}:
                spdx = ""
        row = {
            "repo_id": spec["repo_id"],
            "html_url": str(repo.get("html_url") or ""),
            "description": str(repo.get("description") or ""),
            "readme_excerpt": spec["readme_excerpt"],
            "problem_excerpt": spec["problem_excerpt"],
            "user_excerpt": spec["user_excerpt"],
            "scope_excerpt": spec["scope_excerpt"],
            "code_paths": "|".join(spec["code_paths"]),
            "created_at": str(repo.get("created_at") or ""),
            "pushed_at": str(repo.get("pushed_at") or ""),
            "days_since_push": str(_days(repo.get("pushed_at"), collected_at)),
            "stargazers_count": str(repo.get("stargazers_count")),
            "forks_count": str(repo.get("forks_count")),
            "open_issues": str(repo.get("open_issues_count")),
            "licence_spdx": spdx,
            "core_terms": "|".join(spec["core_terms"]),
            "cohort": spec.get("cohort") or "discovery",
            "data_origin": origin,
            "collected_at": collected_at,
        }
        candidates.append(row)

    prior_rows = []
    for spec in snapshot["prior_art"]:
        repo = repos.get(spec["alternative_id"]) or {}
        licence = repo.get("license") or {}
        spdx = ""
        if isinstance(licence, dict):
            spdx = str(licence.get("spdx_id") or "")
        elif isinstance(licence, str):
            spdx = licence
        if spdx in {"NOASSERTION", "null", "None"}:
            spdx = ""
        licence_spdx = str(spec.get("licence_spdx") or spdx)
        stars_val = spec.get("stars")
        if stars_val is None or stars_val == "":
            stars_val = repo.get("stargazers_count", "")
        pushed_val = str(spec.get("pushed_at") or repo.get("pushed_at") or "")
        url_val = str(spec.get("url") or repo.get("html_url") or f"https://github.com/{spec['alternative_id']}")

        prior_rows.append(
            {
                "repo_id": spec["repo_id"],
                "alternative_id": spec["alternative_id"],
                "url": url_val,
                "licence_spdx": licence_spdx,
                "stars": str(stars_val),
                "pushed_at": pushed_val,
                "days_since_push": str(_days(pushed_val, collected_at)) if pushed_val else "",
                "excerpt": spec.get("excerpt") or "",
                "candidate_excerpt": spec.get("candidate_excerpt") or "",
                "required_terms": "|".join(spec["required_terms"]),
                "data_origin": origin,
                "collected_at": collected_at,
            }
        )

    demand_rows = []
    for spec in snapshot["demand"]:
        alternative = repos.get(spec.get("alternative_id") or "")
        pushed = str((alternative or {}).get("pushed_at") or spec.get("pushed_at") or "")
        stars = spec.get("stars")
        if stars is None and alternative is not None:
            stars = alternative.get("stargazers_count")
        days = ""
        if pushed:
            days = str(_days(pushed, collected_at))
        demand_rows.append(
            {
                "repo_id": spec["repo_id"],
                "signal_id": spec["signal_id"],
                "kind": spec["kind"],
                "url": spec.get("url") or str((alternative or {}).get("html_url") or ""),
                "excerpt": spec.get("excerpt") or "",
                "metric_name": spec.get("metric_name") or "",
                "metric_value": "" if spec.get("metric_value") is None else str(spec.get("metric_value")),
                "comments": "" if spec.get("comments") is None else str(spec.get("comments")),
                "reactions": "" if spec.get("reactions") is None else str(spec.get("reactions")),
                "stars": "" if stars is None else str(stars),
                "pushed_at": pushed,
                "days_since_push": days,
                "query": spec.get("query") or "",
                "data_origin": origin,
                "collected_at": collected_at,
            }
        )
    _check_columns(candidates, DISCOVERY_COLUMNS)
    _check_columns(prior_rows, PRIOR_ART_COLUMNS)
    _check_columns(demand_rows, DEMAND_COLUMNS)
    return candidates, prior_rows, demand_rows


def provenance(snapshot: dict, raw_path: Path = RAW) -> dict:
    payload = raw_path.read_bytes()
    return {
        "collected_at": snapshot["collected_at"],
        "data_origin": snapshot["data_origin"],
        "token_used": False,
        "token_written": False,
        "commands": snapshot.get("commands") or [],
        "readme_sha256": snapshot.get("readme_sha256") or {},
        "raw_sha256": hashlib.sha256(payload).hexdigest(),
        "note": (
            "Days are the date difference between pushed_at and collected_at. "
            "Search total_count values are that query's total, not a census. "
            "README excerpts were checked as substrings of the fetched README bodies "
            "whose sha256 values are stored here. Those bodies are not committed."
        ),
    }


def write_outputs(root: Path = ROOT) -> None:
    snapshot = load_snapshot(root / "data" / "discovery" / "raw" / "snapshot.json")
    candidates, prior_rows, demand_rows = build_tables(snapshot)
    real = root / "data" / "real"
    sample = root / "data" / "sample"
    _write_csv(real / "discovery_candidates.csv", DISCOVERY_COLUMNS, candidates)
    _write_csv(real / "prior_art.csv", PRIOR_ART_COLUMNS, prior_rows)
    _write_csv(real / "demand_signals.csv", DEMAND_COLUMNS, demand_rows)
    _write_csv(sample / "discovery_candidates.csv", DISCOVERY_COLUMNS, [])
    _write_csv(sample / "prior_art.csv", PRIOR_ART_COLUMNS, [])
    _write_csv(sample / "demand_signals.csv", DEMAND_COLUMNS, [])
    (real / "discovery_provenance.json").write_text(
        json.dumps(provenance(snapshot, root / "data" / "discovery" / "raw" / "snapshot.json"), indent=2) + "\n",
        encoding="utf-8",
    )


def _days(pushed_at: str | None, collected_at: str) -> int:
    count = days_between(pushed_at, collected_at)
    if count is None:
        raise ValueError(f"cannot compute days for {pushed_at}")
    return count


def _check_columns(rows: list[dict[str, str]], columns: tuple[str, ...]) -> None:
    expected = set(columns)
    for row in rows:
        if set(row) != expected:
            missing = expected - set(row)
            extra = set(row) - expected
            raise ValueError(f"column mismatch missing={missing} extra={extra}")


def _write_csv(path: Path, columns: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def main() -> int:
    parser = argparse.ArgumentParser(description="Discovery collector and normaliser.")
    parser.add_argument("--collect", action="store_true", help="Perform live GitHub search collection.")
    parser.add_argument("--date", type=str, default="2026-10-22", help="Snapshot date.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Output directory for snapshot.")
    parser.add_argument("--limit", type=int, default=45, help="Minimum raw repos to collect.")
    parser.add_argument("--resume", action="store_true", default=True, help="Resume from partial cache.")
    args = parser.parse_args()

    if args.collect or args.output_dir is not None:
        result = collect_live_discovery(
            date_str=args.date,
            output_dir=args.output_dir,
            min_raw_repos=args.limit,
            resume=args.resume,
        )
        print(f"Collected live discovery into {result['output_dir']}")
        print(f"Raw repos: {result['raw_repos_count']}, Candidate verdicts: {result['verdicts']}")
        return 0

    write_outputs()
    print("wrote discovery_candidates.csv prior_art.csv demand_signals.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
