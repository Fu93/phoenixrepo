import json
from pathlib import Path

from app.agents.planner import build_revival_prd, evidence_context, render_markdown
from app.agents.validate import critique_planner
from app.investigation import InvestigationOrchestrator
from app.llm.provider import BoundLLM
from app.llm.standin import SampleStandIn
from config.settings import Settings
from scripts.write_revival_prd import write_prd
from tests.agents.test_revival_policy import _attempts, _profile, _signals


def _sources():
    import csv

    from app.agents.policy import normalize_source
    from app.sql_catalog import SOURCE_COLUMNS

    with Path("data/real/revival_sources.csv").open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert tuple(reader.fieldnames or ()) == SOURCE_COLUMNS
        return [normalize_source(row) for row in reader if row["repo_id"] == "jondot/hygen"]


def _document():
    row = _profile("jondot/hygen")
    signals = _signals("jondot/hygen")
    attempts = _attempts("jondot/hygen")
    sources = _sources()
    return build_revival_prd(
        row,
        signals,
        attempts,
        sources,
        evidence_context("jondot/hygen", signals, attempts, sources),
    )


def test_committed_hygen_prd_matches_the_deterministic_builder(tmp_path):
    import shutil

    frozen = tmp_path / "frozen_real"
    shutil.copytree("data/real", frozen)
    for name in ("revival_attempts.csv", "revival_attempts_provenance.json", "revival_sources.csv"):
        shutil.copy(Path("tests/fixtures/hygen_pre_revival") / name, frozen / name)  # the PRD records the pre-revival state
    markdown_path, json_path = write_prd("jondot/hygen", frozen, tmp_path / "out")
    committed_md = Path("docs/revival/jondot__hygen.prd.md").read_text(encoding="utf-8")
    committed_json = json.loads(Path("docs/revival/jondot__hygen.prd.json").read_text(encoding="utf-8"))
    assert markdown_path.read_text(encoding="utf-8") == committed_md
    assert json.loads(json_path.read_text(encoding="utf-8")) == committed_json
    text = committed_md
    assert "Drafted by: deterministic" in text
    assert "llm_drafted: false" in text
    assert "https://github.com/jondot/hygen/issues/450" in text
    assert "https://github.com/jondot/hygen/actions/runs/37141993934" in text
    assert "Use Node.js 14.x" in text
    assert "not a test failure" in text
    assert "ahead_by 0" in text
    assert "total_count 0" in text
    assert "actions/checkout@v2" in text
    assert "actions/setup-node@v2" in text
    assert "actions/checkout@v4" in text
    assert "actions/setup-node@v4" in text
    assert "20.x" in text
    assert "yarn hygen:build -- init self" in text
    assert "Cache service responded with 400" in text
    assert "GHSA-ghr5-ch3p-vcr6" in text
    assert "GHSA-phwq-j96m-2c2q" in text
    assert "GHSA-77c7-pq4r-6mcq" in text
    assert "Non-factual framing" in text
    assert "`yarn test` exits 0." in text
    assert "node dist/bin.js --version" in text
    assert "Error: please specify a generator." in text
    assert "Stage 1 — REVIVE" in text
    assert "Stage 2 — REFACTOR / OPTIMISE" in text
    assert "Stage 3 — UPGRADE" in text
    assert "27 Oct 2026" in text
    assert "Puppeteer" not in text
    assert "tests failed" not in text
    document = committed_json
    assert document["llm_drafted"] is False
    assert [stage["id"] for stage in document["stages"]] == ["revive", "refactor", "upgrade"]
    assert document["stages"][0]["gate_for_resurrected"] is True
    assert document["stages"][1]["gate_for_resurrected"] is False
    assert document["stages"][2]["gate_for_resurrected"] is False


