import pytest

from adapters.factory import build_adapters
from config.settings import Settings


def test_sample_adapters_are_not_integrated():
    zetaris, meterless = build_adapters(Settings(sample_mode=True), "sample")
    assert zetaris.provider() == "zetaris"
    assert meterless.provider() == "meterless"
    assert zetaris.enabled() is False
    assert meterless.enabled() is False
    assert zetaris.status()["integrated"] is False
    assert meterless.status()["integrated"] is False


def test_adapters_do_not_call_sponsors():
    zetaris, meterless = build_adapters(Settings(), "sample")
    with pytest.raises(NotImplementedError):
        zetaris.collect("sample://phoenix")
    with pytest.raises(NotImplementedError):
        meterless.record("RUN_STARTED", {"run_id": "test"})
