"""Gate 1 reads candidate, prior-art, and demand rows through Zetaris SQL.

The transport is a fake HTTP client. These tests do not start Zetaris.
"""

from __future__ import annotations

import csv
import html
import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from app.adapters.zetaris import ZetarisClient
from app.adapters.zetaris_evidence import ZetarisEvidenceSource, resolve_evidence_source
from app.agents.relevance import RelevanceClient
from app.sql_catalog import (
    DEMAND_COLUMNS,
    DISCOVERY_COLUMNS,
    PRIOR_ART_COLUMNS,
    demand_sql,
    discovery_sql,
    prior_art_sql,
)
from app.tools import Budget, ToolBox
from scripts.evidence_report import events_for_repo, load_events, render_repo_html
from scripts.rerun_unjudged import is_infra_review
from scripts.score_candidates import score_all
from scripts.verify_trace import verify

REPO = "acme/widget"
API_KEY = "zk-test-do-not-leak-bb48"
STALL_PUSH = "2025-01-01T00:00:00Z"
COLLECTED = "2025-10-01T00:00:00Z"
ALT_PUSH = "2025-09-01T00:00:00Z"
STALL_DAYS = (date(2025, 10, 1) - date(2025, 1, 1)).days
ALT_DAYS = (date(2025, 10, 1) - date(2025, 9, 1)).days


def test_resolve_evidence_source_uses_zetaris_when_url_is_set():
    assert resolve_evidence_source({}) == "csv"
    assert resolve_evidence_source({"ZETARIS_URL": "http://zetaris.test/api/v1.0"}) == "zetaris"
    assert resolve_evidence_source({"ZETARIS_REST_URL": "http://zetaris.test/api/v1.0"}) == "zetaris"
    assert (
        resolve_evidence_source(
            {"EVIDENCE_SOURCE": "csv", "ZETARIS_URL": "http://zetaris.test/api/v1.0"}
        )
        == "csv"
    )
    assert resolve_evidence_source({"EVIDENCE_SOURCE": "Zetaris"}) == "zetaris"
    with pytest.raises(ValueError):
        resolve_evidence_source({"EVIDENCE_SOURCE": "sample"})


def test_evidence_source_sends_the_catalog_sql(monkeypatch):
    monkeypatch.delenv("EVIDENCE_SOURCE", raising=False)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        seen.append(body["sql"])
        view = _view(body["sql"])
        columns, rows = _payload(view)
        return httpx.Response(200, json={"headers": list(columns), "data": rows})

    source = ZetarisEvidenceSource(_client(handler))
    bundle = source.fetch(REPO)

    assert seen == [discovery_sql(REPO), prior_art_sql(REPO), demand_sql(REPO)]
    assert bundle.candidate["stargazers_count"] == "4242"
    assert bundle.candidate["description"] != "CSV_ONLY_MARKER"
    assert [q.sql for q in bundle.queries] == seen
    assert [q.row_count for q in bundle.queries] == [1, 1, 0]
    assert all(q.evidence_source == "zetaris" for q in bundle.queries)
    assert all(q.query_id and q.content_hash for q in bundle.queries)
    assert API_KEY not in json.dumps([q.__dict__ for q in bundle.queries])


def test_gate1_success_uses_zetaris_rows_and_records_the_trace(tmp_path, monkeypatch):
    monkeypatch.setenv("ZETARIS_URL", "http://zetaris.test/api/v1.0")
    monkeypatch.delenv("EVIDENCE_SOURCE", raising=False)
    monkeypatch.delenv("ZETARIS_REST_URL", raising=False)
    snap = _snapshot(tmp_path / "snap", stars="1", description="CSV_ONLY_MARKER")
    log_dir = tmp_path / "logs"
    run_id = "zetaris-success"

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert request.headers["Authorization"] == f"Bearer {API_KEY}"
        columns, rows = _payload(_view(body["sql"]))
        return httpx.Response(200, json={"headers": list(columns), "data": rows})

    results = _score(tmp_path, snap, log_dir, run_id, client=_client(handler))
    row = _row(results, REPO)

    assert results["evidence_source"] == "zetaris"
    assert row["evidence_source"] == "zetaris"
    assert row["verdict"] == "ALREADY_EXISTS"
    assert "zetaris-only/alt" in row["reason"]
    assert row["rescue_potential"]["components"]["stars"] == 4242
    assert "CSV_ONLY_MARKER" not in json.dumps(results)
    assert REPO not in {item["repo_id"] for item in results["candidates"]}

    events, path = _events(log_dir, run_id)
    ok, detail = verify(path)
    assert ok, detail
    queries = [e for e in events if e["action"] == "zetaris_query"]
    assert [e["input_summary"]["sql"] for e in queries] == [
        discovery_sql(REPO),
        prior_art_sql(REPO),
        demand_sql(REPO),
    ]
    assert [e["output_summary"]["row_count"] for e in queries] == [1, 1, 0]
    assert all(e["input_summary"]["evidence_source"] == "zetaris" for e in queries)
    assert all(e["status"] == "ok" for e in queries)
    assert API_KEY not in path.read_text(encoding="utf-8")

    loaded = load_events(log_dir, run_ids=[run_id])
    page = render_repo_html(REPO, row["verdict"], row["reason"], events_for_repo(loaded, REPO), run_id=run_id)
    assert "evidence_source" in page
    assert ">zetaris<" in page or "evidence_source: zetaris" in page or ">zetaris</code>" in page
    for statement in (discovery_sql(REPO), prior_art_sql(REPO), demand_sql(REPO)):
        assert html.escape(statement) in page
    assert queries[0]["output_summary"]["query_id"] in page
    assert queries[0]["output_summary"]["content_hash"] in page


