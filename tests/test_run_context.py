from run.context import RunContext


def test_run_context_has_unique_run_id():
    a = RunContext.create("sample://repo", "sample", 0)
    b = RunContext.create("sample://repo", "sample", 0)
    assert a.run_id != b.run_id


def test_same_configuration_has_same_fingerprint():
    a = RunContext.create("sample://repo", "sample", 0)
    b = RunContext.create("sample://repo", "sample", 0)
    assert a.fingerprint == b.fingerprint


def test_different_configuration_changes_fingerprint():
    a = RunContext.create("sample://repo", "sample", 0)
    b = RunContext.create("sample://repo", "sample", 1)
    assert a.fingerprint != b.fingerprint
