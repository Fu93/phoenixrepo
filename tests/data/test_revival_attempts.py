import json
from pathlib import Path

from app.sql_catalog import ATTEMPT_COLUMNS, SOURCE_COLUMNS


def test_attempt_snapshot_records_the_fork_api_facts():
    provenance = json.loads(Path("data/real/revival_attempts_provenance.json").read_text(encoding="utf-8"))
    assert provenance["token_used"] is False
    assert provenance["token_written"] is False
    assert provenance["fork"] == "Fu93/hygen"
    assert provenance["upstream"] == "jondot/hygen"
    assert provenance["fork_default_branch"] == "master"
    assert provenance["issue_number"] == 450
    blob = json.dumps(provenance)
    assert "Bearer " not in blob
    assert "ghp_" not in blob
    assert "github_pat_" not in blob
    statuses = provenance["statuses"]
    assert statuses["fork"] == 200
    assert statuses["compare"] == 200
    assert statuses["issue"] == 200
    assert statuses["runs"] == 200


def test_attempt_and_source_columns_match_the_sql_contract():
    import csv

    with Path("data/real/revival_attempts.csv").open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert tuple(reader.fieldnames or ()) == ATTEMPT_COLUMNS
        attempts = list(reader)
    with Path("data/real/revival_sources.csv").open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert tuple(reader.fieldnames or ()) == SOURCE_COLUMNS
        sources = list(reader)
    assert [row["stage"] for row in attempts] == ["revive", "refactor", "upgrade"]
    issue = next(row for row in sources if row["source_id"] == "issue-450")
    assert issue["url"] == "https://github.com/jondot/hygen/issues/450"
    assert issue["title"] == "hygen --version treated as error"
    assert "Error: please specify a generator." in issue["excerpt"]
    compare = next(row for row in sources if row["kind"] == "compare")
    assert "ahead_by 0" in compare["excerpt"]
    assert compare["url"] == "https://github.com/jondot/hygen/compare/master...Fu93:master"
    actions = next(row for row in sources if row["kind"] == "actions")
    assert "total_count 0" in actions["excerpt"]
