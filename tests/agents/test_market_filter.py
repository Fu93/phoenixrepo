"""The idea filter ignores a repository's own stars and cites external evidence."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import pytest

from app.adapters.sample_zetaris import SampleZetaris
from app.agents.discovery_planner import build_discovery_prd, render_discovery_markdown
from app.agents.market_filter import (
    accept_proposed_alternative,
    alternative_qualifies,
    apply_market_overlay,
    days_between,
    decide_discovery,
)
from app.sql_catalog import DEMAND_COLUMNS, DISCOVERY_COLUMNS, PRIOR_ART_COLUMNS, demand_sql, discovery_sql
from scripts.collect_discovery import build_tables, load_snapshot
from scripts.write_discovery_prd import build_for_repo

INVOICE = "tharani-2006/Invoice-generator-application"
YTKB = "SergioMendolia/ytkb"
HYGEN = "jondot/hygen"
ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "real"


def _rows(name: str, columns: tuple[str, ...]) -> list[dict[str, str]]:
    with (DATA / name).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert tuple(reader.fieldnames or ()) == columns
        return [dict(row) for row in reader]


def _candidate(repo_id: str) -> dict[str, str]:
    return next(row for row in _rows("discovery_candidates.csv", DISCOVERY_COLUMNS) if row["repo_id"] == repo_id)


def _prior(repo_id: str) -> list[dict[str, str]]:
    return [row for row in _rows("prior_art.csv", PRIOR_ART_COLUMNS) if row["repo_id"] == repo_id]


def _demand(repo_id: str) -> list[dict[str, str]]:
    return [row for row in _rows("demand_signals.csv", DEMAND_COLUMNS) if row["repo_id"] == repo_id]


def test_snapshot_rebuilds_the_committed_csvs():
    candidates, prior_rows, demand_rows = build_tables(load_snapshot())
    assert candidates == _rows("discovery_candidates.csv", DISCOVERY_COLUMNS)
    assert prior_rows == _rows("prior_art.csv", PRIOR_ART_COLUMNS)
    assert demand_rows == _rows("demand_signals.csv", DEMAND_COLUMNS)


def test_days_are_recomputed_from_the_stored_timestamps():
    for row in _rows("discovery_candidates.csv", DISCOVERY_COLUMNS) + _rows("prior_art.csv", PRIOR_ART_COLUMNS):
        assert int(row["days_since_push"]) == days_between(row["pushed_at"], row["collected_at"])


def test_stars_forks_and_issues_do_not_change_the_verdict():
    candidate = dict(_candidate(YTKB))
    baseline, reason = decide_discovery(candidate, _prior(YTKB), _demand(YTKB))
    inflated = dict(candidate)
    inflated["stargazers_count"] = "9999"
    inflated["forks_count"] = "9999"
    inflated["open_issues"] = "9999"
    assert decide_discovery(inflated, _prior(YTKB), _demand(YTKB)) == (baseline, reason)
    assert "9999" not in reason
    assert baseline == "CANDIDATE"


def test_invoice_zero_star_repo_is_already_exists_via_invoify():
    decision, reason = decide_discovery(_candidate(INVOICE), _prior(INVOICE), _demand(INVOICE))
    assert decision == "ALREADY_EXISTS"
    assert "https://github.com/al1abb/invoify" in reason
    assert "real PDF" in reason
    row = _candidate(INVOICE)
    assert row["stargazers_count"] == "0"
    assert row["licence_spdx"] == ""
    assert "license-MIT" in row["readme_excerpt"]
    assert int(row["days_since_push"]) >= 180


def test_ytkb_passes_on_external_demand_and_not_on_a_search_total():
    decision, reason = decide_discovery(_candidate(YTKB), _prior(YTKB), _demand(YTKB))
    assert decision == "CANDIDATE"
    assert "https://github.com/foliant-docs/foliant/pull/80" in reason
    search_only = [row for row in _demand(YTKB) if row["kind"] == "github_search"]
    assert search_only
    assert any(row["metric_value"] == "10" for row in search_only)
    assert any(row["metric_value"] == "0" for row in search_only)
    blocked, blocked_reason = decide_discovery(_candidate(YTKB), _prior(YTKB), search_only)
    assert blocked == "NO_GO"
    assert blocked_reason == "external demand for the problem was not shown. Method: phrase/keyword."
    assert all(not alternative_qualifies(row) for row in _prior(YTKB))


def test_hygen_prior_art_does_not_cover_the_cited_inject_phrase():
    rows = _prior(HYGEN)
    assert rows
    assert all(not alternative_qualifies(row) for row in rows)
    assert any("injecting into existing files" in row["candidate_excerpt"].casefold() for row in rows)
    assert all("injecting into existing files" not in row["excerpt"].casefold() for row in rows)
    decision, reason = apply_market_overlay("RESURRECTION_FAILED", "CI has decayed.", [], rows, [])
    assert decision == "RESURRECTION_FAILED"
    assert "do not change the revival verdict" in reason


def test_empty_discovery_rows_leave_the_revival_verdict_alone():
    assert apply_market_overlay("CANDIDATE", "policy pass", [], [], []) == ("CANDIDATE", "policy pass")


def test_a_model_url_counts_only_when_the_stored_row_qualifies():
    invoice_rows = _prior(INVOICE)
    accepted = accept_proposed_alternative("https://github.com/al1abb/invoify/", invoice_rows)
    assert accepted is not None
    assert accepted["alternative_id"] == "al1abb/invoify"
    assert accept_proposed_alternative("https://github.com/plopjs/plop", _prior(HYGEN)) is None
    assert accept_proposed_alternative("https://github.com/not-in-the-table/nope", invoice_rows) is None
    missed = dict(invoice_rows[0])
    missed["excerpt"] = missed["excerpt"].replace("PDF", "document")
    assert not alternative_qualifies(missed)
    assert accept_proposed_alternative(missed["url"], [missed]) is None


def test_an_unlicensed_or_stale_alternative_does_not_qualify():
    row = dict(_prior(INVOICE)[0])
    row["licence_spdx"] = "NOASSERTION"
    assert not alternative_qualifies(row)
    row = dict(_prior(INVOICE)[0])
    row["licence_spdx"] = ""
    assert not alternative_qualifies(row)
    row = dict(_prior(INVOICE)[0])
    row["pushed_at"] = "2025-01-01T00:00:00Z"
    row["days_since_push"] = str(days_between(row["pushed_at"], row["collected_at"]))
    assert not alternative_qualifies(row)


def test_boilerplate_paths_and_a_missing_excerpt_are_no_go():
    row = dict(_candidate(YTKB))
    row["code_paths"] = "README.md|go.mod|.github/workflows/ci.yml"
    decision, reason = decide_discovery(row, [], _demand(YTKB))
    assert decision == "NO_GO"
    assert "template" in reason
    row = dict(_candidate(YTKB))
    row["user_excerpt"] = "someone who was not described"
    decision, reason = decide_discovery(row, [], _demand(YTKB))
    assert decision == "NO_GO"
    assert "does not state a concrete target user" in reason


def test_sample_cache_can_read_the_discovery_view():
    client = SampleZetaris()
    result = client.run_sql(discovery_sql(YTKB))
    assert result.rows[0]["stargazers_count"] == "0"
    assert result.rows[0]["data_origin"].startswith("github_rest_api:")
    empty = client.run_sql(demand_sql("sample/phoenix-ledger"))
    assert empty.rows == []


def test_committed_ytkb_prd_matches_the_builder_and_stays_inside_the_snapshot():
    document = build_for_repo(YTKB, DATA)
    markdown = render_discovery_markdown(document)
    committed = (ROOT / "docs" / "revival" / "SergioMendolia__ytkb.prd.md").read_text(encoding="utf-8")
    assert committed == markdown
    assert [stage["id"] for stage in document["stages"]] == ["revive", "refactor", "upgrade", "productise"]
    assert "not sealed" in committed
    assert "Run hash: absent" not in committed
    blob = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            DATA / "discovery_candidates.csv",
            DATA / "prior_art.csv",
            DATA / "demand_signals.csv",
            ROOT / "data" / "discovery" / "raw" / "snapshot.json",
        )
    )
    for url in re.findall(r"https://[^\s)]+", committed):
        assert url in blob
    for phrase in ("go build -o ytkb", "go install", "Manage images and attachments"):
        assert phrase in committed
        assert phrase in blob
    assert "licence_spdx is empty" in committed
    assert "total_count=10" in committed
    assert "total_count=0" in committed
    assert any(row["metric_value"] == "10" for row in _demand(YTKB))
    assert any(row["metric_value"] == "0" for row in _demand(YTKB))
    with pytest.raises(SystemExit, match="ALREADY_EXISTS"):
        build_for_repo(INVOICE, DATA)


def test_docs_keep_hygen_as_a_once_popular_showcase():
    snapshot = json.loads((ROOT / "data" / "discovery" / "raw" / "snapshot.json").read_text(encoding="utf-8"))
    fork = snapshot["hygen_fork"]
    assert fork["draft"] is True
    assert fork["merged"] is False
    assert fork["u7_run"]["conclusion"] is None
    assert fork["u7_run"]["status"] == "in_progress"
    assert {job["conclusion"] for job in fork["u7_jobs"]} == {"success"}
    messages = [commit["message"] for commit in fork["commits"]]
    assert any(message.startswith("U7:") for message in messages)
    assert not any(message.startswith("U8:") for message in messages)
    prd = (ROOT / "docs" / "PRD.md").read_text(encoding="utf-8")
    architecture = (ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert "5927" in prd
    assert "once-popular" in prd
    assert "not a 0-star idea repository" in prd
    assert fork["pr_url"] in prd
    assert "in_progress" in prd
    assert "conclusion is null" in prd
    assert "ALREADY_EXISTS" in architecture
    assert "own stars" in architecture
