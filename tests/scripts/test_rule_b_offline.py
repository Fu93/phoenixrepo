from scripts.rule_b_offline import summary


def test_safety_holds_do_not_get_counted_as_correct_refusals():
    rows = [
        {"off": "CANDIDATE", "on": "NEEDS_REVIEW", "acceptable": ["ALREADY_EXISTS"]},
        {"off": "CANDIDATE", "on": "NEEDS_REVIEW", "acceptable": ["CANDIDATE", "NEEDS_REVIEW"]},
        {"off": "NEEDS_REVIEW", "on": "NEEDS_REVIEW", "acceptable": ["CANDIDATE", "NEEDS_REVIEW"]},
    ]
    result = summary(rows)
    assert result["agreement_on"] == result["agreement_off"] == 2
    assert result["automatic_false_passes_on"] == 0
    assert result["refusal_label_errors_on"] == 1
    assert result["additional_reviews_all"] == 2
    assert result["additional_reviews_previously_agreeing"] == 1
    assert result["review_verdicts_off"] == 1
    assert result["review_verdicts_on"] == 3
