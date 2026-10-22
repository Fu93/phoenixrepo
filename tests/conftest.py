"""Keep the suite off live Zetaris and Nightshift credentials."""

import pytest

SECRET_ENV_VARS = (
    "ZETARIS_API_KEY",
    "METERLESS_RUNTIME_TOKEN",
    "NVIDIA_API_KEY",
    "MODEL_API_KEY",
)


@pytest.fixture(autouse=True)
def isolate_provider_credentials(monkeypatch):
    """Drop process secrets. API tests also skip a developer .env file."""

    for name in SECRET_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    from api import run as api_run
    from config.settings import Settings

    def offline_settings() -> Settings:
        return Settings(_env_file=None)

    monkeypatch.setattr(api_run, "get_settings", offline_settings)
