import json
import re
from pathlib import Path

from app.agents.upgrade_planner import (
    build_upgrade_prd,
    classify_issue,
    in_range,
    render_upgrade_markdown,
    resolution_plan,
    select_issues,
)
from scripts.collect_hygen_upgrade import RAW, assemble, reduce_audit
from scripts.write_upgrade_prd import write_upgrade_prd

VERSION = re.compile(r"\d+\.\d+\.\d+")
GHSA = re.compile(r"GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}")


def test_range_and_resolution_floors_follow_the_audit_strings():
    assert in_range("5.0.0", ">=5.0.0 <5.0.1") is True
    assert in_range("5.0.1", ">=5.0.0 <5.0.1") is False
    assert in_range("2.1.1", ">=5.0.0 <5.0.1") is False
    assert in_range("3.0.4", "<3.1.4") is True
    assert in_range("5.1.9", "<3.1.4") is False
    assert in_range("4.1.0", ">=3.0.0 <3.15.2") is False
    assert in_range("3.14.0", ">=3.0.0 <3.15.2") is True
    assert in_range("1.0.3", "<=1.1.3") is True
    fixed, unfixed = resolution_plan(
        [
            {"module": "minimatch", "ghsa": "A", "patched": ">=3.0.5", "vulnerable": "<3.0.5"},
            {"module": "minimatch", "ghsa": "B", "patched": ">=3.1.4", "vulnerable": "<3.1.4"},
            {"module": "sprintf-js", "ghsa": "C", "patched": "<0.0.0", "vulnerable": "<=1.1.3"},
        ]
    )
    assert fixed[0]["floor"] == "3.1.4"
    assert unfixed[0]["module"] == "sprintf-js"


def test_issue_classifier_keeps_the_three_concrete_issues():
    snapshot = assemble(RAW)
    selection = select_issues(snapshot)
    assert [issue["number"] for issue in selection["chosen"]] == [396, 96, 411]
    assert [issue["number"] for issue in selection["commit_order"]] == [411, 96, 396]
    assert selection["next_eligible"]["number"] == 316
    reasons = {item["issue"]["number"]: item["reason"] for item in selection["skipped"]}
    assert "governance" in reasons[212]
    assert "status question" in reasons[458]
    assert "question" in reasons[111]
    assert "unspecified" in reasons[106]
    assert "wider than one commit" in reasons[208]
    assert classify_issue({"title": "build fails", "body": "```\nx\n```"}, build_succeeded=True).startswith("not a remaining")


def test_committed_upgrade_prd_matches_the_planner(tmp_path):
    snapshot = assemble(RAW)
    committed_snapshot = json.loads(Path("data/upgrade/jondot__hygen/snapshot.json").read_text(encoding="utf-8"))
    assert snapshot == committed_snapshot
    markdown_path, json_path = write_upgrade_prd(tmp_path, snapshot)
    committed_md = Path("docs/revival/jondot__hygen.upgrade.prd.md").read_text(encoding="utf-8")
    committed_json = json.loads(Path("docs/revival/jondot__hygen.upgrade.prd.json").read_text(encoding="utf-8"))
    assert markdown_path.read_text(encoding="utf-8") == committed_md
    assert json.loads(json_path.read_text(encoding="utf-8")) == committed_json
    document = build_upgrade_prd(snapshot)
    assert render_upgrade_markdown(document) == committed_md
    assert document["drafted_by"] == "deterministic"
    assert document["llm_drafted"] is False
    assert [task["id"] for task in document["tasks"]] == [f"U{index}" for index in range(1, 9)]
    assert [task["issue_number"] for task in document["tasks"] if task["issue_number"]] == [411, 96, 396]


