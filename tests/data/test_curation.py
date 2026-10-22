import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.curate_real_repos import (
    build_profile,
    build_signals,
    close_substitute_query,
    days_between,
    latest_conclusion,
    path_shows_tests,
    root_names,
    successor_from_readme,
    workflows_in_paths,
    write_csv,
)
from app.agents.policy import decide, normalize_profile, normalize_signal
from app.sql_catalog import PROFILE_COLUMNS, SIGNAL_COLUMNS

COLLECTED = datetime(2026, 10, 22, tzinfo=timezone.utc)


def _repo(**overrides):
    payload = {
        "full_name": "example/widget",
        "description": None,
        "license": None,
        "language": None,
        "stargazers_count": None,
        "forks_count": None,
        "archived": None,
        "pushed_at": None,
        "open_issues_count": None,
        "html_url": None,
    }
    payload.update(overrides)
    return payload


def test_null_api_fields_stay_empty():
    row = build_profile(
        _repo(),
        contents=["README.md"],
        tree=["README.md"],
        tree_truncated=False,
        actions={"workflow_runs": []},
        workflows_present=False,
        topics=[],
        similar_count="",
        readme="This project lives at https://example.com.",
        collected_at=COLLECTED,
    )
    assert row["description"] == ""
    assert row["licence_spdx"] == ""
    assert row["primary_language"] == ""
    assert row["stars"] == ""
    assert row["days_since_push"] == ""
    assert row["ci_status"] == ""
    assert row["has_ci"] == "false"
    assert row["similar_repo_count"] == ""
    assert row["unique_capability"] == ""
    assert row["successor_url"] == ""
    assert row["archived"] == ""


def test_missing_archived_key_stays_empty():
    repo = _repo()
    del repo["archived"]
    row = build_profile(
        repo,
        contents=[],
        tree=[],
        tree_truncated=False,
        actions={"workflow_runs": [{"conclusion": None}]},
        workflows_present=False,
        topics=[],
        similar_count="",
        readme="",
        collected_at=COLLECTED,
    )
    assert row["archived"] == ""
    assert row["ci_status"] == ""
    assert row["has_ci"] == "true"


def test_readme_successor_ignores_homepage_and_badges():
    readme = "\n".join(
        [
            "[build](https://img.shields.io/badge/build-passing)",
            "Deprecated in favor of Go modules.",
            "See https://golang.org/ref/mod for the replacement.",
        ]
    )
    url, snippet = successor_from_readme(readme)
    assert url == "https://golang.org/ref/mod"
    assert "in favor of" in snippet.lower()
    assert successor_from_readme("Homepage: https://golang.github.io/dep/") == ("", "")


def test_close_substitute_query_is_not_a_topic_total():
    query = close_substitute_query("dep", "Go dependency management tool", "Go")
    assert query.startswith("language:Go ")
    assert "topic:" not in query
    assert "in:name,description" in query
    assert close_substitute_query("dep", "", "Go") == "language:Go dep in:name"


def test_test_paths_and_workflow_files():
    assert path_shows_tests("pkg/widget_test.go")
    assert path_shows_tests("tests/test_widget.py")
    assert not path_shows_tests("src/widget.go")
    assert workflows_in_paths([".github/workflows/ci.yml"])
    assert not workflows_in_paths([".github/ISSUE_TEMPLATE.md"])
    assert root_names(["README.md", "src/main.go"]) == ["README.md"]


def test_search_failure_leaves_similar_count_empty_and_signals_do_not_invent_ci():
    row = build_profile(
        _repo(
            full_name="example/quiet",
            description="A small tool",
            license={"spdx_id": "MIT"},
            language="Python",
            stargazers_count=3,
            forks_count=1,
            archived=True,
            pushed_at="2020-01-01T00:00:00Z",
            open_issues_count=0,
            html_url="https://github.com/example/quiet",
        ),
        contents=["README.md", "LICENSE"],
        tree=["README.md", "LICENSE", "quiet.py"],
        tree_truncated=False,
        actions={"workflow_runs": []},
        workflows_present=False,
        topics=["logging"],
        similar_count="",
        readme="Moved to https://example.com/next.",
        collected_at=COLLECTED,
    )
    assert row["similar_repo_count"] == ""
    assert row["licence_spdx"] == "MIT"
    assert row["has_tests"] == "false"
    assert row["successor_url"] == "https://example.com/next"
    assert days_between("2020-01-01T00:00:00Z", COLLECTED) == row["days_since_push"]
    signals = {item["signal"]: item for item in build_signals(row)}
    assert "ci_failure" not in signals
    assert "ci_unknown" in signals
    assert "not a failure" in signals["ci_unknown"]["detail"]
    assert "homepage" not in signals["successor_recorded"]["detail"]
    assert "policy bar of 40" not in signals["alternatives_factor"]["detail"]
    assert latest_conclusion({"workflow_runs": []}) == ("", False)
    assert latest_conclusion("nope") == (None, None)


