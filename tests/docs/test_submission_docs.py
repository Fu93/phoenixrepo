"""Judge-facing docs stay consistent with the frozen gate-1 facts.

These checks lock claims a later number-fill must not contradict. They do not require the
TODO(numbers) slots to stay empty: filling a slot with a number from the live file is the
point of the slot, and must not break CI the night before submission.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

README = (ROOT / "README.md").read_text(encoding="utf-8")
DRAFT = (ROOT / "docs" / "SUBMISSION_DRAFT.md").read_text(encoding="utf-8")
SCRIPT = (ROOT / "docs" / "VIDEO_SCRIPT.md").read_text(encoding="utf-8")
CHECKLIST = (ROOT / "docs" / "HACKOS_CHECKLIST.md").read_text(encoding="utf-8")
JUDGE_FACING = (("README", README), ("draft", DRAFT), ("video", SCRIPT))

ZETARIS_REPOS = ("tharani-2006/Invoice-generator-application", "SergioMendolia/ytkb")
# zetaris_check.py calls no model, so it cannot be described as producing verdicts, and the
# 16 evaluation repositories were fetched live with evidence_source=csv.
FORBIDDEN = (
    "went through zetaris for gate-1 verdicts",
    "all 16 went through zetaris",
    "all 16 evaluation repositories went through zetaris",
    "all 16 repositories went through zetaris",
    "tests reported passing on current node",
)
DENIALS = (
    "does not claim that all 16 went through zetaris",
    "does not claim that all 16 evaluation repositories went through zetaris",
)


def _positive(text: str) -> str:
    folded = " ".join(text.split()).lower()
    for denial in DENIALS:
        folded = folded.replace(denial, "")
    return folded


def test_no_judge_facing_doc_overstates_zetaris_or_hygen():
    for name, text in JUDGE_FACING:
        remaining = _positive(text)
        for claim in FORBIDDEN:
            assert claim not in remaining, (name, claim)


def test_readme_and_draft_name_the_zetaris_scope():
    for name, text in (("README", README), ("draft", DRAFT)):
        for repo in ZETARIS_REPOS:
            assert repo in text, name
        assert "evidence_source=csv" in text, name
        assert "scripts/zetaris_check.py" in text, name
        assert "writes no verdict" in text, name


def test_readme_results_and_why_nemotron():
    assert "## Results" in README
    assert "## Why Nemotron" in README
    assert "Best use of NVIDIA Nemotron" in README
    assert "scripts/fill_results.py logs/eval-nemotron-v3.json --write" in README
    for slot in ("eval-summary-table", "per-case-table", "nemotron-answer-share", "provider-split",
                 "calendarsync-final-verdict", "calendarsync-final-run-hash", "zetaris-gate1-run"):
        assert f"<!-- BEGIN numbers: {slot} -->" in README and f"<!-- END numbers: {slot} -->" in README, slot
    assert "A live run on one machine, not a stored accuracy." in README
    for needle in ("nvidia-direct", "PHOENIX_NEMOTRON_POLICY", "MODEL_NAME_DEEP", "--require-nemotron",
                   "nvidia/nemotron-3-ultra-550b-a55b", "BUDGET_EXHAUSTED` can never produce `CANDIDATE`"):
        assert needle in README, needle


def test_calendarsync_story_names_both_observed_verdicts():
    for text in (README, DRAFT, SCRIPT):
        assert "CalendarSync" in text
        assert "displays public Google Calendar events" in text
    for text in (README, SCRIPT):
        assert "CANDIDATE" in text and "NEEDS_REVIEW" in text


def test_video_script_is_three_minutes_and_ends_on_limits():
    assert "0:00–0:20" in SCRIPT
    assert "2:50–3:00" in SCRIPT
    assert "https://fu93.github.io/phoenixrepo/" in SCRIPT
    assert "--require-nemotron" in SCRIPT
    assert "What this cut does not claim" in SCRIPT
    assert SCRIPT.rstrip().count("\n") < 60


def test_submission_draft_sections_and_deadlines():
    for heading in (
        "## (ii) Project description",
        "## (iii) \"Built before the event\" declaration",
        "## (iv) AI-assistance disclosure",
        "## (v) Remaining steps, in order",
    ):
        assert heading in DRAFT
    assert "62a1540" in DRAFT
    assert "12:00 UTC" in DRAFT and "21:00 UTC" in DRAFT and "23:45 UTC" in DRAFT
    assert "docs/VIDEO_SCRIPT.md" in DRAFT


def test_hackos_checklist_has_the_track_the_skeleton_and_the_deadlines():
    for needle in ("Track 02", "62a1540", "1 to 4 minutes", "27 Oct 2026 12:00 UTC", "27 Oct 2026 21:00 UTC", "27 Oct 2026 23:45 UTC"):
        assert needle in CHECKLIST, needle
