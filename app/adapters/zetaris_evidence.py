"""Gate 1 evidence from Zetaris phoenix views.

The statements are the single-table SELECTs in ``app.sql_catalog``. They go
through a client with ``run_sql`` — the REST ``ZetarisClient`` in this repo.
There is no JDBC client here. A failed call does not read the CSV snapshot.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from app.errors import ZetarisError
from app.sql_catalog import demand_sql, discovery_sql, prior_art_sql

UNREACHABLE_REASON = (
    "infra: zetaris: unreachable. Gate 1 did not fall back to CSV and did not return CANDIDATE."
)


def missing_candidate_reason(repo_id: str) -> str:
    return (
        f"infra: zetaris: phoenix.discovery_candidates returned no row for {repo_id}. "
        "Gate 1 did not fall back to CSV and did not return CANDIDATE."
    )


def resolve_evidence_source(env: Mapping[str, str] | None = None) -> str:
    """``zetaris`` when a sponsor URL is set, unless ``EVIDENCE_SOURCE=csv`` is explicit.

    ``ZETARIS_URL`` is the REST base (the same value as ``ZETARIS_REST_URL``,
    the prefix before ``/sql-editor/sqls/run``). The Settings default URL does
    not count: only a variable that is actually present does.
    """

    source = os.environ if env is None else env
    explicit = str(source.get("EVIDENCE_SOURCE") or "").strip().lower()
    if explicit:
        if explicit not in {"zetaris", "csv"}:
            raise ValueError("EVIDENCE_SOURCE must be zetaris or csv.")
        return explicit
    if str(source.get("ZETARIS_URL") or "").strip():
        return "zetaris"
    if str(source.get("ZETARIS_REST_URL") or "").strip():
        return "zetaris"
    return "csv"


@dataclass
class QueryRecord:
    repo_id: str
    evidence_source: str
    sql: str
    row_count: int | None
    query_id: str
    content_hash: str
    status: str
    error: str = ""
    view: str = ""


@dataclass
class RepoBundle:
    repo_id: str
    candidate: dict[str, str] | None
    prior_rows: list[dict[str, str]] = field(default_factory=list)
    demand_rows: list[dict[str, str]] = field(default_factory=list)
    queries: list[QueryRecord] = field(default_factory=list)


class ZetarisUnavailable(Exception):
    """The selected Zetaris client did not answer. CSV is not a substitute."""

    def __init__(self, message: str, *, repo_id: str, queries: list[QueryRecord]) -> None:
        super().__init__(message)
        self.repo_id = repo_id
        self.queries = queries


class ZetarisPreflightError(RuntimeError):
    """Zetaris was selected for this run and did not answer one SELECT before any repository.

    Raised once, before judging, so a stopped Docker stack is one clear error instead of a
    NEEDS_REVIEW row for every repository in the batch.
    """


def preflight_message(url: str, detail: str) -> str:
    where = url or "the configured ZETARIS_URL / ZETARIS_REST_URL"
    return (
        f"Zetaris is the evidence source for this run (EVIDENCE_SOURCE=zetaris, or ZETARIS_URL / "
        f"ZETARIS_REST_URL is set) but it did not answer at {where}: {detail}. Nothing was judged. "
        "Start the Zetaris stack and retry, or set EVIDENCE_SOURCE=csv to judge from the snapshot "
        "CSVs (every row is then recorded as evidence_source=csv)."
    )


def preflight(client: Any, repo_ids: list[str]) -> QueryRecord:
    """Send one read-only catalog SELECT. Return its record, or raise ZetarisPreflightError.

    The statement is discovery_sql(<first cohort id>): a single-table SELECT with one
    predicate, the same text a judged repository uses, so it needs no extra permission and
    the sample SQL cache answers it too.
    """
    url = str(getattr(client, "rest_url", "") or "") if client is not None else ""
    if client is None:
        raise ZetarisPreflightError(preflight_message(url, "no Zetaris client could be built (is ZETARIS_API_KEY set?)"))
    probe_id = next((rid for rid in repo_ids if _safe_discovery_sql(rid)), "")
    if not probe_id:
        raise ZetarisPreflightError(preflight_message(url, "the snapshot names no valid repo_id to query"))
    sql = discovery_sql(probe_id)
    try:
        result = client.run_sql(sql, limit=1)
    except Exception as exc:  # noqa: BLE001 — any failure here means the source is not usable
        raise ZetarisPreflightError(preflight_message(url, f"{type(exc).__name__}: {str(exc)[:200]}")) from exc
    return QueryRecord(
        repo_id=probe_id,
        evidence_source="zetaris",
        sql=result.sql or sql,
        row_count=len(result.rows),
        query_id=str(result.query_id or ""),
        content_hash=str(result.content_hash or ""),
        status="ok",
        view="phoenix.discovery_candidates",
    )


class ZetarisEvidenceSource:
    """Fetch one repository's gate-1 rows with the catalog SQL."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def fetch(self, repo_id: str) -> RepoBundle:
        queries: list[QueryRecord] = []
        statements = (
            ("phoenix.discovery_candidates", discovery_sql(repo_id)),
            ("phoenix.prior_art", prior_art_sql(repo_id)),
            ("phoenix.demand_signals", demand_sql(repo_id)),
        )
        results = []
        for view, sql in statements:
            try:
                result = self.client.run_sql(sql)
            except (ZetarisError, OSError, TimeoutError) as exc:
                queries.append(
                    QueryRecord(
                        repo_id=repo_id,
                        evidence_source="zetaris",
                        sql=sql,
                        row_count=None,
                        query_id="",
                        content_hash="",
                        status="error",
                        error="unreachable",
                        view=view,
                    )
                )
                raise ZetarisUnavailable("unreachable", repo_id=repo_id, queries=queries) from exc
            queries.append(
                QueryRecord(
                    repo_id=repo_id,
                    evidence_source="zetaris",
                    sql=result.sql or sql,
                    row_count=len(result.rows),
                    query_id=str(result.query_id or ""),
                    content_hash=str(result.content_hash or ""),
                    status="ok",
                    view=view,
                )
            )
            results.append(result)
        candidate_rows = _matching(results[0].rows, repo_id)
        return RepoBundle(
            repo_id=repo_id,
            candidate=candidate_rows[0] if candidate_rows else None,
            prior_rows=_matching(results[1].rows, repo_id),
            demand_rows=_matching(results[2].rows, repo_id),
            queries=queries,
        )


