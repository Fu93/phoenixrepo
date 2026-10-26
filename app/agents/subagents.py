"""Capped sub-agents for targeted evidence gathering per criterion."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.agents.venture import canonical_criterion_name
from app.tools.base import ToolBox

CRITERION_TOOL_ALLOWLIST: dict[str, frozenset[str]] = {
    "demand_evidence": frozenset({"github", "web_fetch", "npm", "pypi"}),
    "feasibility": frozenset({"github"}),
    "moat": frozenset({"web_fetch"}),
    "productization_path": frozenset({"web_fetch"}),
    "problem_severity": frozenset({"web_fetch", "github"}),
    "differentiation": frozenset({"github", "web_fetch"}),
}

DEFAULT_SUBAGENT_CALL_BUDGET = 5
DEFAULT_MAX_SUBAGENTS = 6


def is_candidate_repo_url(url: str, candidate_repo: str) -> bool:
    """True if the URL refers directly to the candidate repo itself rather than an external source."""
    if not url or not candidate_repo:
        return False
    norm_url = url.strip().rstrip("/").lower()
    norm_repo = candidate_repo.strip().lower()
    if f"api.github.com/repos/{norm_repo}" in norm_url:
        return True
    if f"github.com/{norm_repo}" in norm_url and "search" not in norm_url:
        return True
    if f"repo:{norm_repo}" in norm_url:
        return True
    return False


def contains_candidate_stars_demand(text: str, candidate_repo: str) -> bool:
    """True if text cites the candidate repo's own stars or forks as demand."""
    lowered = text.lower()
    forbidden = (
        "candidate stars",
        "repo stars",
        "repo's stars",
        "own stars",
        "stars count",
    )
    if any(phrase in lowered for phrase in forbidden):
        return True
    if "stars" in lowered and candidate_repo.lower() in lowered:
        # Check if claiming repo's own stars
        if re.search(r"\b(has|with|\d+)\s+stars?\b", lowered):
            return True
    return False


