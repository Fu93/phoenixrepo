from pathlib import Path

from adapters.factory import build_adapters
from config.settings import Settings


def test_sponsor_adapters_are_real_integrations():
    zetaris, meterless = build_adapters(Settings(), "sample")
    assert zetaris.status()["integration"] == "SAMPLE_CACHE"
    assert meterless.status()["integration"] == "OPENAI_COMPATIBLE_RUNTIME"
    assert meterless.status()["name"] == "Meterless Runtime"


def test_dockerfile_does_not_copy_env():
    content = Path("Dockerfile").read_text(encoding="utf-8")
    assert "COPY .env" not in content
    assert "ZETARIS_API_KEY=" not in content
    assert "METERLESS_RUNTIME_TOKEN=" not in content
    assert "NVIDIA_API_KEY=" not in content
