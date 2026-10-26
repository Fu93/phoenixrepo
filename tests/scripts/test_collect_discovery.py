"""Offline unit tests for scripts/collect_discovery.py using mock HTTP transports."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import httpx
import pytest

from app.agents.market_filter import idea_block_reason
from app.sql_catalog import DEMAND_COLUMNS, DISCOVERY_COLUMNS, PRIOR_ART_COLUMNS
from scripts.collect_discovery import (
    collect_live_discovery,
    extract_candidate_features,
    is_non_product,
    request_with_retry,
)

ROOT = Path(__file__).resolve().parents[2]


def test_is_non_product_identifies_learning_and_templates():
    assert is_non_product("learn-python-course", "Python tutorial and course materials")
    assert is_non_product("my-dotfiles", "My personal dotfiles for zsh and neovim")
    assert is_non_product("awesome-react", "A curated list of awesome React libraries")
    assert is_non_product("react-starter-kit", "Next.js starter template for developers")
    assert is_non_product("leetcode-solutions", "My solutions for leetcode and interview prep")
    assert is_non_product("empty-repo", "")
    assert not is_non_product("doc-sync", "Sync markdown documentation across team workspaces.")


def test_extract_candidate_features_ensures_exact_substrings():
    repo_meta = {
        "full_name": "acme/note-sync",
        "description": "CLI tool to synchronize markdown notes with cloud storage.",
        "html_url": "https://github.com/acme/note-sync",
        "created_at": "2023-05-10T12:00:00Z",
        "pushed_at": "2024-01-15T10:00:00Z",
        "stargazers_count": 0,
        "forks_count": 0,
        "open_issues_count": 0,
        "license": {"spdx_id": "MIT"},
    }
    readme = (
        "# note-sync\n\n"
        "CLI tool to synchronize markdown notes with cloud storage.\n\n"
        "Built for developers and writers who want local-first notes.\n\n"
        "## Features\n"
        "- Bi-directional sync between local folder and remote bucket\n"
        "- Conflict resolution with diff view\n"
    )
    tree_paths = [
        "src/main.rs",
        "src/sync/engine.rs",
        "src/storage/s3.rs",
        "Cargo.toml",
        ".gitignore",
        "README.md",
    ]
    collected_at = "2026-10-22T23:00:00+00:00"

    features = extract_candidate_features(
        repo_meta, readme, tree_paths, collected_at=collected_at
    )
    assert features is not None
    assert features["repo_id"] == "acme/note-sync"
    assert features["problem_excerpt"] == repo_meta["description"]
    assert features["user_excerpt"] in (repo_meta["description"] + "\n" + readme)
    assert features["scope_excerpt"] in (repo_meta["description"] + "\n" + readme)
    assert len(features["code_paths"]) >= 2
    assert "src/main.rs" in features["code_paths"]
    assert "Cargo.toml" not in features["code_paths"]

    # Build row to check idea_block_reason
    row = {
        "repo_id": features["repo_id"],
        "description": features["problem_excerpt"],
        "readme_excerpt": features["readme_excerpt"],
        "problem_excerpt": features["problem_excerpt"],
        "user_excerpt": features["user_excerpt"],
        "scope_excerpt": features["scope_excerpt"],
        "code_paths": "|".join(features["code_paths"]),
        "pushed_at": repo_meta["pushed_at"],
        "collected_at": collected_at,
        "days_since_push": "1011",
        "core_terms": "|".join(features["core_terms"]),
    }
    assert idea_block_reason(row) is None


def test_collect_live_discovery_offline_mock(tmp_path: Path):
    candidate_meta = {
        "full_name": "org/test-product",
        "description": "Self-hosted dashboard for monitoring background cron jobs.",
        "html_url": "https://github.com/org/test-product",
        "created_at": "2023-04-01T10:00:00Z",
        "pushed_at": "2024-03-01T10:00:00Z",
        "stargazers_count": 0,
        "forks_count": 0,
        "open_issues_count": 0,
        "license": {"spdx_id": "Apache-2.0"},
        "default_branch": "main",
        "language": "TypeScript",
    }
    candidate_readme = (
        "# test-product\n\n"
        "Self-hosted dashboard for monitoring background cron jobs.\n"
        "Designed for devops engineers and system administrators.\n\n"
        "- Real-time alerts on cron failure via webhooks\n"
        "- Visual execution history\n"
    )

    alt_meta = {
        "full_name": "alt/cron-monitor",
        "description": "Open source dashboard and alerts for cron jobs.",
        "html_url": "https://github.com/alt/cron-monitor",
        "created_at": "2022-01-01T10:00:00Z",
        "pushed_at": "2026-08-01T10:00:00Z",
        "stargazers_count": 350,
        "forks_count": 25,
        "open_issues_count": 5,
        "license": {"spdx_id": "MIT"},
        "default_branch": "main",
        "language": "TypeScript",
    }

    def mock_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/search/repositories" in url:
            if "alt" in url or "stars:>100" in url:
                return httpx.Response(200, json={"items": [alt_meta]})
            return httpx.Response(200, json={"items": [candidate_meta]})
        if "/search/issues" in url:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": 999,
                            "number": 42,
                            "title": "Need alerting for failed cron dashboard jobs",
                            "html_url": "https://github.com/external/issue/42",
                            "comments": 3,
                            "reactions": {"total_count": 2},
                        }
                    ]
                },
            )
        if "/repos/org/test-product/readme" in url:
            return httpx.Response(200, text=candidate_readme)
        if "/repos/org/test-product/git/trees" in url:
            return httpx.Response(
                200,
                json={
                    "tree": [
                        {"path": "src/index.ts", "type": "blob"},
                        {"path": "src/server.ts", "type": "blob"},
                        {"path": "package.json", "type": "blob"},
                    ]
                },
            )
        if "/repos/org/test-product" in url:
            return httpx.Response(200, json=candidate_meta)
        if "/repos/alt/cron-monitor" in url:
            return httpx.Response(200, json=alt_meta)
        return httpx.Response(404, json={"message": "Not found"})

    transport = httpx.MockTransport(mock_handler)
    out_dir = tmp_path / "2026-10-22"

    result = collect_live_discovery(
        date_str="2026-10-22",
        output_dir=out_dir,
        min_raw_repos=3,
        target_candidates_min=1,
        resume=False,
        token="fake-test-token",
        transport=transport,
    )

    assert Path(result["output_dir"]).exists()
    assert (out_dir / "discovery_candidates.csv").exists()
    assert (out_dir / "prior_art.csv").exists()
    assert (out_dir / "demand_signals.csv").exists()
    assert (out_dir / "discovery_provenance.json").exists()

    with (out_dir / "discovery_candidates.csv").open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert tuple(reader.fieldnames or ()) == DISCOVERY_COLUMNS
        rows = list(reader)
        assert any(r["repo_id"] == "org/test-product" for r in rows)

    with (out_dir / "prior_art.csv").open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert tuple(reader.fieldnames or ()) == PRIOR_ART_COLUMNS

    with (out_dir / "demand_signals.csv").open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert tuple(reader.fieldnames or ()) == DEMAND_COLUMNS


def test_request_with_retry_handles_rate_limits():
    call_count = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(429, headers={"Retry-After": "0.01"})
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(mock_handler)
    with httpx.Client(transport=transport) as client:
        resp = request_with_retry(
            client,
            "https://api.github.com/test",
            headers={},
            max_retries=3,
        )
        assert resp is not None
        assert resp.status_code == 200
        assert call_count == 2


def test_resume_from_cache_does_not_refetch(tmp_path: Path):
    out_dir = tmp_path / "cache_test"
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_path = out_dir / "snapshot_cache.json"

    dummy_cache = {
        "collected_at": "2026-10-22T23:00:00+00:00",
        "data_origin": "github_rest_api:2026-10-22T23:00:00Z",
        "repos": {
            "cached/tool": {
                "full_name": "cached/tool",
                "html_url": "https://github.com/cached/tool",
                "description": "Cached tool for offline testing.",
                "created_at": "2023-01-01T00:00:00Z",
                "pushed_at": "2024-01-01T00:00:00Z",
                "stargazers_count": 0,
                "forks_count": 0,
                "open_issues_count": 0,
                "license": {"spdx_id": "MIT"},
            }
        },
        "candidates": [
            {
                "repo_id": "cached/tool",
                "readme_excerpt": "Cached tool for offline testing.",
                "problem_excerpt": "Cached tool for offline testing.",
                "user_excerpt": "Cached tool",
                "scope_excerpt": "offline testing",
                "code_paths": ["src/main.py", "src/util.py"],
                "core_terms": ["tool", "testing"],
                "cohort": "discovery",
            }
        ],
        "prior_art": [],
        "demand": [],
    }
    cache_path.write_text(json.dumps(dummy_cache), encoding="utf-8")

    transport_called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal transport_called
        transport_called = True
        return httpx.Response(500, text="Should not be called")

    transport = httpx.MockTransport(handler)

    res = collect_live_discovery(
        date_str="2026-10-22",
        output_dir=out_dir,
        min_raw_repos=1,
        target_candidates_min=0,
        resume=True,
        transport=transport,
    )
    assert not transport_called
    assert res["raw_repos_count"] >= 1
