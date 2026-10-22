import pytest

from adapters.factory import build_adapters
from app.errors import ZetarisError
from config.settings import Settings


def test_sample_adapters_are_integrated_without_credentials():
    zetaris, meterless = build_adapters(Settings(sample_mode=True), "sample")
    assert zetaris.provider() == "zetaris"
    assert meterless.provider() == "meterless"
    assert zetaris.enabled() is True
    assert zetaris.status()["integration"] == "SAMPLE_CACHE"
    assert zetaris.status()["load_bearing"] is True
    assert meterless.status()["integration"] == "OPENAI_COMPATIBLE_RUNTIME"
    assert meterless.status()["primary"] is True
    assert meterless.enabled() is False


def test_sample_zetaris_runs_sql_locally():
    zetaris, _meterless = build_adapters(Settings(sample_mode=True), "sample")
    result = zetaris.run_sql(
        "SELECT repo_id, licence_spdx FROM phoenix.repo_evidence WHERE repo_id = 'sample/phoenix-ledger'"
    )
    assert result.mode == "sample-cache"
    assert result.rows[0]["licence_spdx"] == "MIT"


def test_meterless_without_a_token_does_not_call_the_network():
    _zetaris, meterless = build_adapters(Settings(sample_mode=True), "sample")
    with pytest.raises(Exception) as caught:
        meterless.complete([{"role": "user", "content": "hello"}])
    assert "token" in str(caught.value).lower()


def test_live_zetaris_without_a_key_cannot_query():
    zetaris, _meterless = build_adapters(Settings(sample_mode=False, zetaris_api_key=None), "live")
    assert zetaris.enabled() is False
    with pytest.raises(ZetarisError):
        zetaris.run_sql("SELECT 1")