def test_planner_critic_rejects_a_contradiction_and_a_missing_field():
    document = _document()
    lines = [*document["findings"], *document["tasks"], *document["risks"]]
    known = {evidence_id for line in lines for evidence_id in line["evidence_ids"]}
    row = _profile("jondot/hygen")
    catalog = {}
    for line in lines:
        for citation in line.get("citations") or []:
            for evidence_id in citation["evidence_ids"]:
                catalog[evidence_id] = {
                    "query_id": citation["query_id"],
                    "sql": citation["sql"],
                    "content_hash": citation.get("content_hash") or "",
                }
    assert critique_planner(document, known_evidence_ids=known, row=row, evidence_by_id=catalog).rejected is False
    contradicted = json.loads(json.dumps(document))
    contradicted["findings"][0]["text"] = contradicted["findings"][0]["text"].replace(
        f"licence {row['licence_spdx']}", "licence GPL-3.0"
    )
    critique = critique_planner(contradicted, known_evidence_ids=known, row=row, evidence_by_id=catalog)
    assert critique.rejected is True
    assert "contradicts" in critique.reason
    broken = {"findings": [{"id": "F1", "text": "x", "evidence_ids": ["EV"], "query_id": "q", "sql": "s"}], "stages": [{"id": "revive", "tasks": 1}]}
    assert critique_planner(broken, known_evidence_ids=known).rejected is True


def test_planner_critic_rejects_a_line_without_evidence():
    document = _document()
    known = {evidence_id for finding in document["findings"] for evidence_id in finding["evidence_ids"]}
    known.update(evidence_id for task in document["tasks"] for evidence_id in task["evidence_ids"])
    assert critique_planner(document, known_evidence_ids=known).rejected is False
    bad = json.loads(json.dumps(document))
    bad["findings"].append(
        {
            "id": "F-BAD",
            "text": "A large user community is waiting.",
            "evidence_ids": [],
            "query_id": "snapshot",
            "sql": "SELECT 1",
        }
    )
    critique = critique_planner(bad, known_evidence_ids=known)
    assert critique.rejected is True
    assert "F-BAD" in critique.claim_ids
    assert "no evidence" in critique.reason


class _BadThenStandIn:
    def __init__(self):
        self.standin = SampleStandIn()
        self.planner_calls = 0

    def complete(self, messages, model=None):
        text = messages[-1]["content"]
        if "ROLE: planner" in text:
            self.planner_calls += 1
            if self.planner_calls == 1:
                return json.dumps(
                    {
                        "findings": [
                            {
                                "id": "F-BAD",
                                "text": "Everyone depends on this.",
                                "evidence_ids": [],
                                "query_id": "",
                                "sql": "",
                            }
                        ],
                        "stages": [],
                    }
                )
        return self.standin.complete(messages, model=model)


def test_uncited_planner_draft_falls_back_to_the_deterministic_prd(tmp_path):
    settings = Settings(
        sample_mode=True,
        hmem_store_path=str(tmp_path / "hmem.json"),
        log_dir=str(tmp_path / "logs"),
        _env_file=None,
    )
    scripted = _BadThenStandIn()
    result = InvestigationOrchestrator(
        "sample/phoenix-ledger",
        "sample",
        0,
        settings,
        llm=BoundLLM(scripted, "sample-standin", "deterministic-rule-standin", True),
        hmem_path=tmp_path / "hmem.json",
    ).run()
    assert result["decision"] == "CANDIDATE"
    assert "CANDIDATE" in result["metadata"]["verdict_text"]
    assert "Run hash " in result["metadata"]["verdict_text"]
    prd = result["metadata"]["revival_prd"]
    assert prd["drafted_by"] == "deterministic"
    assert prd["llm_drafted"] is False
    assert prd["stages"] == ["revive", "refactor", "upgrade"]
    assert scripted.planner_calls == 2
    text = render_markdown(prd["document"])
    assert "Do not invent an issue" in text
    actions = [
        json.loads(line)["action"]
        for line in Path(result["log_file"]).read_text(encoding="utf-8").splitlines()
    ]
    assert "CRITIQUE_REJECT" in actions
    assert "PRD_WRITTEN" in actions


