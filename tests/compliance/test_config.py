from pathlib import Path


def test_env_example_exists():
    assert Path(".env.example").exists()


def test_env_example_does_not_contain_real_secret():
    content = Path(".env.example").read_text(encoding="utf-8")
    for token in ("sk-", "AIza", "Bearer "):
        assert token not in content
