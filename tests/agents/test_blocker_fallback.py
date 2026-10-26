"""ALREADY_EXISTS with a blocker name the checks cannot match falls back to a verified blocker."""
from __future__ import annotations

import json
from pathlib import Path

from app.agents.relevance import RelevanceClient, judge_discovery, quote_in_text

_CAND = {
    "repo_id": "test/selfdb-like",
    "description": "Self-hosted backend with auth and storage.",
    "readme_excerpt": "A self-hosted backend with auth, database and storage for your apps.",
}


def _alt(stars: int) -> dict:
    return {
        "alternative_id": "Erudika/para",
        "url": "https://github.com/Erudika/para",
        "same_problem": True,
        "overlap": "full",
        "quotes": ["a multipurpose backend"],
        "description": "Para - a multipurpose backend framework for object persistence and auth.",
        "stars": stars,
        "pushed_at": "2026-10-01T00:00:00Z",
        "archived": False,
        "licence_spdx": "Apache-2.0",
        "page_kind": "product",
    }


def _llm_names(name: str):
    def handler(prompt, sys):
        return json.dumps({
            "verdict": "ALREADY_EXISTS", "reason": "an existing backend does this",
            "problem_quote": "A self-hosted backend", "alternative_repo": name,
            "alternative_quote": "open source backend as a service",
        })
    return handler


def test_unmatched_name_falls_back_to_the_verified_blocker(tmp_path: Path):
    client = RelevanceClient(cache_dir=tmp_path, mock_handler=_llm_names("Supabase"))
    verdict, reason = judge_discovery(_CAND, prior_rows=[], demand_rows=[], client=client, problem_alternatives=[_alt(1500)])
    assert verdict == "ALREADY_EXISTS"
    assert "Erudika/para" in reason and "a multipurpose backend" in reason
    assert "did not verify" in reason and "Supabase" in reason


def test_no_fallback_when_no_alternative_passes_the_maturity_rule(tmp_path: Path):
    client = RelevanceClient(cache_dir=tmp_path, mock_handler=_llm_names("Supabase"))
    verdict, reason = judge_discovery(_CAND, prior_rows=[], demand_rows=[], client=client, problem_alternatives=[_alt(12)])
    assert verdict == "NEEDS_REVIEW"
    assert "not found in candidate prior-art" in reason


def test_quote_tolerates_dash_spacing_and_unicode_dashes():
    page = "Raindrop.io — save links‑and publish them to WordPress…"
    assert quote_in_text("Raindrop.io - save links-and publish them to WordPress...", page)
    assert quote_in_text("raindrop.io-save links - and publish", page)
    assert not quote_in_text("Raindrop.io saves links", page)


def test_no_fallback_when_the_blocker_quote_is_not_in_its_own_text(tmp_path: Path):
    alt = {**_alt(1500), "description": "A Java framework."}
    client = RelevanceClient(cache_dir=tmp_path, mock_handler=_llm_names("Supabase"))
    verdict, _ = judge_discovery(_CAND, prior_rows=[], demand_rows=[], client=client, problem_alternatives=[alt])
    assert verdict == "NEEDS_REVIEW"
