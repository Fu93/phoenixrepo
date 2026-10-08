from pathlib import Path


def test_dockerfile_exists():
    assert Path("Dockerfile").exists()


def test_dockerfile_uses_cpu_only_base():
    content = Path("Dockerfile").read_text(encoding="utf-8").lower()
    assert "cuda" not in content
    assert "nvidia/cuda" not in content


def test_application_binds_to_all_interfaces():
    assert 'host="0.0.0.0"' in Path("main.py").read_text(encoding="utf-8")
