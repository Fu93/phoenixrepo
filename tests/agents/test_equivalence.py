"""The guard keeps a capability map only when every quote is in the stored text."""

from __future__ import annotations

import json
from pathlib import Path

from app.agents.equivalence import judge_discovery, quote_verified, text_hash
from app.agents.market_filter import decide_discovery

ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads((ROOT / "data" / "eval" / "cases.json").read_text(encoding="utf-8"))["cases"]


def _case(repo_id: str) -> dict:
    return next(case for case in CASES if case["repo_id"] == repo_id)


def test_a_grounded_invoify_map_is_already_exists_and_a_fake_quote_is_not():
    case = _case("tharani-2006/Invoice-generator-application")
    mapping = {
        "capabilities": [
            {
                "id": "pdf",
                "statement": "Export a PDF invoice.",
                "citation": "PDF export",
                "evidence_id": "candidate:tharani-2006/Invoice-generator-application",
            },
            {
                "id": "invoice",
                "statement": "Generate invoices.",
                "citation": "invoice generator",
                "evidence_id": "candidate:tharani-2006/Invoice-generator-application",
            },
        ],
        "mappings": [
            {
                "capability_id": "pdf",
                "alternative_url": "https://github.com/al1abb/invoify",
                "status": "covered",
                "quote": "get a real PDF",
                "evidence_id": "prior:al1abb/invoify",
            },
            {
                "capability_id": "invoice",
                "alternative_url": "https://github.com/al1abb/invoify",
                "status": "covered",
                "quote": "invoice generator",
                "evidence_id": "prior:al1abb/invoify",
            },
        ],
    }
    decision, reason = judge_discovery(
        case["candidate"], case["prior_art"], case["demand"], case["evidence"], mapping
    )
    assert decision == "ALREADY_EXISTS"
    assert "Method: functional-equivalence." in reason
    assert "get a real PDF" in reason
    forged = json.loads(json.dumps(mapping))
    forged["mappings"][0]["quote"] = "get a real PDF and email it"
    rejected, rejected_reason = judge_discovery(
        case["candidate"], case["prior_art"], case["demand"], case["evidence"], forged
    )
    assert rejected != "ALREADY_EXISTS"
    assert "not an exact substring" in rejected_reason or "Gaps:" in rejected_reason


def test_hygen_phrase_misses_plop_and_a_grounded_map_covers_it():
    case = _case("jondot/hygen")
    phrase, phrase_reason = decide_discovery(case["candidate"], case["prior_art"], case["demand"])
    assert phrase == "NO_GO"
    assert "Method: phrase/keyword." in phrase_reason
    mapping = {
        "capabilities": [
            {
                "id": "generator",
                "statement": "In-project code generator.",
                "citation": "code generator that lives",
                "evidence_id": "candidate:jondot/hygen",
            },
            {
                "id": "inject",
                "statement": "Inject into existing files.",
                "citation": "Injecting into existing files",
                "evidence_id": "candidate:jondot/hygen",
            },
        ],
        "mappings": [
            {
                "capability_id": "generator",
                "alternative_url": "https://github.com/plopjs/plop",
                "status": "covered",
                "quote": "simple way to generate code",
                "evidence_id": "prior:plopjs/plop",
            },
            {
                "capability_id": "inject",
                "alternative_url": "https://github.com/plopjs/plop",
                "status": "covered",
                "quote": "find/replace text in the file located at the `path` specified",
                "evidence_id": "prior:plopjs/plop",
            },
        ],
    }
    decision, reason = judge_discovery(
        case["candidate"], case["prior_art"], case["demand"], case["evidence"], mapping
    )
    assert decision == "ALREADY_EXISTS"
    assert "Method: functional-equivalence." in reason
    assert "find/replace text in the file" in reason


def test_a_tampered_content_hash_rejects_the_quote():
    case = _case("matt-dz/wecook")
    evidence = json.loads(json.dumps(case["evidence"]))
    mealie = next(item for item in evidence if item["id"] == "prior:mealie-recipes/mealie")
    assert quote_verified("self hosted recipe manager", {**mealie, "hash_matches_text": True})
    mealie["content_hash"] = "0" * 64
    mapping = {
        "capabilities": [
            {
                "id": "recipes",
                "statement": "Self-hosted recipe manager.",
                "citation": "self-hosted recipe manager",
                "evidence_id": "candidate:matt-dz/wecook",
            }
        ],
        "mappings": [
            {
                "capability_id": "recipes",
                "alternative_url": "https://github.com/mealie-recipes/mealie",
                "status": "covered",
                "quote": "self hosted recipe manager",
                "evidence_id": "prior:mealie-recipes/mealie",
            }
        ],
    }
    decision, reason = judge_discovery(case["candidate"], case["prior_art"], case["demand"], evidence, mapping)
    assert decision == "NO_GO"
    assert "unverified" in reason
    assert "Method: functional-equivalence." in reason
    assert text_hash(mealie["text"]) != mealie["content_hash"]


