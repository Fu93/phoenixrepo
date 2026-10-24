import csv
from pathlib import Path

from scripts.run_sharded import make_shards, merge


def test_make_shards_disjoint_and_complete(tmp_path: Path):
    snap = tmp_path / "snap"
    snap.mkdir()
    (snap / "prior_art.csv").write_text("repo_id\n", encoding="utf-8")
    with (snap / "discovery_candidates.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["repo_id", "description"])
        for i in range(10):
            w.writerow([f"o/r{i}", "d"])
    shards = make_shards(snap, tmp_path / "out", 4)
    ids = []
    for d in shards:
        assert (d / "prior_art.csv").exists()
        ids += [r["repo_id"] for r in csv.DictReader((d / "discovery_candidates.csv").open(encoding="utf-8"))]
    assert sorted(ids) == sorted(f"o/r{i}" for i in range(10))
    assert len(ids) == len(set(ids))


def test_merge_sums_model_calls_and_never_autoselects_protagonist():
    parts = [
        {"run_id": "x-s0", "llm_used": True, "model_calls": {"m": 2}, "candidates": [{"repo_id": "a"}], "ineligible": []},
        {"run_id": "x-s1", "llm_used": True, "model_calls": {"m": 3, "n": 1}, "candidates": [], "ineligible": [{"repo_id": "b", "verdict": "NEEDS_REVIEW"}]},
    ]
    m = merge("x", parts, Path("."))
    assert m["model_calls"] == {"m": 5, "n": 1}
    assert [c["repo_id"] for c in m["candidates"]] == ["a"]
    assert m["protagonist"] is None


def test_count_unjudged_counts_only_unjudged():
    from scripts.run_sharded import count_unjudged

    merged = {"ineligible": [{"verdict": "UNJUDGED"}, {"verdict": "NEEDS_REVIEW"}, {"verdict": "UNJUDGED"}, {"verdict": "ALREADY_EXISTS"}]}
    assert count_unjudged(merged) == 2
    assert count_unjudged({}) == 0