def test_deterministic_hygen_plan_stays_grounded_when_evidence_is_supplied():
    document = _document()
    lines = [*document["findings"], *document["tasks"], *document["risks"]]
    known = {evidence_id for line in lines for evidence_id in line["evidence_ids"]}
    row = _profile("jondot/hygen")
    blob = "\n".join(source.get("excerpt") or "" for source in _sources())
    critique = critique_planner(
        document,
        known_evidence_ids=known,
        row=row,
        evidence_text=blob,
    )
    assert critique.rejected is False


def test_copied_citations_do_not_save_ungrounded_prose():
    document = _document()
    lines = [*document["findings"], *document["tasks"], *document["risks"]]
    known = {evidence_id for line in lines for evidence_id in line["evidence_ids"]}
    row = _profile("jondot/hygen")
    draft = json.loads(json.dumps(document))
    task = draft["stages"][0]["tasks"][0]
    task["text"] = "GPL licensed, 0 tests, rewrite in Rust"
    task["acceptance"] = ["cargo build exits 0"]
    critique = critique_planner(
        draft,
        known_evidence_ids=known,
        row=row,
        evidence_text="yarn test\nnode-version: [20.x]",
    )
    assert critique.rejected is True
    assert "GPL" in critique.reason
    assert "has_tests" in critique.reason or "no tests" in critique.reason
    assert "Rust" in critique.reason
    assert "cargo" in critique.reason


class _MissingWindow:
    def __init__(self):
        self.standin = SampleStandIn()
        self.planner_calls = 0

    def complete(self, messages, model=None):
        text = messages[-1]["content"]
        if "ROLE: planner" in text:
            self.planner_calls += 1
            payload = json.loads(self.standin.complete(messages, model=model))
            payload.pop("window", None)
            return json.dumps(payload)
        return self.standin.complete(messages, model=model)


def test_a_draft_missing_window_falls_back_without_crashing(tmp_path):
    settings = Settings(
        sample_mode=True,
        hmem_store_path=str(tmp_path / "hmem.json"),
        log_dir=str(tmp_path / "logs"),
        _env_file=None,
    )
    scripted = _MissingWindow()
    result = InvestigationOrchestrator(
        "sample/phoenix-ledger",
        "sample",
        0,
        settings,
        llm=BoundLLM(scripted, "sample-standin", "deterministic-rule-standin", True),
        hmem_path=tmp_path / "hmem.json",
    ).run()
    assert result["decision"] == "CANDIDATE"
    prd = result["metadata"]["revival_prd"]
    assert prd["drafted_by"] == "deterministic"
    text = render_markdown(prd["document"])
    assert "Window:" in text
    assert scripted.planner_calls == 2
    actions = [
        json.loads(line)["action"]
        for line in Path(result["log_file"]).read_text(encoding="utf-8").splitlines()
    ]
    assert "PLANNER_DETERMINISTIC_FALLBACK" in actions


class _UngroundedDraft:
    def __init__(self):
        self.standin = SampleStandIn()

    def complete(self, messages, model=None):
        text = messages[-1]["content"]
        if "ROLE: planner" in text:
            payload = json.loads(self.standin.complete(messages, model=model))
            task = payload["stages"][0]["tasks"][0]
            task["text"] = "GPL licensed, 0 tests, rewrite in Rust"
            task["acceptance"] = ["cargo build exits 0"]
            return json.dumps(payload)
        return self.standin.complete(messages, model=model)


def test_ungrounded_model_plan_is_replaced_before_render(tmp_path):
    settings = Settings(
        sample_mode=True,
        hmem_store_path=str(tmp_path / "hmem.json"),
        log_dir=str(tmp_path / "logs"),
        _env_file=None,
    )
    result = InvestigationOrchestrator(
        "sample/phoenix-ledger",
        "sample",
        0,
        settings,
        llm=BoundLLM(_UngroundedDraft(), "sample-standin", "deterministic-rule-standin", True),
        hmem_path=tmp_path / "hmem.json",
    ).run()
    text = render_markdown(result["metadata"]["revival_prd"]["document"])
    assert "cargo build exits 0" not in text
    assert "rewrite in Rust" not in text
    assert result["metadata"]["revival_prd"]["drafted_by"] == "deterministic"
