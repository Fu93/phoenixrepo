from pathlib import Path

from app.adapters.sample_zetaris import SampleZetaris


def test_default_cache_can_read_the_real_snapshot_without_relabelling_it():
    client = SampleZetaris()
    result = client.run_sql(
        "SELECT repo_id, licence_spdx, data_origin FROM phoenix.repo_evidence WHERE repo_id = 'jxnblk/rgx'"
    )
    assert result.mode == "sample-cache"
    assert result.rows[0]["licence_spdx"] == ""
    assert result.rows[0]["data_origin"].startswith("github_rest_api:")


def test_explicit_cache_dir_does_not_mix_in_real_rows(tmp_path):
    sample = Path("data/sample")
    cache = tmp_path / "cache"
    cache.mkdir()
    for name in ("repo_evidence.csv", "revival_signals.csv"):
        (cache / name).write_text((sample / name).read_text(encoding="utf-8"), encoding="utf-8")
    client = SampleZetaris(cache_dir=cache)
    result = client.run_sql("SELECT repo_id FROM phoenix.repo_evidence WHERE repo_id = 'golang/dep'")
    assert result.rows == []
