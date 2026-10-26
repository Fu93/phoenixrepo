"""Judge-facing docs stay consistent with the frozen gate-1 facts.

These checks lock sentences a later number-fill must not contradict.
They do not score a live run and they do not invent one.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

README = (ROOT / "README.md").read_text(encoding="utf-8")
DRAFT = (ROOT / "docs" / "SUBMISSION_DRAFT.md").read_text(encoding="utf-8")
SCRIPT = (ROOT / "docs" / "VIDEO_SCRIPT.md").read_text(encoding="utf-8")
CHECKLIST = (ROOT / "docs" / "HACKOS_CHECKLIST.md").read_text(encoding="utf-8")

ZETARIS_REPOS = (
    "tharani-2006/Invoice-generator-application",
    "SergioMendolia/ytkb",
)
FORBIDDEN_ZETARIS_CLAIMS = (
    "all 16 went through Zetaris",
    "all 16 evaluation repositories went through Zetaris",
    "all 16 repositories went through Zetaris",
)


def _positive_zetaris_claims(text: str) -> str:
    """Drop the required denials so a later edit cannot add the claim beside them."""

    folded = " ".join(text.split()).lower()
    for denial in (
        "does not claim that all 16 went through zetaris",
        "does not claim that all 16 evaluation repositories went through zetaris",
        "does not claim that all 16 repositories went through zetaris",
        "it does not claim that all 16 evaluation repositories went through zetaris",
    ):
        folded = folded.replace(denial, "")
    return folded


def test_zetaris_scope_is_the_two_data_real_repos_in_every_judge_facing_doc():
    for name, text in (("README", README), ("draft", DRAFT), ("video", SCRIPT)):
        for repo in ZETARIS_REPOS:
            assert repo in text, name
        assert "evidence_source=csv" in text, name
        assert "scripts/zetaris_check.py" in text, name
        assert "16 evaluation" in text, name
        remaining = _positive_zetaris_claims(text)
        for claim in FORBIDDEN_ZETARIS_CLAIMS:
            assert claim not in remaining, name


def test_readme_has_results_and_why_nemotron_with_visible_tbd_slots():
    assert "## Results" in README
    assert "## Why Nemotron" in README
    assert "Best use of NVIDIA Nemotron" in README
    assert "PYTHONPATH=. python scripts/eval_summary.py logs/eval-nemotron-v2.json --markdown" in README
    assert "A live run on one machine, not a stored accuracy." in README
    assert "PASTE eval_summary --markdown OUTPUT HERE" in README
    for marker in (
        "<!-- TODO(numbers): eval-summary-table -->",
        "<!-- TODO(numbers): nemotron-answer-share -->",
        "<!-- TODO(numbers): provider-split -->",
        "<!-- TODO(numbers): calendarsync-final-verdict -->",
        "<!-- TODO(numbers): calendarsync-final-run-hash -->",
    ):
        assert marker in README
        # The HTML comment is invisible on GitHub; the word TBD next to it is the visible slot.
        after = README.split(marker, 1)[1][:400]
        assert "TBD" in after
    assert "answers by NVIDIA Nemotron" in README
    assert "nvidia-direct" in README
    assert "PHOENIX_NEMOTRON_POLICY" in README
    assert "MODEL_NAME_DEEP" in README
    assert "PHOENIX_LLM_EXTRA_BODY" in README
    assert "PHOENIX_LLM_DEEP_EXTRA_BODY" in README
    assert "does not pattern-match" in README
    assert "BUDGET_EXHAUSTED` can never produce `CANDIDATE`" in README


def test_calendarsync_story_is_a_template_and_names_both_observed_verdicts():
    for text in (README, DRAFT):
        assert "DSRegoitia/CalendarSyncProject" in text
        assert "displays public Google Calendar events" in text
        assert "WRONG" in text
        assert "two independent runs" in text.lower() or "Two independent runs" in text
        assert "CANDIDATE" in text and "NEEDS_REVIEW" in text
        assert "<!-- TODO(numbers): calendarsync-final-verdict -->" in text
        assert "<!-- TODO(numbers): calendarsync-final-run-hash -->" in text


def test_video_script_is_three_minutes_and_ends_on_limits():
    assert "0:00–0:25" in SCRIPT
    assert "2:48–3:00" in SCRIPT
    assert "https://fu93.github.io/phoenixrepo/" in SCRIPT
    assert "Plop" in SCRIPT and "Yeoman" in SCRIPT
    assert "hygen" in SCRIPT
    assert "verify_trace" in SCRIPT
    assert "<!-- TODO(numbers): nemotron-answer-share -->" in SCRIPT
    assert "<!-- TODO(numbers): provider-split -->" in SCRIPT
    assert "What this cut does not claim" in SCRIPT


def test_submission_draft_keeps_its_sections_and_leaves_only_number_slots_open():
    for heading in (
        "## (i) Video script, 3:00",
        "## (ii) Project description",
        "## (iii) \"Built before the event\" declaration",
        "## (iv) AI-assistance disclosure",
        "## (v) Remaining steps, in order",
    ):
        assert heading in DRAFT
    assert "62a1540" in DRAFT
    assert "<!-- TODO(numbers): build-window-commit-count -->" in DRAFT
    assert "12:00 UTC" in DRAFT and "21:00 UTC" in DRAFT and "23:45 UTC" in DRAFT
    assert "docs/VIDEO_SCRIPT.md" in DRAFT


def test_hackos_checklist_has_the_track_the_skeleton_and_the_deadlines():
    assert "Track 02" in CHECKLIST
    assert "62a1540" in CHECKLIST
    assert "1 to 4 minutes" in CHECKLIST
    assert "27 Oct 2026 12:00 UTC" in CHECKLIST
    assert "27 Oct 2026 21:00 UTC" in CHECKLIST
    assert "27 Oct 2026 23:45 UTC" in CHECKLIST
