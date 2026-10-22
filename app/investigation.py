"""Multi-agent revival investigation. Zetaris is the only evidence source."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from app.adapters.sample_zetaris import SampleZetaris
from app.adapters.zetaris import ZetarisClient, ZetarisResult
from app.agents.planner import (
    attempt_evidence_id,
    build_revival_prd,
    evidence_context,
    label_prd,
    prd_basename,
    render_json,
    render_markdown,
    source_evidence_id,
)
from app.agents.policy import (
    apply_revival_attempts,
    decide,
    explanation,
    normalize_attempt,
    normalize_profile,
    normalize_signal,
    normalize_source,
)
from app.agents.llm_judge import call_llm_judge, combine
from app.agents.validate import Critique, critique_archaeologist, critique_builder, critique_judge, critique_planner
from app.engines.hmem.ledger import TrustLedgerService
from app.engines.hmem.retrieval import MemoryRetrievalService
from app.engines.hmem.store import MemoryStoreService
from app.engines.hmem.types import QueryResult
from app.engines.markovian import ChunkManager, MarkovianConfig
from app.errors import InvestigationError, ZetarisError
from app.adapters.meterless import MeterlessError
from app.llm.parsing import parse_model_json
from app.llm.provider import BoundLLM, build_llm
from app.llm.standin import SampleStandIn
from app.paths import repo_root
from app.sql_catalog import attempts_sql, profile_sql, require_repo_id, signals_sql, sources_sql
from app.trace_log import HackathonTrace
from artifacts.store import ArtifactStore
from audit.trail import AuditTrail
from config.settings import Settings
from config.versions import INVESTIGATION_VERSION, PIPELINE_VERSION
from evidence.factory import create_evidence
from evidence.graph import EvidenceGraphEngine
from evidence.models import Claim
from run.context import RunContext
from state.machine import PhoenixState, StateMachine


def _judge_failure_kind(exc: Exception) -> str:
    status = getattr(exc, "status_code", None)
    text = str(exc).lower()
    if status == 401 or "unauthorized" in text or getattr(exc, "error_type", "") == "MeterlessUnauthorized":
        return "unauthorized"
    if isinstance(exc, TimeoutError) or "timed out" in text or "timeout" in text:
        return "timeout"
    return "error"


class InvestigationOrchestrator:
    def __init__(
        self,
        repository: str,
        mode: str,
        max_repair_iterations: int,
        settings: Settings,
        *,
        zetaris: SampleZetaris | ZetarisClient | None = None,
        llm: BoundLLM | None = None,
        hmem_path: Path | None = None,
    ) -> None:
        self.repository = require_repo_id(repository)
        self.mode = mode
        self.max_repair_iterations = max_repair_iterations
        self.settings = settings
        self.sample = mode == "sample"
        if zetaris is None:
            zetaris = self._build_zetaris()
        if zetaris is None:
            raise InvestigationError("ZetarisRequired", "Removing Zetaris stops the investigation.", 500)
        self.zetaris = zetaris
        self.llm = llm or build_llm(settings, sample=self.sample)
        self.context = RunContext.create(self.repository, mode, max_repair_iterations)
        self.context.foundation_only = False
        self.run_id = self.context.run_id
        self.trace = HackathonTrace(self.run_id, settings.log_dir, settings.secret_values())
        self.audit = AuditTrail(self.run_id)
        self.graph = EvidenceGraphEngine(self.run_id)
        self.state = StateMachine(PhoenixState.INGESTED, audit_trail=self.audit)
        self.artifacts = ArtifactStore()
        store_path = hmem_path if hmem_path is not None else Path(settings.hmem_store_path)
        ledger_path = store_path.with_suffix(".ledger.jsonl")
        clock = lambda: int(time.time() * 1000)
        self.hmem_clock = clock
        self.ledger = TrustLedgerService(ledger_path, clock=clock)
        self.memory = MemoryStoreService(ledger=self.ledger, path=store_path, clock=clock)
        self.retrieval = MemoryRetrievalService(self.memory)
        self.chunks = ChunkManager(
            MarkovianConfig(
                max_chunks=settings.markovian_max_chunks,
                max_carryover_chars=1200,
                chunk_timeout_seconds=settings.agent_step_timeout_seconds,
            )
        )
        self.started = time.monotonic()
        self.queries: list[dict[str, str]] = []
        self.upgrade: dict[str, Any] = {
            "changes_verdict": False,
            "note": "Refactor and upgrade stages do not change the verdict. The verdict uses stage revive only.",
            "stages": [],
        }
        self.revival_prd: dict[str, Any] = {}
        self.critiques: list[dict[str, Any]] = []
        self.judge_events: list[dict[str, Any]] = []
        self.judge_failure = ""
        self.judge_exhausted = False
        self.provider_degraded = False

    def run(self) -> dict[str, Any]:
        self.trace.event(
            agent_name="orchestrator",
            action="RUN_STARTED",
            input_summary={"repository": self.repository, "mode": self.mode},
            output_summary={"investigation_version": INVESTIGATION_VERSION},
            target_agent="archaeologist",
        )
        self.memory.hydrate()
        profile_result, signal_result, attempt_result, source_result, row, signals, attempts, sources = self._collect()
        profile_evidence_id = self._add_row_evidence(profile_result, row, "repository")
        signal_evidence_ids = [
            self._add_signal_evidence(signal_result, signal, index)
            for index, signal in enumerate(signals)
        ]
        attempt_evidence_ids = self._add_attempt_evidence(attempt_result, attempts)
        source_evidence_ids = self._add_source_evidence(source_result, sources)
        known = {profile_evidence_id, *signal_evidence_ids, *attempt_evidence_ids, *source_evidence_ids}
        priors = self._prior_verdicts(row["repo_id"])
        prior_evidence_ids = [self._add_prior_evidence(item) for item in priors]
        known.update(prior_evidence_ids)

        claims = self._archaeologist(row, profile_evidence_id, known)
        self._remember_claims(claims, row)
        recalled = self._recall_evidence(row)
        if not recalled.memories:
            raise InvestigationError(
                "HMemRecallFailed",
                "H-MEM retrieval returned no evidence memories after they were stored.",
                500,
            )
        judgment = self._judge(row, signals, known, recalled)
        decision, policy_reason, self.upgrade = apply_revival_attempts(row, signals, attempts)
        self._advance(PhoenixState.RECONNAISSANCE_COMPLETE, "Repository row loaded from Zetaris.", [profile_evidence_id])
        self._advance(PhoenixState.EVIDENCE_COMPLETE, "Profile and revival signals loaded.", list(known))
        self._advance(PhoenixState.INTENT_RECONSTRUCTED, "Claims are tied to Zetaris rows.", [profile_evidence_id])
        self._advance(PhoenixState.MARKET_ANALYZED, policy_reason, [profile_evidence_id])
        self._advance(PhoenixState.VALUE_DECISION, decision, [profile_evidence_id])

        repairs_used = 0
        if decision != "NO_GO":
            self._architect(row, profile_evidence_id)
            self._builder(row, decision, known)
            self._advance(PhoenixState.PRODUCT_BLUEPRINT_COMPLETE, "Revival contract drafted.", [profile_evidence_id])
            self._advance(PhoenixState.ENGINEERING_RESEARCH_COMPLETE, "Evidence policy checked.", list(known))
            self._advance(PhoenixState.IMPLEMENTATION_CONTRACT_READY, "Builder output accepted.", [profile_evidence_id])
            self._advance(PhoenixState.BUILDING, "No upstream code is modified.", [profile_evidence_id])
            self._advance(PhoenixState.VALIDATING, "Validator compared the contract to the rows.", list(known))
            while decision == "RESURRECTION_FAILED" and repairs_used < self.max_repair_iterations:
                self._advance(PhoenixState.DIAGNOSING, policy_reason, list(known))
                signal_result, signals = self._diagnostician(row, policy_reason, known)
                for index, signal in enumerate(signals):
                    evidence_id = self._add_signal_evidence(signal_result, signal, index + len(signal_evidence_ids))
                    signal_evidence_ids.append(evidence_id)
                    known.add(evidence_id)
                decision, policy_reason, self.upgrade = apply_revival_attempts(row, signals, attempts)
                repairs_used += 1
                self._advance(PhoenixState.REPAIRING, "Signals re-queried from Zetaris.", list(known))
                self._advance(PhoenixState.VALIDATING, policy_reason, list(known))
            if decision == "RESURRECTION_FAILED":
                self._advance(PhoenixState.RESURRECTION_FAILED, policy_reason, list(known))
            elif decision == "RESURRECTED":
                self._advance(PhoenixState.RESURRECTED, policy_reason, list(known))
            elif decision == "CANDIDATE":
                self._advance(PhoenixState.CANDIDATE, policy_reason, list(known))
            else:
                self._advance(PhoenixState.NO_GO, policy_reason, [profile_evidence_id])
            self._planner(row, signals, attempts, sources, known)
        else:
            self._advance(PhoenixState.NO_GO, policy_reason, [profile_evidence_id])

        row_decision, _row_reason = decide(row, signals)
        if judgment.get("accepted") and row_decision == decision:
            verdict_text = judgment["explanation"]
            if "not yet been attempted" in policy_reason and "not yet been attempted" not in verdict_text:
                verdict_text = verdict_text.rstrip() + " Revival has not yet been attempted."
        else:
            verdict_text = explanation(row, decision, policy_reason)
        judge_note = self._judge_verdict_note()
        if judge_note:
            verdict_text = verdict_text.rstrip() + " " + judge_note
        decision_claim = self._add_decision_claim(verdict_text, list(known))
        verification = self.graph.verify_claim(decision_claim.id)
        self.trace.event(
            agent_name="validator",
            action="CLAIM_VERIFIED",
            input_summary={"claim_id": decision_claim.id},
            output_summary=verification,
            confidence=verification["confidence"],
            status="ok",
        )
        memory = self._remember_verdict(row, decision, verdict_text, list(known))
        pack = self._export(row, decision)
        self.trace.event(
            agent_name="orchestrator",
            action="VERDICT",
            input_summary={"decision": decision},
            output_summary={"reason": policy_reason, "memory_id": memory.id},
            confidence=verification["confidence"],
            status="ok",
        )
        run_hash = self.trace.seal()
        requested = str(getattr(self.llm, "requested_model", "") or "")
        served = getattr(self.llm, "served_model", None)
        verdict_text = (
            verdict_text.rstrip()
            + f" Run hash {run_hash}."
            + f" Requested model {requested or 'empty'}."
            + f" Served model {served or 'empty'}."
        )
        self._stamp_run_hash(run_hash)
        return self._response(
            decision=decision,
            reason=policy_reason,
            verdict_text=verdict_text,
            verification=verification,
            pack=pack,
            recalled=recalled,
            priors=priors,
            verdict_memory_id=memory.id,
            model_accepted=bool(judgment.get("accepted")),
            run_hash=run_hash,
        )

    def _build_zetaris(self):
        if self.sample:
            return SampleZetaris()
        if not self.settings.zetaris_api_key:
            raise InvestigationError(
                "ZetarisNotConfigured",
                "Live mode requires ZETARIS_API_KEY. There is no cache fallback.",
                400,
            )
        return ZetarisClient(
            rest_url=self.settings.zetaris_rest_url,
            org_id=self.settings.zetaris_org_id,
            api_key=self.settings.zetaris_api_key,
            timeout_seconds=self.settings.zetaris_timeout_seconds,
        )

    def _collect(self):
        self.trace.event(
            agent_name="archaeologist",
            action="EVIDENCE_COLLECTION_STARTED",
            input_summary={"repository": self.repository},
            target_agent="zetaris",
        )
        profile_result = self._query(profile_sql(self.repository))
        signal_result = self._query(signals_sql(self.repository))
        attempt_result = self._query(attempts_sql(self.repository))
        source_result = self._query(sources_sql(self.repository))
        if not profile_result.rows:
            raise InvestigationError(
                "RepositoryNotInZetaris",
                f"phoenix.repo_evidence has no row for {self.repository}.",
                404,
            )
        row = normalize_profile(profile_result.rows[0])
        signals = [normalize_signal(item) for item in signal_result.rows]
        attempts = [normalize_attempt(item) for item in attempt_result.rows]
        sources = [normalize_source(item) for item in source_result.rows]
        self.profile_result = profile_result
        self.signal_result = signal_result
        self.attempt_result = attempt_result
        self.source_result = source_result
        self.trace.event(
            agent_name="archaeologist",
            action="EVIDENCE_COLLECTION_COMPLETED",
            output_summary={
                "profile_query_id": profile_result.query_id,
                "signal_query_id": signal_result.query_id,
                "attempt_query_id": attempt_result.query_id,
                "source_query_id": source_result.query_id,
                "signal_rows": len(signals),
                "attempt_rows": len(attempts),
                "source_rows": len(sources),
            },
            target_agent="validator",
        )
        return profile_result, signal_result, attempt_result, source_result, row, signals, attempts, sources

    def _query(self, sql: str) -> ZetarisResult:
        self._check_run_budget()
        try:
            result = self.zetaris.run_sql(sql)
        except ZetarisError:
            raise
        except InvestigationError:
            raise
        self.queries.append(
            {
                "query_id": result.query_id,
                "sql": result.sql,
                "source": result.source,
                "mode": result.mode,
                "content_hash": result.content_hash,
            }
        )
        self.trace.event(
            agent_name="archaeologist",
            action="ZETARIS_QUERY",
            input_summary=sql,
            output_summary={
                "query_id": result.query_id,
                "rows": len(result.rows),
                "source": result.source,
                "content_hash": result.content_hash,
            },
            target_agent="validator",
        )
        return result

    def _add_row_evidence(self, result: ZetarisResult, row: dict, evidence_type: str) -> str:
        evidence_id = "EV-PROFILE-" + _slug(row["repo_id"])
        evidence = create_evidence(
            evidence_id=evidence_id,
            evidence_type=evidence_type,
            source=result.source,
            locator=result.query_id,
            content=json.dumps(row, ensure_ascii=False, sort_keys=True),
            collector="zetaris_adapter",
            metadata=self._evidence_metadata(result),
        )
        self.graph.add_evidence(evidence)
        return evidence_id

    def _add_signal_evidence(self, result: ZetarisResult, signal: dict, index: int) -> str:
        evidence_id = f"EV-SIGNAL-{_slug(signal['repo_id'])}-{index}-{_slug(signal['signal'])}"
        evidence = create_evidence(
            evidence_id=evidence_id,
            evidence_type="test",
            source=result.source,
            locator=result.query_id,
            content=json.dumps(signal, ensure_ascii=False, sort_keys=True),
            collector="zetaris_adapter",
            metadata=self._evidence_metadata(result),
        )
        self.graph.add_evidence(evidence)
        return evidence_id

    def _add_json_evidence(self, evidence_id: str, result: ZetarisResult, payload: dict) -> str:
        evidence = create_evidence(
            evidence_id=evidence_id,
            evidence_type="external",
            source=result.source,
            locator=result.query_id,
            content=json.dumps(payload, ensure_ascii=False, sort_keys=True),
            collector="zetaris_adapter",
            metadata=self._evidence_metadata(result),
        )
        self.graph.add_evidence(evidence)
        return evidence_id

    def _add_attempt_evidence(self, result: ZetarisResult, attempts: list[dict]) -> list[str]:
        if not attempts:
            return [self._add_json_evidence(attempt_evidence_id(self.repository, "none"), result, {"rows": 0})]
        return [
            self._add_json_evidence(attempt_evidence_id(attempt["repo_id"], attempt["stage"]), result, attempt)
            for attempt in attempts
        ]

    def _add_source_evidence(self, result: ZetarisResult, sources: list[dict]) -> list[str]:
        if not sources:
            return [self._add_json_evidence(source_evidence_id(self.repository, "none"), result, {"rows": 0})]
        return [
            self._add_json_evidence(source_evidence_id(source["repo_id"], source["source_id"]), result, source)
            for source in sources
        ]

    def _planner(self, row: dict, signals: list[dict], attempts: list[dict], sources: list[dict], known: set[str]) -> None:
        context = evidence_context(
            row["repo_id"],
            signals,
            attempts,
            sources,
            queries={
                "profile": {
                    "query_id": self.profile_result.query_id,
                    "sql": self.profile_result.sql,
                    "content_hash": self.profile_result.content_hash,
                },
                "signals": {
                    "query_id": self.signal_result.query_id,
                    "sql": self.signal_result.sql,
                    "content_hash": self.signal_result.content_hash,
                },
                "attempts": {
                    "query_id": self.attempt_result.query_id,
                    "sql": self.attempt_result.sql,
                    "content_hash": self.attempt_result.content_hash,
                },
                "sources": {
                    "query_id": self.source_result.query_id,
                    "sql": self.source_result.sql,
                    "content_hash": self.source_result.content_hash,
                },
            },
        )
        catalog = _evidence_catalog(context)
        document = build_revival_prd(row, signals, attempts, sources, context)
        accepted = None
        critique = Critique(False)
        for attempt in (1, 2):
            payload = self.chunks.run(
                f"planner-{attempt}",
                lambda carryover, attempt=attempt, document=document: self._role_chunk(
                    "planner",
                    attempt,
                    {"deterministic_prd": document, "row": row},
                    carryover,
                    target="validator",
                ),
            )
            critique = critique_planner(
                payload,
                known_evidence_ids=known,
                row=row,
                evidence_by_id=catalog,
                evidence_text=_planner_evidence_text(row, signals, attempts, sources),
            )
            if not critique.rejected:
                accepted = label_prd(payload, model_produced=not self.llm.sample_standin)
                break
            self._record_critique(critique, attempt - 1)
        if accepted is None:
            accepted = label_prd(document, model_produced=False)
            self.trace.event(
                agent_name="validator",
                action="PLANNER_DETERMINISTIC_FALLBACK",
                input_summary=critique.reason,
                output_summary="The deterministic PRD replaced the rejected draft.",
                target_agent="planner",
                status="ok",
            )
        try:
            self.revival_prd = self._write_prd(accepted)
        except (KeyError, TypeError) as exc:
            accepted = label_prd(document, model_produced=False)
            self.trace.event(
                agent_name="validator",
                action="PLANNER_DETERMINISTIC_FALLBACK",
                input_summary=f"{type(exc).__name__}: {exc}",
                output_summary="The deterministic PRD replaced a draft that could not be rendered.",
                target_agent="planner",
                status="ok",
            )
            self.revival_prd = self._write_prd(accepted)

    def _write_prd(self, document: dict) -> dict[str, Any]:
        import os

        override = os.environ.get("PHOENIX_PRD_DIR")
        if override:
            directory = Path(override)
        else:
            directory = Path(self.settings.log_dir) / "revival" / self.run_id
        markdown_path = ""
        json_path = ""
        if directory is not None:
            directory.mkdir(parents=True, exist_ok=True)
            base = prd_basename(str(document["repo_id"]))
            markdown_file = directory / f"{base}.prd.md"
            json_file = directory / f"{base}.prd.json"
            markdown_file.write_text(render_markdown(document), encoding="utf-8")
            json_file.write_text(render_json(document), encoding="utf-8")
            markdown_path = str(markdown_file)
            json_path = str(json_file)
        self.trace.event(
            agent_name="planner",
            action="PRD_WRITTEN",
            output_summary={
                "drafted_by": document.get("drafted_by"),
                "llm_drafted": document.get("llm_drafted"),
                "markdown_path": markdown_path,
                "json_path": json_path,
            },
            status="ok",
        )
        return {
            "drafted_by": document.get("drafted_by"),
            "llm_drafted": document.get("llm_drafted"),
            "markdown_path": markdown_path,
            "json_path": json_path,
            "stages": [stage["id"] for stage in document.get("stages", [])],
            "document": document,
        }

    def _evidence_metadata(self, result: ZetarisResult) -> dict[str, str]:
        return {
            "query_id": result.query_id,
            "sql": result.sql,
            "content_hash": result.content_hash,
            "source": result.source,
            "zetaris_mode": result.mode,
        }

    def _archaeologist(self, row: dict, profile_evidence_id: str, known: set[str]) -> list[dict]:
        accepted: list[dict] | None = None
        evidence_backed: list[dict] | None = None
        critique = Critique(False)
        for attempt in (1, 2):
            payload = self.chunks.run(
                f"archaeologist-{attempt}",
                lambda carryover, attempt=attempt: self._role_chunk(
                    "archaeologist",
                    attempt,
                    {"row": row, "profile_evidence_id": profile_evidence_id},
                    carryover,
                    target="validator",
                ),
            )
            guard = critique_archaeologist(payload, known_evidence_ids=known, row=row)
            critique = self._apply_llm_judge(
                guard,
                role="archaeologist",
                subject={"role": "archaeologist", "row": row, "claims": payload.get("claims")},
                attempt=attempt,
            )
            if not guard.rejected:
                evidence_backed = payload["claims"]
            if not critique.rejected:
                accepted = payload["claims"]
                break
            self._record_critique(critique, attempt - 1)
        if accepted is None and evidence_backed is not None:
            self._note_judge_exhausted("archaeologist")
            accepted = evidence_backed
        if accepted is None:
            raise InvestigationError("ValidatorBlocked", critique.reason or "Archaeologist claims were rejected.", 422)
        for claim in accepted:
            model = Claim(
                id=claim["id"],
                statement=claim["statement"],
                confidence=float(claim.get("confidence", 0.5)),
            )
            self.graph.add_claim(model)
            for evidence_id in claim["evidence_ids"]:
                self.graph.link_support(claim["id"], evidence_id)
        return accepted

    def _judge(self, row: dict, signals: list[dict], known: set[str], recalled: QueryResult) -> dict:
        memories = [
            {"id": item.memory.id, "content": item.memory.content, "score": item.relevance}
            for item in recalled.memories
        ]
        payload_in = {
            "row": row,
            "signals": signals,
            "evidence_ids": sorted(known),
            "memories": memories,
        }
        accepted_payload = None
        evidence_backed = None
        critique = Critique(False)
        for attempt in (1, 2):
            payload = self.chunks.run(
                f"market-judge-{attempt}",
                lambda carryover, attempt=attempt: self._role_chunk(
                    "market_judge",
                    attempt,
                    payload_in,
                    carryover,
                    target="validator",
                ),
            )
            guard = critique_judge(payload, known_evidence_ids=known, row=row, signals=signals)
            critique = self._apply_llm_judge(
                guard,
                role="market_judge",
                subject={
                    "role": "market_judge",
                    "row": row,
                    "signals": signals,
                    "decision": payload.get("decision"),
                    "explanation": payload.get("explanation"),
                    "evidence_ids": payload.get("evidence_ids"),
                },
                attempt=attempt,
            )
            if not guard.rejected:
                evidence_backed = payload
            if not critique.rejected:
                accepted_payload = payload
                break
            self._record_critique(critique, attempt - 1)
        if accepted_payload is None and evidence_backed is not None:
            self._note_judge_exhausted("market_judge")
        if accepted_payload is None:
            self.trace.event(
                agent_name="validator",
                action="MODEL_EXPLANATION_REJECTED",
                input_summary=critique.reason,
                output_summary="The evidence policy text will be used.",
                target_agent="market_judge",
                status="rejected",
                retry_count=1,
                llm=self._llm_trace(),
            )
            return {"accepted": False, "explanation": ""}
        return {"accepted": True, "explanation": accepted_payload["explanation"]}

    def _architect(self, row: dict, profile_evidence_id: str) -> None:
        self.chunks.run(
            "architect",
            lambda carryover: self._role_chunk(
                "architect",
                1,
                {"row": row, "profile_evidence_id": profile_evidence_id},
                carryover,
                target="builder",
            ),
        )

    def _builder(self, row: dict, decision: str, known: set[str]) -> None:
        critique = Critique(False)
        accepted = False
        for attempt in (1, 2):
            payload = self.chunks.run(
                f"builder-{attempt}",
                lambda carryover, attempt=attempt: self._role_chunk(
                    "builder",
                    attempt,
                    {"row": row, "decision": decision, "evidence_ids": sorted(known)},
                    carryover,
                    target="validator",
                ),
            )
            critique = critique_builder(payload, known_evidence_ids=known)
            if not critique.rejected:
                accepted = True
                break
            self._record_critique(critique, attempt - 1)
        if not accepted:
            raise InvestigationError("ValidatorBlocked", critique.reason or "Builder output was rejected.", 422)

    def _diagnostician(self, row: dict, reason: str, known: set[str]) -> tuple[ZetarisResult, list[dict]]:
        self.chunks.run(
            "diagnostician",
            lambda carryover: self._role_chunk(
                "diagnostician",
                1,
                {"row": row, "validation_reason": reason, "evidence_ids": sorted(known)},
                carryover,
                target="zetaris",
            ),
        )
        result = self._query(signals_sql(row["repo_id"]))
        return result, [normalize_signal(item) for item in result.rows]

    def _role_chunk(self, role: str, attempt: int, payload: dict, carryover: str, target: str):
        self._check_run_budget()
        user = (
            f"ROLE: {role}\nATTEMPT: {attempt}\nCARRYOVER:\n{carryover}\n"
            f"EVIDENCE_JSON:\n{json.dumps(payload, ensure_ascii=False, default=str)}\n"
            "Return one JSON object and cite only evidence ids from EVIDENCE_JSON. "
            "Reasoning before the answer is allowed. The last value in the reply must be that JSON object."
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "You are one role in PhoenixRepo. Use only the Zetaris rows in the user message. "
                    "Do not invent demand, users, or test results."
                ),
            },
            {"role": "user", "content": user},
        ]
        try:
            raw = self.llm.complete(messages, deadline=self._llm_deadline())
        except MeterlessError as exc:
            self._flush_llm_fallbacks()
            self.provider_degraded = True
            self.trace.event(
                agent_name=role,
                action="PROVIDER_DEGRADED",
                input_summary={
                    "lane": getattr(self.llm, "lane", ""),
                    "requested_model": getattr(self.llm, "requested_model", ""),
                    "served_model": getattr(self.llm, "served_model", None),
                    "reason": str(exc)[:300],
                },
                output_summary="Deterministic stand-in used after Nightshift lanes failed.",
                target_agent="validator",
                retry_count=attempt - 1,
                status="degraded",
                llm=self._llm_trace(),
            )
            raw = SampleStandIn().complete(messages)
            self.llm.requested_model = SampleStandIn.model_name
            self.llm.served_model = SampleStandIn.model_name
        else:
            self._flush_llm_fallbacks()
        model_trace = {
            "requested_model": self.llm.requested_model,
            "served_model": self.llm.served_model,
        }
        try:
            parsed = parse_model_json(
                raw,
                expected_keys={
                    "archaeologist": ("claims",),
                    "market_judge": ("decision", "explanation"),
                    "architect": ("contract",),
                    "builder": ("steps",),
                    "diagnostician": ("action", "claims", "decision"),
                    "planner": ("findings", "stages"),
                }.get(role, ("decision", "claims", "steps", "contract", "explanation")),
            )
        except InvestigationError as exc:
            self.trace.event(
                agent_name=role,
                action="MODEL_OUTPUT",
                input_summary={"attempt": attempt, **model_trace},
                output_summary=exc.message,
                target_agent="validator",
                retry_count=attempt - 1,
                status="error",
                llm=model_trace,
            )
            raise
        summary = {key: parsed.get(key) for key in ("decision", "claims", "steps", "contract", "action") if key in parsed}
        self.trace.event(
            agent_name=role,
            action="MODEL_OUTPUT",
            input_summary={"attempt": attempt, "carryover_chars": len(carryover), **model_trace},
            output_summary=summary or parsed,
            target_agent=target,
            retry_count=attempt - 1,
            confidence=0.9 if self.llm.sample_standin else None,
            llm=model_trace,
        )
        carry = json.dumps({"role": role, "attempt": attempt, "keys": sorted(parsed)}, ensure_ascii=False)
        return parsed, carry

    def _apply_llm_judge(self, critique: Critique, *, role: str, subject: dict, attempt: int) -> Critique:
        if self.llm.sample_standin:
            return critique
        started = time.perf_counter()
        try:
            outcome = call_llm_judge(self.llm, subject=subject)
        except Exception as exc:
            kind = _judge_failure_kind(exc)
            if not self.judge_failure:
                self.judge_failure = kind
            latency_ms = int((time.perf_counter() - started) * 1000)
            message = str(exc)
            self._log_judge(
                role=role,
                attempt=attempt,
                status=kind,
                requested_model=str(getattr(self.llm, "requested_model", "") or ""),
                served_model=getattr(self.llm, "served_model", None),
                latency_ms=latency_ms,
                output={
                    "raw_decision": "unavailable",
                    "failure": kind,
                    "reason": message[:300],
                    "advisory": True,
                },
            )
            return critique
        combined, note = combine(critique, outcome)
        status = "ok" if outcome.parsed and outcome.decision == "accept" else outcome.decision
        self._log_judge(
            role=role,
            attempt=attempt,
            status=status,
            requested_model=outcome.requested_model,
            served_model=outcome.served_model,
            latency_ms=outcome.latency_ms,
            output={
                "raw_decision": outcome.decision if outcome.parsed else "unparsed",
                "reason": outcome.reason[:300],
                "claim_ids": outcome.claim_ids,
                "parsed": outcome.parsed,
                "guard_override": bool(note),
                "advisory": True,
                "raw_text": outcome.raw[:180],
            },
        )
        return combined

    def _log_judge(self, *, role: str, attempt: int, status: str, requested_model: str, served_model, latency_ms: int, output: dict) -> None:
        self.judge_events.append({"role": role, "status": status, "failure": output.get("failure", "")})
        self.trace.event(
            agent_name="validator",
            action="LLM_JUDGE",
            input_summary={
                "role": role,
                "requested_model": requested_model,
                "served_model": served_model,
                "latency_ms": latency_ms,
                "advisory": True,
            },
            output_summary=output,
            target_agent=role,
            retry_count=attempt - 1,
            status=status,
            llm={"requested_model": requested_model, "served_model": served_model},
        )

    def _note_judge_exhausted(self, role: str) -> None:
        self.judge_exhausted = True
        self.trace.event(
            agent_name="validator",
            action="LLM_JUDGE_EXHAUSTED",
            input_summary={
                "role": role,
                "advisory": True,
                "requested_model": getattr(self.llm, "requested_model", ""),
                "served_model": getattr(self.llm, "served_model", None),
            },
            output_summary="The advisory judge kept rejecting after the retry. The deterministic policy verdict is still produced.",
            target_agent=role,
            status="advisory",
            llm=self._llm_trace(),
        )

    def _judge_verdict_note(self) -> str:
        if self.judge_failure:
            return (
                f"The LLM judge did not complete ({self.judge_failure}); "
                "the deterministic policy verdict was kept."
            )
        if self.judge_exhausted:
            return (
                "The LLM judge rejected the claim after retries; "
                "it is advisory and the deterministic policy verdict was kept."
            )
        return ""

    def _record_critique(self, critique: Critique, retry_count: int) -> None:
        self.critiques.append(
            {"target_agent": critique.target_agent, "reason": critique.reason, "retry_count": retry_count}
        )
        self.trace.event(
            agent_name="validator",
            action="CRITIQUE_REJECT",
            input_summary={"claim_ids": critique.claim_ids},
            output_summary=critique.reason,
            target_agent=critique.target_agent,
            retry_count=retry_count,
            status="rejected",
        )
        self.trace.event(
            agent_name="orchestrator",
            action="RETRY",
            input_summary=critique.reason,
            output_summary={"next_attempt": retry_count + 2},
            target_agent=critique.target_agent,
            retry_count=retry_count + 1,
            status="retry",
        )
        self.audit.record(
            actor="validator",
            event_type="CRITIQUE_REJECT",
            reason=critique.reason,
            metadata={"target_agent": critique.target_agent, "claim_ids": critique.claim_ids},
        )

    def _remember_claims(self, claims: list[dict], row: dict) -> None:
        for claim in claims:
            content = f"repository {row['repo_id']} category {row['category']} {claim['statement']}"
            confidence = float(claim.get("confidence", 0.9))
            refs = list(claim["evidence_ids"])
            existing = self.memory.find_evidence_records(row["repo_id"], claim["id"])
            if existing:
                keeper = existing[0]
                self.memory.refresh(
                    keeper.id,
                    content=content,
                    confidence=confidence,
                    goal_run_id=self.run_id,
                    refs=refs,
                )
                for extra in existing[1:]:
                    self.memory.supersede(extra.id, keeper.id)
                continue
            self.memory.add(
                content,
                source="zetaris:phoenix.repo_evidence",
                origin="document",
                label="evidence claim",
                layer="working",
                tags=["evidence", row["category"]],
                entities=[row["repo_id"]],
                refs=refs,
                goal_run_id=self.run_id,
                extra={"kind": "evidence", "claim_id": claim["id"]},
                confidence=confidence,
            )

    def _recall_evidence(self, row: dict) -> QueryResult:
        result = self.retrieval.query(
            text=f"repository {row['repo_id']} category {row['category']} licence evidence",
            entities=[row["repo_id"]],
            tags=["evidence"],
            kind="evidence",
            require_entities=True,
            include_superseded=False,
            top_n=20,
            threshold=0.35,
        )
        result.memories = [
            item
            for item in result.memories
            if row["repo_id"] in item.memory.entities and item.memory.extra.get("kind") == "evidence"
        ]
        result.trace.scores = {item.memory.id: item.relevance for item in result.memories}
        self.trace.event(
            agent_name="hmem",
            action="HMEM_RECALL",
            input_summary={"repo_id": row["repo_id"], "strategy": result.trace.strategy},
            output_summary={
                "memory_ids": [item.memory.id for item in result.memories],
                "scores": result.trace.scores,
            },
            target_agent="market_judge",
            confidence=result.memories[0].relevance if result.memories else 0.0,
            status="ok" if result.memories else "error",
        )
        return result

    def _prior_verdicts(self, repo_id: str) -> list:
        result = self.retrieval.query(
            text=f"repository {repo_id} verdict",
            entities=[repo_id],
            tags=["verdict"],
            kind="verdict",
            require_entities=True,
            include_superseded=False,
            top_n=5,
            threshold=0.0,
        )
        priors = [
            item
            for item in result.memories
            if item.memory.extra.get("kind") == "verdict"
            and not item.memory.superseded_by
            and repo_id in item.memory.entities
        ]
        self.trace.event(
            agent_name="hmem",
            action="HMEM_PRIOR_VERDICT",
            input_summary={"repo_id": repo_id},
            output_summary={"memory_ids": [item.memory.id for item in priors]},
            target_agent="market_judge",
            status="ok",
        )
        return priors

    def _add_prior_evidence(self, item) -> str:
        memory = item.memory
        evidence_id = "EV-HMEM-" + memory.id
        evidence = create_evidence(
            evidence_id=evidence_id,
            evidence_type="external",
            source="hmem",
            locator=memory.id,
            content=memory.content,
            collector="memory_retrieval_service",
            confidence=item.relevance,
            metadata={
                "memory_id": memory.id,
                "prior_run_id": memory.goal_run_id or "",
                "query_id": memory.id,
                "sql": "",
                "source": "hmem",
                "zetaris_mode": "not-zetaris",
            },
        )
        self.graph.add_evidence(evidence)
        statement = (
            f"H-MEM recalls {memory.id} from run {memory.goal_run_id}: "
            f"{memory.extra.get('decision')}."
        )
        claim = Claim(id=f"CLM-PRIOR-{memory.id}", statement=statement, confidence=item.relevance)
        self.graph.add_claim(claim)
        self.graph.link_support(claim.id, evidence_id)
        return evidence_id

    def _remember_verdict(self, row: dict, decision: str, text: str, evidence_ids: list[str]):
        older = self.memory.find_verdicts(row["repo_id"])
        record = self.memory.add(
            f"repository {row['repo_id']} verdict {decision}. {text}",
            source="phoenixrepo-verdict",
            origin="plan_completion",
            label="verdict",
            layer="working",
            tags=["verdict", row["category"], decision],
            entities=[row["repo_id"]],
            refs=evidence_ids,
            goal_run_id=self.run_id,
            extra={"kind": "verdict", "decision": decision, "run_id": self.run_id},
            confidence=0.95,
        )
        self.memory.promote(record.id, "long_term")
        for previous in older:
            if previous.id != record.id and not previous.superseded_by:
                self.memory.supersede(previous.id, record.id)
        return record

    def _add_decision_claim(self, text: str, evidence_ids: list[str]) -> Claim:
        claim = Claim(id="CLM-DECISION", statement=text, confidence=0.9)
        self.graph.add_claim(claim)
        for evidence_id in evidence_ids:
            metadata = self._evidence_meta(evidence_id)
            if metadata.get("source") == "hmem":
                continue
            self.graph.link_support(claim.id, evidence_id)
        if not claim.evidence_ids:
            raise InvestigationError("ValidatorBlocked", "The verdict has no Zetaris evidence links.", 500)
        return claim

    def _evidence_meta(self, evidence_id: str) -> dict:
        evidence = self.graph.get_evidence(evidence_id)
        return dict(evidence.metadata) if evidence else {}

    def _export(self, row: dict, decision: str):
        pack = self.graph.export_pack(
            run_id=self.run_id,
            metadata={
                "repository": self.repository,
                "mode": self.mode,
                "foundation_only": False,
                "pipeline_version": PIPELINE_VERSION,
                "investigation_version": INVESTIGATION_VERSION,
                "decision": decision,
                "data_origin": row["data_origin"],
                "max_repair_iterations": self.max_repair_iterations,
            },
        )
        path = self.artifacts.save_evidence_pack(pack)
        context_path = self.artifacts.save_run_context(self.context)
        self.trace.event(
            agent_name="orchestrator",
            action="EVIDENCE_PACK_CREATED",
            output_summary={"path": str(path), "integrity_hash": pack.integrity_hash},
            status="ok",
        )
        for event in self.audit.events:
            self.trace.event(
                agent_name=event.actor,
                action="AUDIT",
                input_summary=event.event_type,
                output_summary=event.reason or "",
                status="ok",
            )
        return {"pack": pack, "path": path, "context_path": context_path}

    def _response(
        self,
        *,
        decision: str,
        reason: str,
        verdict_text: str,
        verification: dict,
        pack: dict,
        recalled: QueryResult,
        priors: list,
        verdict_memory_id: str,
        model_accepted: bool,
        run_hash: str,
    ) -> dict[str, Any]:
        links = []
        for claim in self.graph.graph.claims:
            evidence_links = []
            for evidence_id in claim.evidence_ids:
                meta = self._evidence_meta(evidence_id)
                evidence_links.append(
                    {
                        "evidence_id": evidence_id,
                        "query_id": meta.get("query_id"),
                        "sql": meta.get("sql"),
                        "content_hash": meta.get("content_hash"),
                        "source": meta.get("source"),
                        "zetaris_mode": meta.get("zetaris_mode"),
                    }
                )
            links.append({"claim_id": claim.id, "statement": claim.statement, "evidence": evidence_links})
        hmem_recalled = [
            {
                "memory_id": item.memory.id,
                "content": item.memory.content,
                "score": item.relevance,
                "evidence_refs": item.memory.provenance.refs,
                "layer": item.memory.layer,
                "run_id": item.memory.goal_run_id,
            }
            for item in recalled.memories
        ]
        return {
            "run_id": self.run_id,
            "run_fingerprint": self.context.fingerprint,
            "mode": self.mode,
            "foundation_only": False,
            "status": "COMPLETE",
            "state": self.state.current_state.value,
            "evidence_count": len(self.graph.graph.evidence),
            "claims_count": len(self.graph.graph.claims),
            "audit_events": len(self.audit.events),
            "decision": decision,
            "claim_verification": verification,
            "evidence_pack": {
                "path": str(pack["path"]),
                "context_path": str(pack["context_path"]),
                "integrity_hash": pack["pack"].integrity_hash,
            },
            "log_file": _display_path(self.trace.path),
            "metadata": {
                "investigation_version": INVESTIGATION_VERSION,
                "reason": reason,
                "verdict_text": verdict_text,
                "run_hash": run_hash,
                "upgrade": self.upgrade,
                "revival_prd": self.revival_prd,
                "claims": links,
                "zetaris": {
                    "mode": self.queries[0]["mode"] if self.queries else self.zetaris.mode(),
                    "queries": self.queries,
                    "load_bearing": True,
                },
                "llm": {
                    "provider": self.llm.provider,
                    "model": self.llm.model,
                    "requested_model": self.llm.requested_model,
                    "served_model": self.llm.served_model,
                    "provenance": {
                        "requested_model": self.llm.requested_model,
                        "served_model": self.llm.served_model,
                        "run_hash": run_hash,
                    },
                    "sample_standin": self.llm.sample_standin,
                    "degraded": self.provider_degraded,
                    "fallbacks": list(getattr(self.llm, "fallback_log", []) or []),
                    "label": self.llm.label(),
                    "explanation_accepted": model_accepted,
                    "primary_provider": "meterless",
                    "judge": {
                        "advisory": True,
                        "exhausted": self.judge_exhausted,
                        "failure": self.judge_failure,
                        "events": self.judge_events,
                    },
                },
                "hmem": {
                    "engine": "H-MEM",
                    "recalled": hmem_recalled,
                    "prior_verdicts": [
                        {
                            "memory_id": item.memory.id,
                            "decision": item.memory.extra.get("decision"),
                            "run_id": item.memory.goal_run_id,
                            "score": item.relevance,
                        }
                        for item in priors
                    ],
                    "verdict_memory_id": verdict_memory_id,
                },
                "collaboration": {
                    "agents": [
                        "archaeologist",
                        "market_judge",
                        "architect",
                        "builder",
                        "validator",
                        "diagnostician",
                        "planner",
                    ],
                    "critique_events": self.critiques,
                    "markovian_chunks": [chunk.name for chunk in self.chunks.chunks],
                },
                "pre_existing_foundation": "Evidence graph, state machine, audit trail, and API shape from the 8 Oct 2026 skeleton.",
                "scope": (
                    "The terminal state is an evidence-policy verdict over Zetaris rows. "
                    "A repository that is not NO_GO also gets a revival PRD with stage revive, "
                    "refactor, and upgrade. This run does not clone, compile, test, or push commits. "
                    "A row that passes the policy with no qualifying stage-1 success is CANDIDATE. "
                    "RESURRECTED requires a stage-1 success on the fork default branch whose commit "
                    "is ahead of the upstream base and whose workflow runs the repo's build or test "
                    "commands. That success can override an upstream CI failure or unknown CI. It "
                    "cannot override NO_GO, missing tests, or a missing licence file. A qualifying "
                    "stage-1 failure is RESURRECTION_FAILED and cites that run. The latest attempt "
                    "wins. Refactor and upgrade are an upgrade result and do not change the verdict. "
                    "The trace hash chain shows the record was not altered after the run. It does not "
                    "show that the record is true."
                ),
            },
        }

    def _flush_llm_fallbacks(self) -> None:
        events = list(getattr(self.llm, "fallback_log", []) or [])
        if not events:
            return
        self.llm.fallback_log.clear()
        for event in events:
            self.trace.event(
                agent_name="orchestrator",
                action="LLM_FALLBACK",
                input_summary={
                    "lane": event.get("lane"),
                    "requested_model": event.get("requested_model"),
                    "served_model": event.get("served_model"),
                    "reason": event.get("reason"),
                },
                output_summary=event.get("reason") or "",
                status="fallback",
                llm={
                    "requested_model": event.get("requested_model"),
                    "served_model": event.get("served_model"),
                },
            )

    def _stamp_run_hash(self, run_hash: str) -> None:
        document = self.revival_prd.get("document") if isinstance(self.revival_prd, dict) else None
        if not isinstance(document, dict):
            return
        document["run_hash"] = run_hash
        self.revival_prd["run_hash"] = run_hash
        markdown_path = self.revival_prd.get("markdown_path") or ""
        json_path = self.revival_prd.get("json_path") or ""
        if markdown_path:
            Path(markdown_path).write_text(render_markdown(document), encoding="utf-8")
        if json_path:
            Path(json_path).write_text(render_json(document), encoding="utf-8")

    def _advance(self, state: PhoenixState, reason: str, evidence_ids: list[str]) -> None:
        self.state.transition(
            state,
            actor="orchestrator",
            reason=reason,
            evidence_ids=evidence_ids,
        )

    def _llm_trace(self) -> dict[str, Any]:
        return {
            "requested_model": getattr(self.llm, "requested_model", "") or "",
            "served_model": getattr(self.llm, "served_model", None),
        }

    def _llm_deadline(self) -> float | None:
        step_deadline = getattr(self.chunks, "step_deadline", None)
        if step_deadline is None:
            return None
        budget = float(self.chunks.config.chunk_timeout_seconds)
        slack = 0.05 if budget >= 5 else min(0.2, budget * 0.3)
        return float(step_deadline) - slack

    def _check_run_budget(self) -> None:
        if time.monotonic() - self.started > self.settings.run_timeout_seconds:
            raise InvestigationError("RunTimeout", "The investigation exceeded its run timeout.", 504)


def _planner_evidence_text(row: dict, signals: list[dict], attempts: list[dict], sources: list[dict]) -> str:
    """Everything the deterministic template is allowed to quote, plus the row itself."""

    parts = [
        str(row.get("primary_language") or ""),
        str(row.get("licence_spdx") or ""),
        str(row.get("description") or ""),
        str(row.get("ci_status") or ""),
    ]
    for signal in signals:
        parts.append(str(signal.get("detail") or ""))
        parts.append(str(signal.get("signal") or ""))
    for attempt in attempts:
        parts.append(str(attempt.get("workflow_steps") or ""))
        parts.append(str(attempt.get("workflow_path") or ""))
    for source in sources:
        parts.append(str(source.get("excerpt") or ""))
        parts.append(str(source.get("title") or ""))
        parts.append(str(source.get("url") or ""))
    return "\n".join(parts)


def _evidence_catalog(context: dict) -> dict[str, dict[str, str]]:
    catalog: dict[str, dict[str, str]] = {}

    def add(item: dict) -> None:
        evidence_id = item.get("evidence_id")
        if not evidence_id:
            return
        catalog[str(evidence_id)] = {
            "query_id": item.get("query_id") or "",
            "sql": item.get("sql") or "",
            "content_hash": item.get("content_hash") or "",
        }

    add(context.get("profile") or {})
    for key in ("signals", "attempts", "sources"):
        for item in context.get(key) or []:
            add(item)
    return catalog


def _slug(value: str) -> str:
    return "".join(char if char.isalnum() else "-" for char in value).strip("-")


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return str(path)
