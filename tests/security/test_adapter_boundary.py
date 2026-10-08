from pathlib import Path

from adapters.factory import build_adapters
from config.settings import Settings


def test_sponsor_adapters_stay_interface_only():
    zetaris, meterless = build_adapters(Settings(), "sample")
    assert zetaris.status()["integration"] == "INTERFACE_ONLY"
    assert meterless.status()["integration"] == "INTERFACE_ONLY"


def test_dockerfile_does_not_copy_env():
    content = Path("Dockerfile").read_text(encoding="utf-8")
    assert "COPY .env" not in content
    assert "ZETARIS_API_KEY=" not in content
    assert "METERLESS_API_KEY=" not in content
