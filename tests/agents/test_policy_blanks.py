from app.agents.policy import decide, normalize_profile, normalize_signal


def _row(**overrides) -> dict:
    row = {
        "repo_id": "sample/phoenix-ledger",
        "licence_spdx": "MIT",
        "days_since_push": "640",
        "similar_repo_count": "2",
        "unique_capability": "offline ledger",
        "successor_url": "",
        "has_tests": "True",
        "has_license_file": "True",
        "ci_status": "success",
        "category": "accounting",
        "description": "ledger",
        "data_origin": "synthetic_labelled",
    }
    row.update(overrides)
    return normalize_profile(row)


def test_null_markers_are_empty_policy_strings():
    profile = _row(
        licence_spdx=" None ",
        unique_capability="null",
        successor_url="NaN",
        ci_status="NULL",
        description=" none ",
        category=" nan ",
    )
    assert profile["licence_spdx"] == ""
    assert profile["unique_capability"] == ""
    assert profile["successor_url"] == ""
    assert profile["ci_status"] == ""
    assert profile["description"] == ""
    assert profile["category"] == ""
    assert profile["days_since_push"] == 640


def test_null_successor_does_not_invent_a_successor():
    profile = _row(successor_url=" null ")
    signals = [
        normalize_signal(
            {
                "repo_id": "sample/phoenix-ledger",
                "signal": "tests_present",
                "detail": " none ",
                "supports_revival": "True",
            }
        )
    ]
    assert signals[0]["detail"] == ""
    decision, _reason = decide(profile, signals)
    assert decision == "RESURRECTED"


def test_null_capability_and_licence_are_no_go():
    assert decide(_row(unique_capability="NaN"), [])[0] == "NO_GO"
    assert decide(_row(licence_spdx="NULL"), [])[0] == "NO_GO"


def test_real_licence_and_capability_are_kept():
    profile = _row()
    assert profile["licence_spdx"] == "MIT"
    assert profile["unique_capability"] == "offline ledger"
    assert profile["ci_status"] == "success"