def cohort_repo_ids(data_dir: Path) -> list[str]:
    """Repository ids named by the snapshot. Row bodies are not evidence."""

    path = data_dir / "discovery_candidates.csv"
    if not path.exists():
        return []
    try:
        csv.field_size_limit(10 * 1024 * 1024)
    except OverflowError:
        pass
    ids: list[str] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            repo_id = str((row or {}).get("repo_id") or "").strip()
            if repo_id:
                ids.append(repo_id)
    return ids


def build_live_client(env: Mapping[str, str] | None = None, transport: Any | None = None) -> Any:
    """REST client. The API key is passed to the client and is not returned or logged."""

    from app.adapters.zetaris import ZetarisClient
    from config.settings import Settings, usable_secret

    source = os.environ if env is None else env
    settings = Settings()
    url = (
        str(source.get("ZETARIS_URL") or "").strip()
        or str(source.get("ZETARIS_REST_URL") or "").strip()
        or str(settings.zetaris_rest_url or "").strip()
    )
    org = str(source.get("ZETARIS_ORG_ID") or "").strip() or str(settings.zetaris_org_id or "1")
    key = settings.zetaris_api_key or usable_secret(str(source.get("ZETARIS_API_KEY") or ""))
    return ZetarisClient(
        rest_url=url,
        org_id=org,
        api_key=key or "",
        timeout_seconds=float(settings.zetaris_timeout_seconds),
        transport=transport,
    )


def load_zetaris_cohort(
    data_dir: Path,
    client: Any | None = None,
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]], list[QueryRecord], list[dict[str, str]]]:
    """Query each cohort id. A failure becomes a blocked row, never a CSV read."""

    repo_ids = cohort_repo_ids(data_dir)
    if client is None:
        try:
            client = build_live_client()
        except Exception:
            client = None
    if client is None:
        if repo_ids:
            raise ZetarisPreflightError(preflight_message("", "no Zetaris client could be built (is ZETARIS_API_KEY set?)"))
        return _blocked_without_client(repo_ids)
    preflight_record = preflight(client, repo_ids) if repo_ids else None

    source = ZetarisEvidenceSource(client)
    candidates: list[dict[str, str]] = []
    prior: list[dict[str, str]] = []
    demand: list[dict[str, str]] = []
    queries: list[QueryRecord] = []
    if preflight_record is not None:
        preflight_record.status = "preflight"
        queries.append(preflight_record)
    blocked: list[dict[str, str]] = []
    for repo_id in repo_ids:
        try:
            bundle = source.fetch(repo_id)
        except ZetarisUnavailable as exc:
            queries.extend(exc.queries)
            blocked.append({"repo_id": repo_id, "reason": UNREACHABLE_REASON})
            continue
        except Exception:
            sql = _safe_discovery_sql(repo_id)
            queries.append(
                QueryRecord(
                    repo_id=repo_id,
                    evidence_source="zetaris",
                    sql=sql,
                    row_count=None,
                    query_id="",
                    content_hash="",
                    status="error",
                    error="unreachable",
                    view="phoenix.discovery_candidates",
                )
            )
            blocked.append({"repo_id": repo_id, "reason": UNREACHABLE_REASON})
            continue
        queries.extend(bundle.queries)
        if not bundle.candidate:
            blocked.append({"repo_id": repo_id, "reason": missing_candidate_reason(repo_id)})
            continue
        candidates.append(bundle.candidate)
        prior.extend(bundle.prior_rows)
        demand.extend(bundle.demand_rows)
    return candidates, prior, demand, queries, blocked


def _blocked_without_client(repo_ids: list[str]) -> tuple[
    list[dict[str, str]],
    list[dict[str, str]],
    list[dict[str, str]],
    list[QueryRecord],
    list[dict[str, str]],
]:
    queries: list[QueryRecord] = []
    blocked: list[dict[str, str]] = []
    for repo_id in repo_ids:
        queries.append(
            QueryRecord(
                repo_id=repo_id,
                evidence_source="zetaris",
                sql=_safe_discovery_sql(repo_id),
                row_count=None,
                query_id="",
                content_hash="",
                status="error",
                error="unreachable",
                view="phoenix.discovery_candidates",
            )
        )
        blocked.append({"repo_id": repo_id, "reason": UNREACHABLE_REASON})
    return [], [], [], queries, blocked


def _safe_discovery_sql(repo_id: str) -> str:
    try:
        return discovery_sql(repo_id)
    except ValueError:
        return ""


def _matching(rows: list[dict[str, Any]], repo_id: str) -> list[dict[str, str]]:
    matched: list[dict[str, str]] = []
    for row in rows:
        text = {str(key): "" if value is None else str(value) for key, value in row.items()}
        if str(text.get("repo_id") or "") == repo_id:
            matched.append(text)
    return matched