def test_ytkb_push_is_a_gap_when_the_quote_is_only_markdown_export():
    case = _case("SergioMendolia/ytkb")
    mapping = {
        "capabilities": [
            {
                "id": "kb",
                "statement": "Sync knowledge base articles.",
                "citation": "knowledge base",
                "evidence_id": "candidate:SergioMendolia/ytkb",
            },
            {
                "id": "push",
                "statement": "Push changes back.",
                "citation": "push them back online",
                "evidence_id": "candidate:SergioMendolia/ytkb",
            },
        ],
        "mappings": [
            {
                "capability_id": "kb",
                "alternative_url": "https://github.com/foliant-docs/foliant",
                "status": "partial",
                "quote": "Comprehensive markdown-based documentation toolkit",
                "evidence_id": "prior:foliant-docs/foliant",
            },
            {
                "capability_id": "push",
                "alternative_url": "https://github.com/foliant-docs/foliant",
                "status": "missing",
                "quote": "",
                "evidence_id": "prior:foliant-docs/foliant",
            },
        ],
    }
    decision, reason = judge_discovery(
        case["candidate"], case["prior_art"], case["demand"], case["evidence"], mapping
    )
    assert decision == "CANDIDATE"
    assert "push" in reason
    assert "https://github.com/foliant-docs/foliant/pull/80" in reason
    assert "Method: functional-equivalence." in reason


def test_palette_tailwind_gap_overrides_the_phrase_hit():
    case = _case("bytesbeard/color-palette-generator")
    phrase, _reason = decide_discovery(case["candidate"], case["prior_art"], case["demand"])
    assert phrase == "ALREADY_EXISTS"
    mapping = {
        "capabilities": [
            {
                "id": "harmony",
                "statement": "Triadic and related palettes.",
                "citation": "triadic",
                "evidence_id": "candidate:bytesbeard/color-palette-generator",
            },
            {
                "id": "tailwind",
                "statement": "Export a Tailwind config.",
                "citation": "Export as TailwindCSS config, CSS variables, or JSON format",
                "evidence_id": "candidate:bytesbeard/color-palette-generator",
            },
        ],
        "mappings": [
            {
                "capability_id": "harmony",
                "alternative_url": "https://github.com/royalfig/color-palette-generator",
                "status": "covered",
                "quote": "Split Complementary, Triadic",
                "evidence_id": "prior:royalfig/color-palette-generator",
            },
            {
                "capability_id": "tailwind",
                "alternative_url": "https://github.com/royalfig/color-palette-generator",
                "status": "partial",
                "quote": "Export options: CSS variables, CSS file download, and palette images",
                "evidence_id": "prior:royalfig/color-palette-generator",
            },
        ],
    }
    decision, reason = judge_discovery(
        case["candidate"], case["prior_art"], case["demand"], case["evidence"], mapping
    )
    assert decision == "NO_GO"
    assert "tailwind" in reason
    assert "Method: functional-equivalence." in reason


def test_anki_licence_file_does_not_cover_vocabulary():
    case = _case("shubhamkarande/VocaPop")
    mapping = {
        "capabilities": [
            {
                "id": "cards",
                "statement": "Flashcards.",
                "citation": "flashcard",
                "evidence_id": "candidate:shubhamkarande/VocaPop",
            },
            {
                "id": "vocab",
                "statement": "Vocabulary learning.",
                "citation": "learning vocabulary",
                "evidence_id": "candidate:shubhamkarande/VocaPop",
            },
        ],
        "mappings": [
            {
                "capability_id": "cards",
                "alternative_url": "https://github.com/ankitects/anki",
                "status": "covered",
                "quote": "spaced repetition flashcard program",
                "evidence_id": "prior:ankitects/anki",
            },
            {
                "capability_id": "vocab",
                "alternative_url": "https://github.com/ankitects/anki",
                "status": "missing",
                "quote": "",
                "evidence_id": "prior:ankitects/anki",
            },
        ],
    }
    decision, reason = judge_discovery(
        case["candidate"], case["prior_art"], case["demand"], case["evidence"], mapping
    )
    assert decision == "NO_GO"
    assert "vocab" in reason
    assert "GNU Affero General Public License" not in reason or "vocab" in reason
