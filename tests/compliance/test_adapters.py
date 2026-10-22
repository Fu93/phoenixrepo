import os
import subprocess
import sys
from pathlib import Path

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
    for name in ("ZETARIS_API_KEY", "METERLESS_RUNTIME_TOKEN", "NVIDIA_API_KEY"):
        env.pop(name, None)
    env["PYTHONPATH"] = str(repo)
    code = (
        "from tests.compliance.test_adapters import "
        "test_sample_adapters_are_integrated_without_credentials, "
        "test_meterless_without_a_token_does_not_call_the_network\n"
        "test_sample_adapters_are_integrated_without_credentials()\n"
        "test_meterless_without_a_token_does_not_call_the_network()\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