def test_unreachable_zetaris_is_needs_review_and_does_not_read_csv(tmp_path, monkeypatch):
    monkeypatch.setenv("EVIDENCE_SOURCE", "zetaris")
    monkeypatch.delenv("ZETARIS_URL", raising=False)
    snap = _snapshot(tmp_path / "snap", stars="9999", description="CSV_ONLY_MARKER")
    log_dir = tmp_path / "logs"
    run_id = "zetaris-down"
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        seen.append(body["sql"])
        raise httpx.ConnectError("connection refused")

    results = _score(tmp_path, snap, log_dir, run_id, client=_client(handler))
    row = _row(results, REPO)

    assert seen == [discovery_sql(REPO)]
    assert row["verdict"] == "NEEDS_REVIEW"
    assert "infra: zetaris" in row["reason"]
    assert row["evidence_source"] == "zetaris"
    assert results["evidence_source"] == "zetaris"
    assert REPO not in {item["repo_id"] for item in results["candidates"]}
    assert "CSV_ONLY_MARKER" not in json.dumps(results)
    assert "9999" not in json.dumps(results)
    assert is_infra_review(row)
    assert API_KEY not in json.dumps(results)

    events, path = _events(log_dir, run_id)
    ok, detail = verify(path)
    assert ok, detail
    failed = [e for e in events if e["action"] == "zetaris_query"]
    assert failed
    assert failed[0]["status"] == "error"
    assert failed[0]["input_summary"]["sql"] == discovery_sql(REPO)
    assert failed[0]["input_summary"]["evidence_source"] == "zetaris"
    assert failed[0]["output_summary"]["row_count"] is None
    assert API_KEY not in path.read_text(encoding="utf-8")


def test_csv_mode_is_explicit_and_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("EVIDENCE_SOURCE", "csv")
    monkeypatch.setenv("ZETARIS_URL", "http://zetaris.test/api/v1.0")
    snap = _snapshot(tmp_path / "snap", stars="1", description="CSV_ONLY_MARKER")
    log_dir = tmp_path / "logs"
    run_id = "csv-explicit"

    class Exploding:
        def run_sql(self, sql: str, limit: int = 100):
            raise AssertionError(f"csv mode called Zetaris: {sql}")

    results = _score(tmp_path, snap, log_dir, run_id, client=Exploding())
    row = _row(results, REPO)

    assert results["evidence_source"] == "csv"
    assert row["evidence_source"] == "csv"
    assert row["verdict"] != "CANDIDATE"
    assert "CSV_ONLY_MARKER" in json.dumps(results) or row["verdict"] == "NO_GO"
    assert "infra: zetaris" not in row["reason"]

    events, path = _events(log_dir, run_id)
    ok, detail = verify(path)
    assert ok, detail
    assert not [e for e in events if e["action"] == "zetaris_query"]
    recorded = [
        e
        for e in events
        if (e.get("input_summary") if isinstance(e.get("input_summary"), dict) else {}).get("evidence_source") == "csv"
        or (e.get("output_summary") if isinstance(e.get("output_summary"), dict) else {}).get("evidence_source") == "csv"
    ]
    assert recorded
    assert API_KEY not in path.read_text(encoding="utf-8")

    loaded = load_events(log_dir, run_ids=[run_id])
    page = render_repo_html(REPO, row["verdict"], row["reason"], events_for_repo(loaded, REPO), run_id=run_id)
    assert "evidence_source" in page
    assert "csv" in page