def test_tasks_cite_real_versions_and_label_windows_as_hypothesis():
    snapshot = assemble(RAW)
    document = build_upgrade_prd(snapshot)
    blob = json.dumps(snapshot)
    text = render_upgrade_markdown(document)
    for version in VERSION.findall(text):
        assert version in blob, version
    for ghsa in GHSA.findall(text):
        assert ghsa in blob
    by_id = {task["id"]: task for task in document["tasks"]}
    assert by_id["U1"]["packages"] == ["typescript", "ts-jest", "ts-node", "@types/jest"]
    assert "27.5.1" in by_id["U1"]["change"]
    assert "4.9.5" in by_id["U1"]["change"]
    assert "hypothesis" in by_id["U1"]["hypothesis"]
    assert "GHSA-hp3w-g68c-fv3c" in by_id["U2"]["change"]
    assert "5.0.1" in by_id["U2"]["change"]
    assert "3.1.4" in by_id["U2"]["change"]
    assert "1.1.21" in by_id["U2"]["change"]
    assert "7.0.5" in by_id["U2"]["change"]
    assert "3.15.2" in by_id["U2"]["change"]
    assert "hypothesis" in by_id["U2"]["hypothesis"].lower() or "hypothesis" in by_id["U2"]["hypothesis"]
    assert "GHSA-8mmm-9v2q-x3f9" in by_id["U3"]["change"]
    assert by_id["U7"]["hypothesis"].lower().count("hypothesis") >= 1
    assert "windows-latest" in by_id["U7"]["acceptance"]
    assert "skipped" in by_id["U7"]["change"]
    assert "D:\\a" not in text
    assert "passed on windows" not in text.lower()
    assert "30.5.2" in by_id["U8"]["change"]
    assert "7.0.2" in by_id["U8"]["change"]
    assert "hypothesis" in by_id["U8"]["hypothesis"]
    for task in document["tasks"]:
        assert task["change"]
        assert "yarn" in task["acceptance"]
        assert task["risk"]
        assert task["hypothesis"]
        assert task["citations"]
        assert all(citation["content_hash"] in text for citation in task["citations"])
        assert "cursor/revive-hygen-326e" in task["prompt"]
        assert snapshot["commit"] in task["prompt"]
        assert "ejs" not in task["packages"]
        assert "degit" not in task["packages"]
    assert "142" in text and "731" in text
    assert "https://github.com/Fu93/hygen/actions/runs/39821847516" in text
    assert "https://github.com/jondot/hygen/issues/396" in text
    assert "https://github.com/jondot/hygen/issues/96" in text
    assert "https://github.com/jondot/hygen/issues/411" in text
    assert "Non-factual framing" in text


def test_audit_reducer_counts_a_small_stream():
    payload = "\n".join(
        [
            json.dumps(
                {
                    "type": "auditAdvisory",
                    "data": {
                        "advisory": {
                            "id": 1,
                            "github_advisory_id": "GHSA-aaaa-bbbb-cccc",
                            "module_name": "left",
                            "severity": "high",
                            "vulnerable_versions": "<1.0.0",
                            "patched_versions": ">=1.0.0",
                            "title": "x",
                        },
                        "resolution": {"path": "left"},
                    },
                }
            ),
            json.dumps(
                {
                    "type": "auditSummary",
                    "data": {"vulnerabilities": {"info": 0, "low": 0, "moderate": 0, "high": 1, "critical": 0}, "totalDependencies": 1},
                }
            ),
        ]
    )
    payload += "\n" + json.dumps(
        {
            "type": "auditAdvisory",
            "data": {
                "advisory": {
                    "id": 2,
                    "github_advisory_id": "GHSA-dddd-eeee-ffff",
                    "module_name": "js-yaml",
                    "severity": "moderate",
                    "vulnerable_versions": "<3.14.2",
                    "patched_versions": ">=3.14.2",
                    "title": "three",
                },
                "resolution": {"path": "front-matter>js-yaml"},
            },
        }
    )
    reduced = reduce_audit(payload)
    assert reduced["advisory_rows"] == 2
    assert reduced["high_critical_by_root"] == {"left": 1}
    assert reduced["direct_advisories"][0]["ghsa"] == "GHSA-aaaa-bbbb-cccc"
    assert reduced["js_yaml_4x"] == []
