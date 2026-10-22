"""Multi-agent revival investigation. Zetaris is the only evidence source."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from app.adapters.sample_zetaris import SampleZetaris
from app.adapters.zetaris import ZetarisClient, ZetarisResult
from app.agents.policy import decide, explanation, normalize_profile, normalize_signal
from app.agents.validate import Critique, critique_archaeologist, critique_builder, critique_judge
from app.engines.hmem.ledger import TrustLedgerService
from app.engines.hmem.retrieval import MemoryRetrievalService
from app.engines.hmem.store import MemoryStoreService
from app.engines.hmem.types import QueryResult
from app.engines.markovian import ChunkManager, MarkovianConfig
from app.errors import InvestigationError, ZetarisError
from app.llm.parsing import parse_model_json
from app.llm.provider import BoundLLM, build_llm
from app.sql_catalog import profile_sql, require_repo_id, signals_sql
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
                max_chunks=8,
                max_carryover_chars=1200,
                chunk_timeout_seconds=settings.agent_step_timeout_seconds,
            )
        )
        self.started = time.monotonic()
        self.queries: list[dict[str, str]] = []
        self.critiques: list[dict[str, Any]] = []

    def run(self) -> dict[str, Any]:
        self.trace.event(
            agent_name="orchestrator",
            action="RUN_STARTED",
            input_summary={"repository": self.repository, "mode": self.mode},
            output_summary={"investigation_version": INVESTIGATION_VERSION},
            target_agent="archaeologist",
        )
        self.memory.hydrate()
        profile_result, signal_result, row, signals = self._collect()
        profile_evidence_id = self._add_row_evidence(profile_result, row, "repository")
        signal_evidence_ids = [
            self._add_signal_evidence(signal_result, signal, index)
            for index, signal in enumerate(signals)
        ]
        known = {profile_evidence_id, *signal_evidence_ids}
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
        decision, policy_reason = decide(row, signals)
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
                decision, policy_reason = decide(row, signals)
                repairs_used += 1
                self._advance(PhoenixState.REPAIRING, "Signals re-queried from Zetaris.", list(known))
                self._advance(PhoenixState.VALIDATING, policy_reason, list(known))
            if decision == "RESURRECTION_FAILED":
                self._advance(PhoenixState.RESURRECTION_FAILED, policy_reason, list(known))
            elif decision == "RESURRECTED":
                self._advance(PhoenixState.RESURRECTED, policy_reason, list(known))
            else:
                self._advance(PhoenixState.NO_GO, policy_reason, [profile_evidence_id])
        else:
            self._advance(PhoenixState.NO_GO, policy_reason, [profile_evidence_id])

        verdict_text = judgment["explanation"] if judgment.get("accepted") else explanation(row, decision, policy_reason)
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
        if not profile_result.rows:
            raise InvestigationError(
                "RepositoryNotInZetaris",
                f"phoenix.repo_evidence has no row for {self.repository}.",
                404,
            )
        row = normalize_profile(profile_result.rows[0])
        signals = [normalize_signal(item) for item in signal_result.rows]
        self.trace.event(
            agent_name="archaeologist",
            action="EVIDENCE_COLLECTION_COMPLETED",
            output_summary={
                "profile_query_id": profile_result.query_id,
                "signal_query_id": signal_result.query_id,
                "signal_rows": len(signals),
            },
            target_agent="validator",
        )
        return profile_result, signal_result, row, signals

    def _query(self, sql: str) -> ZetarisResult:
        self._check_run_budget()
        try:
            result = self.zetaris.run_sql(sql)
        except ZetarisError:
            raise
        except InvestigationError:
            raise
        self.queries.append(
            {"query_id": result.query_id, "sql": result.sql, "source": result.source, "mode": result.mode}
        )
        self.trace.event(
            agent_name="archaeologist",
            action="ZETARIS_QUERY",
            input_summary=sql,
            output_summary={"query_id": result.query_id, "rows": len(result.rows), "source": result.source},
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

    def _evidence_metadata(self, result: ZetarisResult) -> dict[str, str]:
        return {
            "query_id": result.query_id,
            "sql": result.sql,
            "source": result.source,
            "zetaris_mode": result.mode,
        }

    def _archaeologist(self, row: dict, profile_evidence_id: str, known: set[str]) -> list[dict]:
        accepted: list[dict] | None = None
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
            critique = critique_archaeologist(payload, known_evidence_ids=known, row=row)
            if not critique.rejected:
                accepted = payload["claims"]
                break
            self._record_critique(critique, attempt - 1)
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
            critique = critique_judge(payload, known_evidence_ids=known, row=row, signals=signals)
            if not critique.rejected:
                accepted_payload = payload
                break
            self._record_critique(critique, attempt - 1)
        if accepted_payload is None:
            self.trace.event(
                agent_name="validator",
                action="MODEL_EXPLANATION_REJECTED",
                input_summary=critique.reason,
                output_summary="The evidence policy text will be used.",
                target_agent="market_judge",
                status="rejected",
                retry_count=1,
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
            "Return one JSON object. Cite only evidence ids from EVIDENCE_JSON."
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
        raw = self.llm.complete(messages)
        try:
            parsed = parse_model_json(raw)
        except InvestigationError as exc:
            self.trace.event(
                agent_name=role,
                action="MODEL_OUTPUT",
                input_summary={"attempt": attempt},
                output_summary=exc.message,
                target_agent="validator",
                retry_count=attempt - 1,
                status="error",
            )
            raise
        summary = {key: parsed.get(key) for key in ("decision", "claims", "steps", "contract", "action") if key in parsed}
        self.trace.event(
            agent_name=role,
            action="MODEL_OUTPUT",
            input_summary={"attempt": attempt, "carryover_chars": len(carryover)},
            output_summary=summary or parsed,
            target_agent=target,
            retry_count=attempt - 1,
            confidence=0.9 if self.llm.sample_standin else None,
        )
        carry = json.dumps({"role": role, "attempt": attempt, "keys": sorted(parsed)}, ensure_ascii=False)
        return parsed, carry

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
                "claims": links,
                "zetaris": {
                    "mode": self.queries[0]["mode"] if self.queries else self.zetaris.mode(),
                    "queries": self.queries,
                    "load_bearing": True,
                },
                "llm": {
                    "provider": self.llm.provider,
                    "model": self.llm.model,
                    "sample_standin": self.llm.sample_standin,
                    "label": self.llm.label(),
                    "explanation_accepted": model_accepted,
                    "primary_provider": "meterless",
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
                    ],
                    "critique_events": self.critiques,
                    "markovian_chunks": [chunk.name for chunk in self.chunks.chunks],
                },
                "pre_existing_foundation": "Evidence graph, state machine, audit trail, and API shape from the 8 Oct 2026 skeleton.",
                "scope": (
                    "The terminal state is an evidence-policy verdict over Zetaris rows. "
                    "This run does not clone, compile, test, or republish the repository. "
                    "RESURRECTED means the cited rows satisfy the revival rules. "
                    "RESURRECTION_FAILED means the rows say it is worth reviving and the recorded "
                    "tests or CI do not pass inside the repair budget. NO_GO means the rows fail "
                    "the value rules."
                ),
            },
        }

    def _advance(self, state: PhoenixState, reason: str, evidence_ids: list[str]) -> None:
        self.state.transition(
            state,
            actor="orchestrator",
            reason=reason,
            evidence_ids=evidence_ids,
        )

    def _check_run_budget(self) -> None:
        if time.monotonic() - self.started > self.settings.run_timeout_seconds:
            raise InvestigationError("RunTimeout", "The investigation exceeded its run timeout.", 504)


def _slug(value: str) -> str:
    return "".join(char if char.isalnum() else "-" for char in value).strip("-")


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return str(path)
