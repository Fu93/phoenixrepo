from pathlib import Path

from config.settings import Settings


def test_env_example_exists():
    assert Path(".env.example").exists()


def test_model_name_overrides_meterless_model(monkeypatch):
    monkeypatch.delenv("MODEL_NAME", raising=False)
    monkeypatch.delenv("METERLESS_MODEL", raising=False)
    assert Settings(_env_file=None).requested_model() == "meterless/auto"
    monkeypatch.setenv("METERLESS_MODEL", "meterless/fast")
    assert Settings(_env_file=None).requested_model() == "meterless/fast"
    monkeypatch.setenv("MODEL_NAME", "nvidia/nemotron-3-super-120b-a12b")
    assert Settings(_env_file=None).requested_model() == "nvidia/nemotron-3-super-120b-a12b"


def test_env_example_does_not_contain_real_secret():
    content = Path(".env.example").read_text(encoding="utf-8")
    for token in ("sk-", "AIza", "Bearer "):
        assert token not in content
