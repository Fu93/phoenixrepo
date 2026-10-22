import os
import subprocess
import sys
from pathlib import Path

import pytest

from adapters.factory import build_adapters
from app.errors import ZetarisError
from config.settings import Settings

_SECRET_ENV = ("ZETARIS_API_KEY", "METERLESS_RUNTIME_TOKEN", "NVIDIA_API_KEY", "MODEL_API_KEY")


def _offline_settings(**overrides) -> Settings:
    """Ignore the process environment and any .env in the working directory."""

    for name in _SECRET_ENV:
        os.environ.pop(name, None)
    overrides.setdefault("_env_file", None)
    return Settings(**overrides)


def _forbid_meterless_http():
    import app.adapters.meterless as meterless_mod

    original = meterless_mod.httpx.Client

    class Refuse:
        def __init__(self, *args, **kwargs):
            raise AssertionError("test opened an HTTP client for Meterless")

    meterless_mod.httpx.Client = Refuse
    return meterless_mod, original


def test_sample_adapters_are_integrated_without_credentials(monkeypatch):
    for name in _SECRET_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("METERLESS_RUNTIME_TOKEN", "dummy-runtime-token")
    monkeypatch.setenv("ZETARIS_API_KEY", "dummy-zetaris-key")
    monkeypatch.setenv("NVIDIA_API_KEY", "dummy-nvidia-key")
    module, original = _forbid_meterless_http()
    try:
        zetaris, meterless = build_adapters(_offline_settings(sample_mode=True), "sample")
        assert zetaris.provider() == "zetaris"
        assert meterless.provider() == "meterless"
        assert zetaris.enabled() is True
        assert zetaris.status()["integration"] == "SAMPLE_CACHE"
        assert zetaris.status()["load_bearing"] is True
        assert meterless.status()["integration"] == "OPENAI_COMPATIBLE_RUNTIME"
        assert meterless.status()["primary"] is True
        assert meterless.enabled() is False
        with pytest.raises(Exception) as caught:
            meterless.complete([{"role": "user", "content": "hello"}])
    finally:
        module.httpx.Client = original
    assert "token" in str(caught.value).lower()


def test_sample_zetaris_runs_sql_locally(monkeypatch):
    for name in _SECRET_ENV:
        monkeypatch.delenv(name, raising=False)
    zetaris, _meterless = build_adapters(_offline_settings(sample_mode=True), "sample")
    result = zetaris.run_sql(
        "SELECT repo_id, licence_spdx FROM phoenix.repo_evidence WHERE repo_id = 'sample/phoenix-ledger'"
    )
    assert result.mode == "sample-cache"
    assert result.rows[0]["licence_spdx"] == "MIT"


def test_meterless_without_a_token_does_not_call_the_network(monkeypatch):
    for name in _SECRET_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("METERLESS_RUNTIME_TOKEN", "dummy-runtime-token")
    monkeypatch.setenv("ZETARIS_API_KEY", "dummy-zetaris-key")
    monkeypatch.setenv("NVIDIA_API_KEY", "dummy-nvidia-key")
    module, original = _forbid_meterless_http()
    try:
        _zetaris, meterless = build_adapters(_offline_settings(sample_mode=True), "sample")
        assert meterless.enabled() is False
        with pytest.raises(Exception) as caught:
            meterless.complete([{"role": "user", "content": "hello"}])
    finally:
        module.httpx.Client = original
    assert "token" in str(caught.value).lower()


def test_live_zetaris_without_a_key_cannot_query(monkeypatch):
    for name in _SECRET_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ZETARIS_API_KEY", "dummy-zetaris-key")
    monkeypatch.setenv("NVIDIA_API_KEY", "dummy-nvidia-key")
    zetaris, _meterless = build_adapters(
        _offline_settings(sample_mode=False, zetaris_api_key=None),
        "live",
    )
    assert zetaris.enabled() is False
    with pytest.raises(ZetarisError):
        zetaris.run_sql("SELECT 1")


def _isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> Settings:
    (tmp_path / ".env").write_text(body, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    for name in ("ZETARIS_API_KEY", "METERLESS_RUNTIME_TOKEN", "NVIDIA_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return Settings(sample_mode=True)


def test_shipped_env_example_leaves_meterless_unconfigured(tmp_path, monkeypatch):
    example = Path(".env.example").read_text(encoding="utf-8")
    settings = _isolated_settings(tmp_path, monkeypatch, example)
    assert settings.zetaris_api_key is None
    assert settings.meterless_runtime_token is None
    assert settings.nvidia_api_key is None
    assert settings.secret_values() == []
    _zetaris, meterless = build_adapters(settings, "sample")
    assert meterless.enabled() is False
    with pytest.raises(Exception) as caught:
        meterless.complete([{"role": "user", "content": "hello"}])
    assert "token" in str(caught.value).lower()


def test_placeholder_secrets_are_treated_as_unset(tmp_path, monkeypatch):
    body = (
        "ZETARIS_API_KEY=your-zetaris-api-key\n"
        "METERLESS_RUNTIME_TOKEN=your-nightshift-runtime-token\n"
        "NVIDIA_API_KEY=your-nvidia-key\n"
        "SAMPLE_MODE=true\n"
    )
    settings = _isolated_settings(tmp_path, monkeypatch, body)
    assert settings.zetaris_api_key is None
    assert settings.meterless_runtime_token is None
    _zetaris, meterless = build_adapters(settings, "sample")
    assert meterless.enabled() is False
    with pytest.raises(Exception) as caught:
        meterless.complete([{"role": "user", "content": "hello"}])
    assert "token" in str(caught.value).lower()


def test_process_environment_zetaris_key_overrides_dotenv(tmp_path, monkeypatch):
    settings_blank = _isolated_settings(tmp_path, monkeypatch, "ZETARIS_API_KEY=\nSAMPLE_MODE=true\n")
    assert settings_blank.zetaris_api_key is None
    monkeypatch.setenv("ZETARIS_API_KEY", "from-the-process-environment")
    loaded = Settings(sample_mode=True)
    assert loaded.zetaris_api_key == "from-the-process-environment"
    monkeypatch.setenv("ZETARIS_API_KEY", "your-zetaris-api-key")
    assert Settings(sample_mode=True).zetaris_api_key is None


def test_copied_env_example_runs_the_adapter_checks(tmp_path):
    """The documented quickstart copies .env.example to .env and then runs pytest."""

    repo = Path(__file__).resolve().parents[2]
    (tmp_path / ".env").write_text((repo / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
    env = os.environ.copy()
    env["ZETARIS_API_KEY"] = "dummy-zetaris-key"
    env["METERLESS_RUNTIME_TOKEN"] = "dummy-runtime-token"
    env["NVIDIA_API_KEY"] = "dummy-nvidia-key"
    env["PYTHONPATH"] = str(repo)
    code = """
from tests.compliance.test_adapters import _forbid_meterless_http, _offline_settings, build_adapters
module, original = _forbid_meterless_http()
try:
    zetaris, meterless = build_adapters(_offline_settings(sample_mode=True), "sample")
    assert zetaris.enabled() is True
    assert meterless.enabled() is False
    try:
        meterless.complete([{"role": "user", "content": "hello"}])
    except Exception as exc:
        assert "token" in str(exc).lower()
    else:
        raise SystemExit("complete() called the network or returned")
finally:
    module.httpx.Client = original
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