class SubAgent:
    """Single-purpose agent investigating exactly ONE criterion for ONE repository."""

    def __init__(
        self,
        criterion: str,
        repo_id: str,
        toolbox: ToolBox,
        max_calls: int = DEFAULT_SUBAGENT_CALL_BUDGET,
    ) -> None:
        self.raw_criterion = criterion
        canonical = canonical_criterion_name(criterion)
        self.criterion = canonical if canonical is not None else criterion
        self.repo_id = repo_id
        allowed = CRITERION_TOOL_ALLOWLIST.get(self.criterion, frozenset({"web_fetch"}))
        self.allowed_tools = allowed
        # Carve child toolbox with allowed tools only and child budget
        self.toolbox = toolbox.child_box(allowed_tools=allowed, max_calls=max_calls)
        self.gathered_evidence: list[dict[str, Any]] = []
        self.rate_limited = False

    def spawn_subagent(self, *args: Any, **kwargs: Any) -> Any:
        """In-rule: sub-agents cannot spawn sub-agents."""
        raise RuntimeError("Sub-agents cannot spawn sub-agents.")

    def investigate(
        self,
        *,
        query: str | None = None,
        alternative_repos: list[str] | None = None,
        url: str | None = None,
        package: str | None = None,
        sql: str | None = None,
    ) -> list[dict[str, Any]]:
        """Gather evidence for this criterion using allow-listed tools.

        Demand evidence must come strictly from OUTSIDE the candidate repo;
        the candidate repo's own stars or forks are never cited as demand.
        """
        evidence_items: list[dict[str, Any]] = []

        if self.criterion == "demand_evidence":
            # 1. Search issues on alternatives or external queries
            if "github" in self.allowed_tools:
                queries = []
                if query:
                    queries.append(query)
                for alt in alternative_repos or []:
                    queries.append(f"repo:{alt} is:issue")
                for q in queries:
                    res = self.toolbox.call("github", {"query": q})
                    if not res.ok and "RATE_LIMITED" in (res.error or ""):
                        self.rate_limited = True
                        break
                    if res.ok and res.data:
                        # Validate external source: never candidate repo
                        if not is_candidate_repo_url(res.source_url, self.repo_id):
                            item = res.to_evidence_item(repo_id=self.repo_id)
                            if not contains_candidate_stars_demand(item["text"], self.repo_id):
                                evidence_items.append(item)

            # 2. Package registries (npm / pypi)
            if package:
                if "npm" in self.allowed_tools:
                    res = self.toolbox.call("npm", {"package": package})
                    if res.ok and res.data:
                        evidence_items.append(res.to_evidence_item(repo_id=self.repo_id))
                if "pypi" in self.allowed_tools:
                    res = self.toolbox.call("pypi", {"package": package})
                    if res.ok and res.data:
                        evidence_items.append(res.to_evidence_item(repo_id=self.repo_id))

            # 3. Web fetch for discussions/articles
            if url and "web_fetch" in self.allowed_tools:
                if not is_candidate_repo_url(url, self.repo_id):
                    res = self.toolbox.call("web_fetch", {"url": url})
                    if res.ok and res.data:
                        item = res.to_evidence_item(repo_id=self.repo_id)
                        if not contains_candidate_stars_demand(item["text"], self.repo_id):
                            evidence_items.append(item)

        elif self.criterion == "feasibility":
            if "github" in self.allowed_tools:
                # Check candidate repo metadata or readme for test/CI info
                res = self.toolbox.call("github", {"repo": self.repo_id, "action": "readme"})
                if not res.ok and "RATE_LIMITED" in (res.error or ""):
                    self.rate_limited = True
                elif res.ok and res.data:
                    evidence_items.append(res.to_evidence_item(repo_id=self.repo_id))
                else:
                    res_meta = self.toolbox.call("github", {"repo": self.repo_id})
                    if not res_meta.ok and "RATE_LIMITED" in (res_meta.error or ""):
                        self.rate_limited = True
                    elif res_meta.ok and res_meta.data:
                        evidence_items.append(res_meta.to_evidence_item(repo_id=self.repo_id))

        elif self.criterion in {"moat", "productization_path"}:
            if url and "web_fetch" in self.allowed_tools:
                res = self.toolbox.call("web_fetch", {"url": url})
                if res.ok and res.data:
                    evidence_items.append(res.to_evidence_item(repo_id=self.repo_id))

        else:
            # General fallback for problem_severity or differentiation
            if url and "web_fetch" in self.allowed_tools:
                res = self.toolbox.call("web_fetch", {"url": url})
                if res.ok and res.data:
                    evidence_items.append(res.to_evidence_item(repo_id=self.repo_id))
            elif query and "github" in self.allowed_tools:
                res = self.toolbox.call("github", {"query": query})
                if res.ok and res.data:
                    evidence_items.append(res.to_evidence_item(repo_id=self.repo_id))

        self.gathered_evidence.extend(evidence_items)
        return evidence_items


@dataclass
class SubAgentManager:
    """Manages spawning of sub-agents and enforces the hard cap per run."""

    max_subagents: int = DEFAULT_MAX_SUBAGENTS
    spawned_count: int = 0
    subagents: list[SubAgent] = field(default_factory=list)

    def can_spawn(self) -> bool:
        return self.spawned_count < self.max_subagents

    def spawn(
        self,
        criterion: str,
        repo_id: str,
        toolbox: ToolBox,
        max_calls: int = DEFAULT_SUBAGENT_CALL_BUDGET,
    ) -> SubAgent:
        if not self.can_spawn():
            raise RuntimeError(f"Sub-agent cap reached ({self.max_subagents}).")
        sub = SubAgent(criterion=criterion, repo_id=repo_id, toolbox=toolbox, max_calls=max_calls)
        self.spawned_count += 1
        self.subagents.append(sub)
        return sub