def _client(handler) -> ZetarisClient:
    return ZetarisClient(
        rest_url="http://zetaris.test/api/v1.0",
        org_id="1",
        api_key=API_KEY,
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )


def _score(tmp_path: Path, snap: Path, log_dir: Path, run_id: str, client) -> dict:
    return score_all(
        snapshot=snap,
        output_json=tmp_path / "scores.json",
        output_md=tmp_path / "scores.md",
        use_llm=False,
        run_id=run_id,
        log_dir=log_dir,
        custom_toolbox=ToolBox(tools={}, budget=Budget(max_calls=4, max_seconds=5)),
        command_str="score_candidates.py --test",
        relevance_client=RelevanceClient(token="", cache_dir=tmp_path / "rel"),
        zetaris_client=client,
    )


def _row(results: dict, repo_id: str) -> dict:
    for bucket in ("ineligible", "candidates"):
        for item in results.get(bucket) or []:
            if item.get("repo_id") == repo_id:
                return item
    raise AssertionError(f"{repo_id} missing from {list(results)}")


def _events(log_dir: Path, run_id: str) -> tuple[list[dict], Path]:
    path = log_dir / f"trace-{run_id}.jsonl"
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        for key in ("input_summary", "output_summary"):
            value = record.get(key)
            if isinstance(value, str) and value[:1] in "{[":
                try:
                    record[key] = json.loads(value)
                except json.JSONDecodeError:
                    pass
        events.append(record)
    return events, path


def _view(sql: str) -> str:
    for name in ("discovery_candidates", "prior_art", "demand_signals"):
        if f"phoenix.{name}" in sql:
            return name
    raise AssertionError(f"unexpected SQL: {sql}")


def _payload(view: str) -> tuple[tuple[str, ...], list[list[str]]]:
    if view == "discovery_candidates":
        row = _candidate_row()
        return DISCOVERY_COLUMNS, [[row[column] for column in DISCOVERY_COLUMNS]]
    if view == "prior_art":
        row = _prior_row()
        return PRIOR_ART_COLUMNS, [[row[column] for column in PRIOR_ART_COLUMNS]]
    row = {column: "" for column in DEMAND_COLUMNS}
    return DEMAND_COLUMNS, [] if not row else []


def _candidate_row() -> dict[str, str]:
    problem = "generate invoices as pdf"
    user = "freelancers who bill clients"
    scope = "a web form that writes a pdf"
    readme = f"{problem}. For {user}. Scope: {scope}. invoice and pdf"
    return {
        "repo_id": REPO,
        "html_url": "https://github.com/acme/widget",
        "description": readme,
        "readme_excerpt": readme,
        "problem_excerpt": problem,
        "user_excerpt": user,
        "scope_excerpt": scope,
        "code_paths": "src/app.py|src/pdf.py",
        "created_at": "2024-01-01T00:00:00Z",
        "pushed_at": STALL_PUSH,
        "days_since_push": str(STALL_DAYS),
        "stargazers_count": "4242",
        "forks_count": "3",
        "open_issues": "1",
        "licence_spdx": "MIT",
        "core_terms": "invoice|pdf",
        "cohort": "discovery",
        "data_origin": "zetaris-test",
        "collected_at": COLLECTED,
    }


def _prior_row() -> dict[str, str]:
    return {
        "repo_id": REPO,
        "alternative_id": "zetaris-only/alt",
        "url": "https://example.com/zetaris-only",
        "licence_spdx": "MIT",
        "stars": "200",
        "pushed_at": ALT_PUSH,
        "days_since_push": str(ALT_DAYS),
        "excerpt": "invoice pdf tool",
        "candidate_excerpt": "invoice pdf",
        "required_terms": "invoice|pdf",
        "data_origin": "zetaris-test",
        "collected_at": COLLECTED,
    }


def _snapshot(directory: Path, *, stars: str, description: str) -> Path:
    directory.mkdir(parents=True)
    candidate = {column: "" for column in DISCOVERY_COLUMNS}
    candidate.update(
        {
            "repo_id": REPO,
            "html_url": "https://github.com/acme/widget",
            "description": description,
            "stargazers_count": stars,
            "forks_count": "0",
            "open_issues": "0",
            "days_since_push": "1",
            "cohort": "discovery",
            "data_origin": "csv-snapshot",
        }
    )
    _write(directory / "discovery_candidates.csv", DISCOVERY_COLUMNS, [candidate])
    _write(directory / "prior_art.csv", PRIOR_ART_COLUMNS, [])
    _write(directory / "demand_signals.csv", DEMAND_COLUMNS, [])
    return directory


def _write(path: Path, columns: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})