def test_committed_snapshot_matches_the_view_schema_and_provenance():
    root = Path("data/real")
    provenance = json.loads((root / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["token_written"] is False
    assert "collected_at" in provenance
    assert provenance["calls"]
    blob = json.dumps(provenance)
    assert "ghp_" not in blob
    assert "github_pat_" not in blob
    assert "Bearer " not in blob
    with (root / "repo_evidence.csv").open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert tuple(reader.fieldnames or ()) == PROFILE_COLUMNS
        rows = {row["repo_id"]: row for row in reader}
    with (root / "revival_signals.csv").open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert tuple(reader.fieldnames or ()) == SIGNAL_COLUMNS
    assert set(rows) == {
        "golang/dep",
        "jxnblk/rgx",
        "discreteoptimization/setcover",
        "jondot/hygen",
        "42wim/matterbridge",
    }
    notes = {item["full_name"]: item for item in provenance["repos"]}
    signals_by_repo: dict[str, list[dict[str, str]]] = {}
    with (root / "revival_signals.csv").open(newline="", encoding="utf-8") as handle:
        for signal in csv.DictReader(handle):
            signals_by_repo.setdefault(signal["repo_id"], []).append(signal)
    verdicts = {}
    for repo_id, row in rows.items():
        homepage = notes[repo_id]["homepage"]
        if homepage:
            assert row["successor_url"] != homepage
        assert row["data_origin"].startswith("github_rest_api:")
        assert "topic:" not in notes[repo_id]["similar_query"]
        if row["ci_status"] not in {"", "success", "failure", "cancelled", "timed_out", "startup_failure"}:
            raise AssertionError(f"unexpected ci_status {row['ci_status']}")
        decision, reason = decide(
            normalize_profile(row),
            [normalize_signal(item) for item in signals_by_repo[repo_id]],
        )
        verdicts[repo_id] = (decision, reason)
    assert set(decision for decision, _reason in verdicts.values()) == {"NO_GO", "RESURRECTION_FAILED", "RESURRECTED"}
    dep_decision, dep_reason = verdicts["golang/dep"]
    assert dep_decision == "NO_GO"
    assert dep_reason.startswith("a successor URL is recorded")
    assert rows["golang/dep"]["licence_spdx"] == "BSD-3-Clause"
    assert rows["golang/dep"]["successor_url"] == "https://golang.org/ref/mod"
    rgx_decision, rgx_reason = verdicts["jxnblk/rgx"]
    assert rgx_decision == "NO_GO"
    assert rgx_reason.startswith("licence missing is not in the permissive set")
    assert rows["jxnblk/rgx"]["licence_spdx"] == ""
    setcover_decision, setcover_reason = verdicts["discreteoptimization/setcover"]
    assert setcover_decision == "RESURRECTION_FAILED"
    assert setcover_reason.startswith("has_tests is false")
    assert rows["discreteoptimization/setcover"]["archived"] == "false"
    hygen_decision, hygen_reason = verdicts["jondot/hygen"]
    assert hygen_decision == "RESURRECTION_FAILED"
    assert hygen_reason.startswith("ci_status is failure")
    assert notes["jondot/hygen"]["ci_run_event"] == "schedule"
    assert notes["jondot/hygen"]["ci_run_url"] == "https://github.com/jondot/hygen/actions/runs/37141993934"
    bridge_decision, bridge_reason = verdicts["42wim/matterbridge"]
    assert bridge_decision == "RESURRECTED"
    assert "successful CI conclusion" in bridge_reason
    assert rows["42wim/matterbridge"]["archived"] == "false"
    assert rows["42wim/matterbridge"]["licence_spdx"] == "Apache-2.0"
    assert notes["42wim/matterbridge"]["ci_run_name"] == "CodeQL"
    assert notes["42wim/matterbridge"]["ci_run_url"] == "https://github.com/42wim/matterbridge/actions/runs/39323383247"
    assert "golang/dep" in {item["full_name"] for item in provenance["repos"]}
    assert any(call["url"].endswith("/repos/golang/dep") for call in provenance["calls"])


def test_write_csv_drops_private_columns(tmp_path):
    path = tmp_path / "repo_evidence.csv"
    write_csv(path, PROFILE_COLUMNS, [{"repo_id": "a/b", "_successor_snippet": "secret-looking", "licence_spdx": ""}])
    text = path.read_text(encoding="utf-8")
    assert "_successor_snippet" not in text
    assert "secret-looking" not in text
